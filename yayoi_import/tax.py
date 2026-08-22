"""消費税の税区分名と税額。

弥生会計の仕訳インポート形式では、税額は仕入税額控除の割合を掛ける前の全額を書き、
経過措置かどうかは税区分名（「区分80%」）のほうで表す。実ファイル419件で確認済み。

  課対仕入内10%適格     … 適格請求書あり。全額控除
  課対仕入内10%区分80%  … 区分記載請求書等。8割控除の経過措置
  課対仕入内軽減8%適格  … 軽減税率
  課税売上内10%         … 課税売上
  対象外 / 非課売上     … 税額なし
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

#: 税区分の基本形 → 税率(%)。
TAX_RATES: dict[str, int] = {
    "課税売上10%": 10,
    "課対仕入10%": 10,
    "課税売上8%（軽）": 8,
    "課対仕入8%（軽）": 8,
}

#: 仕入税額控除の割合 → 弥生の税区分名に入る文字列。
INVOICE_LABELS: dict[str, str] = {
    "100%": "適格",
    "80%経過措置": "区分80%",
    "50%経過措置": "区分50%",
}

DEDUCTION_RATES: dict[str, Decimal] = {
    "100%": Decimal(1),
    "80%経過措置": Decimal("0.8"),
    "50%経過措置": Decimal("0.5"),
}

#: 税区分の基本形 → 弥生の税区分名のひな形。{invoice} に適格／区分80%が入る。
_NAME_TEMPLATES: dict[str, str] = {
    "課対仕入10%": "課対仕入内10%{invoice}",
    "課対仕入8%（軽）": "課対仕入内軽減8%{invoice}",
    "課税売上10%": "課税売上内10%",
    "課税売上8%（軽）": "課税売上内軽減8%",
}


def category_name(tax_category: str, deduction: str = "100%") -> str:
    """弥生の税区分名を組み立てる。"""
    template = _NAME_TEMPLATES.get(tax_category)
    if template is None:
        return tax_category  # 対象外・非課売上など、そのまま使うもの
    label = INVOICE_LABELS.get(deduction)
    if label is None:
        raise ValueError(f"仕入税額控除の割合が不正です: {deduction!r}")
    return template.format(invoice=label)


def tax_amount(amount: int, tax_category: str) -> int:
    """インポートCSVに書く消費税額。内税・切捨て、控除割合は掛けない。"""
    rate = TAX_RATES.get(tax_category)
    if rate is None:
        return 0
    return amount * rate // (100 + rate)


def deductible_tax(amount: int, tax_category: str, deduction: str = "100%") -> int:
    """実際に控除できる消費税額。

    インポートCSVには書かないが、申告書上の仕入税額を見積もるのに使う。
    弥生は切捨て後の税額に控除割合を掛け、四捨五入する。
    """
    base = tax_amount(amount, tax_category)
    ratio = DEDUCTION_RATES.get(deduction)
    if ratio is None:
        raise ValueError(f"仕入税額控除の割合が不正です: {deduction!r}")
    if ratio == 1:
        return base
    return int((Decimal(base) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
