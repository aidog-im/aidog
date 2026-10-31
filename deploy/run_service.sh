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
#
# English comments per repo i18n policy.
set -u
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

if [ -z "${AIDOG_TG_ALLOWED_USERS:-}" ]; then
  AIDOG_TG_ALLOWED_USERS=$(sed -n 's/^AIDOG_TG_ALLOWED_USERS=\([^[:space:]]*\).*/\1/p' \
    "$REPO/secrets.env" 2>/dev/null | head -1)
fi
if [ -z "${MYCORE_TG_TOKEN:-}" ]; then
  MYCORE_TG_TOKEN=$(sed -n 's/^MYCORE_TG_TOKEN=\([^[:space:]]*\).*/\1/p' \
    "$REPO/secrets.env" 2>/dev/null | head -1)
fi
export AIDOG_TG_ALLOWED_USERS MYCORE_TG_TOKEN

if [ "${1:-}" = "--check" ]; then
  [ -z "${AIDOG_TG_ALLOWED_USERS:-}" ] && exit 3
  [ -z "${MYCORE_TG_TOKEN:-}" ] && exit 4
  exit 0
fi
cd "$REPO" || exit 2
exec python3 -m mycore.service
