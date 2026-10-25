from datetime import date
from decimal import Decimal

from gulf_idp.normalize import detect_currency, parse_amount, parse_date, to_ascii_digits


def test_digits():
    assert to_ascii_digits("١٢٣٤٥٦٧٨٩٠") == "1234567890"
    assert to_ascii_digits("۱۲") == "12"


def test_amounts():
    assert parse_amount("١٢٬٤٥٠٫٠٠٠ د.ك") == (Decimal("12450.000"), "KWD")
    assert parse_amount("SAR 1,150.00") == (Decimal("1150.00"), "SAR")
    assert parse_amount("1.234,50 AED") == (Decimal("1234.50"), "AED")
    assert parse_amount("٤٥٠٠ ر.ق") == (Decimal("4500"), "QAR")
    assert parse_amount("n/a")[0] is None
    assert detect_currency("ريال سعودي") == "SAR" and detect_currency("درهم") == "AED"


def test_dates():
    assert parse_date("٢٠٢٦/٠٣/١٥")[:2] == (date(2026, 3, 15), "gregorian")
    assert parse_date("15/03/2026")[0] == date(2026, 3, 15)
    assert parse_date("15 مارس 2026")[0] == date(2026, 3, 15)
    assert parse_date("March 3 2026")[0] is None  # day-first only
    g, cal, _ = parse_date("١ رمضان ١٤٤٧ هـ")
    assert cal == "hijri" and g == date(2026, 2, 18)
    assert parse_date("1447/09/01")[1] == "hijri"
    assert parse_date("31/02/2026")[0] is None
