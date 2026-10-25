"""OCR -> classify -> extract (LLM if configured, else rules) -> normalise -> validate -> review decision."""
from __future__ import annotations

from pathlib import Path

from . import llm
from .extract import Field, finalize, needs_review, rules_extract
from .ocr import read_document
from .schemas import SCHEMAS, classify


def process(path: str | Path) -> dict:
    ocr = read_document(path)
    sid, cconf = classify(ocr.text)
    if sid is None:
        return dict(document_type=None, classification_confidence=0.0, engine=ocr.engine, extractor="none", fields={},
                    review_reasons=["could not tell what kind of document this is"], ocr_text=ocr.text)
    schema = SCHEMAS[sid]
    extractor, errors = "rules", []
    cfg = llm.config()
    raws = None
    if cfg:
        try:
            raws, extractor = llm.llm_extract(schema, ocr.text, cfg), f"llm ({cfg['name']})"
        except Exception as e:  # noqa: BLE001  fall back, but say so in the audit trail
            errors.append(f"LLM call failed, used rules instead: {type(e).__name__}: {e}")
    if raws is None:
        raws = rules_extract(schema, ocr.lines, exact_layer=ocr.engine == "pdf-text-layer")
    fields: dict[str, Field] = finalize(schema, raws)
    missing = [f.name for f in schema.fields if f.required and f.name != "currency" and fields[f.name].value is None]
    if missing and ocr.engine.startswith("tesseract") and extractor == "rules":
        # tesseract sometimes drops whole lines in one page-segmentation mode; retry the gaps with another one
        second = rules_extract(schema, read_document(path, psm=4).lines)
        got = [m for m in missing if m in second]
        if got:
            for m in got:
                raws[m] = second[m]
                raws[m].source = "rules (OCR retry, psm 4)"
            fields = finalize(schema, raws)
    return dict(document_type=sid, classification_confidence=cconf, engine=ocr.engine, extractor=extractor,
                fields={k: v.as_dict() for k, v in fields.items()}, review_reasons=needs_review(schema, fields, cconf),
                ocr_text=ocr.text, warnings=errors)
