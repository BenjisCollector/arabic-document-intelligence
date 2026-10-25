"""Text extraction. Local: PDF text layer (clean Latin PDFs) or Tesseract ara+eng. Production: Azure Document Intelligence.

Backend selection: OCR_BACKEND=auto|tesseract|azure (auto = azure if AZURE_DI_ENDPOINT + AZURE_DI_KEY are set).
A PDF text layer is used only for mostly-Latin pages; Arabic PDFs are rendered and OCR'd because their text layers
are usually in visual order with broken glyph maps.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
import pymupdf

from .normalize import _BIDI



@dataclass
class Line:
    text: str
    conf: float  # 0..1, engine confidence for this line (1.0 for an exact text layer)
    words: list[float] | None = None  # per-token confidence, aligned with text.split()


@dataclass
class OcrResult:
    lines: list[Line]
    engine: str

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.translate(_BIDI)).strip()


def _render(pdf: Path, dpi: int = 250) -> list[bytes]:
    with pymupdf.open(pdf) as d:
        return [p.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).tobytes("png") for p in d]


def _tesseract(png: bytes, psm: int = 6) -> list[Line]:
    if not shutil.which("tesseract"):
        raise RuntimeError("tesseract not found: brew install tesseract tesseract-lang (needs the 'ara' language pack)")
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(png)
        f.flush()
        out = subprocess.run(["tesseract", f.name, "stdout", "-l", "ara+eng", "--psm", str(psm), "--dpi", "250", "tsv"],
                             capture_output=True, text=True, check=True).stdout
    rows: dict[tuple, list] = {}
    for r in out.splitlines()[1:]:
        c = r.split("\t")
        if len(c) < 12 or not c[11].strip() or float(c[10]) < 0:
            continue
        rows.setdefault((c[2], c[3], c[4]), []).append((float(c[10]), c[11], int(c[7])))
    lines = []
    for ws in sorted(rows.values(), key=lambda w: min(x[2] for x in w)):
        ws = [(c / 100, _clean(t)) for c, t, _ in ws if _clean(t)]
        if ws:
            lines.append(Line(" ".join(t for _, t in ws), round(sum(c for c, _ in ws) / len(ws), 3), [c for c, _ in ws]))
    return lines


def _azure(data: bytes) -> OcrResult:
    """Azure AI Document Intelligence prebuilt-read via REST (no SDK). Requires AZURE_DI_ENDPOINT and AZURE_DI_KEY."""
    ep, key = os.environ["AZURE_DI_ENDPOINT"].rstrip("/"), os.environ["AZURE_DI_KEY"]
    url = f"{ep}/documentintelligence/documentModels/prebuilt-read:analyze?api-version=2024-11-30"
    h = {"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/octet-stream"}
    with httpx.Client(timeout=60) as c:
        r = c.post(url, headers=h, content=data)
        r.raise_for_status()
        op = r.headers["Operation-Location"]
        for _ in range(60):
            j = c.get(op, headers={"Ocp-Apim-Subscription-Key": key}).json()
            if j["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
    if j["status"] != "succeeded":
        raise RuntimeError(f"Azure Document Intelligence failed: {j.get('error')}")
    lines = []
    for page in j["analyzeResult"]["pages"]:
        words = page.get("words", [])
        for ln in page.get("lines", []):
            off = ln["spans"][0]["offset"], ln["spans"][0]["offset"] + ln["spans"][0]["length"]
            cf = [w["confidence"] for w in words if off[0] <= w["span"]["offset"] < off[1]]
            lines.append(Line(_clean(ln["content"]), round(sum(cf) / len(cf), 3) if cf else 0.9))
    return OcrResult(lines, "azure-document-intelligence")


def _pdf_layer(path: Path) -> str:
    """Text layer, bidi-reordered by poppler's pdftotext when available (PyMuPDF returns visual order)."""
    if shutil.which("pdftotext"):
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", str(path), "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return r.stdout.decode("utf-8", "replace")
    with pymupdf.open(path) as d:
        return "\n".join(p.get_text() for p in d)


def _trust_layer(t: str) -> bool:
    """Use the text layer only for mostly-Latin pages. Arabic text layers from PDF generators are written in visual
    order with broken glyph maps, so Arabic-dominant pages are rendered and OCR'd instead."""
    ar, la = len(re.findall(r"[\u0621-\u064A]", t)), len(re.findall(r"[A-Za-z]", t))
    return la > 0 and ar < 0.1 * (ar + la)


def read_document(path: str | Path, psm: int = 6) -> OcrResult:
    path = Path(path)
    backend = os.environ.get("OCR_BACKEND", "auto")
    if backend == "azure" or (backend == "auto" and os.environ.get("AZURE_DI_ENDPOINT") and os.environ.get("AZURE_DI_KEY")):
        return _azure(path.read_bytes())
    if path.suffix.lower() == ".txt":
        return OcrResult([Line(_clean(l), 1.0) for l in path.read_text(encoding="utf-8").splitlines() if _clean(l)], "text-file")
    if path.suffix.lower() == ".pdf":
        layer = _pdf_layer(path)
        if _trust_layer(layer):
            return OcrResult([Line(_clean(l.replace("\ufffd", "")), 1.0) for l in layer.splitlines() if _clean(l)], "pdf-text-layer")
        pages = _render(path)
    else:
        pages = [path.read_bytes()]
    return OcrResult([l for p in pages for l in _tesseract(p, psm)], "tesseract-ara+eng")
