"""FastAPI app: upload, review queue (approve / correct), audit trail, export."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import pymupdf
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import export, llm, store
from .extract import REVIEW_THRESHOLD, Field, Raw, _convert, apply_validation, needs_review
from .pipeline import process
from .schemas import SCHEMAS

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples" / "docs"
app = FastAPI(title="Arabic document intelligence (Gulf edition)")
_lock = threading.Lock()  # one OCR job at a time keeps tesseract from thrashing a laptop


def _run(doc_id: int) -> None:
    d = store.get(doc_id)
    with _lock:
        try:
            r = process(d["path"])
        except Exception as e:  # noqa: BLE001
            store.update(doc_id, status="failed", review_reasons=[f"{type(e).__name__}: {e}"])
            store.audit(doc_id, "system", "failed", note=str(e))
            return
    status = "needs_review" if r["review_reasons"] else "auto_approved"
    store.update(doc_id, status=status, document_type=r["document_type"], classification_confidence=r["classification_confidence"],
                 engine=r["engine"], extractor=r["extractor"], fields=r["fields"], review_reasons=r["review_reasons"],
                 warnings=r.get("warnings", []), ocr_text=r["ocr_text"])
    store.audit(doc_id, "system", "extracted", note=f"{r['document_type']} via {r['engine']} + {r['extractor']}")
    for w in r.get("warnings", []):
        store.audit(doc_id, "system", "warning", note=w)
    if status == "auto_approved":
        store.audit(doc_id, "system", "auto_approved", note=f"every field >= {REVIEW_THRESHOLD} and all validation rules passed")
    else:
        store.audit(doc_id, "system", "queued_for_review", note="; ".join(r["review_reasons"])[:500])


def _ingest(path: Path, name: str, bg: BackgroundTasks | None = None) -> int:
    doc_id = store.create(name, str(path))
    store.audit(doc_id, "system", "uploaded", note=name)
    if bg is not None:
        bg.add_task(_run, doc_id)
    return doc_id


@app.get("/api/config")
def config():
    cfg = llm.config()
    ocr = "azure-document-intelligence" if os.environ.get("AZURE_DI_ENDPOINT") and os.environ.get("AZURE_DI_KEY") \
        else ("tesseract (ara+eng)" if shutil.which("tesseract") else "none: install tesseract")
    return dict(ocr=ocr, llm=cfg["name"] if cfg else "deterministic rules (no LLM key configured)", threshold=REVIEW_THRESHOLD,
                schemas={k: [dict(name=f.name, type=f.type, required=f.required) for f in s.fields] for k, s in SCHEMAS.items()})


@app.post("/api/documents")
async def upload(bg: BackgroundTasks, file: UploadFile = File(...)):
    up = store.DATA / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    dest = up / f"{len(list(up.iterdir())) + 1:04d}_{Path(file.filename or 'upload').name}"
    dest.write_bytes(await file.read())
    return {"id": _ingest(dest, Path(file.filename or dest.name).name, bg)}


@app.post("/api/samples/load")
def load_samples(bg: BackgroundTasks):
    have = store.filenames()
    ids = [_ingest(p, p.name) for p in sorted(SAMPLES.iterdir()) if p.name not in have]

    def go():
        for i in ids:
            _run(i)
    bg.add_task(go)
    return {"queued": len(ids)}


@app.post("/api/reset")
def reset():
    store.reset()
    return {"ok": True}


@app.get("/api/documents")
def documents(status: str | None = None):
    return store.list_docs(status)


@app.get("/api/documents/{doc_id}")
def document(doc_id: int):
    d = store.get(doc_id)
    if not d:
        raise HTTPException(404)
    d["audit"] = store.audit_log(doc_id)
    return d


@app.get("/api/documents/{doc_id}/preview")
def preview(doc_id: int):
    d = store.get(doc_id)
    if not d:
        raise HTTPException(404)
    p = Path(d["path"])
    if p.suffix.lower() == ".pdf":
        with pymupdf.open(p) as pdf:
            return Response(pdf[0].get_pixmap(dpi=110).tobytes("png"), media_type="image/png")
    return FileResponse(p)


class Correction(BaseModel):
    value: str
    reviewer: str = "reviewer"


def _fields(d: dict) -> dict[str, Field]:
    return {k: Field(**v) for k, v in d["fields"].items()}


@app.patch("/api/documents/{doc_id}/fields/{name}")
def correct(doc_id: int, name: str, body: Correction):
    d = store.get(doc_id)
    if not d or d["document_type"] not in SCHEMAS:
        raise HTTPException(404)
    schema = SCHEMAS[d["document_type"]]
    spec = next((f for f in schema.fields if f.name == name), None)
    if not spec:
        raise HTTPException(404, f"unknown field {name}")
    fields = _fields(d)
    old = fields[name].value
    text = body.value.strip()
    if not text:
        raise HTTPException(422, "value is empty")
    value, note, issues = _convert(spec, Raw(text, 1.0), (fields.get("currency") and fields["currency"].value))
    if issues or value is None:
        raise HTTPException(422, "; ".join(issues) or "cannot parse value")
    fields[name] = Field(name, value, text, 1.0, fields[name].evidence, "human", [], f"corrected by {body.reviewer}" + (f" ({note})" if note else ""))
    apply_validation(schema, fields)
    store.update(d["id"], fields={k: v.as_dict() for k, v in fields.items()}, review_reasons=needs_review(schema, fields, d["classification_confidence"] or 1),
                 status="needs_review" if d["status"] == "approved" else d["status"])
    store.audit(doc_id, body.reviewer, "field_corrected", name, old, value)
    return store.get(doc_id)["fields"]


class Approval(BaseModel):
    reviewer: str = "reviewer"


@app.post("/api/documents/{doc_id}/approve")
def approve(doc_id: int, body: Approval):
    d = store.get(doc_id)
    if not d or not d["fields"]:
        raise HTTPException(404)
    problems = [f"{k}: {i}" for k, f in d["fields"].items() for i in f["issues"]]
    if problems:
        raise HTTPException(409, "cannot approve while problems remain: " + "; ".join(problems))
    store.update(doc_id, status="approved", reviewer=body.reviewer)
    store.audit(doc_id, body.reviewer, "approved")
    return store.get(doc_id)


@app.get("/api/audit")
def audit(doc_id: int | None = None):
    return store.audit_log(doc_id)


@app.get("/api/export")
def export_(format: str = "json", all: bool = False):
    docs = [store.get(d["id"]) for d in store.list_docs() if all or d["status"] in ("approved", "auto_approved")]
    docs = [d for d in docs if d["fields"]]
    if format == "csv":
        return Response(export.to_csv(docs), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=documents.csv"})
    if format == "xlsx":
        return Response(export.to_xlsx(docs, store.audit_log()), headers={"Content-Disposition": "attachment; filename=documents.xlsx"},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return Response(export.to_json(docs), media_type="application/json", headers={"Content-Disposition": "attachment; filename=documents.json"})


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")
