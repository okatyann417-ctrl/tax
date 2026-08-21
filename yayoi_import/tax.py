"""消費税額の計算。

弥生会計が実際に付けている税額に合わせてある（前期の仕訳日記帳419件で検証済み）。
内税の税額は ``切捨て(税込 × 税率/(100+税率))``、
80%経過措置はその切捨て後の額に0.8を掛けて四捨五入する。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

#: 税区分名 → 税率(%)。対象外・非課税は税額が立たない。
TAX_RATES: dict[str, int] = {
    "課税売上10%": 10,
    "課対仕入10%": 10,
    "課税売上8%（軽）": 8,
    "課対仕入8%（軽）": 8,
}

#: 仕入税額控除の割合。インボイス経過措置。
DEDUCTION_RATES: dict[str, Decimal] = {
    "100%": Decimal(1),
    "80%経過措置": Decimal("0.8"),
    "50%経過措置": Decimal("0.5"),
}


def tax_amount(amount: int, tax_category: str, deduction: str = "100%") -> int:
    """税込金額から、弥生が付けるのと同じ消費税額を求める。"""
    rate = TAX_RATES.get(tax_category)
    if rate is None:
        return 0
    base = amount * rate // (100 + rate)  # 内税・切捨て
    ratio = DEDUCTION_RATES.get(deduction)
    if ratio is None:
        raise ValueError(f"仕入税額控除の割合が不正です: {deduction!r}")
    if ratio == 1:
        return base
    return int((Decimal(base) * ratio).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
