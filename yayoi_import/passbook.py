"""通帳の取引明細（CSV）を読み、弥生の仕訳へ変換する。

スキャンした通帳PDFはテキスト層を持たないため、読み取り結果はいったん
``date,kind,summary,counterparty,amount,balance`` のCSVに落とす。
``balance`` は通帳に印字されている差引残高で、転記ミスの検算に使う。
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass

from .journal import Entry
from .rules import RuleSet


@dataclass
class Transaction:
    date: _dt.date
    kind: str  # "in" (お預り) / "out" (お支払)
    summary: str
    counterparty: str
    amount: int
    balance: int | None = None


class BalanceMismatch(ValueError):
    """通帳に印字された残高と、積み上げた残高が合わないときに送出する。"""


def read_transactions(path: str) -> list[Transaction]:
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    transactions = []
    for line, row in enumerate(rows, start=2):
        kind = row["kind"].strip()
        if kind not in ("in", "out"):
            raise ValueError(f"{path}:{line} kind は in / out のいずれかです: {kind!r}")
        balance = row["balance"].strip()
        transactions.append(
            Transaction(
                date=_dt.date.fromisoformat(row["date"].strip()),
                kind=kind,
                summary=row["summary"].strip(),
                counterparty=row["counterparty"].strip(),
                amount=int(row["amount"]),
                balance=int(balance) if balance else None,
            )
        )
    return transactions


def verify_balances(transactions: list[Transaction], opening: int) -> int:
    """印字残高との突合。ズレがあれば読み取りミスなので止める。"""
    balance = opening
    for txn in transactions:
        balance += txn.amount if txn.kind == "in" else -txn.amount
        if txn.balance is not None and balance != txn.balance:
            raise BalanceMismatch(
                f"{txn.date} {txn.summary} で残高が合いません: "
                f"計算={balance:,} 通帳={txn.balance:,} 差={balance - txn.balance:,}"
            )
    return balance


def to_entries(
    transactions: list[Transaction], ruleset: RuleSet, start_slip_no: int = 1
) -> list[Entry]:
    """取引明細を仕訳に変換する。1取引が複数行になることがある。"""
    entries: list[Entry] = []
    slip_no = start_slip_no
    for txn in transactions:
        entries.extend(
            ruleset.build(
                date=txn.date,
                kind=txn.kind,
                summary=txn.summary,
                counterparty=txn.counterparty,
                amount=txn.amount,
                slip_no=slip_no,
            )
        )
        slip_no += 1
    return entries


def verify_entries(entries: list[Entry], bank_account: str, expected_net: int) -> None:
    """仕訳側の預金増減が、通帳の増減と一致することを確かめる。"""
    net = 0
    for entry in entries:
        if entry.debit_account == bank_account:
            net += entry.debit_amount
        if entry.credit_account == bank_account:
            net -= entry.credit_amount
    if net != expected_net:
        raise BalanceMismatch(
            f"仕訳の預金増減が通帳と一致しません: 仕訳={net:,} 通帳={expected_net:,}"
        )
