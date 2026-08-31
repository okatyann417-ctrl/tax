import datetime

import docx
import pytest

from yayoi_import.journal import Entry
from yayoi_import.review import build, write_docx, write_markdown


def entry(summary, amount, *warnings, date=datetime.date(2025, 7, 1), slip_no=1):
    e = Entry.simple(
        date=date, debit_account="交際費", credit_account="現金",
        amount=amount, summary=summary, slip_no=slip_no,
    )
    e.warnings.extend(warnings)
    return e


def test_balances_net_debits_against_credits():
    entries = [entry("飲食代", 3000), entry("飲食代", 2000)]
    review = build(entries, slips=2)
    assert ("交際費", "", 5000) in review.balances
    assert ("現金", "", -5000) in review.balances


def test_the_same_warning_is_grouped_with_a_count():
    entries = [
        entry("飲食代", 3000, "[medium] 会議費: 区分記載のため要修正"),
        entry("飲食代", 2000, "[medium] 会議費: 区分記載のため要修正"),
    ]
    review = build(entries, slips=2)
    assert len(review.findings) == 1
    assert review.findings[0].count == 2
    assert review.flagged_rows == 2


def test_findings_are_ordered_by_confidence():
    entries = [
        entry("a", 1000, "[参考] 参考事項"),
        entry("b", 1000, "[medium] 念のため"),
        entry("c", 1000, "[low] 要確認"),
    ]
    review = build(entries, slips=3)
    assert [f.confidence for f in review.findings] == ["low", "medium", "参考"]


def test_the_confidence_tag_is_stripped_from_the_text():
    review = build([entry("a", 1000, "[low] 科目未確定: 内訳を要確認")], slips=1)
    assert review.findings[0].text == "科目未確定: 内訳を要確認"


def test_an_untagged_warning_defaults_to_medium():
    review = build([entry("a", 1000, "税区分を確認")], slips=1)
    assert review.findings[0].confidence == "medium"


def test_markdown_lists_every_section(tmp_path):
    path = tmp_path / "review.md"
    write_markdown(build([entry("a", 1000, "[low] 科目未確定: 内訳を要確認")], slips=1), str(path))
    text = path.read_text(encoding="utf-8")
    assert "## 科目別の残高" in text
    assert "要確認（科目・処理方針が未確定）" in text
    assert "内訳を要確認" in text


def test_docx_has_a_checkbox_and_the_detail_for_each_finding(tmp_path):
    path = tmp_path / "review.docx"
    review = build(
        [entry("a", 1000, "[low] 科目未確定: 内訳を要確認"),
         entry("b", 2000, "[medium] 税区分: 適格請求書の有無を確認")],
        slips=2,
    )
    write_docx(review, str(path))
    document = docx.Document(str(path))
    text = "\n".join(p.text for t in document.tables for r in t.rows for c in r.cells for p in c.paragraphs)
    assert text.count("□") == 2
    assert "内訳を要確認" in text
    assert "適格請求書の有無を確認" in text
    assert "交際費" in text  # 科目別の残高


def test_docx_groups_findings_by_confidence(tmp_path):
    path = tmp_path / "review.docx"
    write_docx(build([entry("a", 1000, "[low] 科目未確定: 要確認")], slips=1), str(path))
    headings = [p.text for p in docx.Document(str(path)).paragraphs]
    assert any("要確認（科目・処理方針が未確定）" in h for h in headings)
    assert not any("念のため確認" in h for h in headings)  # 該当なしの章は出さない
