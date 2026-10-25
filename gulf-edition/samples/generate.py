"""Generate the 14 synthetic sample documents (PDF + scanned PNG) and ground_truth.json.

Dev-only: needs Google Chrome (headless print-to-pdf, correct Arabic shaping), pdftoppm and Pillow.
The outputs are committed, so you only run this to regenerate. All companies/people are fictional.
"""
from __future__ import annotations

import json
import random
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

from PIL import Image, ImageFilter

HERE = Path(__file__).parent
OUT = HERE / "docs"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
INDIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
DEC = {"KWD": 3, "QAR": 2, "SAR": 2, "AED": 2}
CUR_AR = {"KWD": "د.ك", "QAR": "ر.ق", "SAR": "ريال سعودي", "AED": "درهم"}


def money(v: D, cur: str, lang: str, indic=False, arsep=False) -> str:
    s = f"{v:,.{DEC[cur]}f}"
    if indic:
        s = s.translate(INDIC)
    if arsep:
        s = s.replace(",", "٬").replace(".", "٫")
    return f"{s} {CUR_AR[cur]}" if lang == "ar" else f"{cur} {s}"


def num(n, indic):
    return str(n).translate(INDIC) if indic else str(n)


LINES: dict = {}


def page(lang: str, title: str, subtitle: str, brand: str, lines: list[str], items: str = "", clauses: list[str] | None = None) -> str:
    d, al = ("rtl", "ar") if lang == "ar" else ("ltr", "en")
    body = "".join(f"<p class=kv>{l}</p>" for l in lines)
    cl = "".join(f"<li>{c}</li>" for c in (clauses or []))
    foot = ("وثيقة تجريبية - شركات وأشخاص وهميون ولا علاقة لها بأي جهة حقيقية" if lang == "ar"
            else "SYNTHETIC SAMPLE - fictional companies and people, not a real document")
    return f"""<!doctype html><html lang={al} dir={d}><meta charset=utf-8><style>
@page{{size:A4;margin:16mm}} body{{font-family:Arial,'Helvetica Neue',sans-serif;font-size:21px;color:#000;line-height:1.6}}
.top{{display:flex;justify-content:space-between;align-items:center;border-bottom:3px solid #0b6b5d;padding-bottom:10px;margin-bottom:18px}}
.brand{{font-size:17px;font-weight:700;color:#0b6b5d}} h1{{font-size:32px;margin:0}} .sub{{color:#5b6672;font-size:13px}}
p.kv{{margin:10px 0;padding:4px 0;border-bottom:1px solid #dfe6e3}} b{{font-weight:700}}
table{{width:100%;border-collapse:collapse;margin:14px 0;font-size:19px}} th,td{{border:1px solid #cfd8d5;padding:6px 8px;text-align:start}} th{{background:#e6efec}}
ol{{font-size:18px;color:#222}} .foot{{position:fixed;bottom:0;width:100%;text-align:center;color:#8a949c;font-size:13px}}
</style><div class=top><div><h1>{title}</h1><div class=sub>{subtitle}</div></div><div class=brand>{brand}</div></div>
{body}{items}{'<ol>' + cl + '</ol>' if cl else ''}<div class=foot>{foot}</div></html>"""


def items_table(lang, rows, cur, indic):
    h = ("<tr><th>البند</th><th>الكمية</th><th>سعر الوحدة</th></tr>" if lang == "ar" else "<tr><th>Item</th><th>Qty</th><th>Unit price</th></tr>")
    r = "".join(f"<tr><td>{n}</td><td>{num(q, indic)}</td><td>{money(p, cur, lang, indic)}</td></tr>" for n, q, p in rows)
    return f"<table>{h}{r}</table>"


DOCS: list[dict] = []
truth: dict[str, dict] = {}


def invoice(id, lang, cur, supplier, vat_no, inv_no, date_txt, date_iso, sub, items, indic=False, arsep=False,
            broken_total=None, scan=False, customer="", note=""):
    rate = {"KWD": D(0), "QAR": D(0), "SAR": D(15), "AED": D(5)}[cur]
    vat = (sub * rate / 100).quantize(D(1).scaleb(-DEC[cur]))
    tot = sub + vat
    shown = broken_total or tot
    m = lambda v: money(v, cur, lang, indic, arsep)
    rtxt = num(int(rate), indic) + ("٪" if lang == "ar" and indic else "%")
    if lang == "ar":
        L = [f"المورد: {supplier}", f"الرقم الضريبي: {num(vat_no, indic)}", f"رقم الفاتورة: {inv_no}", f"تاريخ الفاتورة: {date_txt}",
             f"العميل: {customer}"]
        S = [f"المجموع الفرعي: {m(sub)}", f"ضريبة القيمة المضافة ({rtxt}): {m(vat)}", f"الإجمالي: {m(shown)}"]
        html = page("ar", "فاتورة ضريبية", "Tax Invoice", supplier.split(" ")[0] + " " + supplier.split(" ")[1] if " " in supplier else supplier,
                    L + [], items_table("ar", items, cur, indic)) 
        html = html.replace("</ol>", "").replace("<div class=foot>", "".join(f"<p class=kv>{s}</p>" for s in S) + "<div class=foot>", 1)
    else:
        L = [f"Supplier: {supplier}", f"VAT number: {vat_no}", f"Invoice number: {inv_no}", f"Invoice date: {date_txt}", f"Customer: {customer}"]
        S = [f"Subtotal: {m(sub)}", f"VAT ({rtxt}): {m(vat)}", f"Total: {m(shown)}"]
        html = page("en", "Tax Invoice", "فاتورة ضريبية", supplier, L, items_table("en", items, cur, indic))
        html = html.replace("<div class=foot>", "".join(f"<p class=kv>{s}</p>" for s in S) + "<div class=foot>", 1)
    DOCS.append(dict(id=id, html=html, scan=scan))
    truth[id] = dict(document_type="invoice", language=lang, expected_to_need_review=bool(broken_total), note=note,
                     fields=dict(supplier_name=supplier, supplier_vat_number=vat_no, invoice_number=inv_no, invoice_date=date_iso,
                                 subtotal=str(sub.quantize(D(1).scaleb(-DEC[cur]))), vat_rate=str(rate), vat_amount=str(vat),
                                 total=str(shown.quantize(D(1).scaleb(-DEC[cur]))), currency=cur))


def contract(id, lang, cur, employer, employee, title, salary, start_txt, start_iso, notice, indic=False, scan=False, note="", hide_notice=False):
    m = money(salary, cur, lang, indic)
    if lang == "ar":
        L = [f"الطرف الأول (صاحب العمل): {employer}", f"الطرف الثاني (الموظف): {employee}", f"المسمى الوظيفي: {title}",
             f"الراتب الشهري: {m}", f"تاريخ المباشرة: {start_txt}"]
        if not hide_notice:
            L.append(f"فترة الإشعار: {num(notice, indic)} يوماً")
        cl = ["يخضع هذا العقد لأحكام نظام العمل المعمول به ويُعد الطرفان ملتزمين ببنوده.", "تبدأ فترة التجربة من تاريخ المباشرة وفق الأنظمة المحلية.",
              "تُسوى مستحقات الموظف عند انتهاء العقد وفق النظام."]
        html = page("ar", "عقد عمل", "Employment Contract", "عقد تجريبي", L, "", cl)
    else:
        L = [f"First party (employer): {employer}", f"Second party (employee): {employee}", f"Job title: {title}",
             f"Monthly salary: {m}", f"Start date: {start_txt}"]
        if not hide_notice:
            L.append(f"Notice period: {num(notice, indic)} days")
        cl = ["This contract is governed by the applicable labour law and binds both parties.", "The probation period starts on the start date.",
              "Final dues are settled at termination in accordance with the law."]
        html = page("en", "Employment Contract", "عقد عمل", "Sample Contract", L, "", cl)
    DOCS.append(dict(id=id, html=html, scan=scan))
    truth[id] = dict(document_type="employment_contract", language=lang, expected_to_need_review=hide_notice, note=note,
                     fields=dict(employer_name=employer, employee_name=employee, job_title=title, salary_monthly=str(salary.quantize(D(1).scaleb(-DEC[cur]))),
                                 currency=cur, start_date=start_iso, notice_period_days=None if hide_notice else notice))


def leave(id, lang, name, emp_id, ltype_txt, ltype, s_txt, s_iso, e_txt, e_iso, days, indic=False, scan=False, note="", bad=False):
    if lang == "ar":
        L = [f"اسم الموظف: {name}", f"الرقم الوظيفي: {num(emp_id, indic)}", f"نوع الإجازة: {ltype_txt}", f"من تاريخ: {s_txt}", f"إلى تاريخ: {e_txt}", f"عدد الأيام: {num(days, indic)}"]
        html = page("ar", "طلب إجازة", "Leave Request", "الموارد البشرية", L)
    else:
        L = [f"Employee name: {name}", f"Employee ID: {emp_id}", f"Leave type: {ltype_txt}", f"From date: {s_txt}", f"To date: {e_txt}", f"Number of days: {days}"]
        html = page("en", "Leave Request", "طلب إجازة", "Human Resources", L)
    DOCS.append(dict(id=id, html=html, scan=scan))
    truth[id] = dict(document_type="leave_request", language=lang, expected_to_need_review=bad, note=note,
                     fields=dict(employee_name=name, employee_id=str(emp_id), leave_type=ltype, start_date=s_iso, end_date=e_iso, days_requested=days))


# ---- the 14 documents (all fictional) ---------------------------------------------------------
# Arabic RTL pages are rendered by a browser, so their PDF text layer is unusable and they go through OCR (Western digits,
# which open-source OCR reads reliably). English/bilingual LTR pages keep a clean text layer and use Arabic-Indic numerals.
invoice("inv-01-kwd-ar", "ar", "KWD", "شركة النخلة الزرقاء التجريبية للتجارة", "300000000000003", "KW-2026-0142", "2026/03/15", "2026-03-15",
        D("1250.000"), [("خدمات استشارية", 10, D("100")), ("ترخيص برنامج", 1, D("250"))], customer="مؤسسة الضفاف الوهمية")
invoice("inv-02-sar-ar-hijri", "ar", "SAR", "مؤسسة الكثيب الذهبي التجريبية للتجارة", "310000000000003", "INV-7781", "12 رجب 1447 هـ", "2026-01-01",
        D("8400.00"), [("أعمال صيانة", 4, D("1500")), ("مواد", 1, D("2400"))], customer="شركة الوادي الوهمية", note="Hijri date, converted to Gregorian")
invoice("inv-03-aed-en-indic", "en", "AED", "Gulf Dunes Logistics LLC (sample)", "100000000000003", "GDL-2026-0457", "٠٢/٠٤/٢٠٢٦", "2026-04-02",
        D("3000.00"), [("Freight", 3, D("800")), ("Handling", 1, D("600"))], indic=True, customer="Fake Retail Co.", note="Arabic-Indic numerals, clean text layer")
invoice("inv-04-qar-ar-month", "ar", "QAR", "شركة اللؤلؤة الرقمية التجريبية", "QA-000000", "QA-3310", "5 مايو 2026", "2026-05-05",
        D("22000.00"), [("تطوير تطبيق", 1, D("22000"))], customer="مجموعة الخليج الوهمية", note="Arabic month name")
invoice("inv-05-kwd-en-arabic-separators", "en", "KWD", "Sample Harbour Trading", "300000000000004", "KW-90210", "٢٠٢٦/٠٦/٢٠", "2026-06-20",
        D("4850.500"), [("Server hosting", 1, D("4850.500"))], indic=True, arsep=True, customer="Fake Noor Est.", note="Arabic decimal and thousands separators")
invoice("inv-06-sar-ar-bad-total", "ar", "SAR", "شركة الواحة الخيالية للتقنية", "320000000000003", "INV-9004", "2026/07/09", "2026-07-09",
        D("5000.00"), [("اشتراك سنوي", 1, D("5000"))], broken_total=D("5570.00"), customer="مؤسسة البيان الوهمية",
        note="Deliberate error: total does not add up")
invoice("inv-07-sar-ar-scan", "ar", "SAR", "مؤسسة السراب الأزرق التجريبية", "330000000000003", "SC-1203", "2026/08/02", "2026-08-02",
        D("12000.00"), [("توريد معدات", 6, D("2000"))], scan=True, customer="شركة الرمال الوهمية", note="Scanned image, OCR path")

contract("ct-01-sar-ar-hijri", "ar", "SAR", "شركة الكثيب الذهبي التجريبية", "سالم عبدالله التجريبي", "مهندس برمجيات", D("18000.00"),
         "1 شعبان 1447 هـ", "2026-01-20", 60, note="Hijri start date")
contract("ct-02-aed-en-indic", "en", "AED", "Blue Palm Trading Co. (sample)", "Layla Sample-Hassan", "Finance Manager", D("25000.00"),
         "٠١/٠٦/٢٠٢٦", "2026-06-01", 30, indic=True, note="Arabic-Indic numerals, clean text layer")
contract("ct-03-kwd-ar", "ar", "KWD", "شركة اللؤلؤة الرقمية التجريبية", "فاطمة الوهمي", "محللة بيانات", D("1200.000"),
         "2026/07/01", "2026-07-01", 30)
contract("ct-04-qar-ar-scan", "ar", "QAR", "مؤسسة الواحة الخيالية", "ناصر التجريبي", "محاسب", D("15000.00"),
         "2026/09/10", "2026-09-10", 45, scan=True, note="Scanned image, OCR path")
contract("ct-05-sar-ar-no-notice", "ar", "SAR", "شركة الرمال الوهمية", "هند الخيالي", "مسؤولة موارد بشرية", D("14000.00"),
         "2026/10/01", "2026-10-01", 60, hide_notice=True, note="Deliberate gap: no notice period in the document")

leave("hr-01-leave-ar", "ar", "أحمد الوهمي", "EMP-4021", "سنوية", "annual", "2026/04/12", "2026-04-12", "2026/04/16", "2026-04-16", 5)
leave("hr-02-leave-en-bad-dates-scan", "en", "Mona Example", "EMP-5532", "Sick", "sick", "20/05/2026", "2026-05-20", "18/05/2026", "2026-05-18", 3, scan=True,
      bad=True, note="Deliberate error: end date before start date; scanned")


def scan(pdf: Path, png: Path, seed: int):
    subprocess.run(["pdftoppm", "-png", "-r", "200", "-singlefile", str(pdf), str(png.with_suffix(""))], check=True)
    rnd = random.Random(seed)
    im = Image.open(png).convert("L")
    im = im.rotate(rnd.uniform(-1.4, 1.4), expand=True, fillcolor=255, resample=Image.BICUBIC).filter(ImageFilter.GaussianBlur(0.5))
    px = im.load()
    for _ in range(im.width * im.height // 40):
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-30, 20)))
    im.point(lambda p: 18 + int(p * 0.9)).save(png, optimize=True)


def main():
    OUT.mkdir(exist_ok=True)
    import os
    for i, d in enumerate(DOCS):
        if os.environ.get('ONLY') and os.environ['ONLY'] not in d['id']:
            continue
        html = HERE / f"_{d['id']}.html"
        html.write_text(d["html"], encoding="utf-8")
        pdf = OUT / f"{d['id']}.pdf"
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--user-data-dir=/tmp/gulf-idp-chrome", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", html.as_uri()],
                       check=True, capture_output=True, timeout=60)
        html.unlink()
        if d["scan"]:
            scan(pdf, OUT / f"{d['id']}.png", i)
            pdf.unlink()
    if not os.environ.get('ONLY'):
        pass
    (HERE / "ground_truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(DOCS)} documents to {OUT}")


if __name__ == "__main__":
    sys.exit(main())
