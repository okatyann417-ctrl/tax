"""OCRした通帳PDFを使って、手で起こした取引明細CSVを裏取りする。

通帳のスキャンにOCRをかけたPDFは、桁が英字に化けたり行ごと落ちたりするため
そのまま帳簿の元にはできない。ただし「こちらが起こした金額がOCR側にも現れるか」
という一方向の確認になら使える。

判定は3段階。
  確認済み  … OCRの文字列にその金額が現れる
  未確認    … OCRがその行を拾えていない（矛盾ではない）
  要確認    … 同じページに1桁違いの数字がある（読み違いの可能性）

あくまで補助的な確認で、これ単独で転記の正しさを保証するものではない。
転記の裏取りは通帳に印字された差引残高の連鎖（passbook.verify_balances）が主で、
こちらはその上に重ねる二重チェックとして使う。
"""

from __future__ import annotations

import datetime as _dt
import itertools
import re
from dataclasses import dataclass

#: OCRが数字を取り違えやすい英字。1文字につき数字の候補を持たせる。
CHARACTER_CONFUSIONS = {
    "a": "89", "A": "4", "b": "5", "B": "8", "o": "0", "O": "0", "u": "0", "U": "0",
    "l": "1", "I": "1", "|": "1", "i": "1", "s": "5", "S": "5", "g": "9", "q": "9",
    "Z": "2", "G": "6", "T": "7", "J": "3",
}

#: 桁区切りと、OCRが数字の間に落とすゴミ（アポストロフィなど）。
_SEPARATOR_CHARS = ",.:_ '\u2018\u2019\u02cb\uff40\u30fb"
_SEPARATORS = re.compile("[" + re.escape(_SEPARATOR_CHARS) + "]")
#: 候補の組合せが増えすぎる断片は諦める（英字だらけでほぼ判読不能な行）。
_MAX_COMBINATIONS = 4096


#: このくらい数字が拾えていないページは、OCRが失敗しているとみなす。
LEGIBLE_MIN_DIGITS = 50


def digit_strings(text: str) -> set[str]:
    """OCRテキストから、金額として読めうる数字列をすべて拾う。"""
    found = _substrings(re.sub(r"\D", "", text))
    pattern = (
        r"[\d" + re.escape("".join(CHARACTER_CONFUSIONS)) + re.escape(_SEPARATOR_CHARS) + r"]{3,}"
    )
    for match in re.finditer(pattern, text):
        core = _SEPARATORS.sub("", match.group())
        if not core or len(core) > 12:
            continue
        options = [[c] if c.isdigit() else list(CHARACTER_CONFUSIONS.get(c, "")) for c in core]
        if any(not option for option in options):
            continue
        combinations = list(itertools.product(*options))
        if len(combinations) > _MAX_COMBINATIONS:
            continue
        for combination in combinations:
            found |= _substrings("".join(combination))
    return found


def _substrings(text: str, minimum: int = 2) -> set[str]:
    return {
        text[i:j]
        for i in range(len(text))
        for j in range(i + minimum, len(text) + 1)
    }


#: 通帳に印字された和暦の日付（07-7-10 = 令和7年7月10日）。
_DATE = re.compile(r"\b(\d{2})-(\d{1,2})[-\u2010\u2011.](\d{1,2})\b")


@dataclass
class Finding:
    label: str
    value: int
    status: str  # "確認済み" / "未確認" / "要確認"
    page: str = ""


@dataclass
class Page:
    name: str
    text: str

    @property
    def digit_count(self) -> int:
        return sum(character.isdigit() for character in self.text)

    @property
    def legible(self) -> bool:
        return self.digit_count >= LEGIBLE_MIN_DIGITS

    @property
    def covers(self) -> tuple[_dt.date, _dt.date] | None:
        """OCRが拾えた日付から、そのページが収録している期間を推定する。

        惜しい数字を探す相手を、その取引が実際に載っているページに絞るために使う。
        """
        dates = []
        for wareki, month, day in _DATE.findall(self.text):
            try:
                dates.append(_dt.date(2018 + int(wareki), int(month), int(day)))
            except ValueError:
                continue
        return (min(dates), max(dates)) if len(dates) >= 3 else None


def check(values: list[tuple[str, int, _dt.date]], pages: list[Page]) -> list[Finding]:
    """金額の並びをOCRの各ページと突き合わせる。

    「要確認」は、判読できていて、かつその取引の日付を収録しているページに
    限って判定する。関係のないページで惜しい数字を探しても雑音にしかならない。
    """
    expanded = {page.name: digit_strings(page.text) for page in pages}
    findings = []
    for label, value, date in values:
        text = str(value)
        hit = next((name for name, seen in expanded.items() if text in seen), None)
        if hit:
            findings.append(Finding(label, value, "確認済み", hit))
            continue
        # 惜しい数字は、その取引が載っているページの中だけで探す。
        near = ""
        for page in pages:
            span = page.covers if page.legible else None
            if not span or not (span[0] <= date <= span[1]):
                continue
            if _has_near_miss(text, re.sub(r"\D", "", page.text)):
                near = page.name
                break
        findings.append(Finding(label, value, "要確認" if near else "未確認", near))
    return findings


def _has_near_miss(text: str, blob: str) -> bool:
    """桁数が同じで1文字だけ違う数字が同じページにあれば、読み違いを疑う。"""
    width = len(text)
    for start in range(len(blob) - width + 1):
        window = blob[start : start + width]
        if sum(1 for a, b in zip(window, text) if a != b) == 1:
            return True
    return False
