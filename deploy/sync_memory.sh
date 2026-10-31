#!/usr/bin/env bash
# Compatibility shim — the canonical tool is deploy/aidog_memory.py (P2+).
# Kept so older notes/docs calling sync_memory.sh keep working.
exec python3 "$(dirname "$0")/aidog_memory.py" sync "$@"
