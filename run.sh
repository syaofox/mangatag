#!/bin/bash

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
cd "$SCRIPT_DIR"

# Gradio 完整 Web UI（抓取 / 更新 Number / 更新压缩包 / 编辑压缩包内 XML）
uv run webui.py
