#!/usr/bin/env python3
"""I'm AiDog web console — zero-dependency (stdlib only) status/settings server.

Concept: MCU-style status surfaces (stance sensing, protocols, live panels).
Style tokens: extracted from aidog.im (owner directive: style reference only).
Security (P3):
  - binds loopback only;
  - write routes require `Authorization: Bearer <WEB_TOKEN>` (slot in secrets.env,
    value never echoed, never logged, never serialized);
  - settings pass a strict allow-list schema before they are persisted;
  - status probes are presence/boolean only; secrets never serialize.
Routes:
  GET  /                        -> static/index.html
  GET  /static/<file>           -> assets
  GET  /api/health              -> liveness + version
  GET  /api/status              -> aggregated status panels + advisory level
  GET  /api/config              -> sanitized config view (no credential values)
  GET  /api/settings            -> console settings document (no secrets)
  POST /api/settings            -> Bearer WEB_TOKEN; validated merge; idempotent
  GET  /api/usage               -> model-channel usage/cost aggregation (cached)
  GET  /api/events              -> sanitized recent event stream
  GET  /api/evolution           -> evolution loop status (schedule/state/log tail)
  POST /api/learn               -> Bearer; lint+pre-screen, writes a skill or
                                   memory artifact (evolution loop console leg)
  POST /api/evolution           -> Bearer; {action:"reflect", period} generates
                                   the period skeleton (same core as /reflect)
  POST /api/chat                -> Bearer; {text} streams an agent turn as SSE
                                   (meta/delta/tool/done/error); {action:"clear"}
                                   archives the console session
  GET  /api/channels            -> channel adapter status (list of {name,
                                   enabled, configured, ...}; empty list +
                                   "note" when the channel module is absent)
  POST /api/channels/webhook    -> Bearer CHANNEL_WEBHOOK_SECRET; {text,
                                   sender?} -> one in-process turn ->
                                   {ok, reply} (no SSE, own session)
  POST /api/channels/send       -> Bearer WEB_TOKEN; {route, target, text}
                                   -> {ok} (200) | {error, details} (400) |
                                   {ok:false, error} (409/502/503)
  GET  /v1/models               -> OpenAI-style model list (Bearer gate on the
                                   dedicated OPENAI_API_TOKEN slot)
  POST /v1/chat/completions     -> OpenAI-style completion over the same agent
                                   loop as /api/chat (stream + non-stream,
                                   stateless, no client-side tool execution)
  GET  /api/capabilities        -> read-only capability catalog (tools, skills,
                                   channels, hooks, cron, browser, terminal,
                                   local models; no auth, no secrets) + the
                                   `exchange` block (W1: venue rows + tools)
  GET  /api/exchange/quote      -> READ-ONLY public market probe: venue +
                                   optional symbol -> {venue, symbol, lines};
                                   own 10/60s sliding window -> 429
  GET  /api/exchange/plans      -> stored W2 order plans (read, no auth —
                                   same contract as every GET; fail-soft 503)
  POST /api/exchange/plans/<id>  -> Bearer WEB_TOKEN; {action: approve|reject|
                                   cancel} -> {plan} | 400 invalid action |
                                   404 unknown plan id (the ONLY approval
                                   surface: the model has no such route)
  GET  /api/chat/history        -> current console session (read, loopback)
  GET  /api/voice               -> voice engine capability (edge-tts presence)
  POST /api/say                 -> synthesize speech -> audio/mpeg (cached,
                                   rate-limited, sanitized, JSON-only origin-guarded;
                                   optional {engine: edge|byok}, default = the
                                   settings media.tts_engine choice)
  POST /api/stt                 -> local speech-to-text: audio/* body on the
                                   independent STT_MAX_BODY budget, same loopback
                                   origin guard + own sliding rate window ->
                                   {text, language, duration} (honest 503/422)
  POST /api/image               -> Bearer WEB_TOKEN; {prompt, size?} ->
                                   {images: [{name, url}]} via imagegen (honest 503)
  GET  /api/media/<name>        -> one generated image from data/images/ (strict
                                   name whitelist, no traversal)
  GET  /static/i18n/<lang>.json -> locale packs served from the SSOT
                                   overlay/locales/ (en default, zh switchable)
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
REPO = WEB_DIR.parent
STATIC = WEB_DIR / "static"

# Locale packs: single source of truth = overlay/locales/*.json, discovered by
# the stdlib-only loader. The repo root goes to the FRONT of sys.path so this
# vendored copy always wins.
if str(REPO) in sys.path:
    sys.path.remove(str(REPO))
sys.path.insert(0, str(REPO))
from lib import i18n  # noqa: E402
from lib import profile_paths  # noqa: E402  (car F §13.1: per-profile path seams)
from mycore import cli, evolution, hooks, mcp, modeltier, terminal  # noqa: E402  (config + MCP/ hooks/ terminal grammar)
from mycore.assembly import build_system_stack, resolve_context_dirs  # noqa: E402
from mycore.loop import run_turn  # noqa: E402
from mycore.model import ChatClient  # noqa: E402
from mycore.service import state_dir as mycore_state_dir  # noqa: E402
from mycore.session import Session  # noqa: E402
from mycore.tools import (KNOWN_TOOL_NAMES, build_registry,  # noqa: E402
                          resolve_disabled_tools)
HOST, PORT = "127.0.0.1", int(os.environ.get("AIDOG_WEB_PORT", "8790"))
try:
    VERSION = (REPO / "VERSION").read_text(encoding="utf-8").strip()  # single source of truth
except OSError:
    VERSION = "0.0.0"
# Car F (§13.1): per-profile path seams — AIDOG_PROFILE unset = these resolve
# to the exact pre-car-F paths (byte-identical legacy behavior). Every read
# point below (load_settings, POST /api/settings, _slot_value, voice/image
# credentials, media listing) follows through these names.
SECRETS = profile_paths.secrets_path(REPO)
SETTINGS_FILE = profile_paths.settings_path(REPO)
STATE_FILE = WEB_DIR / ".state.json"  # runtime state (gitignored): boot history
MAX_BODY = 16384
# Car 4c media face: binary payloads carry their OWN budgets — a voice note or
# a generated image is not JSON, and the transcriber enforces its own limits.
STT_MAX_BODY = 2_000_000        # raw audio bytes per /api/stt request
STT_MAX_DURATION = 60           # decoded seconds accepted (transcriber cap)
IMAGE_MAX_PROMPT = 2000         # chars in one image trial prompt
IMAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")   # served-file whitelist
MEDIA_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}
MEDIA_DIR = profile_paths.media_dir(REPO)  # imagegen's output dir (read-only here)
CHAT_MAX_CHARS = 4000                 # one console chat message (input cap)
CHAT_HISTORY_CAP = 2000               # per-message chars returned by /history
CHAT_SESSION_NAME = "web"             # console session file stem (numeric
                                      # chat_ids belong to the TG service)
_state_lock = threading.Lock()


def _load_shim() -> dict:
    """Parse deploy/upstream_shim.env — the single file that may name the
    transitional upstream kernel; this console keeps zero upstream names of
    its own (deleted wholesale at the mycore swap)."""
    out: dict = {}
    try:
        text = (REPO / "deploy/upstream_shim.env").read_text()
    except OSError:
        return out
    for line in text.splitlines():
        m = re.match(r'^\s*export\s+([A-Z0-9_]+)="([^"]*)"\s*$', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


SHIM = _load_shim()
PROFILE = Path(SHIM.get("AIDOG_PROFILE_DIR") or "/nonexistent")
KERNEL_CMD = SHIM.get("AIDOG_KERNEL_CMD") or ""
KERNEL_VERSION_CMD = SHIM.get("AIDOG_KERNEL_VERSION_CMD") or ""
_BRAND = SHIM.get("AIDOG_UPSTREAM_BRAND") or ""
_BRAND_RE = re.compile(rf"(?i)\b{re.escape(_BRAND)}\b") if _BRAND else None

# ------------------------------------------------------ P5-3 phase resolver --
# Phase-aware observability: the transition wiring (this shim file + the
# profile dir it points at) is the live brain until the mycore swap (P5-5)
# deletes it in the same car. SHIM_PRESENT is the single resolver; every
# shim/profile-dependent read below consults it and falls back to honest
# mycore-only facts when the wiring is gone (never an exception).
SHIM_PRESENT = (REPO / "deploy/upstream_shim.env").exists()

# mycore runtime probes (read-only, repo-relative): the chat service and its
# watchdog live beside the transition stack today and are the sole source
# after the swap. No restarts, no credentials, no machine paths in output.
MYCORE_STATE_DIR = profile_paths.state_dir(REPO)   # per-profile state root (env AIDOG_MC_STATE wins)
MYCORE_PIDFILE = MYCORE_STATE_DIR / "mycore_service.pid"
MYCORE_SERVICE_LOG = MYCORE_STATE_DIR / "service.log"
MYCORE_WD_LOG = REPO / "data" / "mycore_watchdog.log"
MYCORE_WD_STATE = REPO / "deploy" / "watchdog.mycore.local.state.json"
MYCORE_WD_PROC = r"python3 -m mycore\.watchdog"
MYCORE_WD_FRESH_SEC = 300          # state file must be younger than 5 min
SERVICE_LINE_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+([A-Za-z]+)\b")
SERVICE_EVENT_LEVEL = {"ABORT": "urgent", "ERROR": "urgent", "CRITICAL": "urgent",
                       "WARN": "warn", "WARNING": "warn"}

# Console defaults are deployment-neutral on purpose: personal data-source paths
# live in web/settings.json (gitignored) and are edited through POST /api/settings.
DEFAULT_SETTINGS: dict = {
    "language": "en",
    "refresh_sec": 10,
    "sound": False,
    "voice_volume": 80,
    "alert_preview": "off",
    # Display currency (owner 2026-10-18): default $ /USD; costs are computed
    # in source CNY and converted with auto FX (free daily endpoint) or a
    # manual rate (`fx_rate` = CNY per 1 display unit; 0 = auto).
    "currency": "USD",
    "fx_rate": 0.0,
    # mycore kernel model (owner 2026-10-18): test default = local gateway
    # group E + mimo-v2.6-flash; user-configurable via CONSOLE (P4-1b),
    # env MYCORE_BASE_URL / MYCORE_MODEL still override at call time.
    "mycore": {"base_url": "http://127.0.0.1:3460/v1",
               "model": "mimo-v2.6-flash"},
    "usage": {"metrics_url": "http://127.0.0.1:3456/metrics",
              "pricing_config": "",
              "log_dir": ""},
    # Inbound/outbound channel adapters (car C): non-secret config only —
    # credentials live in secrets.env slots (CHANNEL_*), never here.
    "channels": {
        "discord": {"enabled": False, "allow": [], "channel_ids": []},
        "slack": {"enabled": False, "allow": [], "channel_ids": []},
        "email": {"enabled": False, "allow": [], "to": "", "from": "",
                  "smtp_host": "", "smtp_port": 587, "smtp_ssl": False,
                  "imap_host": "", "imap_port": 993},
        "webhook": {"enabled": False, "url": ""},
        "dingtalk": {"enabled": False},
        "feishu": {"enabled": False, "allow": [], "chat_ids": []},
        "wechat": {"enabled": False},
        "whatsapp": {"enabled": False, "phone_number_id": ""},
        "signal": {"enabled": False, "url": "", "allow": []},
    },
    # Media face (car 4c): console voice/image configuration — non-secret only
    # (credential slots TTS_API_KEY / IMAGE_API_KEY live in secrets.env and
    # never serialize). Wake phrases are user data with a bilingual default;
    # the zh phrase is \u-escaped so this source file stays ASCII per the
    # source-language lint (locale packs are the only CJK-bearing files).
    "media": {"image_base_url": "", "image_model": "", "tts_base_url": "", "tts_model": "",
              "stt_model": "base", "tts_engine": "edge",
              "wake_phrases": ["hey aidog", "\u4f60\u597d\u827e\u591a"]},
    # W2 Web3 auto-approval caps (owner decree 2026-10-25): FAIL-CLOSED
    # zeros — 0 means nothing approves itself, so every plan waits for the
    # owner in the console. Non-secret numbers; mycore.exchanges.thresholds
    # re-validates them on every read and collapses anything unusable to 0.
    "web3": {"auto_usd_per_tx": 0.0, "auto_usd_daily": 0.0},
    # Evolution loop schedule (frontend coverage 1.5): a copy of
    # mycore.evolution.DEFAULT_SETTINGS — that constant stays the source of
    # truth (copied, never invented) and this partition is always present in
    # GET /api/settings so the console never has to treat absence as default.
    "evolution": dict(evolution.DEFAULT_SETTINGS),
    # Event hooks partition (car 4a): {} = no hook configured. Present by
    # default for the same reason — the editor renders an honest empty set.
    "hooks": {},
}
ALLOWED_TOP = ("language", "refresh_sec", "sound", "voice_volume",
               "alert_preview", "currency", "fx_rate", "mycore", "usage",
               "evolution", "channels", "hooks", "media", "web3")
USAGE_KEYS = ("metrics_url", "pricing_config", "log_dir")
MYCORE_KEYS = ("base_url", "model", "context_dirs", "upstreams", "mcp", "shell",
               "tools_off")
MYCORE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
# Per-tool kill switch (v0.5.32): each mycore.tools_off entry is one tool
# name — lowercase snake, bounded at 40 chars (mirrors the name grammar the
# registry itself uses). The KNOWN_TOOL_NAMES membership check lives in the
# branch below (import is fail-closed: an unavailable catalog refuses writes).
TOOLS_OFF_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
TOOLS_OFF_MAX = 32
# Supported UI languages = the discovered locale packs (overlay/locales/*);
# fallback keeps validation sane if the packs are ever unreadable.
LANGS = tuple(i18n.languages()) or ("en", "zh")
ALERT_PREVIEWS = ("off", "warn", "urgent")
CURRENCY_SYM = {"USD": "$", "CNY": "¥", "EUR": "€", "GBP": "£", "JPY": "¥"}
CURRENCIES = tuple(CURRENCY_SYM)
# Channel adapter config partition (car C): per-channel allow-lists and the
# field schema validated by validate_settings_patch (all-or-nothing).
CHANNEL_KEYS = ("discord", "slack", "email", "webhook",
                "dingtalk", "feishu", "wechat", "whatsapp", "signal")
_CHANNEL_SCHEMA = {
    "discord": ("enabled", "allow", "channel_ids"),
    "slack": ("enabled", "allow", "channel_ids"),
    "email": ("enabled", "allow", "to", "from", "smtp_host", "smtp_port",
              "smtp_ssl", "imap_host", "imap_port"),
    "webhook": ("enabled", "url"),
    "dingtalk": ("enabled",),
    "feishu": ("enabled", "allow", "chat_ids"),
    "wechat": ("enabled",),
    "whatsapp": ("enabled", "phone_number_id"),
    # signal carries `allow`: its inbound leg is real (daemon polling), and
    # the service gate is fail-closed on channels.signal.allow (empty/absent
    # = deny every sender), so the list must be writable here.
    "signal": ("enabled", "url", "allow"),
}
# Media partition (car 4c): field schema validated like the other blocks —
# URLs must be http(s)+host (empty = off), models/engines are closed enums,
# wake phrases are a short list of single-line strings.
MEDIA_KEYS = ("image_base_url", "image_model", "tts_base_url", "tts_model",
              "stt_model", "tts_engine", "wake_phrases")
# W2 Web3 partition: the two auto-approval caps, all-or-nothing like every
# other block (both keys or neither), numbers only, bounded by the same
# THRESHOLD_MAX_USD mycore.exchanges applies at read time.
WEB3_KEYS = ("auto_usd_per_tx", "auto_usd_daily")
WEB3_CAP_USD = 1000000
STT_MODELS = ("tiny", "base", "small")
TTS_ENGINES = ("edge", "byok")
WAKE_PHRASES_MAX = 3
WAKE_PHRASE_MAX_LEN = 64

# ------------------------------------------- credential-slot catalog (1.1) ---
# Console-editable secrets.env slots: fixed allowlist, grouped for the
# settings credential editor. Anything not listed here (owner infra slots
# AIDOG_CF_* / AIDOG_R2_*, legacy dead slots) is NOT console-writable and is
# never listed — deny by default. WEB_TOKEN is presence-only: the gate must
# not rewrite itself (rotate it by hand), so it is listed but not writable.
SECRET_GROUPS: dict = {
    "core": ("MODEL_API_KEY", "MYCORE_TG_TOKEN", "AIDOG_TG_ALLOWED_USERS",
             "AIDOG_GH_PAT", "MYCORE_WD_CHAT"),
    "gateway": ("OPENAI_API_TOKEN",),
    "web3": ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE",
             "KRAKEN_API_KEY", "KRAKEN_API_SECRET",
             "BINANCE_API_KEY", "BINANCE_API_SECRET"),
    "channels": ("CHANNEL_WEBHOOK_SECRET", "CHANNEL_DISCORD_TOKEN",
                 "CHANNEL_SLACK_TOKEN", "CHANNEL_SMTP_USER", "CHANNEL_SMTP_PASS",
                 "CHANNEL_IMAP_USER", "CHANNEL_IMAP_PASS",
                 "CHANNEL_DINGTALK_WEBHOOK", "CHANNEL_DINGTALK_SECRET",
                 "CHANNEL_FEISHU_APP_ID", "CHANNEL_FEISHU_APP_SECRET",
                 "CHANNEL_WECHAT_APPID", "CHANNEL_WECHAT_APP_SECRET",
                 "CHANNEL_WHATSAPP_TOKEN", "CHANNEL_SIGNAL_TOKEN"),
    "media": ("TTS_API_KEY", "IMAGE_API_KEY"),
    "connectors": ("LINEAR_API_KEY", "NOTION_API_KEY"),
}
SECRET_SLOTS: dict = {name: group for group, names in SECRET_GROUPS.items()
                      for name in names}          # 32 editable slots
SECRET_PRESENCE_ONLY = ("WEB_TOKEN",)             # listed, never writable
SECRET_VALUE_MAX = 4096                           # chars, one line
# Env-var NAMES the operator may export into the console process; only the
# names ever serialize into /api/capabilities (the value may be a URL with
# embedded credentials or a model list).
ENV_OVERRIDE_VARS = ("MYCORE_BASE_URL", "MYCORE_MODEL", "MYCORE_UPSTREAMS",
                     "MYCORE_MCP", "MYCORE_TOOLS_OFF", "AIDOG_CONTEXT_DIRS",
                     "AIDOG_IMAGE_URL", "AIDOG_TTS_URL")

# ---------------------------------------------------------------- helpers ----


def proc_alive(pattern: str) -> bool:
    if not pattern:
        return False
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_status(url: str, timeout: float = 2.5) -> int | None:
    for attempt in (0, 1):  # first call in a fresh process can flake (observed P2)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.status
        except Exception:  # noqa: BLE001
            if attempt == 0:
                time.sleep(0.4)
                continue
            return None
    return None


def fetch_text(url: str, timeout: float = 2.5) -> str | None:
    """GET a loopback text resource with one retry (same flake policy as http_status)."""
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            if attempt == 0:
                time.sleep(0.4)
                continue
            return None
    return None


def read_env_slot(name: str) -> str | None:
    """Own-line read of one slot from secrets.env — the value is never logged."""
    return _slot_value(SECRETS, name)


def _slot_value(path: Path, key: str) -> str | None:
    """Own-line slot read from any env-style file (glue-pit safe). The value
    stays inside the caller's presence check — it never reaches any output."""
    try:
        text = path.read_text()
    except OSError:
        return None
    m = re.search(rf"(?m)^{re.escape(key)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else None


def _read_text(path: Path) -> str:
    try:
        return path.read_text()
    except OSError:
        return ""


def write_env_slot(name: str, value: str) -> bool:
    """Own-line atomic write of one secrets.env slot (frontend coverage 1.2).
    Returns True only when the stored value actually changed (idempotent).

    Every OTHER line of the file is preserved byte-exact: the edit happens on
    the raw bytes, the temp file lands in the same directory and
    `os.replace` swaps it in, then mode 0600 is enforced. `value` stays
    inside this frame — it never reaches a log line, response or exception;
    failures surface as OSError to the caller, which maps them to a
    type-name-only error."""
    path = SECRETS
    try:
        raw = path.read_bytes() if path.exists() else b""
    except OSError:
        raise
    new_val = value.encode("utf-8")
    # Line detection allows the EMPTY value (`NAME=`): an existing placeholder
    # line is replaced in place, never duplicated by an append.
    line_re = re.compile(rb"(?m)^" + re.escape(name.encode("ascii"))
                         + rb"=([^\r\n]*)$")
    hit = line_re.search(raw)
    if hit:
        if hit.group(1) == new_val:
            return False                      # already stored — no write
        updated = raw[:hit.start(1)] + new_val + raw[hit.end(1):]
    else:
        sep = b"" if (not raw or raw.endswith(b"\n")) else b"\n"
        updated = raw + sep + name.encode("ascii") + b"=" + new_val + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    tmp.write_bytes(updated)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return True


# --------------------------------------------------- mycore runtime probes ---

def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _pid_live(pid: int | None, marker: str) -> bool:
    """Liveness via /proc with an identity check (guards against pid reuse).
    Read-only observation: nothing is ever restarted from this module."""
    if not pid or pid <= 1:
        return False
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", "replace")
    except OSError:
        return False
    return marker in cmd


def _service_provisioned() -> bool:
    """Fail-closed provisioning signals: dedicated poll-token slot + owner
    allowlist. Presence only; no value is ever echoed or serialized."""
    token_ok = bool(read_env_slot("MYCORE_TG_TOKEN"))
    allow_ok = bool((os.environ.get("AIDOG_TG_ALLOWED_USERS") or "").strip())
    if not allow_ok and SHIM_PRESENT:
        allow_ok = bool(_slot_value(PROFILE / ".env", "TELEGRAM_ALLOWED_USERS"))
    return token_ok and allow_ok


def mycore_service_facts() -> dict:
    """Chat-service observability: state in running | pending(fail-closed) |
    stopped, pid liveness via /proc, service.log tail-line count + last event
    code. No paths, no credentials in the returned document."""
    pid = _read_pid(MYCORE_PIDFILE)
    running = _pid_live(pid, "mycore.service")
    state = ("running" if running
             else "stopped" if _service_provisioned()
             else "pending(fail-closed)")
    facts: dict = {"state": state, "pid": pid if running else None,
                   "log_lines": 0, "last_event": None, "last_event_at": None}
    tail = _tail_text(MYCORE_SERVICE_LOG, 65536)   # tail window = tail-count
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    facts["log_lines"] = len(lines)
    if lines:
        m = SERVICE_LINE_RE.match(lines[-1])
        if m:
            facts["last_event_at"], facts["last_event"] = m.group(1), m.group(2).upper()
    return facts


def mycore_service_detail(facts: dict) -> str:
    bits = [f"mycore service: {facts['state']}"]
    if facts.get("pid"):
        bits.append(f"pid {facts['pid']}")
    if facts.get("last_event"):
        bits.append(f"last {facts['last_event']}")
    return ", ".join(bits)


def mycore_watchdog_facts() -> dict:
    """Watchdog observability: process liveness, state-file freshness
    (< MYCORE_WD_FRESH_SEC), per-target verdicts, last sanitized log line."""
    facts: dict = {"process": proc_alive(MYCORE_WD_PROC), "state": "unknown",
                   "targets": {}, "age_sec": None, "last": None, "detail": ""}
    try:
        facts["age_sec"] = round(time.time() - MYCORE_WD_STATE.stat().st_mtime)
        doc = json.loads(MYCORE_WD_STATE.read_text())
        tg = doc.get("targets") if isinstance(doc, dict) else None
        if isinstance(tg, dict):
            for name, entry in tg.items():
                facts["targets"][str(name)[:32]] = (
                    str(entry.get("state") or "unknown")[:16]
                    if isinstance(entry, dict) else "unknown")
    except (OSError, ValueError, TypeError):
        pass
    lines = [ln for ln in _tail_text(MYCORE_WD_LOG, 16384).splitlines() if ln.strip()]
    if lines:
        facts["last"] = sanitize_event_text(lines[-1])
    age, targets = facts["age_sec"], facts["targets"]
    if not facts["process"]:
        facts["state"] = "down"
    elif age is None or age > MYCORE_WD_FRESH_SEC or not targets:
        facts["state"] = "unknown"
    elif any(v == "down" for v in targets.values()):
        facts["state"] = "down"
    else:
        facts["state"] = "up"
    detail = f"mycore wd: {facts['state']}"
    if age is not None:
        detail += f", state age {age}s"
    if facts["last"]:
        detail += f" · {facts['last']}"
    facts["detail"] = detail[:160]
    return facts


def tg_polling() -> bool:
    """Telegram panel: gateway alive AND the log shows a live polling session.
    Window widened to 128KB (observed 2026-10-18: a 4000-char tail slid past
    the single Connected marker while the session was actually healthy).
    Fresh reconnect blips (<60s old) report not-polling so an outage in
    progress is not painted green; a give-up marker (attempt 10/10) keeps the
    panel down until a new Connected line appears."""
    if not SHIM_PRESENT:
        # Post-swap: the gateway log is gone; the mycore service is the poller.
        return mycore_service_facts()["state"] == "running"
    if not proc_alive(KERNEL_CMD):
        return False
    try:
        with open(PROFILE / "logs/gateway_run.log", "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 131072))  # last 128KB
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    if "Connected to Telegram (polling mode)" not in tail:
        return False
    last_err: tuple[float, str] | None = None
    for m in re.finditer(
            r"(?m)^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+ \w+ [\w.]+: "
            r".*Telegram network error.*$", tail):
        try:
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        last_err = (ts, m.group(0))
    if last_err:
        ts, line = last_err
        if "attempt 10/10" in line:
            return False                      # adapter gave up — stay down
        if time.time() - ts <= 60:
            return False                      # reconnect in progress, honest
    return True


def read_config_bits() -> dict:
    """Sanitized parse of the runtime config — presence/booleans only, no secrets.
    Phase-aware: the profile config while the shim exists; the product's own
    web/settings.json mycore block after the swap (the profile indirection is
    deleted in the P5-5 car, and the view must degrade to product config —
    never to an error document)."""
    if not SHIM_PRESENT:
        return _mycore_config_bits()
    out: dict = {}
    try:
        text = (PROFILE / "config.yaml").read_text()
    except OSError:
        return {"error": "config unreadable"}
    out["model_provider"] = (re.search(r'(?m)^  provider: "?([^"\n]+)"?$', text) or [None, None])[1]
    out["model_base_url"] = (re.search(r'(?m)^  base_url: (\S+)$', text) or [None, None])[1]
    out["model_default"] = (re.search(r'(?m)^  default: "?([^"\n]+)"?$', text) or [None, None])[1]
    out["fallback_enabled"] = bool(re.search(r"(?m)^fallback_providers:", text))
    out["telemetry_off"] = bool(re.search(r"(?ms)^telemetry:\n  shared_metrics:\n    enabled: false", text))
    out["standalone_shim"] = bool(re.search(r"(?m)^  standalone: true$", text))
    # skills external dirs: scan past the exact key (commented examples skipped),
    # same convention as aidog_doctor's config.skills.external_dirs check
    dirs: list[str] = []
    in_ext = False
    for ln in text.splitlines():
        s = ln.strip()
        if not in_ext:
            if s == "external_dirs:":
                in_ext = True
            continue
        if s.startswith("- "):
            dirs.append(s[2:].strip())
        elif s and not s.startswith("#"):
            break
    out["external_skill_dirs"] = dirs[:8]
    return out


def _mycore_config_bits() -> dict:
    """Post-swap config view: the mycore block of web/settings.json (merged over
    defaults). URLs/model ids only — credentials never serialize. fallback is
    reported from OUR failover chain (mycore.upstreams — the Tier C leg; the
    local gateway's key pool still sits behind the primary endpoint),
    telemetry is off because no upstream telemetry subsystem exists once the
    transition wiring is deleted."""
    mc = load_settings().get("mycore")
    mc = mc if isinstance(mc, dict) else {}
    ups = mc.get("upstreams")
    return {"source": "settings.mycore",
            "model_provider": None,
            "model_base_url": mc.get("base_url"),
            "model_default": mc.get("model"),
            "fallback_enabled": bool(isinstance(ups, str) and ups.strip()),
            "telemetry_off": True,
            "standalone_shim": False,
            "external_skill_dirs": []}


# --------------------------------------------------------------- settings ----


def deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for key, val in patch.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val
    return out


def _is_loopback_http(url: str) -> bool:
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


def validate_settings_patch(patch) -> tuple[dict, list[str]]:
    """Allow-list validation. Returns (clean patch, errors); all-or-nothing."""
    if not isinstance(patch, dict):
        return {}, ["body must be a JSON object"]
    errors: list[str] = []
    clean: dict = {}
    for key in patch:
        if key not in ALLOWED_TOP:
            errors.append(f"unknown key: {key}")
    if "language" in patch:
        if patch["language"] in LANGS:
            clean["language"] = patch["language"]
        else:
            errors.append("language must be one of: " + "|".join(LANGS))
    if "refresh_sec" in patch:
        val = patch["refresh_sec"]
        if isinstance(val, bool) or not isinstance(val, int) or not 5 <= val <= 300:
            errors.append("refresh_sec must be an integer in 5..300")
        else:
            clean["refresh_sec"] = val
    if "sound" in patch:
        if isinstance(patch["sound"], bool):
            clean["sound"] = patch["sound"]
        else:
            errors.append("sound must be a boolean")
    if "voice_volume" in patch:
        val = patch["voice_volume"]
        if isinstance(val, bool) or not isinstance(val, int) or not 0 <= val <= 100:
            errors.append("voice_volume must be an integer in 0..100")
        else:
            clean["voice_volume"] = val
    if "alert_preview" in patch:
        if patch["alert_preview"] in ALERT_PREVIEWS:
            clean["alert_preview"] = patch["alert_preview"]
        else:
            errors.append("alert_preview must be one of: " + "|".join(ALERT_PREVIEWS))
    if "currency" in patch:
        if patch["currency"] in CURRENCIES:
            clean["currency"] = patch["currency"]
        else:
            errors.append("currency must be one of: " + "|".join(CURRENCIES))
    if "fx_rate" in patch:
        val = patch["fx_rate"]
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not 0 <= val <= 100000:
            errors.append("fx_rate must be a number in 0..100000 (0 = auto, else CNY per display unit)")
        else:
            clean["fx_rate"] = float(val)
    if "mycore" in patch:
        mc = patch["mycore"]
        if not isinstance(mc, dict):
            errors.append("mycore must be an object")
        else:
            clean_mc: dict = {}
            for key in mc:
                if key not in MYCORE_KEYS:
                    errors.append(f"unknown mycore key: {key}")
                    continue
                val = mc[key]
                if key == "context_dirs":
                    # os.pathsep-separated project-context dirs; empty = off
                    # (car 1-D1). Single line only — a dir list is one string.
                    if not isinstance(val, str) or len(val) > 256 or "\x00" in val \
                            or "\n" in val or "\r" in val:
                        errors.append("mycore.context_dirs must be one line of at most "
                                      "256 chars (dirs separated by the os pathsep; "
                                      "empty = off)")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "upstreams":
                    # failover chain (car 1-D4): url[|key_env[|model]] entries
                    # separated by commas; empty = off. Grammar lives in
                    # mycore.modeltier — the validator reports its exact reason.
                    if not isinstance(val, str) or len(val) > modeltier.MAX_SPEC_CHARS \
                            or "\x00" in val or "\n" in val or "\r" in val:
                        errors.append("mycore.upstreams must be one line of at most "
                                      f"{modeltier.MAX_SPEC_CHARS} chars "
                                      "(empty = off)")
                        continue
                    if val.strip():
                        try:
                            modeltier.parse_upstreams(val)
                        except ValueError as e:
                            errors.append(f"mycore.upstreams: {e}")
                            continue
                    clean_mc[key] = val
                    continue
                if key == "mcp":
                    # MCP servers (car 2-D5): JSON array, empty = off. The
                    # grammar lives in mycore.mcp — the validator reports its
                    # exact reason; secrets are env SLOT names, never values.
                    if not isinstance(val, str) or len(val) > mcp.MAX_CONFIG_CHARS \
                            or "\x00" in val or "\n" in val or "\r" in val:
                        errors.append("mycore.mcp must be one line of at most "
                                      f"{mcp.MAX_CONFIG_CHARS} chars (empty = off)")
                        continue
                    if val.strip():
                        try:
                            mcp.parse_config(val)
                        except ValueError as e:
                            errors.append(f"mycore.mcp: {e}")
                            continue
                    clean_mc[key] = val
                    continue
                if key == "shell":
                    # terminal backend (car 4b-B): one line, grammar lives in
                    # mycore.terminal — the validator reports its exact reason.
                    if not isinstance(val, str) or len(val) > terminal.MAX_SPEC_CHARS \
                            or "\x00" in val or "\n" in val or "\r" in val:
                        errors.append("mycore.shell must be one line of at most "
                                      f"{terminal.MAX_SPEC_CHARS} chars (empty = local)")
                        continue
                    try:
                        terminal.parse(val)
                    except ValueError as e:
                        errors.append(f"mycore.shell: {e}")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "tools_off":
                    # Per-tool kill switch (v0.5.32, docs/features/
                    # tool_toggles.md): a bounded list of KNOWN tool names.
                    # Fail-closed: wrong shape, bad grammar, duplicates and
                    # unknown names are all refused; only the offending tool
                    # name is ever echoed back (never a value, never a secret).
                    if not isinstance(val, list) or not 1 <= len(val) <= TOOLS_OFF_MAX:
                        errors.append(f"mycore.tools_off must be a list of 1..{TOOLS_OFF_MAX} tool names")
                        continue
                    try:
                        from mycore.tools import KNOWN_TOOL_NAMES
                    except Exception:  # noqa: BLE001 — fail closed, name only
                        errors.append("mycore.tools_off unavailable")
                        continue
                    seen: set = set()
                    bad: str | None = None
                    for item in val:
                        if not isinstance(item, str) or not TOOLS_OFF_NAME_RE.match(item):
                            bad = item if isinstance(item, str) else ""
                            errors.append("mycore.tools_off entries must match "
                                          f"[a-z][a-z0-9_]{{0,39}} (got {bad[:40]!r})")
                            break
                        if item in seen:
                            bad = item
                            errors.append(f"mycore.tools_off duplicate entry: {item}")
                            break
                        if item not in KNOWN_TOOL_NAMES:
                            bad = item
                            errors.append(f"mycore.tools_off unknown tool: {item}")
                            break
                        seen.add(item)
                    if bad is None:
                        clean_mc[key] = val
                    continue
                if not isinstance(val, str) or not val or len(val) > 256 or "\x00" in val:
                    errors.append(f"mycore.{key} must be a non-empty string of at most 256 chars")
                    continue
                if key == "base_url":
                    parts = urllib.parse.urlparse(val)
                    if parts.scheme not in ("http", "https") or not parts.hostname:
                        errors.append("mycore.base_url must be an http(s) URL with a host")
                        continue
                if key == "model" and not MYCORE_MODEL_RE.match(val):
                    errors.append("mycore.model must match [A-Za-z0-9._:/-] (1..100 chars)")
                    continue
                clean_mc[key] = val
            if clean_mc:
                clean["mycore"] = clean_mc
    if "usage" in patch:
        usage = patch["usage"]
        if not isinstance(usage, dict):
            errors.append("usage must be an object")
        else:
            clean_usage: dict = {}
            for key in usage:
                if key not in USAGE_KEYS:
                    errors.append(f"unknown usage key: {key}")
                    continue
                val = usage[key]
                if not isinstance(val, str) or len(val) > 512 or "\x00" in val:
                    errors.append(f"usage.{key} must be a string of at most 512 chars")
                    continue
                if key == "metrics_url" and val and not _is_loopback_http(val):
                    errors.append("usage.metrics_url must be an http loopback URL (127.0.0.1/localhost)")
                    continue
                if key in ("pricing_config", "log_dir") and val and not val.startswith("/"):
                    errors.append(f"usage.{key} must be an absolute path")
                    continue
                clean_usage[key] = val
            if clean_usage:
                clean["usage"] = clean_usage
    if "channels" in patch:
        chans = patch["channels"]
        if not isinstance(chans, dict):
            errors.append("channels must be an object")
        else:
            clean_ch: dict = {}
            for name in chans:
                if name not in CHANNEL_KEYS:
                    errors.append(f"unknown channels key: {name}")
                    continue
                sub = chans[name]
                if not isinstance(sub, dict):
                    errors.append(f"channels.{name} must be an object")
                    continue
                clean_sub: dict = {}
                for key in sub:
                    if key not in _CHANNEL_SCHEMA[name]:
                        errors.append(f"unknown channels.{name} key: {key}")
                        continue
                    val = sub[key]
                    if key in ("enabled", "smtp_ssl"):
                        if not isinstance(val, bool):
                            errors.append(f"channels.{name}.{key} must be a boolean")
                            continue
                        clean_sub[key] = val
                    elif key in ("allow", "channel_ids", "chat_ids"):
                        # sender/channel allow-lists: fail-closed when empty;
                        # entries are single-line id strings, never secrets.
                        if not isinstance(val, list):
                            errors.append(f"channels.{name}.{key} must be a list of strings")
                            continue
                        if len(val) > 256:
                            errors.append(f"channels.{name}.{key} holds at most 256 entries")
                            continue
                        if any(not isinstance(i, str) or len(i) > 256 or "\x00" in i
                               or "\n" in i or "\r" in i for i in val):
                            errors.append(f"channels.{name}.{key} entries must be "
                                          "single-line strings of at most 256 chars")
                            continue
                        clean_sub[key] = list(val)
                    elif key in ("smtp_port", "imap_port"):
                        if isinstance(val, bool) or not isinstance(val, int) \
                                or not 1 <= val <= 65535:
                            errors.append(f"channels.{name}.{key} must be an integer in 1..65535")
                            continue
                        clean_sub[key] = val
                    else:  # string fields: to / from / hosts / url / ids
                        if not isinstance(val, str) or len(val) > 512 \
                                or "\x00" in val or "\n" in val or "\r" in val:
                            errors.append(f"channels.{name}.{key} must be a single-line "
                                          "string of at most 512 chars")
                            continue
                        if key == "phone_number_id" and len(val) > 64:
                            errors.append(f"channels.{name}.{key} must be at most "
                                          "64 chars")
                            continue
                        if key == "url" and val:
                            parts = urllib.parse.urlparse(val)
                            if parts.scheme not in ("http", "https") or not parts.hostname:
                                errors.append(f"channels.{name}.url must be an http(s) URL "
                                              "(empty = off)")
                                continue
                        clean_sub[key] = val
                if clean_sub:
                    clean_ch[name] = clean_sub
            if clean_ch:
                clean["channels"] = clean_ch
    if "media" in patch:
        med = patch["media"]
        if not isinstance(med, dict):
            errors.append("media must be an object")
        else:
            clean_med: dict = {}
            for key in med:
                if key not in MEDIA_KEYS:
                    errors.append(f"unknown media key: {key}")
                    continue
                val = med[key]
                if key in ("image_base_url", "tts_base_url"):
                    if not isinstance(val, str) or len(val) > 256 \
                            or "\x00" in val or "\n" in val or "\r" in val:
                        errors.append(f"media.{key} must be a single-line string "
                                      "of at most 256 chars (empty = off)")
                        continue
                    if val:
                        parts = urllib.parse.urlparse(val)
                        if parts.scheme not in ("http", "https") or not parts.hostname:
                            errors.append(f"media.{key} must be an http(s) URL with a "
                                          "host (empty = off)")
                            continue
                    clean_med[key] = val
                elif key == "tts_model" or key == "image_model":
                    if not isinstance(val, str) or len(val) > 64 \
                            or "\x00" in val or "\n" in val or "\r" in val:
                        errors.append(f"media.{key} must be a single-line string "
                                      "of at most 64 chars (empty = engine default)")
                        continue
                    clean_med[key] = val
                elif key == "stt_model":
                    if val not in STT_MODELS:
                        errors.append("media.stt_model must be one of: "
                                      + "|".join(STT_MODELS))
                        continue
                    clean_med[key] = val
                elif key == "tts_engine":
                    if val not in TTS_ENGINES:
                        errors.append("media.tts_engine must be one of: "
                                      + "|".join(TTS_ENGINES))
                        continue
                    clean_med[key] = val
                elif key == "wake_phrases":
                    if not isinstance(val, list) or len(val) > WAKE_PHRASES_MAX:
                        errors.append("media.wake_phrases must be a list of at most "
                                      f"{WAKE_PHRASES_MAX} phrases")
                        continue
                    if any(not isinstance(p, str) or not p.strip()
                           or len(p) > WAKE_PHRASE_MAX_LEN or "\x00" in p
                           or "\n" in p or "\r" in p for p in val):
                        errors.append("media.wake_phrases entries must be non-empty "
                                      "single-line strings of at most "
                                      f"{WAKE_PHRASE_MAX_LEN} chars")
                        continue
                    clean_med[key] = [p.strip() for p in val]
            if clean_med:
                clean["media"] = clean_med
    if "web3" in patch:
        # W2 auto-approval caps: all-or-nothing (both keys or neither),
        # numbers only (bools refused), bounded 0..1000000 USD. 0 is legal
        # and means "nothing auto-approves" — the fail-closed default.
        w3 = patch["web3"]
        if not isinstance(w3, dict):
            errors.append("web3 must be an object")
        else:
            before_w3 = len(errors)
            missing_w3 = [k for k in WEB3_KEYS if k not in w3]
            if missing_w3:
                errors.append("web3 must set both keys: "
                              + "|".join(WEB3_KEYS))
            clean_w3: dict = {}
            for key in w3:
                if key not in WEB3_KEYS:
                    errors.append(f"unknown web3 key: {key}")
                    continue
                val = w3[key]
                if isinstance(val, bool) or not isinstance(val, (int, float)) \
                        or not (0 <= val <= WEB3_CAP_USD):
                    errors.append(f"web3.{key} must be a number in "
                                  f"0..{WEB3_CAP_USD}")
                    continue
                clean_w3[key] = float(val)
            if len(clean_w3) == len(WEB3_KEYS) and len(errors) == before_w3:
                clean["web3"] = clean_w3
    if "evolution" in patch:
        clean_evo, evo_errors = evolution.validate_evolution_settings(
            patch["evolution"])
        errors.extend(evo_errors)
        if clean_evo:
            clean["evolution"] = clean_evo
    if "hooks" in patch:
        # Event hooks partition (car 4a): schema + argv[0] existence live in
        # mycore.hooks — one source of truth for write validation and load.
        clean_hooks, hook_errors = hooks.validate_hooks(patch["hooks"])
        errors.extend(hook_errors)
        if clean_hooks:
            clean["hooks"] = clean_hooks
    return clean, errors


def load_settings() -> dict:
    """Defaults overlaid with the persisted document; invalid entries dropped."""
    try:
        stored = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    clean, _ = validate_settings_patch(stored)
    return deep_merge(DEFAULT_SETTINGS, clean)


def atomic_write_json(path: Path, doc: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


# ----------------------------------------------------------- reboot watch ---
# Owner directive (2026-10-17): the console must SEE machine reboots instead
# of being blindsided — boot facts persist in .state.json, a boot-time change
# is recorded as an event that survives console restarts.


def _load_state() -> dict:
    try:
        doc = json.loads(STATE_FILE.read_text())
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def system_facts() -> dict:
    """Boot time + uptime + reboot history (detects boot changes across runs)."""
    try:
        uptime_sec = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime_sec = 0.0
    boot_ts = round(time.time() - uptime_sec, 1)
    with _state_lock:
        state = _load_state()
        last = state.get("last_boot_ts")
        reboots = state.get("reboots") if isinstance(state.get("reboots"), list) else []
        if isinstance(last, (int, float)) and abs(float(last) - boot_ts) > 2:
            reboots = ([{"from": round(float(last), 1), "to": boot_ts}] + reboots)[:5]
            try:
                atomic_write_json(STATE_FILE, {"last_boot_ts": boot_ts, "reboots": reboots})
            except OSError:
                pass
        elif last is None:
            try:
                atomic_write_json(STATE_FILE, {"last_boot_ts": boot_ts, "reboots": reboots})
            except OSError:
                pass
    return {"boot_ts": boot_ts, "uptime_sec": round(uptime_sec, 1),
            "reboots": reboots, "console_boot_ts": round(_EVENTS_BOOT_TS, 1)}


# ----------------------------------------------------------------- status ----


_PREV_STATES: dict[str, str] = {}
_TRANSITIONS: deque = deque(maxlen=60)
_EVENTS_BOOT_TS = time.time()
_RAW_FAILS: dict[str, int] = {}
_EFFECTIVE: dict[str, str] = {}


def _event(level: str, src: str, text: str, ts: float | None = None, code: str | None = None) -> dict:
    ev = {"t": round(ts if ts is not None else time.time(), 3), "level": level, "src": src,
          "text": sanitize_event_text(text)}
    if code:
        ev["code"] = code
    return ev


def note_transitions(panels: list[dict]) -> None:
    """Record panel up/down flips as console events (real, server-observed)."""
    now = time.time()
    for p in panels:
        pid, state = p.get("id", "?"), p.get("state", "?")
        prev = _PREV_STATES.get(pid)
        if prev is not None and prev != state:
            level = "urgent" if state == "down" else "warn" if state == "unknown" else "info"
            _TRANSITIONS.append(_event(level, pid, f"{pid}: {prev} -> {state}", now,
                                       code=f"transition.{state}"))
        _PREV_STATES[pid] = state


def _kernel_version_line() -> str:
    """First line of the transition kernel version command (shim-present only),
    display-debranded through the env-derived token — binary output carries the
    upstream name, which this console never keeps as its own. Empty post-swap."""
    if not (SHIM_PRESENT and KERNEL_VERSION_CMD):
        return ""
    try:
        out = os.popen(f"{KERNEL_VERSION_CMD} 2>/dev/null").read()
    except OSError:
        return ""
    first = out.splitlines()[0] if out.splitlines() else ""
    return _BRAND_RE.sub("kernel", first) if _BRAND_RE else first


def _kernel_panel(svc: dict) -> dict:
    """kernel panel — phase-aware: transition kernel version while the shim
    exists; derived from the mycore service (the runtime that replaces it)
    once the wiring is deleted."""
    if SHIM_PRESENT:
        ver = _kernel_version_line()
        return {"id": "kernel", "state": "up" if "stable" in ver else "down",
                "detail": ver[:80]}
    return {"id": "kernel",
            "state": "up" if svc["state"] == "running" else "down",
            "detail": mycore_service_detail(svc)}


def _gateway_panel(svc: dict) -> dict:
    """gateway panel — phase-aware: transition gateway process while the shim
    exists (with the mycore service state alongside as the incoming brain);
    an honest n/a post-swap, never an exception."""
    if SHIM_PRESENT:
        return {"id": "gateway", "state": "up" if proc_alive(KERNEL_CMD) else "down",
                "detail": f"mycore service: {svc['state']}"}
    return {"id": "gateway",
            "state": "up" if svc["state"] == "running" else "down",
            "detail": f"n/a (post-swap): channel served by mycore service ({svc['state']})"}


def _watchdog_panel(wdf: dict) -> dict:
    """watchdog panel — phase-aware: legacy watchdog session + its state file
    while the shim exists; the mycore watchdog (process/state-file freshness)
    afterwards."""
    if SHIM_PRESENT:
        detail = _read_text(PROFILE / "logs/watchdog_state").strip() or "n/a"
        return {"id": "watchdog",
                "state": "up" if proc_alive("deploy/watchdog.sh") else "down",
                "detail": detail}
    return {"id": "watchdog", "state": wdf["state"],
            "detail": wdf.get("detail") or "n/a"}


def collect_status() -> dict:
    if SHIM_PRESENT:
        mem = PROFILE / "memories/MEMORY.md"
        usr = PROFILE / "memories/USER.md"
    else:
        # Post-swap curated memory lives in the body-layer overlay (the profile
        # copy dies with the wiring); car F: per-profile body/memory when a
        # profile is active, same budgets, honest 0 when absent.
        mem, usr = profile_paths.memory_slots(REPO)
    cfg_bits = read_config_bits()
    mbase = (cfg_bits.get("model_base_url") or "http://localhost:3456/v1").rstrip("/")
    mprobe = f"{mbase}/models" if mbase.endswith("/v1") else f"{mbase}/v1/models"
    svc = mycore_service_facts()
    wdf = mycore_watchdog_facts()
    tg_panel: dict = {"id": "telegram", "state": "up" if tg_polling() else "unknown"}
    if not SHIM_PRESENT:
        # Post-swap the polling session belongs to the mycore service.
        tg_panel["detail"] = f"mycore service: {svc['state']}"
    panels = [
        _kernel_panel(svc),
        _gateway_panel(svc),
        _watchdog_panel(wdf),
        {"id": "model", "state": "up" if http_status(mprobe, timeout=5.0) == 200 else "down"},
        tg_panel,
    ]
    # Probe hysteresis: the channel endpoint stalls 5-15s in maintenance windows
    # (observed 2026-10-17: direct probes 30/30 OK while the console saw blips).
    # A panel must fail TWO consecutive polls before it flips down/unknown, so
    # brief stalls don't spam URGENT transitions; recoveries apply immediately.
    # The raw probe value stays visible in `probe` when it differs.
    for p in panels:
        pid, raw = p["id"], p["state"]
        fails = (_RAW_FAILS.get(pid, 0) + 1) if raw != "up" else 0
        _RAW_FAILS[pid] = fails
        effective = _EFFECTIVE.get(pid)
        if raw == "up" or fails >= 2 or effective is None:
            _EFFECTIVE[pid] = raw
        if p["state"] != _EFFECTIVE[pid]:
            p["probe"] = p["state"]
        p["state"] = _EFFECTIVE[pid]
    note_transitions(panels)
    sysfacts = system_facts()
    reasons = [f"{p['id']}: {p['state']}" for p in panels if p["state"] == "down"]
    level = "urgent" if reasons else "info"
    soft = [f"{p['id']}: {p['state']}" for p in panels if p["state"] == "unknown"]
    if soft:
        level = "warn"
        reasons = soft
    return {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": VERSION,
        # Car F: active profile name ("" = default profile = pre-car-F paths);
        # a bare noun, rendered verbatim by the console — no locale key.
        "profile": profile_paths.profile_name(),
        "panels": panels,
        "advisory": {"level": level, "reasons": reasons},
        "system": sysfacts,
        "mycore": {"service": svc, "watchdog": wdf},
        "memory": {
            "curated_chars": len(mem.read_text()) if mem.exists() else 0,
            "curated_limit": 2200,
            "user_chars": len(usr.read_text()) if usr.exists() else 0,
            "user_limit": 1375,
        },
        "config": cfg_bits,
    }


# ------------------------------------------------------------------ usage ----
# Cost/usage data source: local channel metrics endpoint (Prometheus text),
# pricing numbers from the channel config (read-only), and today's request log.
# All sources are loopback/read-only personal infrastructure; the panel degrades
# honestly to "unavailable" when a source is unreachable.

_usage_lock = threading.Lock()
_usage_cache: dict = {"ts": 0.0, "data": None}
USAGE_TTL = 20.0

# ---- display currency / FX (owner 2026-10-18: default $, backend-switchable) --
# Costs are computed in source CNY (pricing config). The display conversion:
#   currency=USD (default) -> auto FX from the free open.er-api.com endpoint
#   (daily update, no key; 6h cache / 5min negative cache), overridable by
#   manual `fx_rate` (CNY per 1 display unit), and honestly marked
#   source="unavailable" (frontend falls back to source CNY) when both fail.
_fx_lock = threading.Lock()
_fx_cache: dict = {"ts": 0.0, "data": None}
FX_TTL = 6 * 3600.0
FX_ERR_TTL = 300.0


def display_fx(settings: dict) -> dict:
    cur = settings.get("currency") if settings.get("currency") in CURRENCY_SYM else "USD"
    try:
        manual = float(settings.get("fx_rate") or 0)
    except (TypeError, ValueError):
        manual = 0.0
    out = {"currency": cur, "symbol": CURRENCY_SYM[cur],
           "rate_cny_per_unit": None, "source": "unavailable",
           "updated": None, "src_currency": "CNY"}
    if cur == "CNY":
        out.update(rate_cny_per_unit=1.0, source="native")
        return out
    if manual > 0:
        out.update(rate_cny_per_unit=round(manual, 6), source="manual")
        return out
    now = time.time()
    with _fx_lock:
        if _fx_cache["data"] is not None and now - _fx_cache["ts"] < (
                FX_TTL if _fx_cache["data"].get("rate_cny_per_unit") else FX_ERR_TTL):
            cached = dict(_fx_cache["data"])
            cached["currency"], cached["symbol"] = cur, CURRENCY_SYM[cur]
            return cached
        data = dict(out)
        try:
            url = f"https://open.er-api.com/v6/latest/{cur}"
            with urllib.request.urlopen(url, timeout=8) as r:
                doc = json.loads(r.read().decode())
            rate = float((doc.get("rates") or {}).get("CNY") or 0)
            if doc.get("result") == "success" and rate > 0:
                data.update(rate_cny_per_unit=round(rate, 6), source="auto",
                            updated=doc.get("time_last_update_utc"))
        except Exception as e:  # noqa: BLE001 — honest degradation, never fake
            data["error"] = type(e).__name__
        _fx_cache["ts"], _fx_cache["data"] = now, data
        return dict(data)


def _load_pricing(path: str) -> dict:
    """Extract only the pricing numbers from the channel config (nothing else)."""
    base = {"available": False, "input_per_1m": 0.0, "output_per_1m": 0.0, "bytes_per_token": 3,
            "currency": "CNY"}
    if not path:
        return base
    try:
        cfg = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return base
    if not isinstance(cfg, dict):
        return base
    prices = cfg.get("prices") if isinstance(cfg.get("prices"), dict) else {}
    try:
        pin = float(prices.get("inputPer1M") or 0)
        pout = float(prices.get("outputPer1M") or 0)
        bpt = float(cfg.get("bytesPerToken") or 3)
    except (TypeError, ValueError):
        return base
    if bpt <= 0 or bpt > 100:
        bpt = 3
    return {"available": True, "input_per_1m": pin, "output_per_1m": pout,
            "bytes_per_token": bpt, "currency": "CNY"}


def _parse_metrics(text: str) -> dict:
    agg = {"keys_active": None, "accounts_total": None, "queue_depth": None,
           "requests_total": 0, "input_bytes": 0, "output_bytes": 0, "health": []}
    for line in text.splitlines():
        if not line or line[0] == "#":
            continue
        m = re.match(r"^([a-zA-Z_:][\w:]*)(\{[^}]*\})?\s+([-+0-9.eE]+)$", line)
        if not m:
            continue
        name, labels, val = m.group(1), m.group(2) or "", float(m.group(3))
        if name == "codex_proxy_keys_active":
            agg["keys_active"] = int(val)
        elif name == "codex_proxy_accounts_total":
            agg["accounts_total"] = int(val)
        elif name == "codex_proxy_queue_depth":
            agg["queue_depth"] = int(val)
        elif name == "codex_proxy_key_requests_total":
            agg["requests_total"] += int(val)
        elif name == "codex_proxy_key_bytes_total":
            if 'type="input"' in labels:
                agg["input_bytes"] += int(val)
            elif 'type="output"' in labels:
                agg["output_bytes"] += int(val)
        elif name == "codex_proxy_key_health_score":
            agg["health"].append(val)
    return agg


def _aggregate_file(path: Path) -> dict | None:
    """Aggregate one day's request log (bytes/duration/status/model mix)."""
    if not path.is_file():
        return None
    req = err = in_b = out_b = dur = 0
    models: dict[str, int] = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    doc = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(doc, dict):
                    continue
                req += 1
                status = doc.get("status")
                if isinstance(status, int) and status >= 400:
                    err += 1
                in_b += int(doc.get("inputBytes") or 0)
                out_b += int(doc.get("outputBytes") or 0)
                dur += int(doc.get("duration") or 0)
                model = str(doc.get("reqModel") or "unknown")
                models[model] = models.get(model, 0) + 1
    except OSError:
        return None
    top = sorted(models.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return {"requests": req, "errors": err, "input_bytes": in_b, "output_bytes": out_b,
            "avg_ms": (dur // req) if req else 0,
            "top_models": [{"model": name, "requests": n} for name, n in top]}


def _aggregate_daily(log_dir: str) -> dict | None:
    """Aggregate today's request log (local date, per rotation naming)."""
    if not log_dir:
        return None
    return _aggregate_file(Path(log_dir) / f"{time.strftime('%Y-%m-%d')}.jsonl")


def _cost_reference(log_dir: str, bpt: float, pricing: dict) -> dict | None:
    """7-day cost baseline (days before today) — honest denominator for the
    console's energy metaphor; None when pricing or history is unavailable."""
    if not log_dir or not pricing.get("available") or not bpt:
        return None
    today_name = f"{time.strftime('%Y-%m-%d')}.jsonl"
    try:
        files = sorted(p for p in Path(log_dir).glob("*.jsonl") if p.name != today_name)
    except OSError:
        return None
    costs: list[float] = []
    for path in files[-7:]:
        agg = _aggregate_file(path)
        if not agg:
            continue
        in_t = _tokens(agg["input_bytes"], bpt)
        out_t = _tokens(agg["output_bytes"], bpt)
        costs.append(round(in_t / 1e6 * pricing["input_per_1m"] +
                           out_t / 1e6 * pricing["output_per_1m"], 4))
    if not costs:
        return None
    return {"days": len(costs),
            "avg_cost": round(sum(costs) / len(costs), 4),
            "max_cost": round(max(costs), 4)}


def _tokens(byte_count: int, bpt: float) -> int:
    return int(byte_count / bpt) if bpt else 0


def collect_usage(settings: dict) -> dict:
    now = time.time()
    with _usage_lock:
        if _usage_cache["data"] is not None and now - _usage_cache["ts"] < USAGE_TTL:
            return _usage_cache["data"]
        cfg = settings.get("usage") if isinstance(settings.get("usage"), dict) else {}
        pricing = _load_pricing(cfg.get("pricing_config", "") or "")
        bpt = pricing["bytes_per_token"]
        metrics_url = cfg.get("metrics_url", "") or ""
        metrics_text = fetch_text(metrics_url) if metrics_url and _is_loopback_http(metrics_url) else None
        pool = None
        if metrics_text:
            raw = _parse_metrics(metrics_text)
            health = raw["health"]
            pool = {
                "keys_active": raw["keys_active"],
                "accounts_total": raw["accounts_total"],
                "queue_depth": raw["queue_depth"],
                "requests_total": raw["requests_total"],
                "input_tokens": _tokens(raw["input_bytes"], bpt),
                "output_tokens": _tokens(raw["output_bytes"], bpt),
                "health_min": round(min(health), 1) if health else None,
                "health_avg": round(sum(health) / len(health), 1) if health else None,
            }
        today_raw = _aggregate_daily(cfg.get("log_dir", "") or "")
        daily_name = f"{time.strftime('%Y-%m-%d')}.jsonl" if today_raw else None
        today = None
        if today_raw:
            in_t = _tokens(today_raw["input_bytes"], bpt)
            out_t = _tokens(today_raw["output_bytes"], bpt)
            cost = round(in_t / 1e6 * pricing["input_per_1m"] + out_t / 1e6 * pricing["output_per_1m"], 4)
            today = dict(today_raw, input_tokens=in_t, output_tokens=out_t, cost=cost)
        data = {
            "available": pool is not None or today is not None,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": {"metrics": metrics_url if pool else None, "daily_log": daily_name},
            "pricing": pricing,
            "display": display_fx(settings),
            "today": today,
            "reference": _cost_reference(cfg.get("log_dir", "") or "", bpt, pricing),
            "pool": pool,
        }
        _usage_cache["ts"], _usage_cache["data"] = now, data
        return data


# ----------------------------------------------------------------- events ----
# Sanitized stream for the live transcript: gateway/kernel log tails (module
# prefix dropped, credentials redacted, transitional product names normalized)
# plus state flips observed by this server. Raw logs remain authoritative.

_TOKEN_RE = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+\S+")
_LOG_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+\s+(\w+)\s+[\w.]+:\s*(.*)$")


def sanitize_event_text(text: str) -> str:
    text = _TOKEN_RE.sub("[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    if _BRAND_RE is not None:
        text = _BRAND_RE.sub("kernel", text)
    return text.strip()[:200]


def _tail_text(path: Path, max_bytes: int = 65536) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _log_events(path: Path, src: str, cap: int = 12) -> list[dict]:
    out: list[dict] = []
    for line in _tail_text(path).splitlines():
        m = _LOG_RE.match(line)
        if not m:
            continue
        ts_s, lvl, body = m.groups()
        try:
            ts = time.mktime(time.strptime(ts_s, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            ts = time.time()
        level = {"INFO": "info", "WARNING": "warn", "ERROR": "urgent",
                 "CRITICAL": "urgent"}.get(lvl, "info")
        out.append(_event(level, src, body, ts))
    return out[-cap:]


def _service_events(path: Path, src: str = "service", cap: int = 8) -> list[dict]:
    """Events from the mycore service log (ISO-8601Z + LEVEL + body lines).
    Real timestamps; every body passes the sanitizer (tokens/bearer/brand
    redacted). Missing file -> no events, never an exception."""
    out: list[dict] = []
    for line in _tail_text(path).splitlines():
        m = SERVICE_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc).timestamp()
        except ValueError:
            ts = time.time()
        code = m.group(2).upper()
        body = line[m.end():].strip()
        out.append(_event(SERVICE_EVENT_LEVEL.get(code, "info"),
                          src, f"{code} {body}".strip(), ts,
                          code=f"service.{code.lower()}"))
    return out[-cap:]


def _mycore_watchdog_events() -> list[dict]:
    """The last mycore watchdog line as one event; file mtime is its write
    time (older lines carry no timestamp in the log, so they are not shown
    as individually-timed events — honesty over fabricated ordering)."""
    try:
        mtime = MYCORE_WD_LOG.stat().st_mtime
    except OSError:
        return []
    lines = [ln for ln in _tail_text(MYCORE_WD_LOG, 16384).splitlines() if ln.strip()]
    if not lines:
        return []
    last = lines[-1]
    low = last.lower()
    if "recovered" in low or "-> up" in low:
        level = "info"
    elif "fail" in low or "is down" in low or "-> down" in low or "\U0001f534" in last:
        level = "urgent"
    elif "⚠" in last or "still down" in low:
        level = "warn"
    else:
        level = "info"
    return [_event(level, "mycore-watchdog", last, mtime, code="mycore.watchdog")]


def collect_skills() -> dict:
    """Skill gallery data: real SKILL.md records from the body-layer skill dirs
    (product overlay + configured external dirs). No fake entries, ever."""
    roots: list[Path] = [REPO / "overlay" / "skills"]
    for d in (read_config_bits().get("external_skill_dirs") or []):
        try:
            p = Path(d).expanduser()
            if p.is_dir():
                roots.append(p)
        except OSError:
            continue
    skills: list[dict] = []
    seen: set[str] = set()
    for root in roots:
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        for child in children:
            if not child.is_dir():
                continue
            meta = child / "SKILL.md"
            if not meta.is_file():
                continue
            try:
                head = meta.read_text(errors="replace")[:4000]
            except OSError:
                continue
            name = child.name
            desc = ""
            in_fm = False
            for line in head.splitlines():
                s = line.strip()
                if s == "---":
                    in_fm = not in_fm
                    continue
                if in_fm:
                    m = re.match(r"^(name|description):\s*(.+)$", s)
                    if m and m.group(1) == "name":
                        name = m.group(2).strip().strip("'\"")[:60]
                    elif m and m.group(1) == "description":
                        desc = m.group(2).strip().strip("'\"")
            if not desc:  # fallback: first prose line after the frontmatter
                for line in head.splitlines():
                    s = line.strip()
                    if s and not s.startswith(("#", "-", ">", "|", "---")) and len(s) > 12:
                        desc = s
                        break
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            skills.append({
                "id": child.name,
                "name": name,
                "desc": desc[:240],
                "source": "body" if root.name == "skills" and root.parent.name == "overlay"
                          else "external",
            })
    skills.sort(key=lambda s: s["name"].lower())
    return {"skills": skills, "count": len(skills),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def collect_events() -> dict:
    events: list[dict] = []
    for rb in system_facts().get("reboots", []):
        try:
            when = datetime.fromtimestamp(float(rb["to"]), timezone.utc).isoformat(timespec="seconds")
        except (KeyError, TypeError, ValueError, OSError):
            when = ""
        events.append(_event("warn", "system", f"machine reboot detected ({when})",
                             float(rb.get("to") or time.time()), code="system.reboot"))
    if SHIM_PRESENT:
        events += _log_events(PROFILE / "logs/gateway_run.log", "gateway")
        events += _log_events(PROFILE / "logs/errors.log", "kernel")
    # mycore runtime lines flow in both phases (the service/watchdog already
    # run today; post-swap they are the only log sources left).
    events += _service_events(MYCORE_SERVICE_LOG)
    events += _mycore_watchdog_events()
    events += list(_TRANSITIONS)
    events.append(_event("info", "console", f"console online ({VERSION})",
                         _EVENTS_BOOT_TS, code="console.boot"))
    events.sort(key=lambda e: e["t"], reverse=True)
    return {"events": events[:30],
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


# ------------------------------------------------------------------ voice --
# Phase ② (owner-approved pilot): edge-tts free online synthesis. Honest
# degradation: capability endpoint reports engine presence; synthesis failures
# return real 5xx codes, never a silent fake. Guards: text <= SAY_MAX_CHARS
# (re-sanitized through sanitize_event_text), sliding-window rate limit,
# JSON-only body + same-origin Origin (blocks cross-site form posts),
# on-disk cache keyed by content hash.

SAY_MAX_CHARS = 200
SAY_RATE_LIMIT = 8          # synthesis requests per sliding minute
SAY_RATE_WINDOW = 60.0
SAY_TIMEOUT = 25            # seconds; online engine on a flaky-proxy host
SAY_VOICES = {"en": "en-GB-RyanNeural", "zh": "zh-CN-YunxiNeural"}
SAY_DIR = WEB_DIR / ".voice"     # gitignored audio cache (runtime state)
_say_times: deque = deque()
_say_lock = threading.Lock()


def collect_voice() -> dict:
    """Voice capability — presence only, no engine internals."""
    engine = shutil.which("edge-tts")
    return {
        "engine": "edge-tts" if engine else None,
        "available": bool(engine),
        "voices": dict(SAY_VOICES),
        "max_chars": SAY_MAX_CHARS,
        "rate_limit": {"max": SAY_RATE_LIMIT, "window_sec": SAY_RATE_WINDOW},
    }


def _say_rate_ok() -> bool:
    now = time.time()
    with _say_lock:
        while _say_times and now - _say_times[0] > SAY_RATE_WINDOW:
            _say_times.popleft()
        if len(_say_times) >= SAY_RATE_LIMIT:
            return False
        _say_times.append(now)
        return True


def _synth_say(text: str, lang: str) -> bytes:
    """Synthesize `text` with edge-tts; return mp3 bytes (cached on disk).
    Raises RuntimeError on engine failure — caller maps to an honest 5xx."""
    voice = SAY_VOICES.get(lang, SAY_VOICES["en"])
    key = hashlib.sha256(f"{voice}|{text}".encode()).hexdigest()[:32]
    cache = SAY_DIR / f"{key}.mp3"
    try:
        if cache.is_file() and cache.stat().st_size > 0:
            return cache.read_bytes()
    except OSError:
        pass
    SAY_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SAY_DIR / f"{key}.part"
    # Text goes through a temp file (--file), never argv: process listings
    # must not expose spoken content (pitfall: no payload material in ps).
    txt = SAY_DIR / f"{key}.txt"
    txt.write_text(text, encoding="utf-8")
    env = dict(os.environ)
    cmd = ["edge-tts", "--voice", voice, "--file", str(txt),
           "--write-media", str(tmp)]
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if proxy:
        cmd += ["--proxy", proxy]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=SAY_TIMEOUT, env=env)
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            detail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
            raise RuntimeError(f"edge-tts failed: {detail[0][:160]}")
        data = tmp.read_bytes()
        os.replace(tmp, cache)
        # keep the cache small: newest 200 clips
        clips = sorted(SAY_DIR.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
        for old in clips[:-200]:
            old.unlink(missing_ok=True)
        return data
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"edge-tts timed out after {SAY_TIMEOUT}s")
    except OSError as e:
        raise RuntimeError(f"edge-tts unavailable: {type(e).__name__}")
    finally:
        txt.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)  # no-op after successful os.replace


# ------------------------------------------------------------ media face --
# Car 4c (console): local speech-to-text, the image trial panel, the BYOK
# speech leg, and the `media` capability block. Rules of this face: lazy
# imports (the mycore media modules may land in a parallel wave — absent =
# honest 503, never a fake), booleans and engine/model NAMES only in the
# catalog (no credential value, no URL ever serializes), and explicit
# failures — byok never silently falls back to edge.

STT_RATE_LIMIT = 30            # transcription requests per sliding minute
STT_RATE_WINDOW = 60.0
_stt_times: deque = deque()
_stt_lock = threading.Lock()
# W1 web3: /api/exchange/quote gets its OWN budget — sharing the STT window
# would let a market probe starve the microphone (and vice versa).
EXCHANGE_QUOTE_RATE_LIMIT = 10  # quote probes per sliding minute
EXCHANGE_QUOTE_RATE_WINDOW = 60.0
_exchange_quote_times: deque = deque()
_exchange_quote_lock = threading.Lock()
BYOK_VOICE = "alloy"           # single OpenAI-compatible voice slot (both langs)
BYOK_MAX_AUDIO = 5_000_000     # upstream speech bytes read cap


def _stt_rate_ok() -> bool:
    """Independent sliding window — /api/stt must not spend /api/say's
    budget (the console can listen and speak at the same time)."""
    now = time.time()
    with _stt_lock:
        while _stt_times and now - _stt_times[0] > STT_RATE_WINDOW:
            _stt_times.popleft()
        if len(_stt_times) >= STT_RATE_LIMIT:
            return False
        _stt_times.append(now)
        return True


def _exchange_quote_rate_ok() -> bool:
    """Independent sliding window for GET /api/exchange/quote — 10 probes
    per minute on a private deque (never the STT or /api/say budget)."""
    now = time.time()
    with _exchange_quote_lock:
        while (_exchange_quote_times and
               now - _exchange_quote_times[0] > EXCHANGE_QUOTE_RATE_WINDOW):
            _exchange_quote_times.popleft()
        if len(_exchange_quote_times) >= EXCHANGE_QUOTE_RATE_LIMIT:
            return False
        _exchange_quote_times.append(now)
        return True


def _no_url(text: str) -> str:
    """Catalog reasons are names and sentences — never a URL: the smoke
    asserts zero credential material, and we go further (no URL at all)."""
    return re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", "url", text).strip()


def media_capability(settings: dict) -> dict:
    """`media` block of GET /api/capabilities — presence/booleans plus
    engine and model NAMES only: no key, no URL, no path, no slot value."""
    med = settings.get("media") if isinstance(settings.get("media"), dict) else {}
    # speech-to-text: lazy import + weight probe (module may be absent)
    stt_blk: dict = {"available": False}
    try:
        from mycore import stt as _stt
        if _stt.available():
            stt_blk = {"available": True,
                       "model": str(med.get("stt_model") or "base")}
    except Exception as e:  # noqa: BLE001 — absent module = unavailable, honestly
        sys.stderr.write(f"capabilities stt probe failed err={type(e).__name__}\n")
    # image generation: configured flag + honest, URL-stripped reason
    img_blk: dict = {"configured": False}
    try:
        from mycore import imagegen as _imagegen
        ok, reason = _imagegen.configured()
        img_blk = {"configured": bool(ok)}
        if not ok:
            why = _no_url(str(reason or ""))[:160]
            if why:
                img_blk["reason"] = why
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities image probe failed err={type(e).__name__}\n")
    # tts: engine presence on this host; byok counts as configured when its
    # base URL exists and the credential policy passes (same policy as the
    # synth leg below — a loopback endpoint may run keyless, remote is closed).
    byok_ok = _tts_byok_cfg(settings)[1]
    active = med.get("tts_engine")
    active = active if active in TTS_ENGINES else "edge"
    tts_blk = {"engines": {"edge": bool(shutil.which("edge-tts")),
                           "byok": bool(byok_ok)},
               "active": active}
    # connectors: slot presence booleans (values never leave mycore.connectors)
    conn_blk: dict = {"linear": False, "notion": False}
    try:
        from mycore import connectors as _connectors
        st = _connectors.status()
        conn_blk = {"linear": bool(st.get("linear")),
                    "notion": bool(st.get("notion"))}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities connectors probe failed err={type(e).__name__}\n")
    return {"stt": stt_blk, "image": img_blk, "tts": tts_blk,
            "connectors": conn_blk}


def _tts_byok_cfg(settings: dict) -> tuple[str, bool, str]:
    """(base, ok, key) for the BYOK speech leg. Base URL priority: env
    AIDOG_TTS_URL > settings media.tts_base_url > mycore.base_url. Key = the
    TTS_API_KEY slot falling back to MODEL_API_KEY (value never echoed, never
    logged, never part of a reason string). A LOOPBACK base may run keyless —
    the same policy imagegen already applies to local gateways; any remote
    endpoint without a slot is fail-closed (ok=False)."""
    med = settings.get("media") if isinstance(settings.get("media"), dict) else {}
    mc = settings.get("mycore") if isinstance(settings.get("mycore"), dict) else {}
    base = (os.environ.get("AIDOG_TTS_URL", "").strip()
            or str(med.get("tts_base_url") or "").strip()
            or str(mc.get("base_url") or "").strip()).rstrip("/")
    if not base:
        return "", False, ""
    key = read_env_slot("TTS_API_KEY") or read_env_slot("MODEL_API_KEY") or ""
    if key:
        return base, True, key
    return base, _is_loopback_http(base), ""


def _synth_say_byok(text: str, settings: dict) -> tuple[bytes, str]:
    """BYOK OpenAI-compatible speech leg (car 4c). Raises RuntimeError with
    an honest reason — the caller answers 503; there is NO edge fallback."""
    base, ok, key = _tts_byok_cfg(settings)
    if not base:
        raise RuntimeError("byok tts: no base URL (set AIDOG_TTS_URL or "
                           "media.tts_base_url in settings)")
    if not ok:
        raise RuntimeError("byok tts: no credential slot for a remote endpoint "
                           "(TTS_API_KEY in secrets.env)")
    med = settings.get("media") if isinstance(settings.get("media"), dict) else {}
    model = str(med.get("tts_model") or "").strip() or "tts-1"
    payload = json.dumps({"model": model, "voice": BYOK_VOICE,
                          "input": text}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + "/audio/speech", data=payload,
                                 method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=SAY_TIMEOUT) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            data = resp.read(BYOK_MAX_AUDIO + 1)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read(512).decode("utf-8", "replace").strip()
        except OSError:
            pass
        raise RuntimeError(f"byok tts upstream HTTP {e.code}: "
                           f"{sanitize_event_text(detail)[:120]}") from None
    except Exception as e:  # noqa: BLE001 — DNS/timeout/refused, honest type only
        raise RuntimeError(f"byok tts unreachable: {type(e).__name__}") from None
    if not data or not ctype.startswith("audio/"):
        raise RuntimeError(f"byok tts returned {ctype or 'no content-type'} "
                           "(expected audio)")
    if len(data) > BYOK_MAX_AUDIO:
        raise RuntimeError(f"byok tts response too large (max {BYOK_MAX_AUDIO} bytes)")
    return data, ctype


# ----------------------------------------------------------------- version ---
# Owner decree (2026-10-17): we cut every release; the console notifies the
# user when origin's manifest is newer. Channel: private GitHub repo now,
# public after mycore — deploy/aidog_update.py derives it from the git remote.

_update_mod = None
_version_lock = threading.Lock()
_version_cache: dict = {"ts": 0.0, "data": None}
VERSION_TTL = 300.0


def _load_update_mod():
    global _update_mod
    if _update_mod is None:
        spec = importlib.util.spec_from_file_location(
            "aidog_update", REPO / "deploy/aidog_update.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _update_mod = mod
    return _update_mod


def collect_version() -> dict:
    now = time.time()
    with _version_lock:
        if _version_cache["data"] is not None and now - _version_cache["ts"] < (
                30.0 if "error" in _version_cache["data"] else VERSION_TTL):
            return _version_cache["data"]
        try:
            mod = _load_update_mod()
            installed = mod.installed_version()
        except Exception as e:  # noqa: BLE001
            data = {"installed": VERSION, "latest": None, "update_available": None,
                    "error": type(e).__name__}
            _version_cache["ts"], _version_cache["data"] = now, data
            return data
        data = {"installed": installed, "latest": None, "update_available": None,
                "date": None, "notes": None}
        try:
            remote = mod.fetch_manifest()
            latest = str(remote.get("version") or "0.0.0")
            data = {"installed": installed, "latest": latest,
                    "update_available": mod.newer(latest, installed),
                    "date": remote.get("date"), "notes": remote.get("notes")}
        except Exception as e:  # noqa: BLE001
            data["error"] = type(e).__name__  # offline/honest: update state unknown
        _version_cache["ts"], _version_cache["data"] = now, data
        return data


# ------------------------------------------------------------ console chat --
# One console-owned session file, same state root as the TG service (numeric
# file names there belong to chat_ids; "web" cannot collide).

def chat_sessions_dir() -> Path:
    return mycore_state_dir() / "sessions"


def chat_session_path() -> Path:
    return chat_sessions_dir() / f"{CHAT_SESSION_NAME}.jsonl"


def chat_history(limit: int = 200) -> dict:
    """Read-only session view for GET /api/chat/history: user/assistant turns
    only (system constitution and tool payloads never leave the server),
    content capped at CHAT_HISTORY_CAP. Loopback read, no bearer needed."""
    sess = Session(chat_session_path())
    out = []
    for m in sess.load()[-limit:]:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        out.append({"role": role, "content": content[:CHAT_HISTORY_CAP]})
    return {"messages": out, "count": len(out)}


def chat_budget() -> int | None:
    """Compaction budget for a console turn: settings mycore.budget when it is
    a positive int (bool excluded), else the cli default — same rule as the
    TG service, so both channels compact at the same threshold."""
    raw = (load_settings().get("mycore") or {}).get("budget")
    if isinstance(raw, bool):
        raw = None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        return int(raw) if int(raw) > 0 else None
    return cli.DEFAULT_BUDGET_CHARS


def clear_chat_session() -> bool:
    """Archive the console session (POST /api/chat {action:'clear'}).
    Returns True when something was rotated."""
    src = chat_session_path()
    if not src.is_file():
        return False
    arch = chat_sessions_dir() / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    dst = arch / f"{CHAT_SESSION_NAME}.{stamp}.jsonl"
    n = 0
    while dst.exists():
        n += 1
        dst = arch / f"{CHAT_SESSION_NAME}.{stamp}.{n}.jsonl"
    shutil.move(str(src), str(dst))
    return True


# ------------------------------------------------------- webhook inbound ---
# The console answers POST /api/channels/webhook synchronously: one
# in-process turn on a webhook-OWNED session (never the console "web"
# session), no SSE — the HTTP response body itself carries the reply.

def _webhook_session_path(sender: str) -> Path:
    """Session file for the logical key webhook:<sender|default>. ':' is not
    a portable file-name character, so it maps to '_' on disk."""
    key = f"webhook:{sender or 'default'}"
    stem = re.sub(r"[^A-Za-z0-9._-]", "_", key)
    return chat_sessions_dir() / f"{stem}.jsonl"


def _run_webhook_turn(text: str, sender: str):
    """Drive one turn synchronously on the webhook session and return the
    TurnResult (reply text through res.text). Model/tool failures are an
    honest stop, never an exception; a failed turn persists nothing so the
    caller can retry without a poisoned session."""
    cfg = cli.resolve_model_config()
    path = _webhook_session_path(sender)
    sess = Session(path)
    history = sess.load()
    if not history and not sess.meta():
        sess = Session.create(path, meta={"model": cfg.model})
        history = []
    history = [m for m in history if m.get("role") != "system"]

    stack = build_system_stack(context_dirs=resolve_context_dirs())
    messages = stack + history
    pre_ids = {id(m) for m in messages}
    messages.append({"role": "user", "content": text})

    res = run_turn(messages, ChatClient(cfg).chat, build_registry(disabled=resolve_disabled_tools()),
                   budget_chars=chat_budget())
    if res.stop == "model_error":
        return res  # nothing persisted: the session stays retry-clean
    delta = [m for m in messages
             if id(m) not in pre_ids and m.get("role") != "system"]
    try:
        sess.append_many(delta)
    except OSError as e:  # noqa: BLE001 — reply stays honest, log only the type
        sys.stderr.write(f"webhook session append failed err={type(e).__name__}\n")
    return res


def collect_channels() -> dict:
    """GET /api/channels — adapter status rows, honestly degraded to an empty
    list plus a note when the channel module is not importable (still in
    development) or violates its contract. Never fabricates rows."""
    try:
        from mycore import channels as _channels
    except Exception as e:  # noqa: BLE001 — module may not exist yet
        sys.stderr.write(f"channels status unavailable err={type(e).__name__}\n")
        return {"channels": [], "note": "channel module unavailable"}
    try:
        rows = _channels.status(load_settings(), REPO)
    except Exception as e:  # noqa: BLE001 — status must never take the console down
        sys.stderr.write(f"channels status failed err={type(e).__name__}\n")
        return {"channels": [], "note": "channel status unavailable"}
    if not isinstance(rows, list):
        return {"channels": [], "note": "channel status unavailable"}
    return {"channels": rows}


# Local fallback route table for POST /api/channels/send validation, used only
# when mycore.channels is not importable (documented route order; the module's
# CHANNELS tuple is authoritative whenever it loads).
_SEND_ROUTES_FALLBACK = ("discord", "slack", "email", "webhook",
                         "dingtalk", "feishu", "wechat", "whatsapp", "signal")


def channel_routes() -> tuple[str, ...]:
    """Known channel routes — mycore.channels.CHANNELS when importable,
    else the local fallback so validation still fails closed with an honest
    list of names."""
    try:
        from mycore import channels as _channels
        routes = tuple(_channels.CHANNELS)
        if routes:
            return routes
    except Exception:  # noqa: BLE001 — fallback keeps validation honest
        pass
    return _SEND_ROUTES_FALLBACK


# ------------------------------------------------- car 4a-W /v1 + catalog --


def _approx_tokens(chars: int) -> int:
    """Declared approximate token count = ceil(chars/4). We run no tokenizer:
    this is a documented heuristic (docs/features/openai_api_server.md),
    never a claim of exactness."""
    return (chars + 3) // 4 if chars > 0 else 0


def _usage_block(prompt_chars: int, completion_chars: int) -> dict:
    """OpenAI `usage` built from the chars/4 approximation (both sides);
    total is always the exact sum of the two reported integers."""
    p = _approx_tokens(prompt_chars)
    c = _approx_tokens(completion_chars)
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


def _openai_content_text(content) -> str | None:
    """OpenAI content value -> plain text: a string passes; an array of
    {type:'text', text} parts is concatenated; anything else (multimodal
    parts) is not representable on this path (None)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text") for p in content
                 if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "".join(parts) if parts else None
    return None


def _fold_openai_messages(messages) -> tuple[str, list[dict], str]:
    """Fold a request `messages` array into (client_system, history, input).
    Rules (feature doc):
      * the array must be non-empty and END with a user message carrying
        text — that message is this turn's input;
      * client "system" messages concatenate into one supplementary line
        appended AFTER our own system stack;
      * earlier user/assistant messages become request history, in order;
      * other roles (tool/function) and unrepresentable contents are dropped
        — v1 carries no client-side tool transcripts.
    ValueError messages are vetted: they never echo request content."""
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    for m in messages:
        if not isinstance(m, dict) or not isinstance(m.get("role"), str):
            raise ValueError("each message must be an object with a string role")
    last = messages[-1]
    if last.get("role") != "user":
        raise ValueError("messages must end with a user message")
    text = _openai_content_text(last.get("content"))
    if text is None or not text.strip():
        raise ValueError("the final user message must carry non-empty text")
    sys_parts: list[str] = []
    history: list[dict] = []
    for m in messages[:-1]:
        role = m.get("role")
        body = _openai_content_text(m.get("content"))
        if not body:
            continue
        if role == "system":
            if body.strip():
                sys_parts.append(body.strip())
        elif role in ("user", "assistant"):
            history.append({"role": role, "content": body})
    return "\n\n".join(sys_parts), history, text.strip()


def collect_capabilities() -> dict:
    """GET /api/capabilities — read-only capability catalog (same no-auth
    contract as /api/status). Every field is a name/count/boolean derived
    from config presence: no credential value, no token-bearing URL, nothing
    from secrets.env ever serializes here (the smoke asserts it)."""
    settings = load_settings()
    try:
        cfg = cli.resolve_model_config()
        model, upstreams_ok = cfg.model, bool(getattr(cfg, "upstreams", ()))
    except Exception as e:  # noqa: BLE001 — catalog must not fail the console
        sys.stderr.write(f"capabilities model config failed err={type(e).__name__}\n")
        model, upstreams_ok = None, False
    # MCP: server NAMES + count only (config carries urls/env-slot refs —
    # the parse is ours; the catalog never echoes the raw config).
    mcp_block: dict = {"count": 0, "names": []}
    try:
        servers = mcp.parse_config(mcp.raw_config(REPO))
        mcp_block = {"count": len(servers), "names": [s.name for s in servers]}
    except ValueError:
        mcp_block["count"] = 0
        mcp_block["names"] = []
        mcp_block["note"] = "mcp config invalid"  # honest, reason-free
    # Tools: names + descriptions from the live registry schema.
    reg = None
    try:
        reg = build_registry(disabled=resolve_disabled_tools())
        fns = [f.get("function", {}) for f in reg.schema()]
        tools = [{"name": f.get("name", ""), "description": f.get("description", "")}
                 for f in fns if isinstance(f, dict)]
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities tools failed err={type(e).__name__}\n")
        tools = []
    # Skills: frontmatter records (read, never executed) — same source as
    # the console gallery.
    skills = [{"name": s.get("name", ""), "description": s.get("desc", "")}
              for s in collect_skills().get("skills", [])
              if isinstance(s, dict)]
    # Channels: enabled/configured NAME lists only — configured() consults
    # slot presence; no slot value is read into this payload.
    rows = collect_channels().get("channels")
    rows = rows if isinstance(rows, list) else []
    channels = {
        "all": list(channel_routes()),
        "enabled": [r.get("name") for r in rows
                    if isinstance(r, dict) and r.get("enabled")],
        "configured": [r.get("name") for r in rows
                       if isinstance(r, dict) and r.get("configured")],
    }
    # Hooks: configured events from the settings hooks block (absent block =
    # empty list — honest before the event-hooks car lands).
    hooks = settings.get("hooks")
    events = (sorted(k for k, v in hooks.items()
                     if isinstance(v, list) and v)
              if isinstance(hooks, dict) else [])
    # Cron: registry size from the scheduler state file (absent/corrupt = 0).
    try:
        from mycore import scheduler as _scheduler
        jobs = len(_scheduler.load_jobs(_scheduler.default_jobs_path()))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities cron failed err={type(e).__name__}\n")
        jobs = 0
    # Frontend coverage 1.3: env-var NAMES exported into this process (names
    # only — a value may carry credentials) + the known-tool catalog the
    # tools_off checkboxes are rendered from.
    env_overrides = [n for n in ENV_OVERRIDE_VARS
                     if os.environ.get(n, "").strip()]
    known_tools = sorted(KNOWN_TOOL_NAMES)
    # Browser automation (car 4b): availability probe only — no launch, and
    # the six tools register in `tools` above IFF this returns True.
    try:
        from mycore import browser as _browser
        browser = {"available": _browser.available()}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities browser failed err={type(e).__name__}\n")
        browser = {"available": False, "note": "browser probe failed"}
    # Terminal backend (car 4b): parsed KIND of settings mycore.shell only —
    # the raw spec (user@host, identity path) never serializes here.
    try:
        shell_spec = (settings.get("mycore") or {}).get("shell") or ""
        backend = terminal.parse(str(shell_spec)).kind
        terminal_blk = {"backend": backend, "spec_set": bool(str(shell_spec).strip())}
    except ValueError:
        terminal_blk = {"backend": None, "spec_set": True,
                        "note": "mycore.shell invalid"}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities terminal failed err={type(e).__name__}\n")
        terminal_blk = {"backend": None, "spec_set": False,
                        "note": "terminal probe failed"}
    # Zero-key onboarding (car 4b): local model server detection, names only.
    # Probes are loopback with a 0.5s cap each; userinfo (if an operator ever
    # put credentials in an override URL) is stripped before serializing.
    try:
        from mycore import localmodels as _localmodels
        detected = []
        for entry in _localmodels.probe():
            url = str(entry.get("base_url") or "")
            parts = urllib.parse.urlsplit(url)
            if parts.username or parts.password:
                host = parts.hostname or ""
                netloc = (f"[{host}]:{parts.port}" if ":" in host else
                          f"{host}:{parts.port}" if parts.port else host)
                url = urllib.parse.urlunsplit(
                    (parts.scheme, netloc, parts.path, parts.query, ""))
            detected.append({"kind": str(entry.get("kind") or "local"),
                             "base_url": url,
                             "model": str(entry.get("model") or "")})
        local_models = {"count": len(detected), "detected": detected}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"capabilities local_models failed err={type(e).__name__}\n")
        local_models = {"count": 0, "detected": [], "note": "probe failed"}
    # W1/W2 web3: exchange block — venue rows exactly as mycore.exchanges
    # returns them (names/kinds/booleans/sentences only, never a slot
    # value), the exchange_* tools present in the live registry, and the
    # registry's honest note when the account tools are withheld. W2 flips
    # the top-level promise from read-only to the REAL surface: three CEX
    # venues carry orders, and only through prepare -> console approval ->
    # execute; withdrawals and on-chain transfers do not exist here.
    # Fail-soft like every other probe: a broken module degrades to an empty
    # block + one stderr line, never a 500.
    ex_tools = [str(t.get("name", "")) for t in tools
                if str(t.get("name", "")).startswith("exchange_")]
    order_gate = "prepare -> console approval -> execute"
    try:
        if reg is None:
            raise RuntimeError("registry unavailable")
        from mycore import exchanges as _exchanges
        exchange_blk = {"read_only": False,
                        "orderable": list(_exchanges.ORDERABLE_VENUES),
                        "order_gate": order_gate,
                        "withdrawals": False,
                        "onchain_transfers": False,
                        "venues": list(_exchanges.catalog()),
                        "tools": ex_tools,
                        "note": getattr(reg, "exchange_note", None)}
    except Exception as e:  # noqa: BLE001 — probe must never break the catalog
        sys.stderr.write(f"capabilities exchange probe failed err={type(e).__name__}\n")
        exchange_blk = {"read_only": False, "orderable": [],
                        "order_gate": order_gate, "withdrawals": False,
                        "onchain_transfers": False, "venues": [],
                        "tools": ex_tools, "note": "exchange probe failed"}
    # W2 web3 block: the auto-approval caps plus the plan counters — pure
    # numbers and one date, no credential, no venue data. Fail-soft: same
    # keys with zeroed values + an honest note, never a 500.
    try:
        from mycore import exchanges as _web3
        th = _web3.thresholds(settings)
        summ = _web3.plan_summary()
        web3_blk = {"auto_usd_per_tx": float(th.get("per_tx") or 0.0),
                    "auto_usd_daily": float(th.get("daily") or 0.0),
                    "pending_plans": int(summ.get("pending_plans") or 0),
                    "approved_plans": int(summ.get("approved_plans") or 0),
                    "spent_today_usd": float(summ.get("spent_today_usd") or 0.0),
                    "ledger_day": str(summ.get("ledger_day") or "")}
    except Exception as e:  # noqa: BLE001 — probe must never break the catalog
        sys.stderr.write(f"capabilities web3 probe failed err={type(e).__name__}\n")
        web3_blk = {"auto_usd_per_tx": 0.0, "auto_usd_daily": 0.0,
                    "pending_plans": 0, "approved_plans": 0,
                    "spent_today_usd": 0.0, "ledger_day": "",
                    "note": "web3 probe failed"}
    return {
        "version": VERSION,
        "mycore": {"model": model, "upstreams_configured": upstreams_ok,
                   "mcp_servers": mcp_block,
                   "known_tools": known_tools},
        "env_overrides": env_overrides,
        "tools": tools,
        "skills": skills,
        "channels": channels,
        "hooks": {"events": events, "count": len(events)},
        "cron": {"jobs": jobs},
        "browser": browser,
        "terminal": terminal_blk,
        "local_models": local_models,
        # car 4c media face: stt/image/tts/connectors — booleans and names only
        "media": media_capability(settings),
        # W1/W2 web3: 9 venues (3 orderable behind the approval gate) +
        # live exchange_* tool names + the fail-closed auto-approval caps
        "exchange": exchange_blk,
        "web3": web3_blk,
    }



def collect_cron() -> dict:
    """GET /api/cron — read-only view of the natural-language cron registry
    (frontend coverage 1.4). Fail-soft by contract: an absent, unreadable or
    corrupt state file answers {"jobs": [], "count": 0}, never a 500, and
    nothing here mutates the registry (load_jobs hands back fresh dicts).

    Rows are built from a fixed field whitelist — the scheduler's own display
    fields (id/spec/schedule/text/origin/due marks) — so no filesystem path
    can enter this payload by construction."""
    try:
        from mycore import scheduler as _scheduler
        jobs = _scheduler.load_jobs(_scheduler.default_jobs_path())
        now = time.time()
        rows: list[dict] = []
        for job in jobs:
            rows.append({
                "id": str(job.get("id", "")),
                "type": str(job.get("type", "")),
                "spec": _scheduler.describe(job),
                "text": str(job.get("text", "")),
                "kind": str(job.get("kind", "")),
                "source": str(job.get("source", "")),
                "origin": job.get("origin")
                          if isinstance(job.get("origin"), (int, str)) else None,
                "created_at": job.get("created_at"),
                "last_fired": job.get("last_fired"),
                "done": bool(job.get("done")),
                "due": bool(_scheduler.is_due(job, now)),
                "next": _scheduler.fmt_beijing(_scheduler.next_fire(job, now)),
            })
        return {"jobs": rows, "count": len(rows)}
    except Exception as e:  # noqa: BLE001 — catalog must not break the console
        sys.stderr.write(f"cron list failed err={type(e).__name__}\n")
        return {"jobs": [], "count": 0}


# ----------------------------------------------------------------- server ----

MIME = {
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "AiDogConsole/0.3"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _locale_pack(self, name: str) -> None:
        """GET /static/i18n/<lang>.json — packs come from overlay/locales/
        (single source of truth), NOT from a static-file copy: the request is
        resolved through the loader's discovered language set, so an unknown
        or malformed code is a 404 (fail-closed) and no path ever escapes
        into the filesystem."""
        code = name[:-5] if name.endswith(".json") else ""
        if not code or not i18n.has_lang(code):
            return self._json({"error": "not found"}, 404)
        return self._json(i18n.pack(code))

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            return self._json({"status": "ok", "version": VERSION, "ts": time.time()})
        if path == "/api/status":
            return self._json(collect_status())
        if path == "/api/config":
            return self._json(read_config_bits())
        if path == "/api/settings":
            return self._json(load_settings())
        if path == "/api/usage":
            return self._json(collect_usage(load_settings()))
        if path == "/api/events":
            return self._json(collect_events())
        if path == "/api/evolution":
            return self._json(evolution.status_summary())
        if path == "/api/chat/history":
            return self._json(chat_history())
        if path == "/api/version":
            return self._json(collect_version())
        if path == "/api/skills":
            return self._json(collect_skills())
        if path == "/api/voice":
            return self._json(collect_voice())
        if path == "/api/channels":
            return self._json(collect_channels())
        if path == "/v1/models":
            return self._handle_openai_models()
        if path == "/api/capabilities":
            return self._json(collect_capabilities())
        if path == "/api/secrets":
            return self._handle_secrets_get()
        if path == "/api/cron":
            return self._json(collect_cron())
        if path == "/api/exchange/quote":
            return self._handle_exchange_quote()
        if path == "/api/exchange/plans":
            return self._handle_exchange_plans()
        if path.startswith("/api/media/"):
            return self._serve_media(path[len("/api/media/"):])
        if path.startswith("/static/i18n/"):
            return self._locale_pack(path[len("/static/i18n/"):])
        if path.startswith("/static/"):
            rel = path[len("/static/"):]
            target = (STATIC / rel).resolve()
            if str(target).startswith(str(STATIC.resolve())) and target.is_file():
                return self._send(200, target.read_bytes(), MIME.get(target.suffix, "text/plain; charset=utf-8"))
            return self._json({"error": "not found"}, 404)
        return self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/api/settings":
            return self._handle_settings_write()
        if path == "/api/secrets":
            return self._handle_secrets_write()
        if path == "/api/say":
            return self._handle_say()
        if path == "/api/stt":
            return self._handle_stt()
        if path == "/api/image":
            return self._handle_image()
        if path == "/api/learn":
            return self._handle_learn()
        if path == "/api/evolution":
            return self._handle_evolution()
        if path == "/api/chat":
            return self._handle_chat()
        if path == "/api/channels/webhook":
            return self._handle_channel_webhook()
        if path == "/api/channels/send":
            return self._handle_channel_send()
        if path == "/v1/chat/completions":
            return self._handle_openai_chat()
        if path.startswith("/api/exchange/plans/"):
            return self._handle_exchange_plan_decide(
                path[len("/api/exchange/plans/"):])
        return self._json({"error": "not found"}, 404)

    # ------------------------------------------ credential-slot endpoints ---
    def _secrets_authed(self) -> bool:
        """Optional Bearer WEB_TOKEN check for GET /api/secrets: lengths are
        revealed only to the valid token. Absent/invalid token omits them and
        answers 200 — presence stays readable like the rest of the catalog
        (no 401). The token value itself never appears in any output."""
        token = read_env_slot("WEB_TOKEN")
        if not token:
            return False
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        return bool(provided) and hmac.compare_digest(provided.encode(),
                                                       token.encode())

    @staticmethod
    def _secret_row(name: str, group: str, authed: bool,
                    writable: bool = True) -> dict:
        """One catalog row: name/group/presence (+ length only for a valid
        token). The value is read for presence and immediately dropped — it
        never leaves this frame."""
        val = read_env_slot(name)
        present = val is not None
        return {"name": name, "group": group, "set": present,
                "length": (len(val) if present else 0) if authed else None,
                "writable": writable}

    def _handle_secrets_get(self) -> None:
        """GET /api/secrets — the console credential-slot catalog (loopback,
        optional auth). Slot VALUES are never present in this payload."""
        authed = self._secrets_authed()
        rows = [self._secret_row(name, group, authed)
                for group, names in SECRET_GROUPS.items()
                for name in names]
        # WEB_TOKEN: presence only — the gate never rewrites itself.
        token = read_env_slot("WEB_TOKEN")
        rows.append({"name": "WEB_TOKEN", "group": "core",
                     "set": token is not None, "length": None,
                     "writable": False})
        return self._json({"slots": rows, "writable": bool(token)})

    def _handle_secrets_write(self) -> None:
        """POST /api/secrets — set/clear one allowlisted slot.

        Gate order: Bearer WEB_TOKEN -> body cap (MAX_BODY via
        _read_json_body) -> name allowlist -> value rules. Nothing is
        written before every check passes. The VALUE never appears in the
        response, the stderr line, or an exception — only name, set, length."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        name = doc.get("name")
        value = doc.get("value")
        if not isinstance(name, str) or not name:
            return self._json({"error": "validation failed",
                               "details": ["name must be a non-empty string"]}, 400)
        group = SECRET_SLOTS.get(name)
        if group is None:
            detail = ("slot is not writable: WEB_TOKEN"
                      if name in SECRET_PRESENCE_ONLY
                      else f"unknown slot: {name[:64]}")
            return self._json({"error": "validation failed",
                               "details": [detail]}, 400)
        if not isinstance(value, str):
            return self._json({"error": "validation failed",
                               "details": ["value must be a string"]}, 400)
        if len(value) > SECRET_VALUE_MAX:
            return self._json({"error": "validation failed",
                               "details": [f"value too long (max {SECRET_VALUE_MAX} chars)"]}, 400)
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            return self._json({"error": "validation failed",
                               "details": ["value must be a single line (no control characters)"]}, 400)
        if value != value.strip():
            # a value the own-line reader could not round-trip is refused
            # rather than silently stored shorter than what was sent.
            return self._json({"error": "validation failed",
                               "details": ["value must not start or end with whitespace"]}, 400)
        try:
            changed = write_env_slot(name, value)
        except OSError as e:  # noqa: BLE001 — type name only, never the value
            return self._json({"error": f"persist failed: {type(e).__name__}"}, 500)
        sys.stderr.write(f"secrets slot updated name={name} "
                         f"set={bool(value)} len={len(value)}\n")
        return self._json({"ok": True, "changed": changed,
                           "slot": {"name": name, "group": group,
                                    "set": bool(value),
                                    "length": len(value)}})

    # ------------------------------------------------------ evolution legs ---
    def _require_token(self) -> bool:
        """Bearer WEB_TOKEN gate (same contract as the settings write). The
        token value never appears in any response, log line, or exception."""
        token = read_env_slot("WEB_TOKEN")
        if not token:
            self._json({"error": "WEB_TOKEN not configured in secrets.env"}, 503)
            return False
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if not provided or not hmac.compare_digest(provided.encode(),
                                                   token.encode()):
            self._json({"error": "unauthorized"}, 401)
            return False
        return True

    def _read_json_body(self) -> dict | None:
        """Length/JSON/dict validation with responses on failure (None = a
        response was already sent)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "bad content-length"}, 400)
            return None
        if length <= 0:
            self._json({"error": "empty body"}, 400)
            return None
        if length > MAX_BODY:
            self._json({"error": f"body too large (max {MAX_BODY} bytes)"}, 413)
            return None
        try:
            doc = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._json({"error": "invalid JSON body"}, 400)
            return None
        if not isinstance(doc, dict):
            self._json({"error": "body must be a JSON object"}, 400)
            return None
        return doc

    @staticmethod
    def _repo_rel(path: object) -> str:
        p = Path(str(path))
        try:
            return p.resolve().relative_to(REPO.resolve()).as_posix()
        except (OSError, ValueError):
            return p.name

    def _handle_learn(self) -> None:
        """Console leg of /learn: same gate (lint + skill_route pre-screen),
        same artifact rules. Shape errors answer 400 before any write; lint
        rejects answer 400 with rule ids and write nothing."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        kind = doc.get("kind")
        if kind == "memory":
            text = doc.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._json({"error": "validation failed",
                                   "details": ["text is required (kind=memory)"]}, 400)
            res = evolution.learn_memory(text)
        elif kind == "skill":
            fields = {k: doc.get(k) for k in ("name", "description", "body")}
            bad = [k for k, v in fields.items()
                   if not isinstance(v, str) or not v.strip()]
            if bad:
                return self._json({"error": "validation failed",
                                   "details": ["missing or empty: "
                                               + ", ".join(sorted(bad))]}, 400)
            res = evolution.learn_skill(fields["name"].strip(),
                                        fields["description"].strip(),
                                        fields["body"].strip())
        else:
            return self._json({"error": "validation failed",
                               "details": ["kind must be 'skill' or 'memory'"]}, 400)
        if not res.ok:
            sys.stderr.write(f"evolution learn rejected kind={kind} "
                             f"rules={','.join(res.errors)}\n")
            return self._json({"error": "lint rejected",
                               "details": res.errors}, 400)
        sys.stderr.write(f"evolution learn kind={kind} "
                         f"path={self._repo_rel(res.path)}\n")
        return self._json({"ok": True, "kind": kind,
                           "path": self._repo_rel(res.path),
                           "prescreen": res.prescreen})

    def _handle_evolution(self) -> None:
        """Console leg of /reflect (explicit trigger; the window key is
        marked done exactly like the cron leg)."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        if doc.get("action", "reflect") != "reflect":
            return self._json({"error": "validation failed",
                               "details": ["action must be 'reflect'"]}, 400)
        period = doc.get("period", "daily")
        if period not in evolution.PERIODS:
            return self._json({"error": "validation failed",
                               "details": ["period must be one of: "
                                           + ",".join(evolution.PERIODS)]}, 400)
        try:
            res = evolution.reflect(period)
        except (OSError, ValueError) as e:
            sys.stderr.write(f"evolution reflect failed period={period} "
                             f"err={type(e).__name__}\n")
            return self._json({"error": "reflect failed"}, 500)
        sys.stderr.write(f"evolution reflect period={period} "
                         f"created={int(res.created)} "
                         f"path={self._repo_rel(res.path)}\n")
        return self._json({"ok": True, "period": period,
                           "created": res.created,
                           "path": self._repo_rel(res.path),
                           "facts": {"skills": len(res.facts.get("skills") or []),
                                     "entries": sum((res.facts.get("entries")
                                                     or {}).values()),
                                     "commits": (res.facts.get("git") or {}) \
                                         .get("count")}})

    # ---------------------------------------------------------- console chat -
    def _sse_start(self) -> None:
        """Switch the response into an SSE stream (HTTP/1.0 handler: no
        Content-Length, so the browser reader runs until close_connection).
        Called only after validation has passed — 401/400 stay plain JSON."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def _sse(self, event: str, data: dict) -> bool:
        """Write one SSE frame. False = the client went away (stop the turn
        observer cleanly; the model call itself still finishes server-side)."""
        frame = (f"event: {event}\n"
                 f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
        try:
            self.wfile.write(frame.encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _handle_chat(self) -> None:
        """POST /api/chat — one streamed agent turn (same loop/stack/session
        contract as the TG pipeline), or {action:'clear'} to archive.

        Events: meta {model} · delta {text} · tool {name, ok} ·
                progress {server, tool, message, progress} (MCP progress,
                message truncated to 200 chars — never long free text) ·
                done {text, stop, iterations, tool_calls} · error {error}.
        `done` always carries the authoritative final text — deltas are only
        a live preview the browser replaces wholesale.
        """
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        if doc.get("action") == "clear":
            return self._json({"ok": True, "archived": clear_chat_session()})
        text = doc.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "validation failed",
                               "details": ["text is required"]}, 400)
        text = text.strip()
        if len(text) > CHAT_MAX_CHARS:
            return self._json({"error": "validation failed",
                               "details": [f"text too long (max {CHAT_MAX_CHARS} chars)"]},
                              400)

        self._sse_start()
        try:
            ok = self._run_chat_turn(text)
        except Exception as e:  # noqa: BLE001 — never leak internals to the stream
            sys.stderr.write(f"chat turn failed err={type(e).__name__}\n")
            self._sse("error", {"error": "internal"})
            return
        if not ok:
            return

    def _run_chat_turn(self, text: str) -> bool:
        """Drive one turn over the console session and stream its events.
        Returns False when the client disconnected (observers must stop)."""
        cfg = cli.resolve_model_config()
        path = chat_session_path()
        sess = Session(path)
        history = sess.load()
        if not history and not sess.meta():
            sess = Session.create(path, meta={"model": cfg.model})
            history = []
        history = [m for m in history if m.get("role") != "system"]

        stack = build_system_stack(context_dirs=resolve_context_dirs())
        messages = stack + history
        pre_ids = {id(m) for m in messages}
        messages.append({"role": "user", "content": text})

        alive = [True]
        if not self._sse("meta", {"model": cfg.model}):
            return False

        def on_event(kind: str, data: dict) -> None:
            # Names only — tool ARGUMENTS may carry machine paths, never sent.
            if kind == "tool_call":
                alive[0] = self._sse("tool", {"name": data.get("name", "?"),
                                              "ok": None})
            elif kind == "tool_result":
                alive[0] = self._sse("tool", {"name": data.get("name", "?"),
                                              "ok": bool(data.get("ok"))})

        def on_delta(piece: str) -> None:
            if alive[0]:
                alive[0] = self._sse("delta", {"text": piece})

        def on_mcp_progress(payload: dict) -> None:
            """MCP progress -> one bounded SSE frame (car 4a-M3). Truncate
            again at this edge: a sink must never forward long free text."""
            if not alive[0]:
                return
            prog = payload.get("progress")
            alive[0] = self._sse("progress", {
                "server": str(payload.get("server") or "")[:64],
                "tool": str(payload.get("tool") or "")[:64],
                "message": str(payload.get("message") or "")[:200],
                "progress": (prog if isinstance(prog, (int, float))
                             and not isinstance(prog, bool) else None)})

        registry = build_registry(disabled=resolve_disabled_tools())
        registry.bind_progress(on_mcp_progress)
        chat_fn = ChatClient(cfg).chat
        res = run_turn(messages, chat_fn, registry,
                       on_event=on_event if alive[0] else None,
                       budget_chars=chat_budget(), on_delta=on_delta)

        delta = [m for m in messages
                 if id(m) not in pre_ids and m.get("role") != "system"]
        try:
            sess.append_many(delta)
        except OSError as e:  # noqa: BLE001 — stream stays honest, log only
            sys.stderr.write(f"chat session append failed err={type(e).__name__}\n")
        if not alive[0]:
            return False
        return self._sse("done", {"text": res.text, "stop": res.stop,
                                  "iterations": res.iterations,
                                  "tool_calls": res.tool_calls})

    # ------------------------------------------------- OpenAI v1 gateway ---
    def _openai_error(self, code: int, message: str, type_: str) -> None:
        """OpenAI-shaped error body: {error:{message,type,code}}. Callers pass
        vetted messages only — a secrets slot value never reaches one."""
        self._json({"error": {"message": message, "type": type_, "code": None}},
                   code)

    def _require_openai_token(self) -> bool:
        """Bearer gate for /v1/* on the DEDICATED slot OPENAI_API_TOKEN (own
        secrets.env line, same contract as CHANNEL_WEBHOOK_SECRET): missing
        slot = 503 naming the slot, wrong/absent bearer = 401, the value is
        never echoed to any response, log line, or exception."""
        token = read_env_slot("OPENAI_API_TOKEN")
        if not token:
            self._openai_error(503, "OPENAI_API_TOKEN not configured in secrets.env",
                               "server_error")
            return False
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if not provided or not hmac.compare_digest(provided.encode(),
                                                   token.encode()):
            self._openai_error(401, "unauthorized", "invalid_request_error")
            return False
        return True

    def _read_openai_body(self) -> dict | None:
        """Body read with OpenAI-shaped refusals (None = already answered)."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._openai_error(400, "invalid JSON body", "invalid_request_error")
            return None
        if length <= 0:
            self._openai_error(400, "invalid JSON body", "invalid_request_error")
            return None
        if length > MAX_BODY:
            self._openai_error(413, f"body too large (max {MAX_BODY} bytes)",
                               "invalid_request_error")
            return None
        try:
            doc = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._openai_error(400, "invalid JSON body", "invalid_request_error")
            return None
        if not isinstance(doc, dict):
            self._openai_error(400, "body must be a JSON object",
                               "invalid_request_error")
            return None
        return doc

    @staticmethod
    def _openai_prepare(client_system: str, history: list[dict], text: str):
        """Build the one-turn message list: OUR system stack first (same
        build_system_stack as /api/chat), then the client system line, the
        folded request history, and this turn's user input. Stateless: no
        Session file is opened or written on this path."""
        cfg = cli.resolve_model_config()
        messages = build_system_stack(context_dirs=resolve_context_dirs())
        if client_system:
            messages.append({"role": "system", "content": client_system})
        messages.extend(history)
        messages.append({"role": "user", "content": text})
        prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
        return cfg, messages, prompt_chars

    @staticmethod
    def _openai_run(cfg, messages, on_delta=None):
        """One turn over the shared agent loop — same ChatClient, same tool
        registry, same compaction budget as /api/chat. run_turn never raises
        for model/tool failures (model_error arrives via TurnResult.stop)."""
        return run_turn(messages, ChatClient(cfg).chat, build_registry(disabled=resolve_disabled_tools()),
                        budget_chars=chat_budget(), on_delta=on_delta)

    def _handle_openai_models(self) -> None:
        """GET /v1/models — one entry: the actually configured model. `created`
        is the product build time (VERSION mtime), not a provenance claim
        about any upstream model."""
        if not self._require_openai_token():
            return
        try:
            model = cli.resolve_model_config().model
        except Exception as e:  # noqa: BLE001 — internals stay server-side
            sys.stderr.write(f"v1 models config failed err={type(e).__name__}\n")
            return self._openai_error(502, "internal", "server_error")
        try:
            created = int((REPO / "VERSION").stat().st_mtime)
        except OSError:
            created = 0
        self._json({"object": "list",
                    "data": [{"id": model, "object": "model",
                              "created": created, "owned_by": "local"}]})

    def _handle_openai_chat(self) -> None:
        """POST /v1/chat/completions — OpenAI chat shape over our agent loop.
        Stateless gateway: the request's messages fold into ONE turn (rules in
        _fold_openai_messages) and nothing persists to any Session file.
        Accepted-and-ignored request fields (feature doc): client `tools` are
        never executed (our agent uses its own registry), the `model` id is
        echoed back as the configured model, sampling params are unused.
        Usage numbers are the declared chars/4 approximation — no tokenizer,
        never claimed exact."""
        if not self._require_openai_token():
            return
        doc = self._read_openai_body()
        if doc is None:
            return
        try:
            client_system, history, text = _fold_openai_messages(doc.get("messages"))
        except ValueError as e:
            return self._openai_error(400, str(e), "invalid_request_error")
        if len(text) > CHAT_MAX_CHARS:
            return self._openai_error(400,
                                      f"text too long (max {CHAT_MAX_CHARS} chars)",
                                      "invalid_request_error")
        try:
            cfg, messages, prompt_chars = self._openai_prepare(client_system,
                                                               history, text)
        except Exception as e:  # noqa: BLE001 — internals stay server-side
            sys.stderr.write(f"v1 turn setup failed err={type(e).__name__}\n")
            return self._openai_error(502, "internal", "server_error")
        if doc.get("stream") is True:
            return self._v1_stream(cfg, messages, prompt_chars,
                                   doc.get("stream_options"))
        try:
            res = self._openai_run(cfg, messages)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"v1 turn failed err={type(e).__name__}\n")
            return self._openai_error(502, "internal", "server_error")
        if res.stop == "model_error":
            return self._openai_error(502, res.text or "model call failed",
                                      "server_error")
        created = int(time.time())
        cid = "chatcmpl-" + hashlib.sha1(
            f"{created}:{prompt_chars}".encode()).hexdigest()[:24]
        self._json({
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": cfg.model,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": res.text},
                         "finish_reason": "stop" if res.stop == "final"
                                          else "length"}],
            "usage": _usage_block(prompt_chars, len(res.text)),
        })

    def _v1_data(self, payload: str) -> bool:
        """One OpenAI SSE `data:` frame (no event: lines — that is the /v1
        dialect). False = the client went away."""
        try:
            self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _v1_stream(self, cfg, messages, prompt_chars: int,
                   stream_options) -> None:
        """`\"stream\": true` leg — chat.completion.chunk frames: a role
        chunk, content chunks from the live preview, a finish_reason chunk
        (then an optional choices=[] usage chunk when
        stream_options.include_usage), then `data: [DONE]`. A model failure
        surfaces as one {"error": …} frame — never an exception, never a
        raise through the stream."""
        self._sse_start()
        created = int(time.time())
        cid = "chatcmpl-" + hashlib.sha1(
            f"{created}:{len(messages)}".encode()).hexdigest()[:24]
        include_usage = (isinstance(stream_options, dict)
                         and bool(stream_options.get("include_usage")))
        alive = [True]

        def emit(choices, usage=None) -> bool:
            if not alive[0]:
                return False
            frame = {"id": cid, "object": "chat.completion.chunk",
                     "created": created, "model": cfg.model,
                     "choices": choices}
            if usage is not None:
                frame["usage"] = usage
            alive[0] = self._v1_data(json.dumps(frame, ensure_ascii=False))
            return alive[0]

        emit([{"index": 0, "delta": {"role": "assistant", "content": ""},
               "finish_reason": None}])

        def on_delta(piece: str) -> None:
            if alive[0]:
                emit([{"index": 0, "delta": {"content": piece},
                       "finish_reason": None}])

        try:
            res = self._openai_run(cfg, messages, on_delta=on_delta)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"v1 stream turn failed err={type(e).__name__}\n")
            if alive[0]:
                self._v1_data(json.dumps({"error": {"message": "internal",
                                                     "type": "server_error",
                                                     "code": None}}))
                self._v1_data("[DONE]")
            return
        if not alive[0]:
            return  # client disconnected; the turn just finishes server-side
        if res.stop == "model_error":
            self._v1_data(json.dumps({"error": {
                "message": res.text or "model call failed",
                "type": "server_error", "code": None}}))
            self._v1_data("[DONE]")
            return
        emit([{"index": 0, "delta": {},
               "finish_reason": "stop" if res.stop == "final" else "length"}])
        if include_usage:
            emit([], usage=_usage_block(prompt_chars, len(res.text)))
        self._v1_data("[DONE]")

    def _require_webhook_secret(self) -> bool:
        """Bearer gate for the inbound webhook route: the secret lives in its
        own secrets.env slot (CHANNEL_WEBHOOK_SECRET). Missing slot = honest
        503 naming the slot; the value never appears in any response, log
        line, or exception."""
        secret = read_env_slot("CHANNEL_WEBHOOK_SECRET")
        if not secret:
            self._json({"error": "CHANNEL_WEBHOOK_SECRET not configured in secrets.env"}, 503)
            return False
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if not provided or not hmac.compare_digest(provided.encode(),
                                                   secret.encode()):
            self._json({"error": "unauthorized"}, 401)
            return False
        return True

    def _handle_channel_webhook(self) -> None:
        """POST /api/channels/webhook — inbound webhook leg: Bearer secret,
        then {text, sender?} -> one in-process turn -> {ok, reply}. No SSE;
        the console 'web' session is never touched; the session key is
        webhook:<sender|default>. Failures answer with honest, generic codes
        — never internal detail."""
        if not self._require_webhook_secret():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        text = doc.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "validation failed",
                               "details": ["text is required"]}, 400)
        text = text.strip()
        if len(text) > CHAT_MAX_CHARS:
            return self._json({"error": "validation failed",
                               "details": [f"text too long (max {CHAT_MAX_CHARS} chars)"]},
                              400)
        sender = doc.get("sender", "default")
        if not isinstance(sender, str):
            return self._json({"error": "validation failed",
                               "details": ["sender must be a string"]}, 400)
        sender = sender.strip() or "default"
        if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", sender):
            return self._json({"error": "validation failed",
                               "details": ["sender must be 1..64 chars of [A-Za-z0-9._@-]"]},
                              400)
        try:
            res = _run_webhook_turn(text, sender)
        except Exception as e:  # noqa: BLE001 — internals never reach the caller
            sys.stderr.write(f"webhook turn failed err={type(e).__name__}\n")
            return self._json({"error": "turn failed"}, 500)
        if res.stop == "model_error":
            sys.stderr.write("webhook turn stopped reason=model_error\n")
            return self._json({"error": "turn failed"}, 500)
        return self._json({"ok": True, "reply": res.text})

    def _handle_channel_send(self) -> None:
        """POST /api/channels/send — outbound console trigger: Bearer WEB_TOKEN
        (same gate and 503/401 contract as the settings write), then
        {route, target, text}. Validation answers 400 with details; a disabled
        route or a missing credential slot answers 409 with a reason that
        names the slot/setting key only (never a value); a failed delivery
        answers 502 — the adapter logged the detail server-side, so internals
        never reach the caller."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        details: list[str] = []
        known = channel_routes()
        route = doc.get("route")
        if route is None:
            details.append("route is required")
        elif not isinstance(route, str):
            details.append("route must be a string")
        elif route not in known:
            details.append("route must be one of: " + ",".join(known))
        target = doc.get("target")
        if not isinstance(target, str):
            details.append("target must be a string")
        else:
            if any(ord(c) < 32 or ord(c) == 127 for c in target):
                details.append("target must not contain control characters")
            if not target.strip():
                details.append("target is required")
            elif len(target) > 256:
                details.append("target must be at most 256 chars")
        text = doc.get("text")
        if not isinstance(text, str):
            details.append("text must be a string")
        elif not text.strip():
            details.append("text is required")
        elif len(text) > CHAT_MAX_CHARS:
            details.append(f"text too long (max {CHAT_MAX_CHARS} chars)")
        if details:
            return self._json({"error": "validation failed",
                               "details": details}, 400)
        try:
            from mycore import channels as _channels
            chans = _channels.load_channels(REPO, load_settings())
            chan = chans.get(route)
            if chan is None:
                raise KeyError(route)
        except Exception as e:  # noqa: BLE001 — honest degradation, no internals
            sys.stderr.write(f"channels send unavailable err={type(e).__name__}\n")
            return self._json({"ok": False,
                               "error": "channel module unavailable"}, 503)
        if not chan.enabled:
            return self._json({"ok": False,
                               "error": "route disabled in settings"}, 409)
        try:
            ok, reason = chan.configured()
        except Exception as e:  # noqa: BLE001 — status must not take the console down
            sys.stderr.write(f"channels send config check failed err={type(e).__name__}\n")
            return self._json({"ok": False,
                               "error": "channel config check unavailable"}, 503)
        if not ok:
            # reason names a slot/settings key only — never a value
            return self._json({"ok": False,
                               "error": reason or "route not configured"}, 409)
        try:
            sent = chan.send(target.strip(), text.strip())
        except Exception as e:  # noqa: BLE001 — contract says send never raises
            sys.stderr.write(f"channels send failed err={type(e).__name__}\n")
            sent = False
        if not sent:
            return self._json({"ok": False, "error": "delivery failed"}, 502)
        return self._json({"ok": True})

    def _handle_say(self) -> None:
        """Speech synthesis. No bearer required (loopback console feature),
        but JSON-only + same-origin Origin guard the write path; rate limit
        bounds the online-engine cost. The spoken text never appears in any
        log line here (only status codes are logged by log_message)."""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 415)
        origin = self.headers.get("Origin")
        if origin:
            try:
                host = urllib.parse.urlparse(origin).netloc.split("@")[-1].split(":")[0]
            except ValueError:
                host = ""
            if host not in ("127.0.0.1", "localhost", "::1"):
                return self._json({"error": "cross-origin request refused"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        if length <= 0:
            return self._json({"error": "empty body"}, 400)
        if length > MAX_BODY:
            return self._json({"error": f"body too large (max {MAX_BODY} bytes)"}, 413)
        try:
            doc = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "invalid JSON body"}, 400)
        if not isinstance(doc, dict):
            return self._json({"error": "body must be a JSON object"}, 400)
        text = doc.get("text")
        lang = doc.get("lang", "en")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "text is required"}, 400)
        if lang not in LANGS:
            return self._json({"error": "lang must be one of: " + "|".join(LANGS)}, 400)
        if len(text) > SAY_MAX_CHARS:
            return self._json({"error": f"text too long (max {SAY_MAX_CHARS} chars)"}, 400)
        # engine selection (car 4c): explicit {engine: edge|byok} beats the
        # settings media.tts_engine choice; absent both = "edge" (the original
        # chain, byte-for-byte unchanged). Validation happens before the rate
        # budget — a bad engine must not spend a synthesis slot.
        engine = doc.get("engine", "")
        if engine is None:
            engine = ""
        if not isinstance(engine, str):
            return self._json({"error": "engine must be one of: edge|byok"}, 400)
        say_settings = load_settings()
        say_media = (say_settings.get("media")
                     if isinstance(say_settings.get("media"), dict) else {})
        engine = engine.strip() or str(say_media.get("tts_engine") or "edge")
        if engine not in TTS_ENGINES:
            return self._json({"error": "engine must be one of: "
                               + "|".join(TTS_ENGINES)}, 400)
        text = sanitize_event_text(text)   # redact tokens/bearer/brand + strip
        if not text:
            return self._json({"error": "text empty after sanitization"}, 400)
        # Rate limit sits after validation: rejected requests must not spend
        # the online-engine budget (what we protect is the engine, not parsing).
        if not _say_rate_ok():
            self.send_response(429)
            self.send_header("Retry-After", str(int(SAY_RATE_WINDOW)))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps({"error": "voice rate limit reached",
                               "retry_after_sec": int(SAY_RATE_WINDOW)}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if engine == "byok":
            # Honest failure only: unconfigured/upstream problems answer 503
            # with the real reason — we never pretend byok fell back to edge.
            try:
                audio, say_ctype = _synth_say_byok(text, say_settings)
            except RuntimeError as e:
                return self._json({"error": str(e)[:160]}, 503)
            return self._send(200, audio, say_ctype)
        if not shutil.which("edge-tts"):
            return self._json({"error": "voice engine not installed"}, 503)
        try:
            audio = _synth_say(text, lang)
        except RuntimeError as e:
            return self._json({"error": str(e)}, 502)
        return self._send(200, audio, "audio/mpeg")

    def _handle_stt(self) -> None:
        """POST /api/stt — local speech-to-text (car 4c). Same local
        contract as /api/say: no bearer (loopback console feature),
        loopback-only Origin, JSON nowhere in this path — the body IS the
        audio, read on the independent STT_MAX_BODY budget instead of
        MAX_BODY, and covered by its own sliding rate window. The transcriber
        is imported lazily (a parallel wave may not have landed it yet) and
        every failure answers with the real reason."""
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if not ctype.startswith("audio/"):
            return self._json({"error": "Content-Type must be an audio payload "
                               "(audio/webm, ogg, mpeg, wav, mp4)"}, 415)
        origin = self.headers.get("Origin")
        if origin:
            try:
                host = urllib.parse.urlparse(origin).netloc.split("@")[-1].split(":")[0]
            except ValueError:
                host = ""
            if host not in ("127.0.0.1", "localhost", "::1"):
                return self._json({"error": "cross-origin request refused"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        if length <= 0:
            return self._json({"error": "empty body"}, 400)
        if length > STT_MAX_BODY:
            return self._json({"error": f"audio too large "
                               f"(max {STT_MAX_BODY} bytes)"}, 413)
        data = self.rfile.read(length)
        if not data:
            return self._json({"error": "empty body"}, 400)
        if not _stt_rate_ok():
            self.send_response(429)
            self.send_header("Retry-After", str(int(STT_RATE_WINDOW)))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = json.dumps({"error": "transcription rate limit reached",
                               "retry_after_sec": int(STT_RATE_WINDOW)}).encode()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        stt_settings = load_settings()
        stt_media = (stt_settings.get("media")
                     if isinstance(stt_settings.get("media"), dict) else {})
        model = str(stt_media.get("stt_model") or "base")
        try:
            from mycore import stt as _stt   # lazy: lands with the media wave
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"stt import failed err={type(e).__name__}\n")
            return self._json({"error": "speech-to-text unavailable "
                               "(stt module not loaded)"}, 503)
        try:
            up = bool(_stt.available())
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"stt probe failed err={type(e).__name__}\n")
            return self._json({"error": f"speech-to-text unavailable "
                               f"({type(e).__name__})"}, 503)
        if not up:
            return self._json({"error": "speech-to-text unavailable "
                               "(local model weights not present)"}, 503)
        try:
            res = _stt.transcribe(data, model=model)
        except Exception as e:  # noqa: BLE001
            reason = sanitize_event_text(str(e).strip() or type(e).__name__)[:160]
            # SttError = bad/undecodable audio (422); a missing model or any
            # other failure is server-side (503). Both carry the real reason.
            code = 503 if (type(e).__name__ != "SttError"
                           or "unavailable" in reason.lower()) else 422
            return self._json({"error": reason}, code)
        try:
            text = str(res.get("text") or "")
            lang = str(res.get("language") or "")
            dur = float(res.get("duration") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return self._json({"error": "speech-to-text returned an "
                               "unexpected result"}, 503)
        if dur > STT_MAX_DURATION:
            return self._json({"error": f"audio too long ({dur:.0f}s — "
                               f"limit {STT_MAX_DURATION}s)"}, 422)
        return self._json({"text": text.strip(), "language": lang,
                           "duration": round(max(dur, 0.0), 3)})

    def _handle_image(self) -> None:
        """POST /api/image — image trial panel (car 4c). Bearer WEB_TOKEN;
        the prompt stays in-process (never logged); upstream failures map to
        honest 503 (unconfigured) / 502 (upstream) / 500 (unexpected)."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        prompt = doc.get("prompt")
        size = doc.get("size", "1024x1024")
        if not isinstance(prompt, str) \
                or not (1 <= len(prompt.strip()) <= IMAGE_MAX_PROMPT):
            return self._json({"error": f"prompt must be a string of "
                               f"1..{IMAGE_MAX_PROMPT} chars"}, 400)
        if size is None:
            size = "1024x1024"
        if not isinstance(size, str) or not re.fullmatch(r"\d{2,5}x\d{2,5}",
                                                         size.strip()):
            return self._json({"error": "size must be WxH (e.g. 1024x1024)"}, 400)
        try:
            from mycore import imagegen as _imagegen  # lazy (parallel wave)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"imagegen import failed err={type(e).__name__}\n")
            return self._json({"error": "image generation unavailable "
                               f"({type(e).__name__})"}, 503)
        try:
            ok, reason = _imagegen.configured()
        except Exception as e:  # noqa: BLE001
            return self._json({"error": "image config check unavailable "
                               f"({type(e).__name__})"}, 503)
        if not ok:
            why = sanitize_event_text(str(reason or "") or "not configured")[:160]
            return self._json({"error": why}, 503)
        try:
            paths = _imagegen.generate(prompt.strip(), size=size.strip())
            # Panel images are console-local previews: drop them from the
            # module outbox so they never accumulate here (the TG service
            # drains ITS own process copy; nothing consumes web's one).
            _imagegen.drain_outbox()
        except Exception as e:  # noqa: BLE001
            reason = sanitize_event_text(str(e).strip() or type(e).__name__)[:160]
            code = 502 if type(e).__name__ == "ImageError" else 500
            return self._json({"error": reason}, code)
        images: list[dict] = []
        for p in paths or []:
            name = Path(str(p)).name
            if IMAGE_NAME_RE.match(name):
                images.append({"name": name, "url": "/api/media/" + name})
        return self._json({"images": images})

    def _handle_exchange_quote(self) -> None:
        """GET /api/exchange/quote?venue=<key>&symbol=<sym> — W1 public
        market probe. READ-ONLY public data only: account reads (portfolio /
        orders) never reach HTTP in this car. Answers 200 {venue, symbol,
        lines} where `symbol` echoes the requested pair or the venue's
        default; ExchangeError (unknown venue, bad symbol, dead upstream,
        missing slot) -> 400 {error} with an honest message that names slots
        but never carries a value; its OWN sliding window (10/60s) -> 429."""
        if not _exchange_quote_rate_ok():
            return self._json({"error": "rate limited"}, 429)
        try:
            from mycore import exchanges as _exchanges
        except Exception as e:  # noqa: BLE001 — module absent = honest 503
            sys.stderr.write(f"exchange quote import failed err={type(e).__name__}\n")
            return self._json({"error": "exchange module unavailable"}, 503)
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        venue = (query.get("venue") or [""])[0].strip()
        symbol = (query.get("symbol") or [""])[0].strip() or None
        try:
            text = _exchanges.quote(venue, symbol, timeout=10.0)
        except _exchanges.ExchangeError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001 — never a 500 on a probe
            sys.stderr.write(f"exchange quote failed err={type(e).__name__}\n")
            return self._json({"error": "exchange probe failed"}, 502)
        # reported symbol: the request's, else the venue's default from the
        # catalog (names only — never a credential, never a slot value).
        if not symbol:
            for row in _exchanges.catalog():
                if isinstance(row, dict) and row.get("key") == venue:
                    symbol = str(row.get("symbol") or "")
                    break
        lines = [ln for ln in str(text).splitlines() if ln.strip()]
        return self._json({"venue": venue, "symbol": symbol or "",
                           "lines": lines})

    def _handle_exchange_plans(self) -> None:
        """GET /api/exchange/plans — the stored W2 plans, newest first.
        Read, no token (same contract as every GET): the payload carries
        plan parameters and counters only, never a credential. Fail-soft:
        an unavailable plan store answers 503 {error}, never a 500."""
        try:
            from mycore import exchanges as _exchanges
        except Exception as e:  # noqa: BLE001 — module absent = honest 503
            sys.stderr.write(f"exchange plans import failed err={type(e).__name__}\n")
            return self._json({"error": "plan store unavailable"}, 503)
        try:
            plans = _exchanges.plans_list()
        except Exception as e:  # noqa: BLE001 — store trouble = honest 503
            sys.stderr.write(f"exchange plans list failed err={type(e).__name__}\n")
            return self._json({"error": "plan store unavailable"}, 503)
        return self._json({"plans": plans})

    def _handle_exchange_plan_decide(self, plan_id: str) -> None:
        """POST /api/exchange/plans/<id> — THE approval surface (W2).
        Bearer WEB_TOKEN first (the model has no token and therefore no
        approve path), then {action: approve|reject|cancel}. An unknown
        plan id is a 404, every other ExchangeError a 400 carrying the
        module's honest text verbatim. The token value never appears in a
        response, a log line, or an exception."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if not isinstance(action, str) \
                or action not in ("approve", "reject", "cancel"):
            return self._json(
                {"error": "invalid action",
                 "details": ["action must be one of approve|reject|cancel"]},
                400)
        pid = str(urllib.parse.unquote(plan_id) or "").strip()
        if not pid:
            return self._json({"error": "not found"}, 404)
        try:
            from mycore import exchanges as _exchanges
        except Exception as e:  # noqa: BLE001 — module absent = honest 503
            sys.stderr.write(f"exchange plan decide import failed "
                             f"err={type(e).__name__}\n")
            return self._json({"error": "plan store unavailable"}, 503)
        try:
            plan = _exchanges.plan_decide(pid, action, decided_by="console")
        except _exchanges.ExchangeError as e:
            msg = str(e)
            if msg.startswith("unknown plan_id"):
                return self._json({"error": msg}, 404)
            return self._json({"error": msg}, 400)
        except Exception as e:  # noqa: BLE001 — store trouble, not a 500
            sys.stderr.write(f"exchange plan decide failed err={type(e).__name__}\n")
            return self._json({"error": "plan decision failed"}, 502)
        return self._json({"plan": plan})

    def _serve_media(self, name: str) -> None:
        """GET /api/media/<name> — one generated image, and nothing else:
        the name must match the [A-Za-z0-9_.-] whitelist, resolve to a file
        DIRECTLY inside data/images/ (parent check kills traversal), and carry
        a known image suffix. Anything else is a plain 404."""
        if not IMAGE_NAME_RE.match(name):
            return self._json({"error": "not found"}, 404)
        try:
            root = MEDIA_DIR.resolve()
            target = (root / name).resolve()
            if target.parent != root or not target.is_file():
                return self._json({"error": "not found"}, 404)
            mime = MEDIA_MIME.get(target.suffix.lower())
            if mime is None:
                return self._json({"error": "not found"}, 404)
            data = target.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._send(200, data, mime)

    def _handle_settings_write(self) -> None:
        """Validated, idempotent settings write. The token value never appears
        in any response, log line, or exception message."""
        token = read_env_slot("WEB_TOKEN")
        if not token:
            return self._json({"error": "WEB_TOKEN not configured in secrets.env"}, 503)
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        if not provided or not hmac.compare_digest(provided.encode(), token.encode()):
            return self._json({"error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json({"error": "bad content-length"}, 400)
        if length <= 0:
            return self._json({"error": "empty body"}, 400)
        if length > MAX_BODY:
            return self._json({"error": f"body too large (max {MAX_BODY} bytes)"}, 413)
        try:
            patch = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._json({"error": "invalid JSON body"}, 400)
        clean, errors = validate_settings_patch(patch)
        if errors:
            return self._json({"error": "validation failed", "details": errors}, 400)
        current = load_settings()
        merged = deep_merge(current, clean)
        changed = merged != current
        if changed:
            try:
                atomic_write_json(SETTINGS_FILE, merged)
            except OSError as e:  # noqa: BLE001
                return self._json({"error": f"persist failed: {type(e).__name__}"}, 500)
            sys.stderr.write(f"settings updated keys={','.join(sorted(clean))}\n")
            # display currency / fx / usage paths affect the next usage render
            if {"currency", "fx_rate", "usage"} & set(clean):
                with _usage_lock:
                    _usage_cache["ts"], _usage_cache["data"] = 0.0, None
        return self._json({"ok": True, "changed": changed, "settings": merged})

    def log_message(self, fmt: str, *args) -> None:  # quiet default
        sys.stderr.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))


def main() -> None:
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"I'm AiDog console -> http://{HOST}:{PORT}/  (loopback only, {VERSION})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
