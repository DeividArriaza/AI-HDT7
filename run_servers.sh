#!/usr/bin/env bash
# Levanta MCP (:8000) y A2A (:9999); logs en pantalla y en logs/. Ctrl+C detiene ambos.
set -euo pipefail
cd "$(dirname "$0")"
PY=${PYTHON:-.venv/bin/python}
mkdir -p logs
trap 'kill 0' INT TERM EXIT
$PY -m mcp_server.server 2>&1 | tee logs/mcp.log &
sleep 2
$PY -m a2a_server.server 2>&1 | tee logs/a2a.log &
wait
