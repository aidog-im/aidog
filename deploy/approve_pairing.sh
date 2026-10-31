#!/usr/bin/env bash
# RETIRED at P5-5 mycore swap (2026-10-19): kept for history; boot post-phase
# no longer starts this (pairing -> configure allowlist in secrets.env
# AIDOG_TG_ALLOWED_USERS).
# Approve the newest pending DM-pairing request for the aidog gateway.
# Workflow (Owner's file-drop pattern): he DMs the bot, drops the bot's reply
# into a file (optional evidence), we approve the latest pending request.
# Usage: bash approve_pairing.sh [evidence_file]
# i18n: English comments per repo policy; output keeps IDs only, no secrets.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# Graceful retirement: post-swap there is no gateway to pair against — without
# the upstream shim there is nothing to source. Transition behavior unchanged.
if [ ! -f "$REPO/deploy/upstream_shim.env" ]; then
  echo "approve_pairing: RETIRED at P5-5 mycore swap (2026-10-19) — pairing is now plain allowlist config (secrets.env AIDOG_TG_ALLOWED_USERS); nothing to do."
  exit 0
fi
# shellcheck source=upstream_shim.env
source "$REPO/deploy/upstream_shim.env" || { echo "missing upstream shim" >&2; exit 1; }

PROOF="${1:-}"
if [ -n "$PROOF" ]; then
  [ -f "$PROOF" ] || { echo "evidence file not found: $PROOF"; exit 1; }
  echo "evidence: $PROOF ($(wc -c < "$PROOF") bytes, content not echoed)"
fi

LIST=$("$AIDOG_KERNEL_BIN" -p aidog pairing list 2>/dev/null || true)
RID=$(echo "$LIST" | awk '/Pending Pairing Requests/ {found=1} found && $1=="telegram" {print $2; exit}')
if [ -z "$RID" ]; then
  echo "no pending telegram pairing request"
  exit 0
fi
echo "approving request: $RID"
"$AIDOG_KERNEL_BIN" -p aidog pairing approve telegram "$RID"
"$AIDOG_KERNEL_BIN" -p aidog pairing list | sed -n '/Approved Users/,+5p'
