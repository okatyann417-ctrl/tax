"""通帳・Excelの1取引を、勘定科目付きの仕訳に変換するルールエンジン。

ルールはYAMLで持たせる。事務所ごと・関与先ごとに科目や取引先が違うため、
判定ロジックをコードに埋め込まず、担当者が編集できる形にしておく。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

from .journal import Entry
from .tax import tax_amount

#: 確信度。low の仕訳は取り込み前に必ず人が確認する。
CONFIDENCE_LEVELS = ("high", "medium", "low")


@dataclass
class Rule:
    """1つの判定ルール。上から順に評価し、最初に当たったものを使う。"""

    name: str
    #: 単一仕訳のときの相手科目。split / compound を使う場合は空でよい。
    account: str = ""
    sub: str = ""
    tax_category: str = "対象外"
    #: 仕入税額控除の割合。"100%" または "80%経過措置"。
    deduction: str = "100%"
    summary: str = ""
    confidence: str = "high"
    note: str = ""

    #: 相手勘定（通常は預金や現金）をこのルールだけ差し替えたいときに指定する。
    #: 売上のように、相手が売掛金で補助科目が取引先ごとに変わる場合に使う。
    contra_account: str | None = None
    contra_sub: str | None = None

    #: このルールに当たった取引を仕訳にしない。別のエクセルや通帳で
    #: 計上済みのものが混ざっている場合に使う。
    exclude: bool = False

    kind: str | None = None  # "in" / "out" / None(両方)
    summary_pattern: str | None = None
    counterparty_pattern: str | None = None
    amount_equals: int | None = None

    #: 元本と利息のように、1つの入出金を複数科目へ按分する場合の内訳。
    #: 各要素の ``amount`` を先に取り、残額を ``remainder: true`` の科目へ寄せる。
    split: list[dict[str, Any]] = field(default_factory=list)

    #: 源泉徴収のように、入出金額と仕訳金額が一致しない複合仕訳の内訳。
    #: ``{"debit": [...], "credit": [...]}`` の形で、諸口を挟んだ複数行に展開する。
    compound: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence が不正です: {self.confidence!r} ({self.name})")
        self._summary_re = re.compile(self.summary_pattern) if self.summary_pattern else None
        self._counterparty_re = (
            re.compile(self.counterparty_pattern) if self.counterparty_pattern else None
        )
        if not self.account and not self.split and not self.compound and not self.exclude:
            raise ValueError(f"相手科目も内訳も指定されていません: {self.name}")

    def matches(self, kind: str, summary: str, counterparty: str, amount: int) -> bool:
        if self.kind is not None and self.kind != kind:
            return False
        if self._summary_re and not self._summary_re.search(summary):
            return False
        if self._counterparty_re and not self._counterparty_re.search(counterparty):
            return False
        if self.amount_equals is not None and self.amount_equals != amount:
            return False
        return True


class RuleSet:
    """ルールの集合。取引に当てはめて仕訳を組み立てる。"""

    def __init__(self, config: dict[str, Any]):
        # 相手勘定。通帳なら普通預金、経費精算書なら現金、売上なら売掛金。
        contra = config.get("contra") or config["bank"]
        self.bank_account: str = contra["account"]
        self.bank_sub: str = contra.get("sub", "")
        self.suspense_account: str = config.get("suspense_account", "諸口")
        self.fallback: dict[str, Any] = config.get("fallback", {})
        self.rules: list[Rule] = [Rule(**r) for r in config["rules"]]

    @classmethod
    def load(cls, path: str) -> "RuleSet":
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh))

    def find(self, kind: str, summary: str, counterparty: str, amount: int) -> Rule | None:
        for rule in self.rules:
            if rule.matches(kind, summary, counterparty, amount):
                return rule
        return None

    def build(
        self,
        *,
        date,
        kind: str,
        summary: str,
        counterparty: str,
        amount: int,
        slip_no: int,
    ) -> list[Entry]:
        """1取引 → 仕訳(1行以上)。当てはまるルールがなければ仮勘定に落とす。"""
        rule = self.find(kind, summary, counterparty, amount)
        if rule is not None and rule.exclude:
            return []
        if rule is None:
            return [self._fallback_entry(date, kind, summary, counterparty, amount, slip_no)]
        if rule.compound:
            return self._compound_entries(rule, date, kind, summary, counterparty, amount, slip_no)
        if rule.split:
            return self._split_entries(rule, date, kind, summary, counterparty, amount, slip_no)
        return [self._entry(rule, date, kind, summary, counterparty, amount, slip_no, amount)]

    # -- 内部 --------------------------------------------------------------

    def _label(self, rule: Rule, summary: str, counterparty: str) -> str:
        """摘要を組み立てる。

        ルールで摘要を決めているならそれをそのまま使う。通帳の表記は半角カナで
        読みづらいため、ルール側で整えた文言を優先する。
        """
        if rule.summary:
            # {counterparty} を書いておくと、振込先の名義をそこに差し込む。
            return rule.summary.format(summary=summary, counterparty=counterparty)
        return " ".join(part for part in (summary, counterparty) if part)

    def _entry(
        self, rule, date, kind, summary, counterparty, amount, slip_no, bank_amount
    ) -> Entry:
        tax = tax_amount(amount, rule.tax_category, rule.deduction)
        bank = dict(
            account=rule.contra_account or self.bank_account,
            sub=self.bank_sub if rule.contra_sub is None else rule.contra_sub,
            tax_category="対象外",
            tax_amount=0,
        )
        other = dict(
            account=rule.account, sub=rule.sub, tax_category=rule.tax_category, tax_amount=tax
        )
        # 入金なら銀行が借方、出金なら銀行が貸方。
        debit, credit = (bank, other) if kind == "in" else (other, bank)
        entry = Entry(
            date=date,
            amount=amount,
            slip_no=slip_no,
            summary=self._label(rule, summary, counterparty),
            debit_account=debit["account"],
            debit_sub=debit["sub"],
            debit_tax_category=debit["tax_category"],
            debit_tax_amount=debit["tax_amount"],
            credit_account=credit["account"],
            credit_sub=credit["sub"],
            credit_tax_category=credit["tax_category"],
            credit_tax_amount=credit["tax_amount"],
        )
        if rule.confidence != "high":
            entry.warnings.append(f"[{rule.confidence}] {rule.name}: {rule.note}".strip(": "))
        return entry

    def _split_entries(
        self, rule, date, kind, summary, counterparty, amount, slip_no
    ) -> list[Entry]:
        """元本＋利息のように、1つの入出金を複数科目へ割り振る。

        金額固定の内訳を先に取り、残額を ``remainder: true`` の科目に寄せる。
        """
        fixed = sum(p["amount"] for p in rule.split if "amount" in p)
        if fixed > amount:
            raise ValueError(f"内訳の合計が入出金額を超えています: {rule.name} {date}")
        entries: list[Entry] = []
        for part in rule.split:
            part_amount = part.get("amount", amount - fixed)
            if part_amount <= 0:
                continue
            part_rule = Rule(
                name=f"{rule.name}／{part['account']}",
                contra_account=rule.contra_account,
                contra_sub=rule.contra_sub,
                account=part["account"],
                sub=part.get("sub", ""),
                tax_category=part.get("tax_category", "対象外"),
                deduction=part.get("deduction", "100%"),
                summary=part.get("summary", rule.summary),
                confidence=part.get("confidence", rule.confidence),
                note=part.get("note", rule.note),
            )
            entries.append(
                self._entry(
                    part_rule, date, kind, summary, counterparty, part_amount, slip_no, amount
                )
            )
        return entries

    def _compound_entries(
        self, rule, date, kind, summary, counterparty, amount, slip_no
    ) -> list[Entry]:
        """源泉徴収のように、入出金額と仕訳金額が一致しない仕訳を諸口で表現する。

        弥生は同一伝票No.の連続行を1つの複合仕訳として取り込む。片側を諸口に
        しておき、諸口の借方合計と貸方合計が一致するように行を並べる。
        """
        sides = {
            "debit": list(rule.compound.get("debit", [])),
            "credit": list(rule.compound.get("credit", [])),
        }
        totals = {}
        for name, parts in sides.items():
            fixed = sum(p["amount"] for p in parts if "amount" in p)
            for part in parts:
                # 金額の指定がない行(通常は銀行側)は残額を引き受ける。
                part.setdefault("amount", None)
            totals[name] = fixed
        gross = max(totals["debit"], totals["credit"])
        for name, parts in sides.items():
            for part in parts:
                if part["amount"] is None:
                    part["amount"] = gross - totals[name]

        debit_total = sum(p["amount"] for p in sides["debit"])
        credit_total = sum(p["amount"] for p in sides["credit"])
        if debit_total != credit_total:
            raise ValueError(
                f"複合仕訳の貸借が一致しません: {rule.name} {date} 借方{debit_total} 貸方{credit_total}"
            )
        bank_side_total = sum(
            p["amount"] for parts in sides.values() for p in parts if p.get("bank")
        )
        if bank_side_total != amount:
            raise ValueError(
                f"銀行側の金額が入出金額と一致しません: {rule.name} {date} "
                f"仕訳{bank_side_total} 通帳{amount}"
            )

        entries: list[Entry] = []
        for name, parts in sides.items():
            for part in parts:
                leg = self._leg(part, rule)
                # 借方明細なら貸方が諸口、貸方明細なら借方が諸口。
                other = dict(
                    account=self.suspense_account, sub="", tax_category="対象外", tax_amount=0
                )
                debit, credit = (leg, other) if name == "debit" else (other, leg)
                entries.append(
                    Entry(
                        date=date,
                        amount=part["amount"],
                        slip_no=slip_no,
                        summary=part.get("summary") or self._label(rule, summary, counterparty),
                        debit_account=debit["account"],
                        debit_sub=debit["sub"],
                        debit_tax_category=debit["tax_category"],
                        debit_tax_amount=debit["tax_amount"],
                        credit_account=credit["account"],
                        credit_sub=credit["sub"],
                        credit_tax_category=credit["tax_category"],
                        credit_tax_amount=credit["tax_amount"],
                    )
                )
        if rule.confidence != "high" or rule.note:
            label = rule.confidence if rule.confidence != "high" else "参考"
            entries[0].warnings.append(f"[{label}] {rule.name}: {rule.note}".strip(": "))
        return entries

    def _leg(self, part: dict[str, Any], rule: Rule) -> dict[str, Any]:
        """複合仕訳の1明細を、勘定科目・補助科目・税区分に展開する。"""
        if part.get("bank"):
            return dict(
                account=self.bank_account, sub=self.bank_sub, tax_category="対象外", tax_amount=0
            )
        category = part.get("tax_category", "対象外")
        return dict(
            account=part["account"],
            sub=part.get("sub", ""),
            tax_category=category,
            tax_amount=tax_amount(part["amount"], category, part.get("deduction", "100%")),
        )

    def _fallback_entry(self, date, kind, summary, counterparty, amount, slip_no) -> Entry:
        account = self.fallback.get("account", "仮払金" if kind == "out" else "仮受金")
        rule = Rule(
            name="未判定",
            account=account,
            confidence="low",
            note="ルール未登録。科目を手当てしてから取り込むこと",
        )
        return self._entry(rule, date, kind, summary, counterparty, amount, slip_no, amount)


def collect_warnings(entries: Iterable[Entry]) -> list[tuple[Entry, str]]:
    """確認が必要な仕訳を平坦化して返す。"""
    return [(e, w) for e in entries for w in e.warnings]
