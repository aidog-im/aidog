#!/usr/bin/env bash
# skill_route — deterministic skill pre-router wrapper (TOP3 before any SKILL.md body load).
# Feature rationale: skill descriptions stay resident (cheap), bodies load on demand;
# this router adds the deterministic layer that survives model swaps / cold starts
# (pain point: default-load bloat, token waste).
# P4 vendoring: the implementation now lives next to this wrapper (lib/skill_route.py),
# self-derived paths only — no machine paths, no cross-project exec.
# English comments per repo i18n policy.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/skill_route.py" "$@"
