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
    entries = build(ruleset, summary="公庫", amount=48771)
    assert [(e.debit_account, e.amount) for e in entries] == [
        ("長期借入金", 43000),
        ("支払利息", 5771),
    ]
    assert sum(e.amount for e in entries) == 48771


def test_split_rejects_amount_below_fixed_part(ruleset):
    with pytest.raises(ValueError, match="内訳の合計"):
        build(ruleset, summary="公庫", amount=1000)


def test_compound_balances_through_suspense_account(ruleset):
    entries = build(ruleset, counterparty="ゼイリシ", amount=149685)
    assert len(entries) == 3
    assert sum(e.amount for e in entries if e.credit_account == "諸口") == 165000
    assert sum(e.amount for e in entries if e.debit_account == "諸口") == 165000
    # 銀行に出ていくのは手取額だけ
    bank = [e for e in entries if e.credit_account == "普通預金"]
    assert [e.amount for e in bank] == [149685]
    # 源泉税は預り金へ
    assert [e.amount for e in entries if e.credit_account == "預り金"] == [15315]
    # 報酬総額には消費税が付く
    fee = next(e for e in entries if e.debit_account == "支払報酬料")
    assert fee.debit_tax_amount == 15000


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
