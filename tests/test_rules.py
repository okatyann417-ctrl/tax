import datetime
import textwrap

import pytest

from yayoi_import.rules import RuleSet

RULES = textwrap.dedent(
    """
    bank:
      account: 普通預金
      sub: A銀行
    fallback:
      account: 仮払金
    rules:
      - name: ATM引出
        kind: out
        summary_pattern: "^カード$"
        account: 現金
        summary: カード
      - name: 借入返済
        kind: out
        summary_pattern: "公庫"
        summary: 公庫
        split:
          - {account: 長期借入金, amount: 43000}
          - {account: 支払利息, remainder: true}
      - name: 報酬(源泉あり)
        kind: out
        counterparty_pattern: "ゼイリシ"
        summary: 税理士報酬
        compound:
          debit:
            - {account: 支払報酬料, amount: 165000, tax_category: 課対仕入10%}
          credit:
            - {bank: true}
            - {account: 預り金, amount: 15315, summary: 源泉所得税}
      - name: 回収
        kind: in
        counterparty_pattern: "トクイサキ"
        account: 売掛金
        sub: 得意先A
        summary: "{counterparty}"
    """
)


@pytest.fixture
def ruleset(tmp_path):
    path = tmp_path / "rules.yaml"
    path.write_text(RULES, encoding="utf-8")
    return RuleSet.load(str(path))


def build(ruleset, **kwargs):
    base = dict(
        date=datetime.date(2025, 7, 10),
        kind="out",
        summary="",
        counterparty="",
        amount=0,
        slip_no=1,
    )
    base.update(kwargs)
    return ruleset.build(**base)


def test_withdrawal_puts_bank_on_credit(ruleset):
    (entry,) = build(ruleset, summary="カード", amount=500000)
    assert (entry.debit_account, entry.credit_account) == ("現金", "普通預金")
    assert entry.credit_sub == "A銀行"
    assert entry.summary == "カード"


def test_deposit_puts_bank_on_debit(ruleset):
    (entry,) = build(ruleset, kind="in", counterparty="トクイサキ", amount=165000)
    assert (entry.debit_account, entry.credit_account) == ("普通預金", "売掛金")
    assert entry.credit_sub == "得意先A"
    assert entry.summary == "トクイサキ"  # {counterparty} が差し込まれる


def test_split_sends_remainder_to_interest(ruleset):
    """元本と利息は1つの複合仕訳になる。相手勘定は先頭行に全額で立つ。"""
    entries = build(ruleset, summary="公庫", amount=48771)
    assert [(e.debit_account, e.debit_amount) for e in entries] == [
        ("長期借入金", 43000),
        ("支払利息", 5771),
    ]
    assert [e.flag for e in entries] == ["2110", "2101"]
    assert entries[0].credit_account == "普通預金"
    assert entries[0].credit_amount == 48771
    assert entries[1].credit_account == ""      # 2行目に相手勘定は立てない
    assert sum(e.debit_amount for e in entries) == sum(e.credit_amount for e in entries)


def test_split_rejects_amount_below_fixed_part(ruleset):
    with pytest.raises(ValueError, match="内訳の合計"):
        build(ruleset, summary="公庫", amount=1000)


def test_compound_uses_flags_not_a_suspense_account(ruleset):
    entries = build(ruleset, counterparty="ゼイリシ", amount=149685)
    assert len(entries) == 2
    assert [e.flag for e in entries] == ["2110", "2101"]
    assert all(e.entry_type == 3 for e in entries)
    # 借方は報酬総額、貸方は手取額と源泉税に割れる
    assert (entries[0].debit_account, entries[0].debit_amount) == ("支払報酬料", 165000)
    assert (entries[0].credit_account, entries[0].credit_amount) == ("普通預金", 149685)
    assert (entries[1].credit_account, entries[1].credit_amount) == ("預り金", 15315)
    assert entries[1].debit_account == ""
    # 報酬総額に消費税が付き、貸借は一致する
    assert entries[0].debit_tax_amount == 15000
    assert sum(e.debit_amount for e in entries) == sum(e.credit_amount for e in entries) == 165000


def test_compound_detects_wrong_bank_amount(ruleset):
    with pytest.raises(ValueError, match="銀行側の金額"):
        build(ruleset, counterparty="ゼイリシ", amount=100000)


def test_unmatched_transaction_falls_back_and_warns(ruleset):
    (entry,) = build(ruleset, summary="ナゾノシハライ", amount=1234)
    assert entry.debit_account == "仮払金"
    assert entry.warnings and "ルール未登録" in entry.warnings[0]


def test_all_rows_share_the_slip_number(ruleset):
    entries = build(ruleset, counterparty="ゼイリシ", amount=149685, slip_no=36)
    assert {e.slip_no for e in entries} == {36}


EXCLUDE_RULES = textwrap.dedent(
    """
    contra:
      account: 売掛金
    rules:
      - name: 通帳で計上済み(取込対象外)
        kind: in
        counterparty_pattern: "税務署"
        exclude: true
      - name: 売上
        kind: in
        counterparty_pattern: "トクイサキ"
        contra_sub: 得意先A
        account: 売上高
        tax_category: 課税売上10%
    """
)


@pytest.fixture
def sales_ruleset(tmp_path):
    path = tmp_path / "sales.yaml"
    path.write_text(EXCLUDE_RULES, encoding="utf-8")
    return RuleSet.load(str(path))


def test_excluded_transaction_produces_no_entry(sales_ruleset):
    assert build(sales_ruleset, kind="in", counterparty="西税務署", amount=80) == []


def test_contra_sub_is_set_per_rule(sales_ruleset):
    (entry,) = build(sales_ruleset, kind="in", counterparty="トクイサキ", amount=165000)
    assert (entry.debit_account, entry.debit_sub) == ("売掛金", "得意先A")
    assert entry.credit_account == "売上高"
    assert entry.credit_tax_amount == 15000


SMALL_AMOUNT_RULES = textwrap.dedent(
    """
    contra:
      account: 現金
    small_amount_special:
      apply: true
      threshold: 10000
    rules:
      - name: 飲食代
        kind: out
        summary_pattern: "接待費"
        account: 交際費
        tax_category: 課対仕入10%
        deduction: 80%経過措置
    """
)


@pytest.fixture
def small_amount_ruleset(tmp_path):
    path = tmp_path / "keihi.yaml"
    path.write_text(SMALL_AMOUNT_RULES, encoding="utf-8")
    return RuleSet.load(str(path))


def test_under_the_threshold_is_treated_as_qualified(small_amount_ruleset):
    """少額特例は税区分名に出る。税額はどちらでも控除前の全額。"""
    (entry,) = build(small_amount_ruleset, summary="接待費", amount=9999)
    assert entry.debit_tax_category == "課対仕入内10%適格"
    assert entry.debit_tax_amount == 909


def test_at_or_above_the_threshold_keeps_the_transitional_category(small_amount_ruleset):
    (entry,) = build(small_amount_ruleset, summary="接待費", amount=10000)
    assert entry.debit_tax_category == "課対仕入内10%区分80%"
    assert entry.debit_tax_amount == 909


def test_special_is_off_unless_applied(tmp_path):
    path = tmp_path / "off.yaml"
    path.write_text(SMALL_AMOUNT_RULES.replace("apply: true", "apply: false"), encoding="utf-8")
    (entry,) = build(RuleSet.load(str(path)), summary="接待費", amount=9999)
    assert entry.debit_tax_category == "課対仕入内10%区分80%"


def test_registration_number_in_the_summary_wins_over_the_transitional_rate(small_amount_ruleset):
    """摘要に登録番号(T+13桁)があれば、区分記載の指定より優先して適格とする。"""
    (entry,) = build(
        small_amount_ruleset, summary="接待費", counterparty="飲食代(T9011201021882)㈱annex",
        amount=59500,
    )
    assert entry.debit_tax_category == "課対仕入内10%適格"


def test_a_number_that_is_not_a_registration_number_is_ignored(small_amount_ruleset):
    (entry,) = build(
        small_amount_ruleset, summary="接待費", counterparty="飲食代(T90112010)", amount=59500
    )
    assert entry.debit_tax_category == "課対仕入内10%区分80%"


def test_detection_can_be_turned_off(tmp_path):
    path = tmp_path / "off.yaml"
    path.write_text(
        SMALL_AMOUNT_RULES.replace("contra:", "detect_invoice_number: false\ncontra:"),
        encoding="utf-8",
    )
    (entry,) = build(
        RuleSet.load(str(path)), summary="接待費",
        counterparty="飲食代(T9011201021882)", amount=59500,
    )
    assert entry.debit_tax_category == "課対仕入内10%区分80%"
