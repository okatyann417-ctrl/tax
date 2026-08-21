#!/usr/bin/env python3
"""通帳CSV → 弥生会計インポートCSV ＋ 確認リストを生成する。"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yayoi_import import passbook
from yayoi_import.journal import write_csv
from yayoi_import.rules import RuleSet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transactions", help="通帳の取引明細CSV")
    parser.add_argument("rules", help="仕訳ルールYAML")
    parser.add_argument("--opening", type=int, required=True, help="期首の預金残高")
    parser.add_argument("--from", dest="date_from", required=True, help="対象期間の開始日")
    parser.add_argument("--to", dest="date_to", required=True, help="対象期間の終了日")
    parser.add_argument("--out", required=True, help="出力する弥生インポートCSV")
    parser.add_argument("--review", help="確認リスト(Markdown)の出力先")
    parser.add_argument("--start-slip-no", type=int, default=1, help="伝票No.の開始番号")
    args = parser.parse_args()

    date_from = _dt.date.fromisoformat(args.date_from)
    date_to = _dt.date.fromisoformat(args.date_to)

    transactions = passbook.read_transactions(args.transactions)
    closing = passbook.verify_balances(transactions, args.opening)
    print(f"残高照合 OK: 期首 {args.opening:,} → 明細末 {closing:,}")

    target = [t for t in transactions if date_from <= t.date <= date_to]
    net = sum(t.amount if t.kind == "in" else -t.amount for t in target)
    print(f"対象期間 {date_from}〜{date_to}: {len(target)}件 / 増減 {net:,}")

    ruleset = RuleSet.load(args.rules)
    entries = passbook.to_entries(target, ruleset, args.start_slip_no)
    passbook.verify_entries(entries, ruleset.bank_account, net)
    print(f"仕訳 {len(entries)}行 / 預金増減の照合 OK")

    written = write_csv(args.out, entries)
    print(f"書き出し: {args.out} ({written}行, Shift_JIS)")

    if args.review:
        write_review(args.review, entries, target, args.opening, args.opening + net)
        print(f"確認リスト: {args.review}")
    return 0


def write_review(path, entries, transactions, opening, closing) -> None:
    flagged = [(e, w) for e in entries for w in e.warnings]
    lines = [
        "# 普通預金 インポート前チェックリスト",
        "",
        f"- 取引件数: {len(transactions)}件 / 仕訳行数: {len(entries)}行",
        f"- 期首残高: {opening:,}円 → 期末残高: {closing:,}円",
        f"- 要確認: {len(flagged)}件",
        "",
        "## 要確認の仕訳",
        "",
        "| 日付 | 伝票No | 借方 | 貸方 | 金額 | 確認事項 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for entry, warning in flagged:
        debit = f"{entry.debit_account}{'/' + entry.debit_sub if entry.debit_sub else ''}"
        credit = f"{entry.credit_account}{'/' + entry.credit_sub if entry.credit_sub else ''}"
        lines.append(
            f"| {entry.date} | {entry.slip_no} | {debit} | {credit} "
            f"| {entry.amount:,} | {warning} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
