#!/bin/bash

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# FastAPI + HTMX：仅「编辑压缩包内 XML」
# 可选环境变量：ALLOWED_BASE_PATHS（逗号分隔）、SESSION_SECRET
export ALLOWED_BASE_PATHS="/home/syaofox/Downloads/1"
uv run uvicorn app:app --host 0.0.0.0 --port 8000
