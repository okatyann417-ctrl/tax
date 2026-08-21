#!/usr/bin/env python3
"""売上入力／経費精算書のエクセル → 弥生会計インポートCSV ＋ 確認リスト。"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yayoi_import.excel_source import read_expenses, read_sales
from yayoi_import.journal import write_csv
from yayoi_import.rules import RuleSet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=["sales", "expenses"], help="読み込むエクセルの種類")
    parser.add_argument("workbook", help="エクセルのパス")
    parser.add_argument("rules", help="仕訳ルールYAML")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--review")
    parser.add_argument("--start-slip-no", type=int, default=1)
    args = parser.parse_args()

    date_from = _dt.date.fromisoformat(args.date_from)
    date_to = _dt.date.fromisoformat(args.date_to)

    if args.source == "sales":
        records = [
            ("in", r.date, r.item, r.customer, r.amount, [])
            for r in read_sales(args.workbook)
        ]
    else:
        records = [
            ("out", r.date, r.category, r.content, r.amount, r.warnings)
            for r in read_expenses(args.workbook)
        ]

    total_all = sum(r[4] for r in records)
    records = [r for r in records if date_from <= r[1] <= date_to]
    total = sum(r[4] for r in records)
    print(f"読込 {len(records)}件 / 金額 {total:,}（期間外を除く前は {total_all:,}）")
    excluded = 0

    ruleset = RuleSet.load(args.rules)
    entries = []
    for slip_no, (kind, date, summary, counterparty, amount, warnings) in enumerate(
        sorted(records, key=lambda r: r[1]), start=args.start_slip_no
    ):
        built = ruleset.build(
            date=date,
            kind=kind,
            summary=summary,
            counterparty=counterparty,
            amount=amount,
            slip_no=slip_no,
        )
        if not built:
            # 他で計上済みとして取込対象から外した取引。
            excluded += amount
            continue
        for note in warnings:
            built[0].warnings.append(f"[medium] エクセルの読み取り: {note}")
        entries.extend(built)

    if excluded:
        print(f"取込対象外 {excluded:,}（他で計上済みのため除外）")
    if sum(e.amount for e in entries) != total - excluded:
        raise SystemExit(
            f"仕訳合計が読込金額と一致しません: {sum(e.amount for e in entries):,} != {total - excluded:,}"
        )
    print(f"仕訳 {len(entries)}行 / 金額の照合 OK")

    written = write_csv(args.out, entries)
    print(f"書き出し: {args.out} ({written}行, Shift_JIS)")

    if args.review:
        write_review(args.review, entries, len(records), total - excluded)
        print(f"確認リスト: {args.review}")
    return 0


def write_review(path, entries, record_count, total) -> None:
    flagged = [(e, w) for e in entries for w in e.warnings]
    by_account = collections.Counter()
    for entry in entries:
        key = (entry.debit_account, entry.debit_sub)
        if entry.debit_account in ("売掛金", "現金"):
            key = (entry.credit_account, entry.credit_sub)
        by_account[key] += entry.amount

    lines = [
        "# インポート前チェックリスト",
        "",
        f"- 取引件数: {record_count}件 / 仕訳行数: {len(entries)}行 / 金額 {total:,}円",
        f"- 要確認: {len(flagged)}件",
        "",
        "## 科目別の集計",
        "",
        "| 勘定科目 | 補助科目 | 金額 |",
        "| --- | --- | ---: |",
    ]
    for (account, sub), amount in by_account.most_common():
        lines.append(f"| {account} | {sub} | {amount:,} |")

    lines += ["", "## 要確認の仕訳", "", "| 日付 | 借方 | 貸方 | 金額 | 確認事項 |", "| --- | --- | --- | ---: | --- |"]
    seen = set()
    for entry, warning in flagged:
        # 同じ理由が何十件も並ぶので、理由ごとに最初の1件だけ出して件数を添える。
        if warning in seen:
            continue
        seen.add(warning)
        count = sum(1 for _, w in flagged if w == warning)
        debit = f"{entry.debit_account}{'/' + entry.debit_sub if entry.debit_sub else ''}"
        credit = f"{entry.credit_account}{'/' + entry.credit_sub if entry.credit_sub else ''}"
        suffix = f"（該当{count}件）" if count > 1 else ""
        lines.append(f"| {entry.date} | {debit} | {credit} | {entry.amount:,} | {warning}{suffix} |")

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
