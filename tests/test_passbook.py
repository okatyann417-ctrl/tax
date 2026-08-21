import datetime

import pytest

from yayoi_import.passbook import (
    BalanceMismatch,
    Transaction,
    read_transactions,
    verify_balances,
)

CSV = """date,kind,summary,counterparty,amount,balance
2025-07-01,in,振込1,トクイサキ,605829,
2025-07-01,out,カード,,500000,306380
2025-07-04,out,カード,,150000,156380
"""


def test_read_transactions(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text(CSV, encoding="utf-8")
    txns = read_transactions(str(path))
    assert [t.amount for t in txns] == [605829, 500000, 150000]
    assert txns[0].date == datetime.date(2025, 7, 1)
    assert txns[0].balance is None
    assert txns[1].balance == 306380


def test_verify_balances_returns_closing():
    txns = [
        Transaction(datetime.date(2025, 7, 1), "in", "振込", "", 605829),
        Transaction(datetime.date(2025, 7, 1), "out", "カード", "", 500000, 306380),
    ]
    assert verify_balances(txns, 200551) == 306380


def test_verify_balances_catches_misread_digit():
    txns = [Transaction(datetime.date(2025, 7, 1), "out", "カード", "", 50000, 306380)]
    with pytest.raises(BalanceMismatch, match="残高が合いません"):
        verify_balances(txns, 806380)


def test_invalid_kind_is_rejected(tmp_path):
    path = tmp_path / "p.csv"
    path.write_text(CSV.replace(",out,", ",OUT,"), encoding="utf-8")
    with pytest.raises(ValueError, match="kind"):
        read_transactions(str(path))
