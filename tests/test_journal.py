import datetime

import pytest

from yayoi_import.journal import (
    COLUMNS,
    Entry,
    JournalError,
    check_balance,
    format_date,
    mark_compound,
    write_csv,
)


def make_entry(**kwargs):
    base = dict(
        date=datetime.date(2025, 7, 1),
        debit_account="旅費交通費",
        credit_account="現金",
        amount=12580,
    )
    base.update(kwargs)
    return Entry.simple(**base)


def test_row_has_25_columns():
    assert len(make_entry().to_row()) == len(COLUMNS) == 25


def test_row_matches_the_layout_yayoi_exports():
    entry = make_entry(
        summary="新幹線代",
        debit_tax_category="課対仕入内10%適格",
        debit_tax_amount=1143,
        slip_no=7,
    )
    row = entry.to_row()
    assert row[0] == "2000"             # 識別フラグ
    assert row[1] == "7"                # 伝票No.
    assert row[3] == "R.07/07/01"       # 取引日付（和暦）
    assert row[4] == "旅費交通費"
    assert row[7] == "課対仕入内10%適格"
    assert row[8] == "12580"
    assert row[9] == "1143"
    assert row[10] == "現金"
    assert row[14] == "12580"
    assert row[16] == "新幹線代"
    assert row[19] == "0"               # タイプ
    assert row[20] == ""                # 生成元
    assert row[22] == row[23] == "0"    # 付箋
    assert row[24] == "no"              # 調整


def test_date_is_written_in_the_japanese_era():
    assert format_date(datetime.date(2024, 7, 1)) == "R.06/07/01"
    assert format_date(datetime.datetime(2026, 6, 30, 9, 0)) == "R.08/06/30"
    assert format_date(None) == ""


def test_dates_before_reiwa_are_rejected():
    with pytest.raises(JournalError, match="令和より前"):
        format_date(datetime.date(2018, 12, 31))


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


def test_simple_entry_must_balance():
    entry = make_entry()
    entry.credit_amount = 999
    with pytest.raises(JournalError, match="貸借が一致しません"):
        entry.to_row()


def test_quoting_matches_yayoi(tmp_path):
    """伝票No.・金額・税額・タイプ以外は引用符で囲む。"""
    path = tmp_path / "out.csv"
    write_csv(str(path), [make_entry(summary="新幹線代", slip_no=1, debit_tax_amount=1143)])
    line = path.read_text(encoding="cp932").rstrip("\r\n")
    assert line.startswith('"2000",1,"","R.07/07/01","旅費交通費"')
    assert ',12580,1143,' in line
    assert line.endswith(',0,"","","0","0","no"')


def test_csv_is_shift_jis_with_crlf(tmp_path):
    path = tmp_path / "out.csv"
    write_csv(str(path), [make_entry(summary="新幹線代", slip_no=1)])
    raw = path.read_bytes()
    assert raw.endswith(b"\r\n")
    assert "新幹線代".encode("cp932") in raw


def test_lookalike_characters_are_substituted(tmp_path):
    entry = make_entry(summary="東京出張（22⁻30日）")
    path = tmp_path / "x.csv"
    write_csv(str(path), [entry])
    assert "東京出張（22-30日）" in path.read_text(encoding="cp932")
    assert any("置換" in w for w in entry.warnings)


def test_unencodable_character_is_reported(tmp_path):
    with pytest.raises(JournalError, match="変換できない文字"):
        write_csv(str(tmp_path / "x.csv"), [make_entry(summary="⼀")])


def compound_pair():
    """源泉徴収のある報酬。借方165,000に対し、貸方は預金と預り金に割れる。"""
    return [
        Entry(
            date=datetime.date(2025, 9, 30), slip_no=36, summary="税理士報酬",
            debit_account="支払報酬料", debit_amount=165000, debit_tax_amount=15000,
            credit_account="普通預金", credit_sub="三菱UFJ銀行", credit_amount=149685,
        ),
        Entry(
            date=datetime.date(2025, 9, 30), slip_no=36, summary="源泉所得税",
            credit_account="預り金", credit_amount=15315,
        ),
    ]


def test_compound_flags_are_assigned_in_order():
    entries = compound_pair()
    mark_compound(entries)
    assert [e.flag for e in entries] == ["2110", "2101"]
    assert all(e.entry_type == 3 for e in entries)


def test_compound_middle_rows_get_2100():
    entries = compound_pair() + [
        Entry(date=datetime.date(2025, 9, 30), slip_no=36, credit_account="雑収入", credit_amount=1)
    ]
    entries[1], entries[2] = entries[2], entries[1]
    mark_compound(entries)
    assert [e.flag for e in entries] == ["2110", "2100", "2101"]


def test_a_single_row_group_stays_simple():
    entries = [make_entry(slip_no=1)]
    mark_compound(entries)
    assert entries[0].flag == "2000" and entries[0].entry_type == 0


def test_compound_row_may_have_an_empty_side():
    entries = compound_pair()
    mark_compound(entries)
    row = entries[1].to_row()
    assert row[4] == "" and row[8] == "0"      # 借方は空
    assert row[10] == "預り金" and row[14] == "15315"


def test_check_balance_accepts_a_balanced_group():
    entries = compound_pair()
    mark_compound(entries)
    check_balance(entries)


def test_check_balance_catches_an_unbalanced_group():
    entries = compound_pair()
    entries[1].credit_amount = 1
    mark_compound(entries)
    with pytest.raises(JournalError, match="貸借が一致しません"):
        check_balance(entries)
