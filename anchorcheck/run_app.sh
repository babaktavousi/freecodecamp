#!/usr/bin/env bash
# Launch AnchorCheck (creates a virtual environment on first run).
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  . .venv/bin/activate
  pip install -r requirements.txt
else
  . .venv/bin/activate
fi
python -m anchorcheck ui
