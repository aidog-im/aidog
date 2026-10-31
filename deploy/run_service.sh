#!/usr/bin/env bash
# Launcher for the mycore production chat service (P5-2).
# Credentials NEVER touch argv: the allowlist derives operator env
# AIDOG_TG_ALLOWED_USERS > own-line slot in secrets.env (P5-5: the profile
# .env indirection retired with the upstream shim); the token derives
# secrets.env MYCORE_TG_TOKEN (absent until the owner provisions the
# dedicated second bot -> fail-closed).
#
# Usage:
#   run_service.sh            # exec the service (from repo root)
#   run_service.sh --check    # readiness only: 0 ready / 3 no allowlist /
#                             # 4 no token / 2 wiring error (no output)
#                             # (car F: invalid AIDOG_PROFILE = 5, fail-closed)
#
# Car F (§13.1) profile seam — AIDOG_PROFILE unset = byte-identical legacy
# behavior; set = this profile's own secrets.env + own state root:
#   AIDOG_PROFILE=<name>   profile dir under <repo>/profiles/<name>
#   AIDOG_MC_STATE         defaults to profiles/<name>/state (explicit env wins)
#
# English comments per repo i18n policy.
set -u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

SECRETS="$REPO/secrets.env"
if [ -n "${AIDOG_PROFILE:-}" ]; then
  # Fail-closed name gate: a typo must never fall through to another
  # profile's brain (same whitelist as lib/profile_paths.py).
  if [[ ! "$AIDOG_PROFILE" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]]; then
    echo "run_service: invalid AIDOG_PROFILE='$AIDOG_PROFILE' (expected [a-z0-9][a-z0-9_-]{0,31})" >&2
    exit 5
  fi
  SECRETS="$REPO/profiles/$AIDOG_PROFILE/secrets.env"
  export AIDOG_MC_STATE="${AIDOG_MC_STATE:-$REPO/profiles/$AIDOG_PROFILE/state}"
fi

if [ -z "${AIDOG_TG_ALLOWED_USERS:-}" ]; then
  AIDOG_TG_ALLOWED_USERS=$(sed -n 's/^AIDOG_TG_ALLOWED_USERS=\([^[:space:]]*\).*/\1/p' \
    "$SECRETS" 2>/dev/null | head -1)
fi
if [ -z "${MYCORE_TG_TOKEN:-}" ]; then
  MYCORE_TG_TOKEN=$(sed -n 's/^MYCORE_TG_TOKEN=\([^[:space:]]*\).*/\1/p' \
    "$SECRETS" 2>/dev/null | head -1)
fi
export AIDOG_TG_ALLOWED_USERS MYCORE_TG_TOKEN

if [ "${1:-}" = "--check" ]; then
  [ -z "${AIDOG_TG_ALLOWED_USERS:-}" ] && exit 3
  [ -z "${MYCORE_TG_TOKEN:-}" ] && exit 4
  exit 0
fi
cd "$REPO" || exit 2
# Profile state root must exist before the service opens its pidfile/log
# (default profile: unchanged — boot_services.sh owns that mkdir).
if [ -n "${AIDOG_PROFILE:-}" ] && [ -n "${AIDOG_MC_STATE:-}" ]; then
  mkdir -p "$AIDOG_MC_STATE" || exit 2
fi
exec python3 -m mycore.service
