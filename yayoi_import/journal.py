"""弥生会計の仕訳インポート形式（25列固定・Shift_JIS）の仕訳データとCSV書き出し。

列の並び・引用符の付け方・日付の書式・複合仕訳の表し方は、弥生会計から
「弥生インポート形式」で書き出した実ファイル（528行）に合わせてある。

複合仕訳は諸口ではなく識別フラグで表す。同じ伝票No.の連続行を
2110（先頭）→2100（中間）→2101（末尾）と並べ、タイプに3を立てる。
各行は借方・貸方をそれぞれ独立に持ち、片側だけの行は反対側を空にして金額0とする。
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass, field
from typing import Iterable, Sequence

#: 25列の論理名。並び順がそのままCSVの列順になる。
COLUMNS: tuple[str, ...] = (
    "識別フラグ", "伝票No", "決算", "取引日付",
    "借方勘定科目", "借方補助科目", "借方部門", "借方税区分", "借方金額", "借方税金額",
    "貸方勘定科目", "貸方補助科目", "貸方部門", "貸方税区分", "貸方金額", "貸方税金額",
    "摘要", "番号", "期日", "タイプ", "生成元", "仕訳メモ", "付箋1", "付箋2", "調整",
)

#: 引用符を付けない列（弥生の書き出しに合わせる）。伝票No.・金額・税額・タイプ。
UNQUOTED_COLUMNS = frozenset({1, 8, 9, 14, 15, 19})

#: 識別フラグ。
FLAG_SIMPLE = "2000"      # 単一仕訳
FLAG_COMPOUND_FIRST = "2110"
FLAG_COMPOUND_MIDDLE = "2100"
FLAG_COMPOUND_LAST = "2101"

#: タイプ。複合仕訳の行は3。
TYPE_SIMPLE = 0
TYPE_COMPOUND = 3

ENCODING = "cp932"
CLOSING_VALUES = ("", "中決", "本決")

#: 手入力のエクセルに紛れ込みやすい、Shift_JISに無い文字の置き換え表。
#: 見た目が同じで文字コードだけ違うものに限る。意味が変わる置換はしない。
CP932_SUBSTITUTES = {
    "⁻": "-", "−": "-", "‒": "-", "–": "-",
    "—": "―", "∼": "～", "≡": "=", " ": " ",
}


class JournalError(ValueError):
    """仕訳データが弥生の取り込み要件を満たさないときに送出する。"""


def sanitize(text: str) -> tuple[str, list[str]]:
    """Shift_JISに無い文字を、見た目が同じ文字へ置き換える。"""
    changes = []
    for bad, good in CP932_SUBSTITUTES.items():
        if bad in text:
            changes.append(f"{bad!r} を {good!r} に置換")
            text = text.replace(bad, good)
    return text, changes


def format_date(value: _dt.date | _dt.datetime | str | None) -> str:
    """取引日付を弥生の書式（和暦 ``R.06/07/01``）に整える。"""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, _dt.datetime):
        value = value.date()
    if value.year < 2019:
        raise JournalError(f"令和より前の日付は扱えません: {value}")
    return f"R.{value.year - 2018:02d}/{value.month:02d}/{value.day:02d}"


@dataclass
class Entry:
    """インポートCSVの1行。

    単一仕訳は借方と貸方が同額になる。複合仕訳の構成行は、片側だけを持つことがある。
    """

    date: _dt.date | _dt.datetime | str

    debit_account: str = ""
    debit_sub: str = ""
    debit_dept: str = ""
    debit_tax_category: str = "対象外"
    debit_amount: int = 0
    debit_tax_amount: int = 0

    credit_account: str = ""
    credit_sub: str = ""
    credit_dept: str = ""
    credit_tax_category: str = "対象外"
    credit_amount: int = 0
    credit_tax_amount: int = 0

    summary: str = ""
    slip_no: int | str = ""
    closing: str = ""
    number: str = ""
    due_date: _dt.date | _dt.datetime | str | None = None
    memo: str = ""
    adjustment: str = "no"

    flag: str = FLAG_SIMPLE
    entry_type: int = TYPE_SIMPLE

    #: 取り込み前に人が確認すべき点。CSVには出力せず、確認リストに使う。
    warnings: list[str] = field(default_factory=list, repr=False)

    @classmethod
    def simple(cls, *, date, debit_account, credit_account, amount, **kwargs) -> "Entry":
        """借方・貸方が同額の単一仕訳を作る。"""
        return cls(
            date=date,
            debit_account=debit_account,
            credit_account=credit_account,
            debit_amount=amount,
            credit_amount=amount,
            **kwargs,
        )

    @property
    def amount(self) -> int:
        """その行が動かす金額。単一仕訳では借方＝貸方。"""
        return max(self.debit_amount, self.credit_amount)

    def validate(self) -> None:
        """弥生が弾く典型的な不備を取り込み前に検出する。"""
        for name, value in (("借方金額", self.debit_amount), ("貸方金額", self.credit_amount)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise JournalError(f"{name}は整数である必要があります: {value!r}")
            if value < 0:
                raise JournalError(f"{name}は0以上である必要があります: {value}")
        if self.debit_amount == 0 and self.credit_amount == 0:
            raise JournalError(f"借方・貸方とも金額が0です: {self.date} {self.summary}")
        if self.debit_amount and not self.debit_account:
            raise JournalError(f"借方金額があるのに勘定科目が空です: {self.date} {self.summary}")
        if self.credit_amount and not self.credit_account:
            raise JournalError(f"貸方金額があるのに勘定科目が空です: {self.date} {self.summary}")
        if self.flag == FLAG_SIMPLE and self.debit_amount != self.credit_amount:
            raise JournalError(
                f"単一仕訳の貸借が一致しません: {self.date} "
                f"借方{self.debit_amount} 貸方{self.credit_amount}"
            )
        if self.closing not in CLOSING_VALUES:
            raise JournalError(f"決算欄が不正です: {self.closing!r}")
        if not format_date(self.date):
            raise JournalError("取引日付が空です")

    def to_row(self) -> list[str]:
        """25列のCSV行に変換する。"""
        self.validate()
        summary, changes = sanitize(self.summary)
        memo, memo_changes = sanitize(self.memo)
        for change in changes + memo_changes:
            note = f"[medium] 摘要の文字を置換: {change}"
            if note not in self.warnings:
                self.warnings.append(note)
        return [
            self.flag,
            str(self.slip_no),
            self.closing,
            format_date(self.date),
            self.debit_account,
            self.debit_sub,
            self.debit_dept,
            self.debit_tax_category if self.debit_account else "対象外",
            str(self.debit_amount),
            str(self.debit_tax_amount),
            self.credit_account,
            self.credit_sub,
            self.credit_dept,
            self.credit_tax_category if self.credit_account else "対象外",
            str(self.credit_amount),
            str(self.credit_tax_amount),
            summary,
            self.number,
            format_date(self.due_date),
            str(self.entry_type),
            "",      # 生成元
            memo,
            "0",     # 付箋1
            "0",     # 付箋2
            self.adjustment,
        ]


def mark_compound(entries: Sequence[Entry]) -> None:
    """複数行で1つの仕訳になるグループに、識別フラグとタイプを立てる。"""
    if len(entries) == 1:
        entries[0].flag = FLAG_SIMPLE
        entries[0].entry_type = TYPE_SIMPLE
        return
    for index, entry in enumerate(entries):
        if index == 0:
            entry.flag = FLAG_COMPOUND_FIRST
        elif index == len(entries) - 1:
            entry.flag = FLAG_COMPOUND_LAST
        else:
            entry.flag = FLAG_COMPOUND_MIDDLE
        entry.entry_type = TYPE_COMPOUND


def check_balance(entries: Iterable[Entry]) -> None:
    """伝票No.ごとに貸借が一致しているか確かめる。"""
    groups: dict[object, list[Entry]] = {}
    for entry in entries:
        groups.setdefault((entry.slip_no, format_date(entry.date)), []).append(entry)
    for (slip_no, date), group in groups.items():
        debit = sum(e.debit_amount for e in group)
        credit = sum(e.credit_amount for e in group)
        if debit != credit:
            raise JournalError(
                f"伝票No.{slip_no}（{date}）の貸借が一致しません: 借方{debit:,} 貸方{credit:,}"
            )


def write_csv(path: str, entries: Iterable[Entry], encoding: str = ENCODING) -> int:
    """仕訳を弥生インポート形式のCSVに書き出し、書いた行数を返す。

    弥生の書き出しに合わせ、伝票No.・金額・税額・タイプ以外は引用符で囲む。
    """
    entries = list(entries)
    rows = [entry.to_row() for entry in entries]
    lines = []
    for row in rows:
        cells = [
            value if index in UNQUOTED_COLUMNS else '"' + value.replace('"', '""') + '"'
            for index, value in enumerate(row)
        ]
        lines.append(",".join(cells))
    body = "\r\n".join(lines) + "\r\n"
    try:
        with open(path, "w", encoding=encoding, newline="") as fh:
            fh.write(body)
    except UnicodeEncodeError as exc:
        raise JournalError(
            f"{encoding} に変換できない文字が含まれています: {exc.object[exc.start:exc.end]!r}"
        ) from exc
    return len(rows)
