import datetime

from yayoi_import.ocr_check import Page, check, digit_strings


def test_digit_strings_recovers_ocr_letter_confusions():
    # OCRは 8 を a、0 を u と読み違えることがある
    assert "48393" in digit_strings("08-6..10 | *4a393 金融公庫")
    assert "499005" in digit_strings("*49a005* T326")


def test_digit_strings_ignores_separators_and_ocr_noise():
    # アンダースコアやアポストロフィが数字の間に落ちても拾える
    assert "272029" in digit_strings("*272_,02'9* LT18")
    assert "105849" in digit_strings("*10 5,8,49")


def test_comma_misread_as_a_digit_is_not_recovered():
    """桁区切りのカンマが数字として読まれた場合は復元しない。

    間の1文字を消して回ると誤検出が増えるため、ここは諦めて「未確認」に倒す。
    残高の連鎖で検算できるので、この取りこぼしは実害にならない。
    """
    assert "287208" not in digit_strings("*2871208* RT326")


def make_pages(text, name="p1"):
    return [Page(name=name, text=text)]


LEGIBLE = "\n".join(
    [
        "08-1-5 *369,315* Q T326",
        "08-1-13 金融公庫 *48652 *318,903* RT326",
        "08-1-19 BZ *1,760",
        "08-1-20 *220,000 *538,903* LT326",
        "08-1-23 *165,000 *703,903* LT326",
        "08-1-27 *A00,000 *303,903* T326",
    ]
)


def test_amount_present_in_ocr_is_confirmed():
    findings = check([("1/13 金融公庫", 48652, datetime.date(2026, 1, 13))], make_pages(LEGIBLE))
    assert findings[0].status == "確認済み"


def test_letter_confusion_still_counts_as_confirmed():
    # *A00,000 は 400,000 の読み違い
    findings = check([("1/27 カード", 400000, datetime.date(2026, 1, 27))], make_pages(LEGIBLE))
    assert findings[0].status == "確認済み"


def test_missing_line_is_unconfirmed_not_a_conflict():
    findings = check([("1/30 振込", 792000, datetime.date(2026, 1, 30))], make_pages(LEGIBLE))
    assert findings[0].status == "未確認"


def test_near_miss_on_the_covering_page_is_flagged():
    # OCRに 318,903 があるので、318,908 と起こしていれば読み違いを疑う
    findings = check([("1/13 残高", 318908, datetime.date(2026, 1, 13))], make_pages(LEGIBLE))
    assert findings[0].status == "要確認"
    assert findings[0].page == "p1"


def test_near_miss_outside_the_page_period_is_not_flagged():
    # 同じ数字でも、そのページが収録していない日付なら判定しない
    findings = check([("3/13 残高", 318908, datetime.date(2026, 3, 13))], make_pages(LEGIBLE))
    assert findings[0].status == "未確認"


def test_illegible_page_never_flags_near_misses():
    page = Page(name="p2", text="08-1-13 ｜ ー ＿ 一 ／")
    findings = check([("1/13 残高", 318908, datetime.date(2026, 1, 13))], [page])
    assert not page.legible
    assert findings[0].status == "未確認"


def test_page_period_is_derived_from_ocr_dates():
    page = Page(name="p1", text=LEGIBLE)
    assert page.covers == (datetime.date(2026, 1, 5), datetime.date(2026, 1, 27))
