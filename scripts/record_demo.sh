#!/usr/bin/env bash
# Graba la demo HDT-7 en tmux (MCP | A2A | cliente A2A) con asciinema.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT=${1:-$REPO/docs/demo.cast}
S=hdt7demo; COLS=190; ROWS=54
PY=.venv/bin/python

tmux kill-session -t $S 2>/dev/null
tmux new-session -d -s $S -x $COLS -y $ROWS -c "$REPO"
tmux set -t $S status off
tmux set -t $S pane-border-status top
tmux set -t $S pane-border-format " #[bold]#{pane_title} "
tmux split-window -v -t $S:0 -c "$REPO"
tmux split-window -v -t $S:0 -c "$REPO"
tmux select-pane -t $S:0.0 -T "1) Servidor MCP - Streamable HTTP :8000/mcp"
tmux select-pane -t $S:0.1 -T "2) Servidor A2A - Multi Agent System :9999"
tmux select-pane -t $S:0.2 -T "3) Terminal - Cliente A2A"
tmux resize-pane -t $S:0.0 -y 17
tmux resize-pane -t $S:0.1 -y 11
for p in 0 1 2; do tmux send-keys -t $S:0.$p "clear" Enter; done
sleep 1

script -qfc "stty rows $ROWS cols $COLS; asciinema rec -q --overwrite -c 'tmux attach -t $S' '$OUT'" /dev/null >/dev/null 2>&1 &
REC=$!
sleep 2

type_in() {  # panel, texto: simula tecleo
  local pane=$1 text=$2 i
  for ((i = 0; i < ${#text}; i++)); do
    local ch="${text:i:1}"
    [[ $ch == ";" ]] && ch='\;'   # tmux interpreta ';' suelto como separador
    tmux send-keys -t $S:0.$pane -l "$ch"; sleep 0.035
  done
  sleep 0.4; tmux send-keys -t $S:0.$pane Enter
}
pane_text() { tmux capture-pane -p -J -S -2000 -t $S:0.$1; }
wait_for() {  # panel, patrón, timeout
  local end=$((SECONDS + ${3:-60}))
  until pane_text $1 | grep -q -- "$2"; do
    ((SECONDS > end)) && { echo "timeout esperando '$2' en panel $1" >&2; return 1; }
    sleep 0.5
  done
}
prompts() { pane_text 2 | grep -c "Usted>"; }
ask() {
  local before; before=$(prompts)
  type_in 2 "$1"
  local end=$((SECONDS + 300))
  until (( $(prompts) > before )); do ((SECONDS > end)) && break; sleep 0.5; done
  sleep ${PAUSE:-30}  # respeta el límite de tokens/min de Groq; agg recorta esta espera
}

type_in 0 "$PY -m mcp_server.server"
wait_for 0 "Uvicorn running" 30; sleep 2
type_in 1 "$PY -m a2a_server.server"
wait_for 1 "startup complete" 30; sleep 2

type_in 2 "curl -s localhost:9999/.well-known/agent-card.json | $PY -c 'import json,sys; c=json.load(sys.stdin); print(c[\"name\"]); [print(\" skill:\", s[\"id\"], \"-\", s[\"name\"]) for s in c[\"skills\"]]'"
sleep 4
type_in 2 "clear; $PY cli.py"
wait_for 2 "Usted>" 30; sleep 2

ask "¿Cuál es el peso máximo para saltar?"
ask "¿Cómo está el clima para saltar mañana?"
ask "¿Puedo saltar el 2026-12-20?"
ask "Quiero agendar una cita de salto para el 2026-11-01"
ask "A nombre de Ana López, somos 2 personas"
ask "Muéstrame las citas agendadas"
type_in 2 "salir"
sleep 6   # cuadro final: logs MCP/A2A y conversación completos

tmux kill-session -t $S
pkill -f "[.]venv/bin/python -m (mcp|a2a)_server" || true
wait $REC 2>/dev/null
echo "grabación: $OUT"
