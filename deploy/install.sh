#!/usr/bin/env bash
# install.sh — one-command fresh install for I'm AiDog (idempotent; safe to re-run).
#
# What it does, in order:
#   1. preflight  — python3 (>=3.10) and tmux present, web port not hijacked
#   2. provision  — create machine-local files from sanitized templates,
#                   ONLY when missing (never overwrites existing values)
#   3. cli        — put `aidog` on PATH (deploy/install_cli.sh, idempotent)
#   4. boot       — start console + mycore service + watchdog
#                   (the chat service is fail-closed by design until the
#                    bot token + allowlist slots are provisioned — that is
#                    reported as "pending", not as a failure)
#   5. verify     — console health, run_service readiness, model endpoint,
#                   empty credential slot NAMES (values are never printed),
#                   doctor summary (bare exit code — no pipes)
#
# Exit codes:
#   0 = fully ready (doctor clean, service provisioned, console healthy)
#   2 = installed, but credentials and/or model backend still pending
#       (fail-closed by design — see the report below)
#   1 = hard failure (missing prerequisite, port conflict, boot failure)
#
# Usage:
#   bash deploy/install.sh     # from a clone of the product repository
#   aidog setup                # interactive wizard (fill slots, then boot +
#                              # verify); --check status, --yes = this script
#
# English output per repo i18n policy (all docs/source are English).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO" || exit 1

WEB_PORT="${AIDOG_WEB_PORT:-8790}"
PENDING=0

say()  { printf '[install] %s\n' "$*"; }
step() { printf '\n[install] == %s\n' "$*"; }
note() { printf '[install]   %s\n' "$*"; }

# ------------------------------------------------------------- preflight ---
step "preflight"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[install] FAIL: python3 not found (install Python 3.10+ first)" >&2; exit 1
fi
python3 - <<'PY' || { echo "[install] FAIL: python3 >= 3.10 required" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)
PY
note "python3 $(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
if ! command -v tmux >/dev/null 2>&1; then
  echo "[install] FAIL: tmux not found (e.g. 'sudo apt-get install tmux')" >&2; exit 1
fi
command -v git >/dev/null 2>&1 || note "WARN: git not found — version updates (aidog update) need it"
if [ ! -d "$REPO/.git" ]; then
  note "WARN: not a git checkout — updates unavailable (fine for archives)"
fi

# Port 8790 must be free, our own console, or a hard conflict.
code="$(curl -s -o /dev/null -m 2 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/health" 2>/dev/null || true)"
if [ "$code" = "200" ]; then
  note "web console :$WEB_PORT already healthy (reusing it)"
elif [ -n "$code" ] && [ "$code" != "000" ]; then
  echo "[install] FAIL: port $WEB_PORT is used by another service (HTTP $code)" >&2
  exit 1
fi

# ----------------------------------------------------------- provision ----
step "machine-local files (created only when missing)"
# Copies a template to a machine-local (gitignored) path if absent.
provision() {
  local dst="$1" src="$2"
  if [ -e "$dst" ]; then
    note "kept existing: $dst"
  elif [ ! -f "$src" ]; then
    note "FAIL: template missing: $src" >&2; PENDING=1
  else
    cp "$src" "$dst" && note "created: $dst"
  fi
}
provision secrets.env                secrets.env.example
provision deploy/watchdog.local      deploy/watchdog.local.example
provision deploy/watchdog.mycore.local deploy/watchdog.mycore.example.json
provision web/settings.json          web/settings.example.json
# deploy/memory_sources.local is deliberately NOT created: its template ships
# placeholder paths (/path/to/...) which would break the memory chain — the
# chain falls back to its documented defaults when the file is absent.
if [ -f secrets.env ]; then
  chmod 600 secrets.env 2>/dev/null || true   # 9p/CIFS mounts reject chmod (harmless there)
fi
# credentials whose value a human must supply (names only — never values).
# Presence rule mirrors doctor's env_line_value: the value must be the only
# content on its own `KEY=value` line (inline comments are NOT a value).
#   required   — the product refuses full operation without them (doctor's
#                mycore.secrets.slots + run_service --check)
#   recommended — features degrade without them (never block READY):
#                 MODEL_API_KEY only matters when the model endpoint needs a
#                 key; AIDOG_GH_PAT powers `aidog update` on the private repo
SLOTS_REQUIRED="WEB_TOKEN MYCORE_TG_TOKEN AIDOG_TG_ALLOWED_USERS"
SLOTS_RECOMMENDED="MODEL_API_KEY AIDOG_GH_PAT"
slot_empty() { ! grep -Eq "^$1=[^[:space:]]+$" secrets.env 2>/dev/null; }
empty_required="" empty_recommended=""
for k in $SLOTS_REQUIRED; do
  slot_empty "$k" && empty_required="$empty_required $k"
done
for k in $SLOTS_RECOMMENDED; do
  slot_empty "$k" && empty_recommended="$empty_recommended $k"
done
if [ -n "$empty_required" ]; then
  note "secrets.env REQUIRED slots still empty:$empty_required"
  note "  -> edit secrets.env (values live only there; never paste them anywhere else)"
  PENDING=1
else
  note "secrets.env: required slots filled (values not shown)"
fi
if [ -n "$empty_recommended" ]; then
  note "recommended slots empty (fine unless you need them):$empty_recommended"
fi

# ----------------------------------------------------------------- cli ----
step "CLI"
if bash deploy/install_cli.sh; then
  note "aidog command installed ($(command -v aidog 2>/dev/null || echo 'path refreshed — open a new shell'))"
else
  echo "[install] FAIL: CLI install failed" >&2; exit 1
fi

# ----------------------------------------------------------------- boot ----
step "boot runtime (console + chat service + watchdog)"
if ! bash deploy/boot_services.sh start; then
  echo "[install] FAIL: boot_services start failed — see its output above" >&2
  exit 1
fi

# --------------------------------------------------------------- verify ----
step "verify"
# 1) web console
if [ "$(curl -s -o /dev/null -m 3 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT/api/health" || true)" = "200" ]; then
  note "web console http://127.0.0.1:$WEB_PORT/api/health -> ok"
else
  note "FAIL: web console not healthy"; PENDING=1
fi
# 2) chat service readiness (rc: 0 ready / 3 no allowlist / 4 no token / 2 wiring)
bash deploy/run_service.sh --check; rc=$?
case "$rc" in
  0) note "chat service: ready (allowlist + token provisioned)";;
  3) note "chat service: PENDING — AIDOG_TG_ALLOWED_USERS not set (fail-closed)"; PENDING=1;;
  4) note "chat service: PENDING — MYCORE_TG_TOKEN not set (fail-closed)"; PENDING=1;;
  *) note "chat service: PENDING — wiring error (rc=$rc)"; PENDING=1;;
esac
# 3) model endpoint (same source of truth as doctor: web/settings.json mycore.base_url)
model_base="$(python3 - <<'PY'
import json
try:
    mc = json.load(open("web/settings.json")).get("mycore") or {}
    print(mc.get("base_url") or "http://127.0.0.1:3460/v1")
except Exception:
    print("http://127.0.0.1:3460/v1")
PY
)"
mcode="$(curl -s -o /dev/null -m 3 -w '%{http_code}' "$model_base/models" 2>/dev/null || true)"
if [ "$mcode" = "200" ]; then
  note "model endpoint $model_base -> ok"
elif [ -n "$mcode" ] && [ "$mcode" != "000" ]; then
  note "model endpoint $model_base -> HTTP $mcode (check MODEL_API_KEY / backend config)"
  PENDING=1
else
  note "model endpoint $model_base unreachable — point web/settings.json mycore.base_url"
  note "  at any OpenAI-compatible endpoint you have (see docs/installation.md)"
  PENDING=1
fi
# 4) doctor — run bare, capture the rc directly (never through a pipe).
# The watchdog persists supervision state at the end of its first cycle;
# wait briefly so a fresh install's doctor run does not race that write.
for _i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  [ -f deploy/watchdog.mycore.local.state.json ] && break
  sleep 1
done
mkdir -p data
python3 deploy/aidog_doctor.py > data/install_doctor.log 2>&1; doctor_rc=$?
note "doctor exit=$doctor_rc — full report: data/install_doctor.log"
tail -n 1 data/install_doctor.log 2>/dev/null | sed 's/^/[install]   /'

# ----------------------------------------------------------------- done ----
step "result"
if [ "$doctor_rc" -eq 0 ] && [ "$PENDING" -eq 0 ]; then
  say "READY — everything provisioned and green."
  say "chat with your bot via Telegram (see docs/installation.md), console: http://127.0.0.1:$WEB_PORT"
  exit 0
else
  say "INSTALLED — pending items above (fail-closed by design, nothing broken)."
  say "fill the listed slots in secrets.env / settings, then re-run:  aidog setup"
  exit 2
fi
