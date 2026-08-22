#!/usr/bin/env python3
"""OCRした通帳PDFで、手起こしの取引明細CSVを裏取りする。"""

from __future__ import annotations

import argparse
import collections
import csv
import datetime
import os
import sys

import pdfplumber

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yayoi_import.ocr_check import Page, check


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transactions", help="手で起こした取引明細CSV")
    parser.add_argument("pdfs", nargs="+", help="OCRした通帳PDF")
    parser.add_argument("--min-digits", type=int, default=3, help="照合する最小桁数")
    args = parser.parse_args()

    pages = []
    for path in args.pdfs:
        with pdfplumber.open(path) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                pages.append(Page(f"{os.path.basename(path)} p{number}", page.extract_text() or ""))
    for page in pages:
        note = "" if page.legible else "  ← ほぼ判読できていません"
        print(f"{page.name}: 数字 {page.digit_count}文字{note}")
    legible = [p for p in pages if p.legible]
    print(f"判読できたページ: {len(legible)}/{len(pages)}")

    rows = list(csv.DictReader(open(args.transactions, encoding="utf-8")))
    values = []
    for row in rows:
        label = f"{row['date']} {row['summary']}{row['counterparty']}"
        for kind in ("amount", "balance"):
            raw = row[kind].strip()
            if len(raw) >= args.min_digits:
                values.append((
                    f"{label} {'金額' if kind == 'amount' else '残高'}",
                    int(raw),
                    datetime.date.fromisoformat(row["date"]),
                ))

    findings = check(values, pages)
    tally = collections.Counter(f.status for f in findings)
    print(f"\n照合 {len(findings)}項目")
    for status in ("確認済み", "未確認", "要確認"):
        print(f"  {status}: {tally[status]}件")

    suspect = [f for f in findings if f.status == "要確認"]
    if suspect:
        print("\n--- 読み違いの可能性がある項目 ---")
        for finding in suspect:
            print(f"  {finding.label} {finding.value:,}  （{finding.page} に惜しい数字あり）")
    print("\n「未確認」はOCRがその行を拾えていないもので、転記との矛盾ではありません。")
    return 1 if suspect else 0


if __name__ == "__main__":
    raise SystemExit(main())
