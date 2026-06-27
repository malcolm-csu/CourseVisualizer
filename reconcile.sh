#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHONPATH="$SCRIPT_DIR/lib/python3.13/site-packages" \
    python3 "$SCRIPT_DIR/reconcile.py" "$@"
