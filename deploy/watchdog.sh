#!/usr/bin/env bash
# RETIRED at P5-5 mycore swap (2026-10-19): kept for history; boot post-phase
# no longer starts this (gateway supervision -> mycore watchdog, aidog_mwd).
# Watchdog for the aidog gateway (P1, transition stack).
# - 60s liveness loop; crash -> auto-restart via tmux + owner TG alert
# - persistent-down retry (5 min throttle): a boot-window failure must never
#   go silent (blind spot observed 2026-10-17: gateway down 17:24->17:33,
#   first bring-up failed, loop then stayed quiet)
# - liveness excludes the tmux server pid (its argv quotes our start command
#   and would self-match the pattern)
# - recovery + daily 09:00-09:05 Beijing heartbeat notifications
# - token is read inside the notifier from the profile .env (never echoed)
# - upstream kernel names/paths come from deploy/upstream_shim.env (sole file)
# English comments per repo i18n policy.
set -u

REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)  # self-deriving: no machine path
# Graceful retirement: post-swap there is no gateway to supervise — without
# the upstream shim there is nothing to source (previously exit 1). Exit 0
# with an explanation so an accidental manual run cannot alarm operators.
# Transition behavior (shim present) is unchanged.
if [ ! -f "$REPO/deploy/upstream_shim.env" ]; then
  echo "watchdog: RETIRED at P5-5 mycore swap (2026-10-19) — gateway supervision moved to the mycore watchdog (aidog_mwd); nothing to supervise, exiting cleanly."
  exit 0
fi
# shellcheck source=upstream_shim.env
source "$REPO/deploy/upstream_shim.env" || { echo "watchdog: missing upstream shim" >&2; exit 1; }

PATTERN=$AIDOG_KERNEL_CMD
ENVF=$AIDOG_PROFILE_DIR/.env
STATE=$AIDOG_PROFILE_DIR/logs/watchdog_state
LOG=$REPO/deploy/watchdog.log
GWLOG=$AIDOG_PROFILE_DIR/logs/gateway_run.log
# Alert chat id is machine-local: deploy/watchdog.local (gitignored, see
# watchdog.local.example). Without it notify() degrades to log-only.
W_CHAT_ID=""
[ -f "$REPO/deploy/watchdog.local" ] && . "$REPO/deploy/watchdog.local"
CHAT_ID="${W_CHAT_ID:-}"
export W_ENV="$ENVF" W_CHAT="$CHAT_ID" W_LOG="$LOG"

# Egress via local proxy (owner-approved, 2026-10-17): direct Telegram API is
# unreachable from this host after a reboot. notify() inherits the exports;
# PROXY_ENV prefixes every `tmux new` command, because tmux children inherit
# the SERVER environment rather than this shell's.
PROXY_URL=${HTTPS_PROXY:-${https_proxy:-http://127.0.0.1:12334}}
NO_PROXY_VAL=${NO_PROXY:-${no_proxy:-localhost,127.0.0.1,::1}}
export HTTP_PROXY="$PROXY_URL" HTTPS_PROXY="$PROXY_URL" \
       http_proxy="$PROXY_URL" https_proxy="$PROXY_URL" \
       NO_PROXY="$NO_PROXY_VAL" no_proxy="$NO_PROXY_VAL"
PROXY_ENV="env HTTP_PROXY=$PROXY_URL HTTPS_PROXY=$PROXY_URL http_proxy=$PROXY_URL https_proxy=$PROXY_URL NO_PROXY=$NO_PROXY_VAL no_proxy=$NO_PROXY_VAL"

srv_pid() { tmux display-message -p '#{pid}' 2>/dev/null; }

gw_alive() {  # gateway process alive, excluding the tmux SERVER pid whose argv
  local p srv  # quotes our own start command and would false-match PATTERN
  srv=$(srv_pid)
  for p in $(pgrep -f "$PATTERN" 2>/dev/null); do
    [ "$p" = "$srv" ] && continue
    return 0
  done
  return 1
}

notify() {  # $1 = text
  if [ -z "${W_CHAT:-}" ]; then
    printf 'notify SKIP (no chat id — configure deploy/watchdog.local)\n' >>"$LOG"
    return 0
  fi
  python3 - "$1" >>"$LOG" 2>&1 <<'PY'
import os, sys, re, urllib.request, urllib.parse, pathlib
tok = re.search(r'(?m)^TELEGRAM_BOT_TOKEN=(\S+)$', pathlib.Path(os.environ['W_ENV']).read_text()).group(1)
data = urllib.parse.urlencode({'chat_id': os.environ['W_CHAT'], 'text': sys.argv[1]}).encode()
req = urllib.request.Request(f'https://api.telegram.org/bot{tok}/sendMessage', data=data)
try:
    r = urllib.request.urlopen(req, timeout=20)
    print('notify:', r.status)
except Exception as e:
    print('notify FAILED:', type(e).__name__, str(e)[:120])
PY
}

restart_gw() {
  tmux kill-session -t aidog_gw 2>/dev/null || true
  tmux new -d -s aidog_gw "$PROXY_ENV $AIDOG_KERNEL_CMD --force > $GWLOG 2>&1"
}

notify "🛰️ [watchdog] watchdog on duty · 60s patrol · daily heartbeat 09:00 (Beijing)"
last_hb_day=""
prev="unknown"
last_retry=0

while true; do
  if gw_alive; then cur=up; else cur=down; fi

  if [ "$cur" = down ]; then
    if [ "$prev" = up ]; then
      notify "🔴 [watchdog] gateway down ($(date '+%H:%M:%S')) — restarting…"
      restart_gw
      sleep 8
      if gw_alive; then
        notify "🟢 [watchdog] back up (tmux aidog_gw)"
        cur=up
      else
        notify "⚠️ [watchdog] restart FAILED — manual intervention needed: tail $GWLOG"
        last_retry=$(date +%s)
      fi
    elif [ "$prev" = unknown ]; then
      restart_gw   # silent bring-up at watchdog start
      sleep 8
      if gw_alive; then cur=up; else last_retry=$(date +%s); fi
    else
      # Persistent down: retry on a 5-minute throttle and alert every time —
      # never stay silent (boot blind spot observed 2026-10-17).
      now=$(date +%s)
      if [ $((now - last_retry)) -ge 300 ]; then
        notify "⚠️ [watchdog] gateway still offline — restarting again…"
        restart_gw
        sleep 8
        if gw_alive; then
          notify "🟢 [watchdog] back up (tmux aidog_gw)"
          cur=up
        fi
        last_retry=$(date +%s)
      fi
    fi
  elif [ "$cur" = up ] && [ "$prev" = down ]; then
    notify "🟢 [watchdog] gateway back online"
  fi

  hhmm=$(date +%H%M); day=$(date +%F)
  if [ "$hhmm" -ge 0900 ] && [ "$hhmm" -lt 0905 ] && [ "$last_hb_day" != "$day" ]; then
    notify "🟢 [HEARTBEAT] aidog embodied system · $day $(date '+%H:%M') · gateway=$cur"
    last_hb_day="$day"
  fi

  echo "$cur" > "$STATE" 2>/dev/null || true
  prev="$cur"
  sleep 60
done
