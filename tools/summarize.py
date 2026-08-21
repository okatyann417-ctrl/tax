#!/usr/bin/env python3
"""生成した弥生インポートCSVを読み、科目別の集計と貸借の検算を出す。

取り込む前に、複数のCSVをまたいで残高がつながっているか（売掛金の計上と回収、
現金の引出と経費）を目で確かめるために使う。
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys


def load(path: str) -> list[list[str]]:
    with open(path, encoding="cp932", newline="") as fh:
        return [row for row in csv.reader(fh) if row]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csvs", nargs="+", help="弥生インポートCSV")
    args = parser.parse_args()

    debit: collections.Counter = collections.Counter()
    credit: collections.Counter = collections.Counter()
    total_rows = 0
    for path in args.csvs:
        rows = load(path)
        total_rows += len(rows)
        subtotal = sum(int(r[8]) for r in rows)
        print(f"{path}: {len(rows)}行 / {subtotal:,}円")
        for row in rows:
            debit[(row[4], row[5])] += int(row[8])
            credit[(row[10], row[11])] += int(row[14])

    debit_total = sum(debit.values())
    credit_total = sum(credit.values())
    print(f"\n合計 {total_rows}行 / 借方 {debit_total:,} / 貸方 {credit_total:,}", end=" ")
    print("→ 貸借一致" if debit_total == credit_total else "→ ★不一致★")

    print(f"\n{'勘定科目':<16}{'補助科目':<26}{'借方':>14}{'貸方':>14}{'差引':>14}")
    print("-" * 84)
    for key in sorted(set(debit) | set(credit), key=lambda k: -(debit[k] + credit[k])):
        account, sub = key
        print(f"{account:<16}{sub:<26}{debit[key]:>14,}{credit[key]:>14,}{debit[key]-credit[key]:>14,}")
    return 0 if debit_total == credit_total else 1


if __name__ == "__main__":
    raise SystemExit(main())
