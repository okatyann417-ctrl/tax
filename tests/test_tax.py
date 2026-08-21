import pytest

from yayoi_import.tax import tax_amount


@pytest.mark.parametrize(
    "amount,category,deduction,expected",
    [
        # 前期の仕訳日記帳（弥生が付けた税額）から採った実績値
        (12580, "課対仕入10%", "100%", 1143),
        (110, "課対仕入10%", "100%", 10),
        (185, "課対仕入10%", "100%", 16),
        (10000, "課対仕入10%", "100%", 909),
        (165000, "課対仕入10%", "100%", 15000),
        # 80%経過措置は「切捨て後の税額 × 0.8」を四捨五入する
        (2600, "課対仕入10%", "80%経過措置", 189),
        (13000, "課対仕入10%", "80%経過措置", 945),
        (5841, "課対仕入10%", "80%経過措置", 425),
        (2830, "課対仕入10%", "80%経過措置", 206),
        # 税額が立たない区分
        (50000, "対象外", "100%", 0),
        (169, "非課売上", "100%", 0),
    ],
)
def test_matches_yayoi(amount, category, deduction, expected):
    assert tax_amount(amount, category, deduction) == expected


def test_unknown_deduction_is_rejected():
    with pytest.raises(ValueError):
        tax_amount(1000, "課対仕入10%", "60%")
