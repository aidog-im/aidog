#!/usr/bin/env bash
# Idempotent starter for the AiDog runtime tmux sessions (post-swap P5-5 view):
#   aidog_web - situational web console (127.0.0.1:8790)
#   aidog_svc - mycore chat service (P5-2; gated fail-closed until the
#               dedicated bot token + allowlist are provisioned)
#   aidog_mwd - mycore watchdog loop (--execute per owner authorization
#               2026-01-19; log-only notify until MYCORE_WD_CHAT is set)
# Transition phase only (shim present): aidog_gw (kernel gateway) and
# aidog_wd (legacy gateway watchdog). Post-swap they are RETIRED: never
# started, and `status` flags any lingering session as a leftover (rc=1).
#
# Designed to be the single entry point invoked at boot (Windows Task
# Scheduler -> wsl.exe, or /etc/wsl.conf [boot] command - see docs for the
# chosen option). Safe to run repeatedly: existing sessions/processes are
# left untouched (idempotent), missing ones are brought up.
#
# Usage:
#   boot_services.sh            # start anything that is missing
#   boot_services.sh status     # report session/process/endpoint state
#   boot_services.sh stop       # stop all three sessions (maintenance)
#
# English comments per repo i18n policy.
set -u

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)  # self-deriving: no machine path
# P5-5 phase resolver: the upstream shim marks the transition stack.
#   transition (shim present) = current behavior: source it, run gw/wd.
#   post (shim deleted)       = gw/wd retired; no upstream facts are sourced,
#                               GW_PATTERN/GWLOG stay empty (see guards below).
if [ -f "$REPO/deploy/upstream_shim.env" ]; then
  PHASE=transition
  # shellcheck source=upstream_shim.env
  source "$REPO/deploy/upstream_shim.env" || { echo "boot_services: missing upstream shim" >&2; exit 1; }
  GW_PATTERN=$AIDOG_KERNEL_CMD
  GWLOG=$AIDOG_PROFILE_DIR/logs/gateway_run.log
else
  PHASE=post
  GW_PATTERN=""
  GWLOG=""
fi
WEB_LOG=$REPO/deploy/web_console.log
WEB_PORT=${AIDOG_WEB_PORT:-8790}

# Post-reboot egress fix (owner-approved, 2026-10-17): direct Telegram API egress
# is unreachable from this host after a reboot, and tmux children inherit the
# SERVER environment (not ours) — so every session must carry the local proxy
# explicitly: global env table for any later `tmux new` (incl. watchdog restarts)
# plus an `env` prefix on each start command for determinism.
PROXY_URL=${HTTPS_PROXY:-${https_proxy:-http://127.0.0.1:12334}}
NO_PROXY_VAL=${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}
PROXY_ENV="env HTTP_PROXY=$PROXY_URL HTTPS_PROXY=$PROXY_URL http_proxy=$PROXY_URL https_proxy=$PROXY_URL NO_PROXY=$NO_PROXY_VAL no_proxy=$NO_PROXY_VAL"

inject_proxy() {
  tmux set-environment -g HTTP_PROXY "$PROXY_URL"
  tmux set-environment -g HTTPS_PROXY "$PROXY_URL"
  tmux set-environment -g http_proxy "$PROXY_URL"
  tmux set-environment -g https_proxy "$PROXY_URL"
  tmux set-environment -g NO_PROXY "$NO_PROXY_VAL"
  tmux set-environment -g no_proxy "$NO_PROXY_VAL"
}

WD_PATTERN='bash .*imaidog/deploy/watchdog.sh'

srv_pid() { tmux display-message -p '#{pid}' 2>/dev/null; }

our_pgrep() {  # pids matching pattern, excluding the tmux SERVER: its argv quotes
  local p srv  # our own `tmux new -d ... "<command>"` string and would self-match.
  srv=$(srv_pid)
  for p in $(pgrep -f "$1" 2>/dev/null); do
    [ "$p" = "$srv" ] && continue
    printf '%s\n' "$p"
  done
}

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }

session_exists() { tmux has-session -t "$1" 2>/dev/null; }

web_up() {
  python3 - "$WEB_PORT" <<'PY' >/dev/null 2>&1
import sys, urllib.request
urllib.request.urlopen(f"http://127.0.0.1:{sys.argv[1]}/", timeout=3).read(64)
PY
}

start_gw() {
  if [ "$PHASE" = post ]; then log "aidog_gw: retired (post-swap)"; return 0; fi
  if session_exists aidog_gw || [ -n "$(our_pgrep "$GW_PATTERN")" ]; then
    log "aidog_gw: already running"
  else
    # First tmux command after a hard reboot can fail on a stale socket
    # (observed 2026-10-17: gateway session silently missing at boot) — verify
    # the session appeared and retry once before letting status report FAIL.
    if ! tmux new -d -s aidog_gw "$PROXY_ENV $AIDOG_KERNEL_CMD --force > $GWLOG 2>&1"; then
      log "aidog_gw: tmux new failed (attempt 1), retrying"
      sleep 1
      tmux new -d -s aidog_gw "$PROXY_ENV $AIDOG_KERNEL_CMD --force > $GWLOG 2>&1" || \
        log "aidog_gw: tmux new failed (attempt 2)"
    elif ! session_exists aidog_gw; then
      sleep 1
      session_exists aidog_gw || tmux new -d -s aidog_gw "$PROXY_ENV $AIDOG_KERNEL_CMD --force > $GWLOG 2>&1" || \
        log "aidog_gw: session missing after start, retry failed"
    fi
    log "aidog_gw: started"
  fi
}

start_wd() {
  if [ "$PHASE" = post ]; then log "aidog_wd: retired (post-swap)"; return 0; fi
  if session_exists aidog_wd || [ -n "$(our_pgrep "$WD_PATTERN")" ]; then
    log "aidog_wd: already running"
  else
    tmux new -d -s aidog_wd "$PROXY_ENV bash $REPO/deploy/watchdog.sh"
    log "aidog_wd: started"
  fi
}

start_web() {
  if session_exists aidog_web || web_up; then
    log "aidog_web: already running"
  else
    tmux new -d -s aidog_web "cd $REPO/web && $PROXY_ENV python3 server.py > $WEB_LOG 2>&1"
    log "aidog_web: started"
  fi
}

start_svc() {
  # P5-2: readiness lives in deploy/run_service.sh --check (single source of
  # provisioning logic; credentials never touch argv).
  if session_exists aidog_svc; then
    log "aidog_svc: already running"
  elif bash "$REPO/deploy/run_service.sh" --check; then
    mkdir -p "$REPO/data/mycore_service"
    tmux new -d -s aidog_svc "cd $REPO && $PROXY_ENV bash deploy/run_service.sh > data/mycore_service/boot.log 2>&1"
    log "aidog_svc: started"
  else
    log "aidog_svc: skipped (fail-closed: allowlist/token not provisioned — awaiting dedicated bot)"
  fi
}

start_mwd() {
  if session_exists aidog_mwd || [ -n "$(our_pgrep 'python3 -m mycore\.watchdog')" ]; then
    log "aidog_mwd: already running"
  else
    mkdir -p "$REPO/data"
    tmux new -d -s aidog_mwd "cd $REPO && $PROXY_ENV python3 -m mycore.watchdog --config deploy/watchdog.mycore.local --execute > data/mycore_watchdog.log 2>&1"
    log "aidog_mwd: started (--execute per owner authorization)"
  fi
}

status() {
  local rc=0 s
  for s in aidog_gw aidog_wd aidog_web aidog_mwd; do
    if [ "$PHASE" = post ] && { [ "$s" = aidog_gw ] || [ "$s" = aidog_wd ]; }; then
      # P5-5: gw/wd sessions must be GONE post-swap — lingering = leftover.
      if session_exists "$s"; then
        echo "⚠️ tmux $s: leftover (retired at P5-5 — kill manually)"; rc=1
      else
        echo "✅ tmux $s: retired (absent)"
      fi
    elif session_exists "$s"; then echo "✅ tmux $s: up"; else echo "❌ tmux $s: DOWN"; rc=1; fi
  done
  # The chat service is expected-absent until provisioning exists: pending is
  # not a failure; provisioned-but-down is.
  if session_exists aidog_svc; then
    echo "✅ tmux aidog_svc: up"
  elif bash "$REPO/deploy/run_service.sh" --check; then
    echo "❌ tmux aidog_svc: provisioned but DOWN"; rc=1
  else
    echo "⏳ tmux aidog_svc: pending (fail-closed: awaiting dedicated bot)"
  fi
  # Gateway liveness only exists in the transition phase (no GW_PATTERN post).
  if [ "$PHASE" = transition ]; then
    if [ -n "$GW_PATTERN" ] && [ -n "$(our_pgrep "$GW_PATTERN")" ]; then echo "✅ gateway process: up"; else echo "❌ gateway process: DOWN"; rc=1; fi
  fi
  if web_up; then echo "✅ web console :$WEB_PORT: up"; else echo "❌ web console :$WEB_PORT: DOWN"; rc=1; fi
  return $rc
}

case "${1:-start}" in
  start)
    log "boot_services: ensuring AiDog runtime sessions"
    inject_proxy
    start_gw; start_wd; start_web; start_svc; start_mwd
    # Give slow starts (gateway Telegram connect) a moment, then self-verify.
    sleep 6
    if status; then
      log "boot_services: all green"
    else
      log "boot_services: FAIL - some services did not come up"
      exit 1
    fi
    ;;
  status) status ;;
  stop)
    for s in aidog_mwd aidog_svc aidog_web aidog_wd aidog_gw; do
      session_exists "$s" && { tmux kill-session -t "$s"; log "$s: stopped"; }
    done
    # watchdog/gateway may linger outside tmux; only kill our own patterns
    pkill -f 'bash .*imaidog/deploy/watchdog.sh' 2>/dev/null && log "watchdog process: stopped"
    # DANGER: GW_PATTERN is empty in the post-swap phase and `pkill -f ''`
    # would match (and kill) EVERYTHING incl. this shell — guard on non-empty.
    if [ -n "$GW_PATTERN" ]; then
      pkill -f "$GW_PATTERN" 2>/dev/null && log "gateway process: stopped"
    fi
    pkill -f 'python3 -m mycore.service' 2>/dev/null && log "mycore service: stopped"
    pkill -f 'python3 -m mycore.watchdog' 2>/dev/null && log "mycore watchdog: stopped"
    ;;
  *) echo "usage: $0 [start|status|stop]"; exit 2 ;;
esac
