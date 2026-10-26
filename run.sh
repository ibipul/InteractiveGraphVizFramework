#!/usr/bin/env bash
# Copyright 2018-2026 Bipul Islam
# Licensed under the Apache License, Version 2.0. See LICENSE and NOTICE.
# SPDX-License-Identifier: Apache-2.0
# One-command launcher: sets up Python, installs dependencies, starts the graph editor.
# Usage: ./run.sh                 (opens http://localhost:8070, or the next free port)
#        PORT=9000 ./run.sh       (use exactly this port)
#        GRAPH_FILE=graphs/mine.json ./run.sh  (save the graph somewhere else)
set -uo pipefail
cd "$(dirname "$0")"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n  \033[31m✗ %s\033[0m\n\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. Python
say "[1/4] Checking Python"
PY=""
for candidate in "${PYTHON:-}" python3 python3.13 python3.12 python3.11; do
  [ -n "$candidate" ] || continue
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || die "Python 3.11 or newer is required (found: $(python3 --version 2>&1)). Install it, or run: PYTHON=/path/to/python3.11 ./run.sh"
ok "Using $("$PY" --version 2>&1)"

# ---------------------------------------------------------------- 2. Dependencies
say "[2/4] Setting up dependencies"
if [ ! -x .venv/bin/python ]; then
  echo "  Creating a virtual environment in .venv ..."
  "$PY" -m venv .venv || die "Could not create the virtual environment. Delete the .venv folder and run ./run.sh again."
fi
if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
  echo "  Installing packages (first run only; usually under a minute)."
  echo "  You'll see pip's progress below. Please wait..."
  echo
  .venv/bin/python -m pip install --upgrade pip --disable-pip-version-check -q \
    && .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt \
    || die "Package install failed (see pip's message above). Check your internet connection and run ./run.sh again. If it keeps failing, delete the .venv folder and retry."
  touch .venv/.deps-installed
  echo
  ok "Packages installed"
else
  ok "Packages already installed (skipping)"
fi

# ---------------------------------------------------------------- 3. Port
say "[3/4] Finding a port"
port_free() { .venv/bin/python -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", int(sys.argv[1])))' "$1" 2>/dev/null; }
is_this_app() { curl -s --max-time 2 "http://127.0.0.1:$1/api/health" 2>/dev/null | grep -q '"interactive-graph"'; }
who_has() { command -v lsof >/dev/null && lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1" (PID "$2")"}'; }

if [ -n "${PORT:-}" ]; then
  if ! port_free "$PORT"; then
    if is_this_app "$PORT"; then
      die "This app is already running on port $PORT: open http://localhost:$PORT. To restart it, press Ctrl+C in the terminal where it runs (or: kill $(lsof -ti tcp:$PORT 2>/dev/null | head -1)), then run ./run.sh again."
    fi
    die "Port $PORT is in use by another program $(who_has "$PORT"). Pick another, e.g.: PORT=9000 ./run.sh"
  fi
else
  # Walk up from 8070: use the first free port, but if an earlier run of this app is found, point to it.
  PORT=8070
  while ! port_free "$PORT"; do
    if is_this_app "$PORT"; then
      warn "An earlier run of this app is already serving http://localhost:$PORT"
      warn "Just use that one. To restart instead, stop it first (Ctrl+C in its terminal, or: kill $(lsof -ti tcp:$PORT 2>/dev/null | head -1)), then run ./run.sh again."
      [ -z "${NO_BROWSER:-}" ] && command -v open >/dev/null && open "http://localhost:$PORT" >/dev/null 2>&1
      [ -z "${NO_BROWSER:-}" ] && command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT" >/dev/null 2>&1
      echo; exit 0
    fi
    warn "Port $PORT is busy (used by $(who_has "$PORT")); trying $((PORT + 1))"
    PORT=$((PORT + 1))
    [ "$PORT" -le 8100 ] || die "No free port between 8070 and 8100. Free one up, or run: PORT=9000 ./run.sh"
  done
fi
ok "Using port $PORT"

# ---------------------------------------------------------------- 4. Server
say "[4/4] Starting the web server"
.venv/bin/python -m uvicorn interactive_graph.server:app --host 127.0.0.1 --port "$PORT" --log-level warning &
SERVER_PID=$!
trap 'echo; echo "  Stopping..."; kill $SERVER_PID 2>/dev/null; wait $SERVER_PID 2>/dev/null; exit 0' INT TERM

for _ in $(seq 1 60); do
  kill -0 $SERVER_PID 2>/dev/null || die "The server stopped during startup (see the error above)."
  is_this_app "$PORT" && break
  sleep 1
done
is_this_app "$PORT" || { kill $SERVER_PID 2>/dev/null; die "The server did not respond within 60 seconds (see any error above)."; }

URL="http://localhost:$PORT"
printf '\n  \033[1;32m●  Ready:  %s\033[0m\n' "$URL"
echo "     Open it in your browser (opening automatically if possible)."
echo "     Leave this terminal open while you use the app. Press Ctrl+C to stop."
echo "     The graph is saved to ${GRAPH_FILE:-data/graph.json} after every edit."
echo
[ -z "${NO_BROWSER:-}" ] && command -v open >/dev/null && open "$URL" >/dev/null 2>&1
[ -z "${NO_BROWSER:-}" ] && command -v xdg-open >/dev/null && xdg-open "$URL" >/dev/null 2>&1
wait $SERVER_PID
