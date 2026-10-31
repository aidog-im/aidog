#!/usr/bin/env bash
# profile_ctl.sh — car F §13.1 per-profile runtime controller.
# One named profile = its own tmux pair + its own console port:
#   aidog_svc_<name>  mycore chat service (own bot token, own state root)
#   aidog_web_<name>  web console on the profile.json web_port
#
# The default stack (aidog_web/aidog_svc/aidog_mwd ...) stays owned by
# deploy/boot_services.sh — this tool never touches those sessions.
# stop uses EXACT tmux session names only (never a broad pkill pattern).
#
# Usage:
#   bash deploy/profile_ctl.sh start  <name>   # --check gate, then tmux pair
#   bash deploy/profile_ctl.sh stop   <name>   # kill exactly the two sessions
#   bash deploy/profile_ctl.sh status <name>   # sessions + port probe
#
# Exit codes: 0 ok · 1 usage/state error · 2 bad profile name · 3 not provisioned
#             4 no bot token · 5 profile dir missing.
# English comments per repo i18n policy.
set -u

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)   # self-deriving
ACTION=${1:-}; NAME=${2:-}

if [ -z "$ACTION" ] || [ -z "$NAME" ]; then
  echo "usage: $0 start|stop|status <name>" >&2
  exit 1
fi
# Same whitelist as lib/profile_paths.py — a bad name never reaches a path.
if [[ ! "$NAME" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]]; then
  echo "profile_ctl: invalid profile name '$NAME' (expected [a-z0-9][a-z0-9_-]{0,31})" >&2
  exit 2
fi

ROOT=$REPO/profiles/$NAME
STATE=$ROOT/state
SVC_SESS=aidog_svc_$NAME
WEB_SESS=aidog_web_$NAME

# Local proxy injection — same pattern as deploy/boot_services.sh (post-reboot
# egress: tmux children inherit the SERVER env, so every session carries it).
PROXY_URL=${HTTPS_PROXY:-${https_proxy:-http://127.0.0.1:12334}}
NO_PROXY_VAL=${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}
PROXY_ENV="env HTTP_PROXY=$PROXY_URL HTTPS_PROXY=$PROXY_URL http_proxy=$PROXY_URL https_proxy=$PROXY_URL NO_PROXY=$NO_PROXY_VAL no_proxy=$NO_PROXY_VAL"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
session_exists() { tmux has-session -t "$1" 2>/dev/null; }

web_port() {
  # profile.json web_port, else the car-F first-profile default 8791.
  python3 - "$ROOT/profile.json" <<'PY' 2>/dev/null
import json, sys
try:
    doc = json.load(open(sys.argv[1]))
except Exception:
    doc = {}
port = doc.get("web_port")
print(port if isinstance(port, int) else 8791)
PY
}

port_up() {
  # Probe OUR console, not just the port: /api/health answers 200+status=ok
  # only for this product's server (a foreign process squatting on the port
  # must not read as "up" — observed: site preview server on 8791).
  python3 - "$1" <<'PY' >/dev/null 2>&1
import json, sys, urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/health", timeout=3) as r:
    doc = json.loads(r.read().decode() or "{}")
raise SystemExit(0 if doc.get("status") == "ok" else 1)
PY
}

require_profile_dir() {
  if [ ! -f "$ROOT/profile.json" ]; then
    echo "profile_ctl: no profile '$NAME' at $ROOT (run: aidog profile create $NAME)" >&2
    exit 5
  fi
}

do_start() {
  require_profile_dir
  # Readiness gate is deploy/run_service.sh --check (single source of truth:
  # allowlist -> rc3, token -> rc4, wiring -> rc2; credentials never touch argv).
  AIDOG_PROFILE=$NAME bash "$REPO/deploy/run_service.sh" --check
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    log "$SVC_SESS: not started (fail-closed: run_service --check rc=$rc — fill $ROOT/secrets.env)"
    exit "$rc"
  fi
  mkdir -p "$STATE"
  if session_exists "$SVC_SESS"; then
    log "$SVC_SESS: already running"
  else
    tmux new -d -s "$SVC_SESS" \
      "cd $REPO && $PROXY_ENV env AIDOG_PROFILE=$NAME bash deploy/run_service.sh > $STATE/boot.log 2>&1"
    session_exists "$SVC_SESS" && log "$SVC_SESS: started" || log "$SVC_SESS: tmux new FAILED"
  fi
  local port; port=$(web_port)
  if session_exists "$WEB_SESS"; then
    log "$WEB_SESS: already running (port $port)"
  else
    tmux new -d -s "$WEB_SESS" \
      "cd $REPO/web && $PROXY_ENV env AIDOG_PROFILE=$NAME AIDOG_WEB_PORT=$port python3 server.py > $STATE/web.log 2>&1"
    session_exists "$WEB_SESS" && log "$WEB_SESS: started (port $port)" || log "$WEB_SESS: tmux new FAILED"
  fi
}

do_stop() {
  # Exact session names ONLY — never `pkill -f` (self-kill hazard + collateral).
  local s rc=0
  for s in "$WEB_SESS" "$SVC_SESS"; do
    if session_exists "$s"; then
      tmux kill-session -t "$s" && log "$s: stopped" || { log "$s: kill FAILED"; rc=1; }
    else
      log "$s: not running"
    fi
  done
  return $rc
}

do_status() {
  local rc=0 port
  port=$(web_port)
  if session_exists "$SVC_SESS"; then
    echo "✅ tmux $SVC_SESS: up"
  elif AIDOG_PROFILE=$NAME bash "$REPO/deploy/run_service.sh" --check; then
    echo "❌ tmux $SVC_SESS: provisioned but DOWN"; rc=1
  else
    echo "⏳ tmux $SVC_SESS: pending (fail-closed: fill $ROOT/secrets.env)"
  fi
  if session_exists "$WEB_SESS"; then echo "✅ tmux $WEB_SESS: up"; else echo "❌ tmux $WEB_SESS: DOWN"; rc=1; fi
  if port_up "$port"; then echo "✅ web console :$port: up"; else echo "❌ web console :$port: DOWN"; rc=1; fi
  return $rc
}

case "$ACTION" in
  start)  do_start ;;
  stop)   do_stop ;;
  status) do_status ;;
  *) echo "usage: $0 start|stop|status <name>" >&2; exit 1 ;;
esac
