"""Deterministic rules extractor + the shared finalize step (type parsing, confidence, validation)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from decimal import Decimal
from typing import Any

from .normalize import detect_currency, money_str, norm_ar, parse_amount, parse_date, to_ascii_digits
from .ocr import Line
from .schemas import F, Schema, validate

REVIEW_THRESHOLD = float(os.environ.get("REVIEW_THRESHOLD", "0.80"))
_PAREN = re.compile(r"\([^()]*\)|\)[^()]*\(")  # '(15%)' and its OCR-reversed form ')15('


@dataclass
class Raw:
    """What an extractor found for one field, before normalisation."""
    text: str | None
    conf: float  # extractor confidence, 0..1
    evidence: str = ""
    source: str = "rules"


@dataclass
class Field:
    name: str
    value: Any
    raw: str | None
    confidence: float
    evidence: str = ""
    source: str = "rules"
    issues: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def calibrate(ocr_conf: float, exact_layer: bool) -> float:
    """Map raw engine line confidence to a field confidence factor. Heuristic, not a probability."""
    if exact_layer:
        return 1.0
    c = min(1.0, max(0.0, (ocr_conf - 0.6) / 0.35))
    return 0.3 + 0.7 * c


def _label_match(line: str, label: str) -> float:
    """Similarity 0..1 of the label against the first tokens of the line (token-based, OCR tolerant)."""
    toks, k = line.split(), len(label.split())
    if len(toks) < k:
        return 0.0
    head = norm_ar(" ".join(toks[:k]))
    if head == label:
        return 1.0
    r = SequenceMatcher(None, head, label).ratio()
    return r if r >= 0.84 else 0.0


def _after_label(line: str, label: str) -> str:
    return " ".join(line.split()[len(label.split()):])


def _value_text(f: F, rest: str) -> str:
    colon = re.search(r"[:：]", rest)
    if f.type == "text":
        return (rest[colon.end():] if colon else rest).strip(" :-–")
    return _PAREN.sub(" ", rest[colon.end():] if colon and re.search(r"\d", _PAREN.sub(" ", rest[colon.end():])) else rest).strip(" :-–")


def _vconf(ln: Line, skip: int, f: F) -> float:
    """Confidence of the value tokens only (after the label), min and mean blended so one bad digit hurts.
    Numeric types look at the digit-bearing tokens only: a garbled currency word must not sink a clean amount."""
    toks = ln.text.split()
    ws = ln.words[skip:] if ln.words and len(ln.words) == len(toks) else None
    if ws is None:
        return ln.conf
    if f.type in ("money", "date", "percent", "int"):
        keep = [w for t, w in zip(toks[skip:], ws) if re.search(r"[\d\u0660-\u0669\u06F0-\u06F9]", t) and not re.search(r"[()%]", t)]
        ws = keep or ws
    return 0.5 * min(ws) + 0.5 * sum(ws) / len(ws) if ws else ln.conf


def rules_extract(schema: Schema, lines: list[Line], exact_layer: bool = False) -> dict[str, Raw]:
    out: dict[str, Raw] = {}
    # 1. label matching: a line goes to the field with the longest matching label whose value parses as that type
    for ln in lines:
        cands = []
        for f in schema.fields:
            for lab in (norm_ar(x) for x in f.ar + f.en):
                if s := _label_match(ln.text, lab):
                    cands.append((len(lab), s, f, lab))
        for _, s, f, lab in sorted(cands, key=lambda c: (-c[0], -c[1])):
            val = _value_text(f, _after_label(ln.text, lab))
            if not val or (f.type not in ("text", "enum") and not re.search(r"\d", to_ascii_digits(val))):
                continue
            conf = round(0.98 * s * calibrate(_vconf(ln, len(lab.split()), f), exact_layer), 3)
            if f.name not in out or conf > out[f.name].conf:
                out[f.name] = Raw(val, conf, ln.text)
            break
    # 2. regex-only fields (e.g. VAT rate printed inside the VAT label)
    for f in schema.fields:
        if f.regex and f.name not in out:
            for ln in lines:
                m = re.search(f.regex, norm_ar(ln.text))
                if m:
                    out[f.name] = Raw(m.group(1) or m.group(2), round(0.9 * calibrate(ln.conf, exact_layer), 3), ln.text)
                    break
    return out


def _convert(f: F, raw: Raw, cur_hint: str | None) -> tuple[Any, str, list[str]]:
    """-> (normalised value, note, issues)"""
    t = (raw.text or "").strip()
    if f.type == "text":
        return (t or None), "", []
    if f.type == "date":
        d, cal, note = parse_date(t)
        return (d.isoformat() if d else None), (f"{cal}: {note}".strip(": ") if d else ""), ([] if d else [f"cannot read a date from '{t}'"])
    if f.type == "money":
        v, cur = parse_amount(t)
        return money_str(v, cur or cur_hint), (cur or ""), ([] if v is not None else [f"cannot read an amount from '{t}'"])
    if f.type == "percent":
        v, _ = parse_amount(t)
        return (str(v) if v is not None else None), "", ([] if v is not None else [f"cannot read a percentage from '{t}'"])
    if f.type == "int":
        m = re.search(r"\d+", to_ascii_digits(t))
        return (int(m.group()) if m else None), "", ([] if m else [f"cannot read a number from '{t}'"])
    if f.type == "enum":
        n = norm_ar(t)
        for key, syns in f.choices.items():
            if key.casefold() in n or any(norm_ar(s) in n for s in syns):
                return key, "", []
        return None, "", [f"'{t}' is not one of {', '.join(f.choices)}"]
    return t, "", []


def finalize(schema: Schema, raws: dict[str, Raw]) -> dict[str, Field]:
    fields: dict[str, Field] = {}
    # currency is derived from the money line carrying a currency marker
    cur = None
    for key in ("total", "salary_monthly"):
        if key in raws and (c := detect_currency(raws[key].text or raws[key].evidence)):
            cur = c
            break
    for f in schema.fields:
        r = raws.get(f.name)
        if f.name == "currency":
            src = next((raws[k] for k in ("total", "salary_monthly") if k in raws), None)
            fields[f.name] = Field(f.name, cur, cur, round(src.conf * 0.98, 3) if cur and src else 0.0, src.evidence if src else "", src.source if src else "rules",
                                   [] if cur else ["currency marker not found"])
            continue
        if r is None or not (r.text or "").strip():
            fields[f.name] = Field(f.name, None, None, 0.0, issues=["not found in document"] if f.required else [])
            continue
        value, note, issues = _convert(f, r, cur)
        fields[f.name] = Field(f.name, value, r.text, 0.0 if value is None else round(r.conf * (0.97 if note.startswith("hijri") else 1), 3),
                               r.evidence, r.source, issues, note)
    vr, va, sb = (fields.get(k) for k in ("vat_rate", "vat_amount", "subtotal"))
    if vr and vr.value is None and va and sb and va.value is not None and sb.value is not None and Decimal(sb.value) > 0:
        r = (Decimal(va.value) / Decimal(sb.value) * 100).quantize(Decimal("0.5"), rounding="ROUND_HALF_EVEN")
        fields["vat_rate"] = Field("vat_rate", str(r.normalize()), None, round(0.9 * min(va.confidence, sb.confidence), 3), va.evidence,
                                   "derived", [], "derived from VAT amount / subtotal; the rate was not legible")
    apply_validation(schema, fields)
    return fields


def apply_validation(schema: Schema, fields: dict[str, Field]) -> None:
    """Cross-field rules. A failing rule caps the confidence of every field it involves."""
    for fl in fields.values():
        fl.issues = [i for i in fl.issues if not i.startswith("rule: ")]
    values = {k: v.value for k, v in fields.items()}
    for names, msg in validate(schema.id, values):
        for n in names:
            if n in fields:
                fields[n].issues.append(f"rule: {msg}")
                fields[n].confidence = min(fields[n].confidence, 0.4)


def needs_review(schema: Schema, fields: dict[str, Field], class_conf: float) -> list[str]:
    reasons = []
    if class_conf < 0.5:
        reasons.append(f"document type uncertain ({class_conf:.2f})")
    for f in schema.fields:
        fl = fields[f.name]
        if fl.issues:
            reasons.append(f"{f.name}: {fl.issues[0]}")
        elif fl.value is not None and fl.confidence < REVIEW_THRESHOLD:
            reasons.append(f"{f.name}: low confidence {fl.confidence:.2f}")
    return reasons
