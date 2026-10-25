"""Write the Gulf schemas in the JSON-Schema format the upstream accelerator registers via /schemavault/.

    uv run python export_schemas.py
    python ../src/ContentProcessorAPI/samples/schemas/register_schema.py https://<your-api> \\
        ../src/ContentProcessorAPI/samples/schemas/gulf/schema_info.json
"""
import json
from pathlib import Path

from gulf_idp.schemas import SCHEMAS, upstream_json_schema

out = Path(__file__).resolve().parent.parent / "src/ContentProcessorAPI/samples/schemas/gulf"
out.mkdir(parents=True, exist_ok=True)
manifest = {"schemas": [], "schemaset": {"Name": "Gulf documents", "Description": "Arabic-first invoice, employment contract and HR leave request schemas (KWD, QAR, SAR, AED)"}}
for s in SCHEMAS.values():
    js = upstream_json_schema(s)
    (out / f"{s.id}.json").write_text(json.dumps(js, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["schemas"].append({"File": f"{s.id}.json", "ClassName": js["title"], "Description": s.description})
(out / "schema_info.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
print("wrote", sorted(p.name for p in out.iterdir()))
