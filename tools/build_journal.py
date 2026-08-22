#!/usr/bin/env python3
"""通帳・売上エクセル・経費精算書から、1本の弥生インポートCSVを作る。

弥生の仕訳日記帳に取り込む形（25列・Shift_JIS・和暦）で、日付順に並べ、
伝票No.を通しで振り直す。複合仕訳は識別フラグでまとめる。
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yayoi_import import passbook
from yayoi_import.excel_source import read_expenses, read_sales, verify_expense_totals
from yayoi_import.journal import check_balance, write_csv
from yayoi_import.rules import RuleSet


def collect(source: str, workbook: str, rules_path: str, date_from, date_to, opening=None):
    """1つの資料から仕訳を起こす。伝票No.は後でまとめて振り直す。"""
    ruleset = RuleSet.load(rules_path)
    groups: list[list] = []

    if source == "passbook":
        transactions = passbook.read_transactions(workbook)
        closing = passbook.verify_balances(transactions, opening)
        target = [t for t in transactions if date_from <= t.date <= date_to]
        net = sum(t.amount if t.kind == "in" else -t.amount for t in target)
        print(f"  通帳: 残高照合 OK（期首 {opening:,} → 明細末 {closing:,}）")
        print(f"  通帳: 対象 {len(target)}件 / 預金増減 {net:,}")
        records = [(t.kind, t.date, t.summary, t.counterparty, t.amount, []) for t in target]
    elif source == "sales":
        rows = [r for r in read_sales(workbook) if date_from <= r.date <= date_to]
        print(f"  売上: {len(rows)}件 / {sum(r.amount for r in rows):,}")
        records = [("in", r.date, r.item, r.customer, r.amount, []) for r in rows]
    else:
        rows = read_expenses(workbook)
        problems = verify_expense_totals(rows, workbook)
        if problems:
            raise SystemExit("経費精算書がシートの合計行と一致しません:\n  " + "\n  ".join(problems))
        rows = [r for r in rows if date_from <= r.date <= date_to]
        print(f"  経費: {len(rows)}件 / {sum(r.amount for r in rows):,}（シート合計行と一致）")
        records = [("out", r.date, r.category, r.content, r.amount, r.warnings) for r in rows]

    for kind, date, summary, counterparty, amount, notes in records:
        built = ruleset.build(
            date=date, kind=kind, summary=summary,
            counterparty=counterparty, amount=amount, slip_no=0,
        )
        if not built:
            continue
        for note in notes:
            built[0].warnings.append(f"[medium] エクセルの読み取り: {note}")
        groups.append(built)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--passbook", nargs=2, metavar=("CSV", "RULES"))
    parser.add_argument("--opening", type=int, help="期首の預金残高（通帳を使う場合は必須）")
    parser.add_argument("--sales", nargs=2, metavar=("XLSX", "RULES"))
    parser.add_argument("--expenses", nargs=2, metavar=("XLSX", "RULES"))
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--review")
    parser.add_argument("--start-slip-no", type=int, default=1)
    args = parser.parse_args()

    date_from = _dt.date.fromisoformat(args.date_from)
    date_to = _dt.date.fromisoformat(args.date_to)

    groups: list[list] = []
    if args.passbook:
        if args.opening is None:
            parser.error("--passbook を使うときは --opening が必要です")
        groups += collect("passbook", *args.passbook, date_from, date_to, args.opening)
    if args.sales:
        groups += collect("sales", *args.sales, date_from, date_to)
    if args.expenses:
        groups += collect("expenses", *args.expenses, date_from, date_to)
    if not groups:
        parser.error("資料が1つも指定されていません")

    # 日付順に並べ、伝票No.を通しで振り直す。複合仕訳は同じ番号でまとめる。
    groups.sort(key=lambda g: (g[0].date, g[0].debit_account, g[0].credit_account))
    entries = []
    for offset, group in enumerate(groups):
        for entry in group:
            entry.slip_no = args.start_slip_no + offset
        entries.extend(group)

    check_balance(entries)
    debit = sum(e.debit_amount for e in entries)
    credit = sum(e.credit_amount for e in entries)
    print(f"\n仕訳 {len(groups)}伝票 / {len(entries)}行")
    print(f"借方合計 {debit:,} / 貸方合計 {credit:,} → {'貸借一致' if debit == credit else '★不一致★'}")

    written = write_csv(args.out, entries)
    print(f"書き出し: {args.out}（{written}行, Shift_JIS, 和暦）")

    if args.review:
        write_review(args.review, entries, len(groups))
        print(f"確認リスト: {args.review}")
    return 0


def write_review(path, entries, slips) -> None:
    flagged = [(e, w) for e in entries for w in e.warnings]
    by_account = collections.Counter()
    for entry in entries:
        if entry.debit_account:
            by_account[(entry.debit_account, entry.debit_sub)] += entry.debit_amount
        if entry.credit_account:
            by_account[(entry.credit_account, entry.credit_sub)] -= entry.credit_amount

    lines = [
        "# インポート前チェックリスト",
        "",
        f"- {slips}伝票 / {len(entries)}行",
        f"- 要確認: {len({w for _, w in flagged})}種類 / {len(flagged)}件",
        "",
        "## 科目別の残高",
        "",
        "| 勘定科目 | 補助科目 | 借方－貸方 |",
        "| --- | --- | ---: |",
    ]
    for (account, sub), amount in sorted(by_account.items(), key=lambda x: -abs(x[1])):
        lines.append(f"| {account} | {sub} | {amount:,} |")

    lines += ["", "## 要確認の仕訳", "", "| 日付 | 伝票No | 借方 | 貸方 | 金額 | 確認事項 |",
              "| --- | ---: | --- | --- | ---: | --- |"]
    seen = set()
    for entry, warning in flagged:
        if warning in seen:
            continue
        seen.add(warning)
        count = sum(1 for _, w in flagged if w == warning)
        debit = f"{entry.debit_account}{'/' + entry.debit_sub if entry.debit_sub else ''}"
        credit = f"{entry.credit_account}{'/' + entry.credit_sub if entry.credit_sub else ''}"
        suffix = f"（該当{count}件）" if count > 1 else ""
        lines.append(
            f"| {entry.date} | {entry.slip_no} | {debit} | {credit} "
            f"| {entry.amount:,} | {warning}{suffix} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
