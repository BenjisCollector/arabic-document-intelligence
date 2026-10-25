#!/usr/bin/env bash
# One command, no Azure: OCR with local Tesseract (Arabic + English), rules extractor (or your OpenAI-compatible LLM if a key is set).
set -euo pipefail
cd "$(dirname "$0")"
command -v tesseract >/dev/null || { echo "Install Tesseract with Arabic data first: brew install tesseract tesseract-lang (or apt install tesseract-ocr tesseract-ocr-ara)"; exit 1; }
tesseract --list-langs 2>/dev/null | grep -qx ara || { echo "Tesseract has no Arabic data (ara). Install tesseract-lang / tesseract-ocr-ara."; exit 1; }
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
PORT="${PORT:-8000}"
echo "Gulf edition demo -> http://127.0.0.1:${PORT}"
ARGS=(--port "$PORT" --load-samples)
[ -z "${NO_OPEN:-}" ] && ARGS+=(--open)  # NO_OPEN=1 to skip opening the browser
exec uv run python -m gulf_idp "${ARGS[@]}"
