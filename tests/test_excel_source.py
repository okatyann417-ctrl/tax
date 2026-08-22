import datetime

import openpyxl
import pytest

from yayoi_import.excel_source import (
    read_expenses,
    read_sales,
    sheet_totals,
    verify_expense_totals,
)


@pytest.fixture
def sales_book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "売上入力"
    for column, value in enumerate(["年", "月", "日", "コード", "取引先名"], start=2):
        ws.cell(4, column, value)
    # 年は和暦。R7/7/20 と R8/6/30。
    for row, (y, m, d, name, item, amount) in enumerate(
        [(7, 7, 20, "得意先A", "顧問料", 166180), (8, 6, 30, "得意先B", "営業支援", 9717)],
        start=5,
    ):
        ws.cell(row, 2, y), ws.cell(row, 3, m), ws.cell(row, 4, d)
        ws.cell(row, 6, name), ws.cell(row, 8, item), ws.cell(row, 11, amount)
    ws.cell(7, 2, 8), ws.cell(7, 3, 6), ws.cell(7, 4, 30)  # 金額なしの行は読み飛ばす
    path = tmp_path / "sales.xlsx"
    wb.save(path)
    return str(path)


def test_wareki_year_is_converted(sales_book):
    rows = read_sales(sales_book)
    assert [r.date for r in rows] == [datetime.date(2025, 7, 20), datetime.date(2026, 6, 30)]
    assert [r.amount for r in rows] == [166180, 9717]


@pytest.fixture
def expense_book(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "25年7月"
    # 日付欄の年がずれている行（入力時のオートフィル）
    ws.cell(9, 1, datetime.datetime(2026, 7, 28))
    ws.cell(9, 4, 5000), ws.cell(9, 10, "スイカチャージ代"), ws.cell(9, 15, 5000)
    # 日付欄が空の行
    ws.cell(10, 9, 440000), ws.cell(10, 10, "東京出張22日"), ws.cell(10, 15, 440000)
    # 1行に複数の費目が入る場合
    ws.cell(11, 1, datetime.datetime(2025, 7, 20))
    ws.cell(11, 7, 2140), ws.cell(11, 8, 18920), ws.cell(11, 10, "飲食代"), ws.cell(11, 15, 21060)
    ws.cell(12, 10, "合計"), ws.cell(12, 15, 466060)
    wb.create_sheet("集計")  # 月次シート以外は無視する
    path = tmp_path / "keihi.xlsx"
    wb.save(path)
    return str(path)


def test_sheet_month_wins_over_cell_year(expense_book):
    rows = read_expenses(expense_book)
    first = rows[0]
    assert first.date == datetime.date(2025, 7, 28)  # シートの年月＋日付欄の日
    assert first.category == "交通費"


def test_missing_date_falls_back_to_month_end(expense_book):
    row = next(r for r in read_expenses(expense_book) if "出張" in r.content)
    assert row.date == datetime.date(2025, 7, 31)
    assert row.warnings == ["日付欄が空のため月末で計上"]


def test_one_line_can_hold_several_categories(expense_book):
    rows = [r for r in read_expenses(expense_book) if r.content == "飲食代"]
    assert sorted((r.category, r.amount) for r in rows) == [("会議費", 2140), ("接待費", 18920)]


def test_total_row_stops_reading(expense_book):
    assert not any(r.content == "合計" for r in read_expenses(expense_book))


def test_rows_without_the_amount_column_are_still_read(tmp_path):
    """右端の「金額」欄が空でも、費目欄に金額があれば計上する。

    関与先の経費精算書は金額欄の入力漏れが多く、合計行のO列も式が崩れている。
    金額の正は費目欄（宿泊費〜その他）のほうにある。
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "25年8月"
    ws.cell(9, 9, 560000), ws.cell(9, 10, "東京出張28日間")  # O列は空
    ws.cell(10, 1, datetime.datetime(2025, 8, 20))
    ws.cell(10, 4, 5000), ws.cell(10, 10, "スイカチャージ代"), ws.cell(10, 15, 5000)
    path = tmp_path / "keihi.xlsx"
    wb.save(path)
    rows = read_expenses(str(path))
    assert sorted(r.amount for r in rows) == [5000, 560000]


def test_totals_are_checked_against_each_sheet(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "25年8月"
    ws.cell(9, 4, 5000), ws.cell(9, 10, "交通費"), ws.cell(9, 15, 5000)
    ws.cell(10, 9, 560000), ws.cell(10, 10, "出張手当")
    # 合計行は費目欄の合計で読む（O列は当てにしない）
    ws.cell(11, 4, 5000), ws.cell(11, 9, 560000), ws.cell(11, 10, "合計"), ws.cell(11, 15, 5000)
    path = tmp_path / "keihi.xlsx"
    wb.save(path)
    assert sheet_totals(str(path)) == {"25年8月": 565000}
    assert verify_expense_totals(read_expenses(str(path)), str(path)) == []


def test_mismatched_total_is_reported(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "25年8月"
    ws.cell(9, 4, 5000), ws.cell(9, 10, "交通費"), ws.cell(9, 15, 5000)
    ws.cell(10, 4, 9999), ws.cell(10, 10, "合計")
    path = tmp_path / "keihi.xlsx"
    wb.save(path)
    problems = verify_expense_totals(read_expenses(str(path)), str(path))
    assert problems and "一致しません" in problems[0]


def test_fractional_amount_is_truncated_and_flagged(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "26年4月"
    ws.cell(9, 9, 49.6), ws.cell(9, 10, "ゴルフプレー費")
    path = tmp_path / "keihi.xlsx"
    wb.save(path)
    (row,) = read_expenses(str(path))
    assert row.amount == 49
    assert any("整数ではありません" in w for w in row.warnings)
