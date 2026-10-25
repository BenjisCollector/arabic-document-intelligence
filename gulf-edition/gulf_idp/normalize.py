"""Arabic-aware normalisers: digits, amounts, Hijri/Gregorian dates."""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from hijridate import Hijri

_DIGITS = {**{0x0660 + i: str(i) for i in range(10)}, **{0x06F0 + i: str(i) for i in range(10)}}
_PUNCT = {0x066B: ".", 0x066C: ",", 0x060C: ",", 0x066A: "%", 0x061B: ";"}
_BIDI = dict.fromkeys(list(range(0x200E, 0x2010)) + list(range(0x202A, 0x202F)) + list(range(0x2066, 0x206A)))
_TASHKEEL = dict.fromkeys(list(range(0x064B, 0x0653)) + [0x0640, 0x0670])
_LETTERS = {0x0623: "ا", 0x0625: "ا", 0x0622: "ا", 0x0649: "ي", 0x0629: "ه", 0x0624: "و", 0x0626: "ي"}


def to_ascii_digits(s: str) -> str:
    """Arabic-Indic / Persian digits and Arabic separators -> ASCII."""
    return s.translate({**_DIGITS, **_PUNCT, **_BIDI})


def norm_ar(s: str) -> str:
    """Aggressive normalisation for label matching (never used for stored values)."""
    s = to_ascii_digits(s).translate({**_TASHKEEL, **_LETTERS}).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%]", " ", s)).strip()


CURRENCIES = {
    "KWD": ["kwd", "kd", "د.ك", "دك", "دينار كويتي", "دينار", "كويتي"],
    "QAR": ["qar", "qr", "ر.ق", "رق", "ريال قطري", "قطري"],
    "SAR": ["sar", "sr", "ر.س", "رس", "ريال سعودي", "ريال", "سعودي"],
    "AED": ["aed", "د.إ", "د.ا", "دإ", "درهم اماراتي", "درهم", "dirham", "dhs"],
}
DECIMALS = {"KWD": 3, "QAR": 2, "SAR": 2, "AED": 2}
VAT_RATE = {"KWD": Decimal("0"), "QAR": Decimal("0"), "SAR": Decimal("15"), "AED": Decimal("5")}


def detect_currency(s: str) -> str | None:
    t = to_ascii_digits(s).translate(_TASHKEEL).casefold().replace("إ", "ا").replace("أ", "ا")
    for code, toks in CURRENCIES.items():
        for tok in toks:
            tok = tok.replace("إ", "ا")
            if re.search(r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])", t):
                return code
    return None


def parse_amount(s: str) -> tuple[Decimal | None, str | None]:
    """'١٢٬٤٥٠٫٠٠٠ د.ك' -> (Decimal('12450.000'), 'KWD'). Last separator is the decimal mark."""
    cur = detect_currency(s)
    t = to_ascii_digits(s)
    m = re.search(r"\d[\d,.\s]*\d|\d", t)
    if not m:
        return None, cur
    num = re.sub(r"\s", "", m.group())
    if "," in num and "." in num:
        dec = "," if num.rfind(",") > num.rfind(".") else "."
        thou = "." if dec == "," else ","
        num = num.replace(thou, "").replace(dec, ".")
    elif "," in num:
        parts = num.split(",")
        # '1,250' is thousands; '1,25' or '12,5' is a decimal comma
        num = num.replace(",", "") if len(parts[-1]) == 3 else num.replace(",", ".")
    elif num.count(".") > 1:
        num = num.replace(".", "")
    try:
        return Decimal(num), cur
    except InvalidOperation:
        return None, cur


GREG_MONTHS = {
    "يناير": 1, "فبراير": 2, "مارس": 3, "ابريل": 4, "أبريل": 4, "مايو": 5, "يونيو": 6, "يوليو": 7,
    "اغسطس": 8, "أغسطس": 8, "سبتمبر": 9, "اكتوبر": 10, "أكتوبر": 10, "نوفمبر": 11, "ديسمبر": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7,
    "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
HIJRI_MONTHS = {
    "محرم": 1, "صفر": 2, "ربيع الاول": 3, "ربيع الأول": 3, "ربيع الثاني": 4, "ربيع الآخر": 4, "ربيع الاخر": 4,
    "جمادى الاولى": 5, "جمادى الأولى": 5, "جمادى الاخرة": 6, "جمادى الآخرة": 6, "رجب": 7, "شعبان": 8,
    "رمضان": 9, "شوال": 10, "ذو القعدة": 11, "ذي القعدة": 11, "ذو الحجة": 12, "ذي الحجة": 12,
}
_HIJRI_NORM = {norm_ar(k): v for k, v in HIJRI_MONTHS.items()}
_GREG_NORM = {norm_ar(k): v for k, v in GREG_MONTHS.items()}


def _valid(y: int, m: int, d: int, hijri: bool) -> date | None:
    try:
        return Hijri(y, m, d).to_gregorian() if hijri else date(y, m, d)
    except (ValueError, OverflowError):
        return None


def parse_date(s: str) -> tuple[date | None, str | None, str]:
    """-> (gregorian date, 'hijri'|'gregorian', note). Day-first for numeric dates (Gulf convention).

    Hijri is detected by year 1300-1500, an 'هـ'/'AH' marker or a Hijri month name and converted
    with the Umm al-Qura calendar (+-1 day vs. the printed civil date is possible).
    """
    t = to_ascii_digits(s)
    n = norm_ar(t)
    marker = bool(re.search(r"\bah\b|\bhijri\b|هجري|\d\s*هـ|\d\s*ه\b", t.casefold() + " " + n))
    y = m = d = None
    mm = re.search(r"(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})", t)
    if mm:
        y, m, d = int(mm[1]), int(mm[2]), int(mm[3])
    else:
        mm = re.search(r"(\d{1,2})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{4})", t)
        if mm:
            d, m, y = int(mm[1]), int(mm[2]), int(mm[3])
        else:
            mm = re.search(r"(\d{1,2})\s+([^\d\s]+(?:\s[^\d\s]+)?)\s+(\d{4})", t)
            if mm:
                d, y = int(mm[1]), int(mm[3])
                name = norm_ar(mm[2])
                for names, hij in ((_HIJRI_NORM, True), (_GREG_NORM, False)):
                    hit = next((v for k, v in names.items() if k == name or name.startswith(k)), None)
                    if hit:
                        m, marker = hit, marker or hij
                        break
    if None in (y, m, d):
        return None, None, "unparseable date"
    hijri = marker or 1300 <= y <= 1500
    g = _valid(y, m, d, hijri)
    if g is None:
        return None, "hijri" if hijri else "gregorian", "invalid calendar date"
    return g, "hijri" if hijri else "gregorian", ("converted from Hijri (Umm al-Qura)" if hijri else "")


def money_str(v: Decimal | None, cur: str | None) -> str | None:
    if v is None:
        return None
    return f"{v.quantize(Decimal(1).scaleb(-DECIMALS.get(cur or '', 2))):.{DECIMALS.get(cur or '', 2)}f}"
