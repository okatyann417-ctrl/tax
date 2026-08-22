import pytest

from yayoi_import.tax import category_name, deductible_tax, tax_amount


@pytest.mark.parametrize(
    "base,deduction,expected",
    [
        ("課対仕入10%", "100%", "課対仕入内10%適格"),
        ("課対仕入10%", "80%経過措置", "課対仕入内10%区分80%"),
        ("課対仕入8%（軽）", "100%", "課対仕入内軽減8%適格"),
        ("課税売上10%", "100%", "課税売上内10%"),
        ("対象外", "100%", "対象外"),
        ("非課売上", "100%", "非課売上"),
    ],
)
def test_category_name_matches_yayoi(base, deduction, expected):
    assert category_name(base, deduction) == expected


def test_unknown_deduction_is_rejected():
    with pytest.raises(ValueError):
        category_name("課対仕入10%", "60%")


@pytest.mark.parametrize(
    "amount,base,expected",
    [
        # 弥生が書き出したインポート形式の実値。内税・切捨て、控除割合は掛けない
        (12580, "課対仕入10%", 1143),
        (110, "課対仕入10%", 10),
        (165000, "課対仕入10%", 15000),
        (7407, "課対仕入10%", 673),      # 区分記載でも税額は控除前の全額
        (2600, "課対仕入10%", 236),
        (100000, "対象外", 0),
        (169, "非課売上", 0),
    ],
)
def test_tax_amount_matches_the_import_format(amount, base, expected):
    assert tax_amount(amount, base) == expected


@pytest.mark.parametrize(
    "amount,deduction,expected",
    [
        # 仕訳日記帳の画面・帳票に出る「控除後」の税額
        (7407, "80%経過措置", 538),
        (2600, "80%経過措置", 189),
        (5841, "80%経過措置", 425),
        (12580, "100%", 1143),
    ],
)
def test_deductible_tax_matches_the_journal_view(amount, deduction, expected):
    assert deductible_tax(amount, "課対仕入10%", deduction) == expected
