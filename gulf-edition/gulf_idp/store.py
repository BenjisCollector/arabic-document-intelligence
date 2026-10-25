"""SQLite persistence: documents (with their extracted fields as JSON) and an append-only audit trail."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DATA = Path(os.environ.get("GULF_IDP_DATA", Path(__file__).resolve().parent.parent / "data"))
_SQL = """
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT, path TEXT, status TEXT, document_type TEXT, classification_confidence REAL,
  engine TEXT, extractor TEXT, fields TEXT, review_reasons TEXT, warnings TEXT, ocr_text TEXT, reviewer TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, ts TEXT, actor TEXT, action TEXT, field TEXT, old TEXT, new TEXT, note TEXT);
"""
_JSON = ("fields", "review_reasons", "warnings")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA / "gulf_idp.db", timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(_SQL)
    return c


def _row(r: sqlite3.Row | None, full: bool = True) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in _JSON:
        d[k] = json.loads(d[k]) if d.get(k) else ([] if k != "fields" else {})
    if not full:
        d.pop("ocr_text", None)
    return d


def create(filename: str, path: str) -> int:
    with closing(_db()) as c, c:
        return c.execute("INSERT INTO documents(filename,path,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                         (filename, path, "processing", now(), now())).lastrowid


def update(doc_id: int, **kw) -> None:
    kw = {k: (json.dumps(v, ensure_ascii=False) if k in _JSON else v) for k, v in kw.items()}
    kw["updated_at"] = now()
    with closing(_db()) as c, c:
        c.execute(f"UPDATE documents SET {','.join(k + '=?' for k in kw)} WHERE id=?", [*kw.values(), doc_id])


def get(doc_id: int) -> dict | None:
    with closing(_db()) as c:
        return _row(c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone())


def list_docs(status: str | None = None) -> list[dict]:
    with closing(_db()) as c:
        q, a = ("SELECT * FROM documents WHERE status=? ORDER BY id", (status,)) if status else ("SELECT * FROM documents ORDER BY id", ())
        return [_row(r, full=False) for r in c.execute(q, a)]


def filenames() -> set[str]:
    with closing(_db()) as c:
        return {r[0] for r in c.execute("SELECT filename FROM documents")}


def audit(doc_id: int, actor: str, action: str, field: str | None = None, old=None, new=None, note: str = "") -> None:
    with closing(_db()) as c, c:
        c.execute("INSERT INTO audit(doc_id,ts,actor,action,field,old,new,note) VALUES(?,?,?,?,?,?,?,?)",
                  (doc_id, now(), actor, action, field, None if old is None else str(old), None if new is None else str(new), note))


def audit_log(doc_id: int | None = None) -> list[dict]:
    with closing(_db()) as c:
        q, a = ("SELECT a.*, d.filename FROM audit a LEFT JOIN documents d ON d.id=a.doc_id WHERE doc_id=? ORDER BY a.id", (doc_id,)) if doc_id \
            else ("SELECT a.*, d.filename FROM audit a LEFT JOIN documents d ON d.id=a.doc_id ORDER BY a.id DESC LIMIT 300", ())
        return [dict(r) for r in c.execute(q, a)]


def reset() -> None:
    with closing(_db()) as c, c:
        c.executescript("DELETE FROM documents; DELETE FROM audit; DELETE FROM sqlite_sequence;")
