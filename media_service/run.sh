#!/usr/bin/env zsh
set -e
cd "${0:A:h}"
exec uv run uvicorn app.main:app --host 127.0.0.1 --port "${WX_PORT:-8765}"
