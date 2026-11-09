#!/usr/bin/env bash
# Launch SLA exclude TUI (uses local .venv)
set -e
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python add_exclude.py --tui "$@"
