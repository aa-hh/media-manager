#!/bin/sh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/.venv/bin/activate"
python "$SCRIPT_DIR/scripts/run.py"
