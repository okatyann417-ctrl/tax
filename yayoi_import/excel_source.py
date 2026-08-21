"""関与先が使っているエクセル（売上入力・出金入力・経費精算書）を読む。

いずれも会計ソフトの出力ではなく手入力の帳票なので、日付の年がずれている、
空行が挟まる、合計行が混ざるといった揺れがある。ここで正規化して、
後段のルールエンジンには整った取引だけを渡す。
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

import openpyxl


@dataclass
class SalesRow:
    date: _dt.date
    customer: str
    item: str
    amount: int
    source_row: int


@dataclass
class ExpenseRow:
    date: _dt.date
    category: str      # 経費精算書の費目欄（交通費・接待費など）
    content: str       # 内容欄
    amount: int
    sheet: str
    source_row: int
    warnings: list[str] = field(default_factory=list)


#: 経費精算書の費目欄。列番号 → 費目名。
EXPENSE_COLUMNS = {3: "宿泊費", 4: "交通費", 5: "郵送料", 6: "通信費", 7: "会議費", 8: "接待費", 9: "その他"}

_SHEET_MONTH = re.compile(r"^(\d+)年(\d+)月$")


def _to_wareki_date(year_field: int, month: int, day: int) -> _dt.date:
    """入力欄の「年」は和暦（令和）。西暦に直す。"""
    return _dt.date(2018 + year_field, month, day)


def read_sales(path: str, sheet: str = "売上入力", first_row: int = 5) -> list[SalesRow]:
    """売上入力シートを読む。日付は締日、金額はK列（売上金額・税込）。"""
    ws = openpyxl.load_workbook(path, data_only=True)[sheet]
    rows: list[SalesRow] = []
    for r in range(first_row, ws.max_row + 1):
        year, month, day = (ws.cell(r, c).value for c in (2, 3, 4))
        if not all(isinstance(v, int) for v in (year, month, day)):
            continue
        amount = ws.cell(r, 11).value or 0
        if not amount:
            continue
        rows.append(
            SalesRow(
                date=_to_wareki_date(year, month, day),
                customer=(ws.cell(r, 6).value or "").strip(),
                item=(ws.cell(r, 8).value or "").strip(),
                amount=int(amount),
                source_row=r,
            )
        )
    return rows


def read_expenses(path: str) -> list[ExpenseRow]:
    """経費精算書（月ごとのシート）を読む。

    日付欄の「年」は入力時にずれていることがあるため、シート名の年月を正とし、
    日付欄からは日だけを取る。月がシートと食い違う行には注記を付ける。
    """
    workbook = openpyxl.load_workbook(path, data_only=True)
    rows: list[ExpenseRow] = []
    for name in workbook.sheetnames:
        matched = _SHEET_MONTH.match(name)
        if not matched:
            continue
        year = 2000 + int(matched.group(1))
        month = int(matched.group(2))
        ws = workbook[name]
        for r in range(9, ws.max_row + 1):
            content = ws.cell(r, 10).value
            if content == "合計":
                break
            if not ws.cell(r, 15).value:
                continue
            warnings: list[str] = []
            cell_date = ws.cell(r, 1).value
            day = cell_date.day if isinstance(cell_date, _dt.datetime) else None
            if day is None:
                day = _days_in_month(year, month)
                warnings.append("日付欄が空のため月末で計上")
            elif isinstance(cell_date, _dt.datetime) and cell_date.month != month:
                warnings.append(
                    f"日付欄の月({cell_date.month}月)がシート({month}月)と違うためシートを優先"
                )
            for column, category in EXPENSE_COLUMNS.items():
                amount = ws.cell(r, column).value
                if not amount:
                    continue
                rows.append(
                    ExpenseRow(
                        date=_dt.date(year, month, min(day, _days_in_month(year, month))),
                        category=category,
                        content=(content or "").strip(),
                        amount=int(amount),
                        sheet=name,
                        source_row=r,
                        warnings=list(warnings),
                    )
                )
    return rows


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (_dt.date(year, month + 1, 1) - _dt.timedelta(days=1)).day
