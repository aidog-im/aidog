#!/usr/bin/env bash
# Approve the newest pending DM-pairing request for the aidog gateway.
# Workflow (Owner's file-drop pattern): he DMs the bot, drops the bot's reply
# into a file (optional evidence), we approve the latest pending request.
# Usage: bash approve_pairing.sh [evidence_file]
# i18n: English comments per repo policy; output keeps IDs only, no secrets.
set -euo pipefail

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
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
