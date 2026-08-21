import datetime

import pytest

from yayoi_import.journal import COLUMNS, Entry, JournalError, format_date, write_csv


def make_entry(**kwargs):
    base = dict(
        date=datetime.date(2025, 7, 1),
        debit_account="旅費交通費",
        credit_account="現金",
        amount=12580,
    )
    base.update(kwargs)
    return Entry(**base)


def test_row_has_25_columns():
    assert len(make_entry().to_row()) == len(COLUMNS) == 25


def test_row_layout():
    entry = make_entry(
        summary="新幹線代", debit_tax_category="課対仕入10%", debit_tax_amount=1143, slip_no=7
    )
    row = entry.to_row()
    assert row[0] == "2000"          # 識別フラグ
    assert row[1] == "7"             # 伝票No.
    assert row[3] == "2025/07/01"    # 取引日付
    assert row[4] == "旅費交通費"
    assert row[7] == "課対仕入10%"
    assert row[8] == "12580"
    assert row[9] == "1143"
    assert row[10] == "現金"
    assert row[14] == "12580"        # 貸借同額
    assert row[16] == "新幹線代"
    assert row[24] == "no"           # 調整


def test_date_accepts_datetime_and_date():
    assert format_date(datetime.datetime(2026, 6, 30, 9, 0)) == "2026/06/30"
    assert format_date(datetime.date(2026, 6, 30)) == "2026/06/30"
    assert format_date(None) == ""


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(amount=0),
        dict(amount=-100),
        dict(debit_account=""),
        dict(credit_account=""),
        dict(closing="決算"),
    ],
)
def test_invalid_entries_are_rejected(kwargs):
    with pytest.raises(JournalError):
        make_entry(**kwargs).to_row()


def test_csv_is_shift_jis_with_crlf(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(str(path), [make_entry(summary="新幹線代", slip_no=1)])
    raw = path.read_bytes()
    assert raw.endswith(b"\r\n")
    assert "新幹線代".encode("cp932") in raw
    assert raw.decode("cp932").count(",") == 24


def test_lookalike_characters_are_substituted(tmp_path):
    # 手入力のエクセルには上付きマイナスなどが紛れ込む
    entry = make_entry(summary="東京出張（22⁻30日）")
    path = tmp_path / "x.csv"
    write_csv(str(path), [entry])
    assert "東京出張（22-30日）" in path.read_text(encoding="cp932")
    assert any("置換" in w for w in entry.warnings)


def test_unencodable_character_is_reported(tmp_path):
    with pytest.raises(JournalError, match="変換できない文字"):
        write_csv(str(tmp_path / "x.csv"), [make_entry(summary="⼀")])
