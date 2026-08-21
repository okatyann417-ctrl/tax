"""弥生会計「仕訳日記帳」インポート形式（25列固定・Shift_JIS）の仕訳データとCSV書き出し。

列順は弥生会計の仕訳インポート形式に準拠する。ヘッダー行は付けない。
実機で列順・税区分表記を確定させるため、弥生から「弥生インポート形式」で
書き出したCSVを1本入手して ``tools/verify_layout.py`` で突き合わせること。
"""

from __future__ import annotations

import csv
import datetime as _dt
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# 25列の論理名（弥生インポート形式）。並び順がそのままCSVの列順になる。
COLUMNS: tuple[str, ...] = (
    "識別フラグ",
    "伝票No",
    "決算",
    "取引日付",
    "借方勘定科目",
    "借方補助科目",
    "借方部門",
    "借方税区分",
    "借方金額",
    "借方税金額",
    "貸方勘定科目",
    "貸方補助科目",
    "貸方部門",
    "貸方税区分",
    "貸方金額",
    "貸方税金額",
    "摘要",
    "番号",
    "期日",
    "タイプ",
    "生成元",
    "仕訳メモ",
    "付箋1",
    "付箋2",
    "調整",
)

#: 弥生の仕訳データを表す識別フラグ。
IDENTIFIER_FLAG = "2000"

#: 弥生が取り込むCSVの文字コード。
ENCODING = "cp932"

#: 手入力のエクセルに紛れ込みやすい、Shift_JISに無い文字の置き換え表。
#: 見た目が同じで文字コードだけ違うものに限る。意味が変わる置換はしない。
CP932_SUBSTITUTES = {
    "\u207b": "-",   # 上付きマイナス
    "\u2212": "-",   # 全角マイナス記号
    "\u2012": "-",   # フィギュアダッシュ
    "\u2013": "-",   # エヌダッシュ
    "\u2014": "\u2015",  # エムダッシュ → 全角ダッシュ
    "\u223c": "\uff5e",  # チルダ演算子 → 全角チルダ
    "\u2261": "=",   # 合同記号
    "\u00a0": " ",   # ノーブレークスペース
}

#: 決算欄に指定できる値。空文字は通常仕訳。
CLOSING_VALUES = ("", "中決", "本決")


class JournalError(ValueError):
    """仕訳データが弥生の取り込み要件を満たさないときに送出する。"""


def sanitize(text: str) -> tuple[str, list[str]]:
    """Shift_JISに無い文字を、見た目が同じ文字へ置き換える。

    置き換えた内容も返すので、勝手に直した箇所を確認リストに出せる。
    """
    changes = []
    for bad, good in CP932_SUBSTITUTES.items():
        if bad in text:
            changes.append(f"{bad!r} を {good!r} に置換")
            text = text.replace(bad, good)
    return text, changes


def format_date(value: _dt.date | _dt.datetime | str | None) -> str:
    """取引日付を弥生が受け付ける ``YYYY/MM/DD`` 形式に整える。"""
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, _dt.datetime):
        value = value.date()
    return f"{value.year:04d}/{value.month:02d}/{value.day:02d}"


@dataclass
class Entry:
    """1行1仕訳（単一仕訳）。複合仕訳は諸口を使って複数行に分割する。"""

    date: _dt.date | _dt.datetime | str
    debit_account: str
    credit_account: str
    amount: int
    summary: str = ""

    debit_sub: str = ""
    debit_dept: str = ""
    debit_tax_category: str = "対象外"
    debit_tax_amount: int = 0

    credit_sub: str = ""
    credit_dept: str = ""
    credit_tax_category: str = "対象外"
    credit_tax_amount: int = 0

    slip_no: int | str = ""
    closing: str = ""
    number: str = ""
    due_date: _dt.date | _dt.datetime | str | None = None
    memo: str = ""
    adjustment: str = "no"

    #: 取り込み前に人が確認すべき点。CSVには出力せず、確認リストに使う。
    warnings: list[str] = field(default_factory=list, repr=False)

    def validate(self) -> None:
        """弥生が弾く典型的な不備を取り込み前に検出する。"""
        if not self.debit_account:
            raise JournalError(f"借方勘定科目が空です: {self}")
        if not self.credit_account:
            raise JournalError(f"貸方勘定科目が空です: {self}")
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise JournalError(f"金額は整数である必要があります: {self.amount!r}")
        if self.amount <= 0:
            raise JournalError(f"金額は正の値である必要があります: {self.amount}")
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
            IDENTIFIER_FLAG,
            str(self.slip_no),
            self.closing,
            format_date(self.date),
            self.debit_account,
            self.debit_sub,
            self.debit_dept,
            self.debit_tax_category,
            str(self.amount),
            str(self.debit_tax_amount),
            self.credit_account,
            self.credit_sub,
            self.credit_dept,
            self.credit_tax_category,
            str(self.amount),
            str(self.credit_tax_amount),
            summary,
            self.number,
            format_date(self.due_date),
            "0",  # タイプ
            "0",  # 生成元
            memo,
            "0",  # 付箋1
            "0",  # 付箋2
            self.adjustment,
        ]


def assign_slip_numbers(entries: Sequence[Entry], start: int = 1) -> None:
    """伝票No.が未設定の仕訳に通し番号を振る。"""
    next_no = start
    for entry in entries:
        if entry.slip_no == "":
            entry.slip_no = next_no
            next_no += 1


def write_csv(path: str, entries: Iterable[Entry], encoding: str = ENCODING) -> int:
    """仕訳を弥生インポート形式のCSVに書き出し、書いた行数を返す。

    弥生が読めない文字（機種依存文字など）が混ざっている場合は、
    取り込み時に化けるより先にここで気付けるようエラーにする。
    """
    entries = list(entries)
    rows = [entry.to_row() for entry in entries]
    try:
        with open(path, "w", encoding=encoding, newline="") as fh:
            writer = csv.writer(fh, lineterminator="\r\n")
            writer.writerows(rows)
    except UnicodeEncodeError as exc:
        raise JournalError(
            f"{encoding} に変換できない文字が含まれています: {exc.object[exc.start:exc.end]!r}"
        ) from exc
    return len(rows)
