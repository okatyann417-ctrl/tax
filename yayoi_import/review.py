"""取り込み前の確認リストを組み立て、MarkdownとWordに書き出す。

同じ仕訳データから両方を作る。Markdownは手元での確認用、Wordは
事務所内での回付や関与先との打合せ用。
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field

from .journal import Entry

#: 確信度の並び順。取り込み前に必ず見るべきものを先に出す。
CONFIDENCE_ORDER = ("low", "medium", "参考")

CONFIDENCE_LABELS = {
    "low": "要確認（科目・処理方針が未確定）",
    "medium": "念のため確認",
    "参考": "参考（処理は確定）",
}

_TAG = re.compile(r"^\[(low|medium|参考)\]\s*")


@dataclass
class Finding:
    confidence: str
    text: str
    count: int
    entry: Entry

    @property
    def debit(self) -> str:
        return _label(self.entry.debit_account, self.entry.debit_sub)

    @property
    def credit(self) -> str:
        return _label(self.entry.credit_account, self.entry.credit_sub)


@dataclass
class Review:
    slips: int
    rows: int
    balances: list[tuple[str, str, int]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def flagged_rows(self) -> int:
        return sum(f.count for f in self.findings)


def _label(account: str, sub: str) -> str:
    if not account:
        return ""
    return f"{account}／{sub}" if sub else account


def build(entries: list[Entry], slips: int) -> Review:
    """仕訳から確認リストの中身を組み立てる。"""
    balances: collections.Counter = collections.Counter()
    for entry in entries:
        if entry.debit_account:
            balances[(entry.debit_account, entry.debit_sub)] += entry.debit_amount
        if entry.credit_account:
            balances[(entry.credit_account, entry.credit_sub)] -= entry.credit_amount

    # 同じ理由の警告は代表1件にまとめ、件数を添える。
    counts: collections.Counter = collections.Counter()
    first: dict[str, Entry] = {}
    for entry in entries:
        for warning in entry.warnings:
            counts[warning] += 1
            first.setdefault(warning, entry)

    findings = []
    for warning, count in counts.items():
        matched = _TAG.match(warning)
        confidence = matched.group(1) if matched else "medium"
        findings.append(
            Finding(
                confidence=confidence,
                text=_TAG.sub("", warning),
                count=count,
                entry=first[warning],
            )
        )
    findings.sort(
        key=lambda f: (CONFIDENCE_ORDER.index(f.confidence), -f.count, f.entry.date)
    )

    return Review(
        slips=slips,
        rows=len(entries),
        balances=[
            (account, sub, amount)
            for (account, sub), amount in sorted(balances.items(), key=lambda x: -abs(x[1]))
        ],
        findings=findings,
    )


def write_markdown(review: Review, path: str) -> None:
    lines = [
        "# インポート前チェックリスト",
        "",
        f"- {review.slips}伝票 / {review.rows}行",
        f"- 要確認: {len(review.findings)}種類 / {review.flagged_rows}件",
        "",
        "## 科目別の残高",
        "",
        "| 勘定科目 | 補助科目 | 借方－貸方 |",
        "| --- | --- | ---: |",
    ]
    lines += [f"| {a} | {s} | {v:,} |" for a, s, v in review.balances]
    for confidence in CONFIDENCE_ORDER:
        group = [f for f in review.findings if f.confidence == confidence]
        if not group:
            continue
        lines += [
            "",
            f"## {CONFIDENCE_LABELS[confidence]}",
            "",
            "| 日付 | 伝票No | 借方 | 貸方 | 金額 | 件数 | 確認事項 |",
            "| --- | ---: | --- | --- | ---: | ---: | --- |",
        ]
        for f in group:
            e = f.entry
            lines.append(
                f"| {e.date} | {e.slip_no} | {f.debit} | {f.credit} "
                f"| {e.amount:,} | {f.count} | {f.text} |"
            )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def write_docx(review: Review, path: str, title: str = "インポート前チェックリスト") -> None:
    """Word形式で書き出す。確信度ごとに章を分け、確認欄を付ける。"""
    import docx
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor

    MINCHO, GOTHIC = "游明朝", "游ゴシック"
    NAVY = RGBColor(0x1F, 0x38, 0x64)

    document = docx.Document()
    section = document.sections[0]
    section.left_margin = section.right_margin = docx.shared.Cm(1.8)
    section.top_margin = section.bottom_margin = docx.shared.Cm(2.0)

    normal = document.styles["Normal"]
    normal.font.name = MINCHO
    normal.font.size = Pt(9.5)
    normal.element.rPr.rFonts.set(docx.oxml.ns.qn("w:eastAsia"), MINCHO)

    def heading(text: str, size: int, space_before: int = 12) -> None:
        p = document.add_paragraph()
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(4)
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(size)
        run.font.color.rgb = NAVY
        run.font.name = GOTHIC
        run.element.rPr.rFonts.set(docx.oxml.ns.qn("w:eastAsia"), GOTHIC)

    def table(headers: list[str], rows: list[list[str]], widths: list[float],
              right: set[int]) -> None:
        t = document.add_table(rows=1, cols=len(headers))
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.LEFT
        for index, header in enumerate(headers):
            cell = t.rows[0].cells[index]
            cell.text = ""
            run = cell.paragraphs[0].add_run(header)
            run.bold = True
            run.font.size = Pt(9)
            run.font.name = GOTHIC
            run.element.rPr.rFonts.set(docx.oxml.ns.qn("w:eastAsia"), GOTHIC)
        for row in rows:
            cells = t.add_row().cells
            for index, value in enumerate(row):
                cells[index].text = ""
                paragraph = cells[index].paragraphs[0]
                if index in right:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                run = paragraph.add_run(value)
                run.font.size = Pt(8.5)
        for row in t.rows:
            for index, width in enumerate(widths):
                row.cells[index].width = docx.shared.Cm(width)

    heading(title, 15, space_before=0)
    summary = document.add_paragraph()
    summary.add_run(
        f"{review.slips}伝票 / {review.rows}行　"
        f"要確認 {len(review.findings)}種類・{review.flagged_rows}件"
    ).font.size = Pt(10)

    heading("科目別の残高", 12)
    table(
        ["勘定科目", "補助科目", "借方－貸方"],
        [[a, s, f"{v:,}"] for a, s, v in review.balances],
        [4.5, 6.0, 4.0],
        right={2},
    )

    for confidence in CONFIDENCE_ORDER:
        group = [f for f in review.findings if f.confidence == confidence]
        if not group:
            continue
        heading(f"{CONFIDENCE_LABELS[confidence]}（{len(group)}種類）", 12)
        # 確認事項は文章が長いので、列に押し込めず1件2〜3行の縦組みにする。
        t = document.add_table(rows=0, cols=2)
        t.style = "Table Grid"
        t.alignment = WD_TABLE_ALIGNMENT.LEFT
        for finding in group:
            entry = finding.entry
            cells = t.add_row().cells
            cells[0].text = ""
            box = cells[0].paragraphs[0]
            box.alignment = WD_ALIGN_PARAGRAPH.CENTER
            box.add_run("□").font.size = Pt(12)

            cells[1].text = ""
            title_p = cells[1].paragraphs[0]
            title_p.paragraph_format.space_after = Pt(2)
            run = title_p.add_run(
                finding.text.split(":")[0] + (f"（{finding.count}件）" if finding.count > 1 else "")
            )
            run.bold = True
            run.font.size = Pt(9.5)
            run.font.name = GOTHIC
            run.element.rPr.rFonts.set(docx.oxml.ns.qn("w:eastAsia"), GOTHIC)

            where = cells[1].add_paragraph()
            where.paragraph_format.space_after = Pt(2)
            run = where.add_run(
                f"{entry.date}　伝票{entry.slip_no}　"
                f"{finding.debit or '（空）'} ／ {finding.credit or '（空）'}　"
                f"{entry.amount:,}円"
            )
            run.font.size = Pt(8.5)
            run.font.color.rgb = RGBColor(0x55, 0x5F, 0x70)

            body = cells[1].add_paragraph()
            body.paragraph_format.space_after = Pt(4)
            detail = finding.text.split(":", 1)
            body.add_run(detail[1].strip() if len(detail) > 1 else finding.text).font.size = Pt(9)

        for row in t.rows:
            row.cells[0].width = docx.shared.Cm(1.2)
            row.cells[1].width = docx.shared.Cm(16.2)

    document.save(path)
