import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SAMPLES = Path(__file__).resolve().parent.parent / "samples" / "docs"
pytestmark = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract not installed")


def test_review_flow_audit_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("GULF_IDP_DATA", str(tmp_path))
    import importlib

    from gulf_idp import api, store
    importlib.reload(store)
    importlib.reload(api)
    c = TestClient(api.app)
    bad = c.post("/api/documents", files={"file": ("inv-06.pdf", (SAMPLES / "inv-06-sar-ar-bad-total.pdf").read_bytes())}).json()["id"]
    good = c.post("/api/documents", files={"file": ("inv-03.pdf", (SAMPLES / "inv-03-aed-en-indic.pdf").read_bytes())}).json()["id"]
    assert c.get(f"/api/documents/{good}").json()["status"] == "auto_approved"
    d = c.get(f"/api/documents/{bad}").json()
    assert d["status"] == "needs_review"
    assert c.post(f"/api/documents/{bad}/approve", json={}).status_code == 409          # totals do not add up
    assert c.patch(f"/api/documents/{bad}/fields/total", json={"value": "abc"}).status_code == 422
    r = c.patch(f"/api/documents/{bad}/fields/total", json={"value": "٥٬٧٥٠٫٠٠ ريال", "reviewer": "Sara"}).json()  # Arabic-Indic input normalised
    assert r["total"]["value"] == "5750.00" and not r["total"]["issues"]
    assert c.post(f"/api/documents/{bad}/approve", json={"reviewer": "Sara"}).json()["status"] == "approved"
    actions = [(e["actor"], e["action"]) for e in c.get(f"/api/audit?doc_id={bad}").json()]
    assert ("Sara", "field_corrected") in actions and ("Sara", "approved") in actions
    assert len(c.get("/api/export?format=json").json()) == 2
    assert c.get("/api/export?format=xlsx").content[:2] == b"PK" and "invoice_number" in c.get("/api/export?format=csv").text
