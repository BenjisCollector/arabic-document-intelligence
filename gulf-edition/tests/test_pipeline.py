import json
import shutil
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from gulf_idp import llm
from gulf_idp.pipeline import process
from gulf_idp.schemas import SCHEMAS, upstream_json_schema, validate

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
TRUTH = json.loads((SAMPLES / "ground_truth.json").read_text())
needs_ocr = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")


def test_validation_rules():
    bad = validate("invoice", dict(subtotal="100.00", vat_amount="15.00", total="120.00", vat_rate="15", currency="SAR"))
    assert any("do not add up" in m for _, m in bad)
    assert not validate("invoice", dict(subtotal="100.000", vat_amount="0.000", total="100.000", vat_rate="0", currency="KWD"))
    assert any("unexpected VAT" in m for _, m in validate("invoice", dict(subtotal="100", vat_amount="15", total="115", vat_rate="15", currency="AED")))
    assert validate("leave_request", dict(start_date="2026-05-20", end_date="2026-05-18"))
    assert validate("employment_contract", dict(notice_period_days=400))


def test_upstream_schema_shape():
    s = upstream_json_schema(SCHEMAS["invoice"])
    assert s["type"] == "object" and "invoice_number" in s["properties"] and s["properties"]["total"]["anyOf"][0]["type"] == "number"


@needs_ocr
def test_sample_set_accuracy_and_review_routing():
    hit = total = 0
    for doc in sorted((SAMPLES / "docs").iterdir()):
        r, exp = process(doc), TRUTH[doc.stem]
        assert r["document_type"] == exp["document_type"], doc.name
        for k, v in exp["fields"].items():
            total += 1
            got = r["fields"][k]["value"]
            hit += (got is None and v is None) or str(got) == str(v)
        if exp["expected_to_need_review"]:
            assert r["review_reasons"], f"{doc.name} should be queued for review"
        # a wrong value must never be waved through with high confidence
        for k, v in exp["fields"].items():
            f = r["fields"][k]
            if f["value"] is not None and str(f["value"]) != str(v):
                assert f["confidence"] < 0.8 or r["review_reasons"], f"{doc.name}.{k} wrong but confident"
    assert hit / total >= 0.95, f"{hit}/{total}"


def test_text_layer_arabic_indic_digits_and_hijri():
    r = process(SAMPLES / "docs" / "inv-05-kwd-en-arabic-separators.pdf")
    assert r["fields"]["total"]["value"] == "4850.500" and r["fields"]["currency"]["value"] == "KWD"
    assert not r["review_reasons"]


def test_llm_path_with_openai_compatible_server(monkeypatch, tmp_path):
    """A tiny local OpenAI-compatible server: proves request shape, grounding check and fallback."""
    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["model"] = body["model"]
            out = {"invoice_number": {"value": "GDL-2026-0457", "evidence": "Invoice number: GDL-2026-0457", "confidence": 0.99},
                   "total": {"value": "AED 9,999.00", "evidence": "Total: AED 9,999.00", "confidence": 0.99}}  # not in the document
            data = json.dumps({"choices": [{"message": {"content": json.dumps(out)}}]}).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
    monkeypatch.setenv("OPENAI_MODEL", "mock")
    r = process(SAMPLES / "docs" / "inv-03-aed-en-indic.pdf")
    assert r["extractor"].startswith("llm") and seen["auth"] == "Bearer test-key" and seen["model"] == "mock"
    assert r["fields"]["invoice_number"]["confidence"] > 0.9 and r["fields"]["invoice_number"]["source"] == "llm"
    assert r["fields"]["total"]["source"] == "llm-ungrounded" and r["fields"]["total"]["confidence"] <= 0.5  # hallucination caught
    srv.shutdown()
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")  # dead endpoint -> falls back to rules, says so
    r = process(SAMPLES / "docs" / "inv-03-aed-en-indic.pdf")
    assert r["extractor"] == "rules" and r["warnings"] and r["fields"]["total"]["value"] == "3150.00"
    assert llm.config()["name"] == "openai:mock"
