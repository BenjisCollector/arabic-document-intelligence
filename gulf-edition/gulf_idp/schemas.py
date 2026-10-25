"""Arabic-first schemas (invoice, employment contract, leave request) and their validation rules."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .normalize import DECIMALS, VAT_RATE, norm_ar


@dataclass(frozen=True)
class F:
    name: str
    type: str  # text | date | money | percent | int | enum
    ar: tuple[str, ...]
    en: tuple[str, ...]
    desc: str
    required: bool = True
    regex: str | None = None  # alternative to label matching, applied to the whole line
    choices: dict[str, tuple[str, ...]] = field(default_factory=dict)  # enum value -> synonyms


@dataclass(frozen=True)
class Schema:
    id: str
    title: str
    keywords: tuple[str, ...]  # classifier evidence (normalised)
    fields: tuple[F, ...]
    description: str


INVOICE = Schema(
    "invoice", "Invoice / فاتورة",
    ("فاتوره", "فاتوره ضريبيه", "invoice", "tax invoice", "رقم الفاتوره", "invoice no"),
    (
        F("supplier_name", "text", ("المورد", "اسم المورد", "البائع", "الشركه المصدره"), ("supplier", "vendor", "seller", "from"), "Supplier legal name"),
        F("supplier_vat_number", "text", ("الرقم الضريبي", "رقم التسجيل الضريبي"), ("vat number", "vat no", "tax registration number", "trn"), "Supplier VAT / tax registration number", required=False),
        F("invoice_number", "text", ("رقم الفاتوره",), ("invoice number", "invoice no", "invoice #"), "Invoice number"),
        F("invoice_date", "date", ("تاريخ الفاتوره", "تاريخ الاصدار"), ("invoice date", "date of issue", "date"), "Invoice date, Gregorian or Hijri, normalised to Gregorian ISO"),
        F("subtotal", "money", ("المجموع الفرعي", "الاجمالي قبل الضريبه", "المبلغ قبل الضريبه"), ("subtotal", "sub total", "total before vat", "net amount"), "Amount before VAT"),
        F("vat_rate", "percent", (), (), "VAT rate in percent", regex=r"(?:ضريبه القيمه المضافه|vat)[^\d%]*?(?:(\d+(?:\.\d+)?)\s*%|%\s*(\d+(?:\.\d+)?))"),
        F("vat_amount", "money", ("ضريبه القيمه المضافه", "مبلغ الضريبه", "الضريبه"), ("vat", "vat amount", "value added tax", "tax"), "VAT amount"),
        F("total", "money", ("الاجمالي", "المبلغ الاجمالي", "الاجمالي المستحق", "اجمالي الفاتوره"), ("total", "grand total", "total due", "amount due"), "Invoice total including VAT"),
        F("currency", "enum", (), (), "KWD, QAR, SAR or AED (derived from the total's currency marker)", choices={"KWD": (), "QAR": (), "SAR": (), "AED": ()}),
    ),
    "Arabic/English tax invoice (KWD, QAR, SAR, AED)",
)

CONTRACT = Schema(
    "employment_contract", "Employment contract / عقد عمل",
    ("عقد عمل", "عقد توظيف", "employment contract", "contract of employment", "employment agreement"),
    (
        F("employer_name", "text", ("الطرف الاول", "صاحب العمل", "الشركه"), ("first party", "employer", "company"), "Employer (first party)"),
        F("employee_name", "text", ("الطرف الثاني", "الموظف", "اسم الموظف"), ("second party", "employee", "employee name"), "Employee (second party)"),
        F("job_title", "text", ("المسمى الوظيفي", "الوظيفه"), ("job title", "position", "role"), "Job title", required=False),
        F("salary_monthly", "money", ("الراتب الشهري", "الراتب الاساسي", "الراتب"), ("monthly salary", "basic salary", "salary"), "Monthly salary with currency"),
        F("start_date", "date", ("تاريخ المباشره", "تاريخ بدء العمل", "تاريخ البدء"), ("start date", "commencement date", "date of joining"), "Start date, Gregorian or Hijri, normalised to Gregorian ISO"),
        F("notice_period_days", "int", ("فتره الاشعار", "مده الاشعار"), ("notice period",), "Notice period in days"),
        F("currency", "enum", (), (), "KWD, QAR, SAR or AED (derived from the salary's currency marker)", choices={"KWD": (), "QAR": (), "SAR": (), "AED": ()}),
    ),
    "Employment contract (parties, salary, start date, notice period)",
)

LEAVE = Schema(
    "leave_request", "HR leave request / طلب إجازة",
    ("طلب اجازه", "نموذج اجازه", "leave request", "leave application", "leave form"),
    (
        F("employee_name", "text", ("اسم الموظف", "الموظف"), ("employee name", "employee"), "Employee name"),
        F("employee_id", "text", ("الرقم الوظيفي", "رقم الموظف"), ("employee id", "employee no", "staff id"), "Employee ID"),
        F("leave_type", "enum", ("نوع الاجازه",), ("leave type", "type of leave"), "annual | sick | unpaid | emergency",
          choices={"annual": ("سنويه", "annual"), "sick": ("مرضيه", "sick"), "unpaid": ("بدون راتب", "unpaid"), "emergency": ("اضطراريه", "طارئه", "emergency")}),
        F("start_date", "date", ("من تاريخ", "تاريخ بدايه الاجازه", "تاريخ البدايه"), ("from date", "start date", "from"), "First day of leave"),
        F("end_date", "date", ("الى تاريخ", "تاريخ نهايه الاجازه", "تاريخ النهايه"), ("to date", "end date", "to"), "Last day of leave"),
        F("days_requested", "int", ("عدد الايام", "مده الاجازه"), ("number of days", "days requested", "days"), "Calendar days requested (inclusive)"),
    ),
    "HR leave request form",
)

SCHEMAS = {s.id: s for s in (INVOICE, CONTRACT, LEAVE)}


def classify(text: str) -> tuple[str | None, float]:
    """Keyword vote over the first lines of text -> (schema id, confidence)."""
    t = norm_ar(text[:1500])
    scores = {s.id: sum(1 for k in s.keywords if k in t) for s in SCHEMAS.values()}
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    if scores[best] == 0:
        return None, 0.0
    return best, round(min(0.99, 0.55 + 0.2 * scores[best]) * (scores[best] / total), 2)


# --- validation: each returns [(field names, message)] --------------------------------------

def _d(v) -> date | None:
    try:
        return date.fromisoformat(v) if v else None
    except ValueError:
        return None


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except Exception:  # noqa: BLE001
        return None


def validate(schema_id: str, v: dict) -> list[tuple[tuple[str, ...], str]]:
    out: list[tuple[tuple[str, ...], str]] = []
    cur = v.get("currency")
    tol = Decimal(1).scaleb(-DECIMALS.get(cur or "", 2))  # one minor unit
    for key in ("invoice_date", "start_date", "end_date"):
        if key in v and v[key]:
            d = _d(v[key])
            if d is None or not (date(2000, 1, 1) <= d <= date(2100, 1, 1)):
                out.append(((key,), f"{key} is not a plausible date"))
    if schema_id == "invoice":
        sub, vat, tot, rate = _dec(v.get("subtotal")), _dec(v.get("vat_amount")), _dec(v.get("total")), _dec(v.get("vat_rate"))
        if None not in (sub, vat, tot) and abs(sub + vat - tot) > tol:
            out.append((("subtotal", "vat_amount", "total"), f"totals do not add up: {sub} + {vat} != {tot}"))
        if None not in (sub, vat, rate) and abs(sub * rate / 100 - vat) > max(tol, sub * Decimal("0.0005")):
            out.append((("subtotal", "vat_rate", "vat_amount"), f"VAT {vat} is not {rate}% of {sub}"))
        if rate is not None and cur in VAT_RATE and rate != VAT_RATE[cur]:
            out.append((("vat_rate",), f"unexpected VAT rate {rate}% for {cur} (usual: {VAT_RATE[cur]}%)"))
        if tot is not None and tot <= 0:
            out.append((("total",), "total must be positive"))
    elif schema_id == "employment_contract":
        sal, n = _dec(v.get("salary_monthly")), v.get("notice_period_days")
        if sal is not None and sal <= 0:
            out.append((("salary_monthly",), "salary must be positive"))
        if isinstance(n, int) and not 0 <= n <= 180:
            out.append((("notice_period_days",), f"notice period {n} days is outside 0-180"))
        a, b = (v.get("employer_name") or "").strip(), (v.get("employee_name") or "").strip()
        if a and a == b:
            out.append((("employer_name", "employee_name"), "employer and employee are the same party"))
    elif schema_id == "leave_request":
        s, e, n = _d(v.get("start_date")), _d(v.get("end_date")), v.get("days_requested")
        if s and e and e < s:
            out.append((("start_date", "end_date"), "end date is before start date"))
        elif s and e and isinstance(n, int) and (e - s) + timedelta(days=1) != timedelta(days=n):
            out.append((("start_date", "end_date", "days_requested"), f"{n} days requested but the dates span {(e - s).days + 1}"))
    return out


def upstream_json_schema(s: Schema) -> dict:
    """Same JSON-Schema shape the upstream accelerator registers via /schemavault/."""
    t = {"text": "string", "date": "string", "enum": "string", "money": "number", "percent": "number", "int": "integer"}
    props = {
        f.name: {"anyOf": [{"type": t[f.type]}, {"type": "null"}], "description": f.desc,
                 "title": f.name.replace("_", " ").title()}
        for f in s.fields
    }
    cls = "".join(p.title() for p in s.id.split("_")) + "Document"
    return {"description": s.description, "properties": props, "required": [f.name for f in s.fields if f.required],
            "title": cls, "type": "object"}
