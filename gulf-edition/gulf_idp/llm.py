"""Optional LLM extraction through any OpenAI-compatible endpoint (OpenAI or Azure OpenAI), plain REST."""
from __future__ import annotations

import json
import os

import httpx

from .extract import Raw
from .normalize import norm_ar
from .schemas import Schema


def config() -> dict | None:
    e = os.environ
    if e.get("AZURE_OPENAI_ENDPOINT") and e.get("AZURE_OPENAI_API_KEY") and e.get("AZURE_OPENAI_DEPLOYMENT"):
        dep = e["AZURE_OPENAI_DEPLOYMENT"]
        ver = e.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
        return dict(name=f"azure-openai:{dep}", headers={"api-key": e["AZURE_OPENAI_API_KEY"]}, model=None,
                    url=f"{e['AZURE_OPENAI_ENDPOINT'].rstrip('/')}/openai/deployments/{dep}/chat/completions?api-version={ver}")
    if e.get("OPENAI_API_KEY"):
        m = e.get("OPENAI_MODEL", "gpt-4o-mini")
        return dict(name=f"openai:{m}", headers={"Authorization": f"Bearer {e['OPENAI_API_KEY']}"}, model=m,
                    url=e.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/") + "/chat/completions")
    return None


def llm_extract(schema: Schema, text: str, cfg: dict) -> dict[str, Raw]:
    spec = "\n".join(f"- {f.name} ({f.type}{'' if f.required else ', optional'}): {f.desc}" for f in schema.fields if f.name != "currency")
    system = (
        "You extract structured data from OCR text of Arabic/English business documents. Return one JSON object mapping each field "
        'name to {"value": string|null, "evidence": the exact line copied from the text, "confidence": 0..1}. '
        "Copy values exactly as printed (keep Arabic-Indic digits, Hijri dates and currency words); do not normalise or invent. "
        "Use null when the field is absent."
    )
    body = {"messages": [{"role": "system", "content": system},
                         {"role": "user", "content": f"Fields:\n{spec}\n\nDocument text:\n{text}"}],
            "temperature": 0, "response_format": {"type": "json_object"}}
    if cfg["model"]:
        body["model"] = cfg["model"]
    r = httpx.post(cfg["url"], headers=cfg["headers"], json=body, timeout=90)
    r.raise_for_status()
    data = json.loads(r.json()["choices"][0]["message"]["content"])
    flat = norm_ar(text)
    out: dict[str, Raw] = {}
    for name, item in data.items():
        if not isinstance(item, dict) or not item.get("value"):
            continue
        ev = str(item.get("evidence", ""))
        grounded = bool(ev) and norm_ar(ev) in flat and norm_ar(str(item["value"])) in flat
        conf = float(item.get("confidence", 0.5))
        out[name] = Raw(str(item["value"]), round(min(conf, 0.95) if grounded else min(conf, 0.5), 3), ev, "llm" if grounded else "llm-ungrounded")
    return out
