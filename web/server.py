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
  GET  /api/backup/export       -> Bearer WEB_TOKEN (REQUIRED — there is no
                                   anonymous read here); ?include=
                                   secrets,settings,memory (default = all
                                   three) -> {ok, schema, schemaVersion,
                                   created, payload}. THE ONLY route in this
                                   server that returns a credential slot
                                   VALUE (CKX-P1 / D4): WEB_TOKEN is never
                                   included, only allow-listed slots ride,
                                   and neither the payload nor this response
                                   is ever written to a log line
  POST /api/backup/restore      -> Bearer WEB_TOKEN; {schema, schemaVersion,
                                   payload, apply:{secrets,settings,memory}}
                                   -> honest per-section result (200 all
                                   applied · 503 partial, with per-slot /
                                   per-file reasons · 400 envelope shape or
                                   schema/version mismatch — nothing written)
  GET  /api/backup/agent        -> CKX-P2 agent-backup status: node runtime,
                                   passphrase slot PRESENCE, local call meter,
                                   schedule row, MCP switch (names/numbers only
                                   -- never a slot value, never a file path)
  POST /api/backup/agent        -> Bearer; {action: schedule | unschedule |
                                   quota_reset | mcp_on | mcp_off[, spec]}.
                                   schedule/unschedule run through the SAME
                                   scheduler grammar as /api/cron; mcp_on/off
                                   rewrite only the `ckx` entry of mycore.mcp
                                   and leave every other server untouched
  GET  /api/usage               -> model-channel usage/cost aggregation (cached)
  GET  /api/events              -> sanitized recent event stream
  GET  /api/evolution           -> evolution loop status (schedule/state/log tail)
  POST /api/learn               -> Bearer; lint+pre-screen, writes a skill or
                                   memory artifact (evolution loop console leg)
  POST /api/evolution           -> Bearer; {action:"reflect", period} generates
                                   the period skeleton (same core as /reflect)
  POST /api/chat                -> Bearer; {text, model?, image?} streams an
                                   agent turn as SSE (meta/delta/tool/done/error);
                                   {action:"clear"} archives the console session;
                                   optional `model` is whitelist-gated (400 on
                                   an unknown id — the TG /model rule);
                                   optional `image` (X16) is one
                                   data:image/(png|jpeg|webp);base64 URL of at
                                   most 1.5M chars riding THIS turn only (400
                                   bad shape / 413 over the body cap; a
                                   non-multimodal endpoint surfaces an honest
                                   vision-rejection note in `done.text`)
  GET  /api/channels            -> channel adapter status (list of {name,
                                   enabled, configured, ...}; empty list +
                                   "note" when the channel module is absent) and
                                   read-only inbound webhook facts ("webhook":
                                   {url, secret_configured} — never a value)
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
  GET  /api/setup               -> activation checklist (required slots + model
                                   endpoint + service; presence/booleans only,
                                   loopback read, no auth)
  POST /api/service/restart     -> Bearer; {target:"service"} restarts the chat
                                   service tmux session only — never this console
                                   (400 bad target / 409 no session)
  POST /api/update/apply        -> Bearer; queues aidog_update.py apply in a
                                   detached child -> 202 (log: data/update_apply.log)
  POST /api/doctor/run          -> Bearer; runs deploy/aidog_doctor.py once ->
                                   {checks, fail, warn, summary, lines} (200 even
                                   on a non-zero exit: FAIL/WARN are honest output)
  POST /api/cron                -> Bearer; {action:"del", id} deletes one job
                                   (404 unknown id) | {action:"create", name,
                                   schedule, prompt, kind?} creates one through
                                   the SAME registry/grammar as NL + CLI
                                   (400 on a bad spec/name/size)
  POST /api/mycore/upstreams/test
                               -> Bearer; {url?} one 5s connectivity probe per
                                   configured failover endpoint -> [{name, ok,
                                   latency_ms, error_class}] — exception class /
                                   HTTP status only: never a URL query, a body
                                   or a key
  POST /api/models/discover    -> Bearer; {base_url, api_key?} ONE read-only
                                   GET of that endpoint's model directory
                                   (the OpenAI-compatible /models read — never
                                   a completion, zero tokens) -> {ok:true,
                                   base_url, count, total, truncated, auth,
                                   models:[{id}]} | {ok:false, reason} where
                                   reason is an HTTP status, an exception class
                                   or a fixed guard word — never the URL, never
                                   the key, never a response body
  GET  /api/mission             -> mission board (0.5.44): {reporting, active,
                                   recent}; fail-soft empty structure, no auth,
                                   no credential/origin/manifest fields.
                                   Car 2: ?id=<mid> -> {mission: row + plan
                                   (or null) + events (tail 20, scrubbed) +
                                   budget + budget_used}; 404 unknown id;
                                   WITHOUT id the list shape is unchanged
  POST /api/mission             -> Bearer; {action: start|stop|pause|resume,
                                   goal?, id?, reporting?, budget?, deadline?,
                                   redlines?, deliverable?} -> mission
                                   row (400 validation / 404 unknown id / 409
                                   conflict — one active mission at a time;
                                   start's budget{max_slices, max_wall_hours,
                                   max_daily_model_calls} freezes into the
                                   mission, defaults = settings at start time;
                                   car 3: optional deadline (ISO8601, future)
                                   / redlines (<=500) / deliverable (<=200) —
                                   all null when absent, injected into the
                                   plan/slice prompts)
  GET  /api/sniff               -> SNIFF car (SENSE board): {enabled, pause,
                                   interval_s, quiet_hours, digest_cap,
                                   config_error, last_swept, sources[] (honest
                                   status/reads/age), rules[], rule_errors,
                                   findings[] newest-first + findings_total,
                                   read_only}; loopback, no auth (mission
                                   GET parity), slot NAMES only, payloads
                                   already field-whitelisted by mycore.sniff
  POST /api/sniff               -> Bearer; {action: sweep|ack, signature?} —
                                   sweep = ONE read-only sweep now (force
                                   beats the interval gate, the per-source
                                   rate cap still applies); ack = mark a
                                   finding acknowledged in the local log
                                   (400 bad action/signature, 404 unknown
                                   signature). No actuation surface exists.
  GET  /api/exchange/quote      -> READ-ONLY public market probe: venue +
                                   optional symbol -> {venue, symbol, lines};
                                   own 10/60s sliding window -> 429
  GET  /api/exchange/plans      -> stored W2 order plans (read, no auth —
                                   same contract as every GET; fail-soft 503)
  GET  /api/quotes              -> Extensions car (Phase B): keyless READ-ONLY
                                   quotes, market=cn|us + symbol ->
                                   {ok, market, symbol, name, price, change,
                                   pct, currency, ts, source}; cn = Sina
                                   (GBK wire + mandatory Referer), us = Yahoo
                                   chart JSON. Honest failures: 400 bad
                                   market/symbol, 404 unknown symbol, 502/504
                                   upstream trouble (never a fabricated
                                   number), own 10/60s window -> 429;
                                   AIDOG_QUOTES_URL = offline mock seam
  POST /api/exchange/plans/<id>  -> Bearer WEB_TOKEN; {action: approve|reject|
                                   cancel} -> {plan} | 400 invalid action |
                                   404 unknown plan id (the ONLY approval
                                   surface: the model has no such route)
  POST /api/extensions/intent    -> Bearer WEB_TOKEN; {text} -> car 4 call
                                   site 1: which Extensions tab owns the
                                   request (Jev choice over finance/home/
                                   vehicle). 200 {available:true, tab,
                                   confidence, source} | 200 {available:false,
                                   reason: WORD} (off / no_key / unreachable /
                                   upstream — an honest "no fast path", never
                                   a guessed tab) | 400 bad text | 401
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
  GET  /api/artifacts           -> X17 gallery: generated-image catalog
                                   {images[{name, url, size, when, ext}],
                                   count, limit, offset, truncated} — newest
                                   first, bounded pages (?limit/?offset), an
                                   honest empty 200 when nothing was
                                   generated, 503 when the dir is unreadable
  GET  /api/sessions            -> X13 read face: {sessions[{id, when, size,
                                   summary[, hits, partial]}], count,
                                   truncated, limit, search?{q, method:
                                   "fts5", engine, fallback,
                                   case_insensitive}, prune{enabled, days,
                                   removed, kept, freed, errors, ran_at,
                                   advisory},
                                   advisory is one-shown per process; ?q= is
                                   real FTS5 full-text search over a lazily
                                   synced persistent index (BM25 order),
                                   honestly degrading to a labelled substring
                                   scan (method "substring", fallback true,
                                   reason fts5_unavailable|
                                   index_unusable|unindexable_query),
                                   ?limit= 1..500;
                                   ?id=<sid>&format=json|md exports ONE
                                   session as an attachment (400 bad id/
                                   format, 404 unknown id) — message content
                                   only, never a credential slot value
  GET  /api/discussions         -> community panel: the product repo's GitHub
                                   Discussions (anonymous REST read, 600s
                                   module cache, ?fresh=1 busts it):
                                   {available, discussions[{number, title,
                                   category, author, comments, updated_at,
                                   url}], checked_at[, reason]} — every
                                   failure answers 200 with available:false
                                   and a category-word reason only
                                   (not_public_yet | not_enabled |
                                   rate_limited | upstream_error | network):
                                   never a URL, an exception string, or a
                                   credential. While the repo is private this
                                   is the DESIGN state, not an error.
  GET  /api/discussions/categories
                                   -> {available, categories[{id, name,
                                   slug}], checked_at[, reason]} — the post
                                   composer's category list (GraphQL, so it
                                   needs AIDOG_COMMUNITY_PAT; without it the
                                   honest reason no_token|no_scope ships and
                                   the UI shows the setup hint)
  GET  /api/discussions/replies?number=N
                                   -> {available, number, replies[{id,
                                   author, body, created_at}], checked_at[,
                                   reason]} — anonymous REST read of one
                                   thread (public repo; 400 bad number)
  POST /api/discussions/create    -> Bearer; {title, body, category} ->
                                   {ok, number, url} | 400 validation |
                                   404 unknown category/discussion | 409
                                   not configured (reason no_token|no_scope)
                                   | 429 (reason busy|rate_limited, retry_s)
                                   | 502 (reason network|upstream_error)
  POST /api/discussions/comment   -> Bearer; {number, body} ->
                                   {ok, number, comment_id} — same ladder;
                                   writes share mycore.community's 30s
                                   interval + 120/2000 char caps with the
                                   agent's community_post tool
  POST /api/slots/verify          -> Bearer; {name} | {all:true} ->
                                   {results[{name, group, set, level:
                                   format|live, ok, reason, detail}]} — one
                                   credential check per row: shape rules
                                   everywhere, a real remote probe for the
                                   slots that have one (community PAT ->
                                   GitHub Discussions scope, GH PAT ->
                                   /user, bot token -> getMe, model key ->
                                   GET {base}/models). Values are never
                                   echoed; unset slots answer ok:null /
                                   reason not_set (honest, not a failure)
  GET  /api/skills/remote       -> official skills catalog (car 4, 0.5.47):
                                   {source: remote|cache|unavailable,
                                   fetched_at, count, skills[7-field rows]};
                                   5s fetch ceiling + 60s cache, fail-soft
                                   ladder (bundled site copy, then an honest
                                   empty 200 — never 5xx), loopback read,
                                   no auth (same contract as GET /api/mission)
  GET  /api/checkpoints        -> X10 read face (Wave 5): metadata-only
                                   checkpoint catalog {entries[{id, ts, when,
                                   path, file, size, sha256}], count} — a
                                   snapshot BYTE never leaves
                                   mycore.checkpoint; ?id=<n> -> {entry} |
                                   400 non-numeric id | 404 unknown id;
                                   fail-soft empty 200, loopback, no auth
  POST /api/checkpoints        -> Bearer; {action:"restore", id} mirrors the
                                   CLI `--restore` one for one (400 bad
                                   shape, 404 unknown id, 409 missing/corrupt
                                   snapshot with mycore's own message in
                                   details[0]); one audit line per attempt
  GET  /api/profiles           -> X11 read face: {profiles[{name, default,
                                   active, secrets, settings, web_port,
                                   state_dir, state_files, can_remove}],
                                   count, active, profiles_root} — presence /
                                   counts / port only (an absent credential is
                                   false, never a redacted string)
  POST /api/profiles           -> Bearer; {action:"create"|"rm", name} runs
                                   the `aidog profile` CLI rules verbatim
                                   (400 invalid/reserved name, duplicate, or a
                                   state-file guard — NO --force on this
                                   route, the default profile is refused
                                   before any subprocess runs)
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
from datetime import datetime, timedelta, timezone
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
from mycore import bridge, checkpoint, cli, decision, evolution, hooks, leash, ledger, mission, mcp, modeltier, sniff, terminal  # noqa: E402  (X10 snapshots + config + MCP/ hooks/ terminal/ mission grammar/ sniff grammar/ decision client/ bridge grammar/ car-7 leash grammar/ D2 ledger grammar)
from mycore import meter as model_meter  # noqa: E402  (v0.5.55 self-metering)
from mycore.assembly import (build_system_stack,  # noqa: E402
                              resolve_context_dirs, resolve_user_rules,
                              DEFAULT_OBEDIENCE, validate_obedience)
from mycore.compact import measure  # noqa: E402  (X14 context usage: the loop's own payload metric)
from mycore.loop import run_turn  # noqa: E402
from mycore.model import ChatClient  # noqa: E402
from mycore.service import state_dir as mycore_state_dir  # noqa: E402
from mycore.session import (Session, export_session, list_sessions,  # noqa: E402
                             prune_old, session_file)
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
# Wave 6 delta2 (X12/X13/X16/X17): caps for the session/gallery/vision faces.
SESSION_LIST_LIMIT = 200          # GET /api/sessions default page size
SESSION_LIST_MAX = 500            # hard cap on ?limit=
SESSIONS_Q_MAX = 200              # chars in ?q= (FTS5 search, honest
                                  # substring fallback — mycore.session)
ARTIFACTS_LIMIT = 60              # GET /api/artifacts default page size
ARTIFACTS_MAX = 200               # hard cap on ?limit=
CHAT_BODY_MAX = 2_000_000         # chat POST body budget (X16 image attach;
                                  # the TEXT cap stays CHAT_MAX_CHARS=4000)
VISION_MAX_CHARS = 1_500_000      # chars in one data-URL image attachment
VISION_URL_RE = re.compile(
    r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]{64,}={0,2}$")
PRUNE_DAYS_MAX = 3650             # mycore.session_prune_days bounds (0 = off)
DISK_LOW_FREE = 1024 ** 3         # X12 advisory: under 1 GiB free = warn once
REASONING_EFFORTS = ("", "low", "medium", "high")  # X15 enum ("" = off)
# Wave 3 backend (X5/X9/X14): caps for the new read/create faces.
UPSTREAM_TEST_TIMEOUT = 5.0           # seconds allowed to ONE endpoint probe
UPSTREAM_TEST_URL_MAX = 2048          # chars in an ad-hoc probe url
# Round D car 1 (D1 model one-click discovery): budgets for ONE read-only
# GET {base}/models. The 10s/1MB/200 ceilings are promises, not estimates —
# the timeout reader below clamps any seam back under the same ceiling.
DISCOVER_TIMEOUT_MAX = 10.0           # seconds for one discovery fetch
DISCOVER_TIMEOUT_MIN = 0.2            # floor (a seam may speed tests up)
DISCOVER_BODY_MAX = 1_048_576         # response body cap: 1 MiB + 1 byte read
DISCOVER_MODELS_MAX = 200             # honest cap on the returned model list
DISCOVER_KEY_MAX = 512                # chars in an inline api_key
CRON_PROMPT_MAX = 4096                # chars in a console-created job body
                                      # (the registry's own MAX_TEXT=4000
                                      # still applies inside create_job)
# Wave 5 delta1 (X10 checkpoints + X11 profiles): the profile name grammar is
# the SAME rule lib/profile_paths and aidog_persist enforce (three call sites,
# one rule — the CLI re-checks it anyway, this copy only saves a subprocess on
# an obviously bad name). Reserved names are never creatable, never removable.
PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
PROFILE_RESERVED = ("default", "profile")
_state_lock = threading.Lock()
# Mission board mutations (0.5.44): the console is a ThreadingHTTPServer, so
# read-modify-write on the registry runs under one lock (single active mission
# by construction — two concurrent starts must not both win).
_mission_lock = threading.Lock()


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
               "model": "mimo-v2.6-flash",
               # v0.5.55 gateway panel: absent == these defaults == today's
               # behavior (empty chain extras, fixed order, no rate caps).
               "endpoints": [], "rotation": "", "max_rpm": 0, "max_tpm": 0,
               # SNIFF car: the sense board partition (always present so the
               # console renders it without an absence check). Fresh lists —
               # the module constant's own lists are never shared here.
               "sniff": {**sniff.DEFAULT_SETTINGS,
                         "sources": [], "rules": []},
               # Car 4 (D6) decision endpoint row: OFF by default = every
               # Jev call site keeps today's behavior byte-for-byte. The KEY
               # is deliberately NOT a settings field — it lives in the
               # DECISION_API_KEY slot (presence-only everywhere), so a
               # settings write can never aim another slot at an external
               # service. Empty url/model = the vendor defaults.
               "decision": {"enabled": False, "url": "", "model": "",
                            "router_model": ""}},
    "usage": {"metrics_url": "http://127.0.0.1:3456/metrics",
              "pricing_config": "", "log_dir": "", "model_pricing": {}},
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
              "wake_phrases": ["hey aidog"]},
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
    # Anonymous usage ledger (D2 car): a copy of mycore.ledger.DEFAULT_
    # SETTINGS — the constant stays the source of truth; the block is always
    # present so the console card renders without an absence check. Module
    # default is OFF with an empty endpoint (fail-closed); the product seed
    # (web/settings.example.json) is what makes fresh installs report.
    "ledger": dict(ledger.DEFAULT_SETTINGS),
    # Mission reporting tier (0.5.44): a copy of mycore.mission.DEFAULT_
    # SETTINGS — the constant stays the source of truth; the partition is
    # always present so the console renders the tier without an absence check.
    "mission": dict(mission.DEFAULT_SETTINGS),
    # Bridge partition (car 6, line 5 / D9): a copy of mycore.bridge.
    # DEFAULT_SETTINGS — the constant is the source of truth and the block is
    # always present so the console renders the card without an absence check.
    # Fail-closed by construction: enabled=false and an empty allow-list
    # route nothing.
    "bridge": dict(bridge.DEFAULT_SETTINGS),
    # Event hooks partition (car 4a): {} = no hook configured. Present by
    # default for the same reason — the editor renders an honest empty set.
    "hooks": {},
    # Guardrails partition (car 7, D11 restraint): a copy of mycore.leash.
    # DEFAULT_GUARDRAILS — the constant stays the source of truth and the
    # block is always present so the console renders the card without an
    # absence check. Both caps 0 = OFF (opt-in): today's behavior unchanged.
    "guardrails": dict(leash.DEFAULT_GUARDRAILS),
    # Obedience partition (car 7, D11 obedience): a copy of mycore.assembly.
    # DEFAULT_OBEDIENCE — empty lists = nothing injected into the stack
    # (the pre-car-7 system stack stays byte-for-byte).
    "obedience": dict(DEFAULT_OBEDIENCE),
}
ALLOWED_TOP = ("language", "refresh_sec", "sound", "voice_volume",
               "alert_preview", "currency", "fx_rate", "mycore", "usage",
               "evolution", "channels", "hooks", "media", "web3", "mission",
               "bridge", "guardrails", "obedience", "ledger")
USAGE_KEYS = ("metrics_url", "pricing_config", "log_dir", "model_pricing")
MYCORE_KEYS = ("base_url", "model", "context_dirs", "upstreams", "mcp", "shell",
               "tools_off", "models", "budget", "session_prune_days",
               "reasoning_effort", "endpoints", "rotation", "max_rpm",
               "max_tpm", "sniff", "decision")
# Car 4 (D6): fields the mycore.decision block accepts — the key slot is NOT
# one of them (see the DEFAULT_SETTINGS note).
DECISION_KEYS = ("enabled", "url", "model", "router_model")
# The intent route only ever forwards a short excerpt (mycore.decision slices
# to 1200 chars before the wire); this is the request-side cap.
INTENT_TEXT_MAX = 2000
# v0.5.55: structured endpoint slots the console form may reference — the
# SAME allowlist mycore.modeltier enforces at parse time (single source of
# truth is the regex; this tuple only documents it for the route layer).
MODEL_KEY_SLOTS = ("MODEL_API_KEY", "OPENAI_API_TOKEN",
                   "MODEL_UP_K1", "MODEL_UP_K2", "MODEL_UP_K3", "MODEL_UP_K4",
                   "MODEL_UP_K5", "MODEL_UP_K6", "MODEL_UP_K7", "MODEL_UP_K8")
MODEL_KEY_SLOT_RE = re.compile(r"^(?:MODEL_API_KEY|OPENAI_API_TOKEN|MODEL_UP_K[1-8])$")
MYCORE_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
# mycore.models / mycore.budget join the schema (activation car §1.6): the
# raw-file consumers (service._allowed_models / service._budget /
# subagent._allowed_models / acp) read the SAME document the console writes,
# so validating them here keeps the console view and the runtime view in
# agreement. `[]` is legal (= whitelist off, honest); budget stays OUT of
# DEFAULT_SETTINGS on purpose — absent = cli default, exactly as before.
MODELS_MAX = 32
MODEL_ID_MAX = 128
BUDGET_MIN, BUDGET_MAX = 4000, 1000000
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
             "AIDOG_GH_PAT", "AIDOG_COMMUNITY_PAT", "MYCORE_WD_CHAT"),
    "gateway": ("OPENAI_API_TOKEN", "DECISION_API_KEY"),
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
    # Car 6 (D10): the Connections card — provider tokens with a READ-ONLY
    # default posture. Named after the upstream's own env-var names so an
    # operator can export the same value for wrangler / gh without invention.
    "connections": ("CLOUDFLARE_API_TOKEN", "GITHUB_TOKEN"),
    # Car 8 (CKX-P2): the agent-backup passphrase. Presence-only like every
    # other row here; its VALUE never enters a response, a log line, argv or
    # the model context — mycore.ckxbackup hands it to ONE child environment.
    "backup": ("AIDOG_CKX_PASSWORD",),
}
SECRET_SLOTS: dict = {name: group for group, names in SECRET_GROUPS.items()
                      for name in names}          # 37 editable slots
SECRET_PRESENCE_ONLY = ("WEB_TOKEN",)             # listed, never writable
SECRET_VALUE_MAX = 4096                           # chars, one line

# ------------------------------------- backup / restore plane (CKX-P1 / D4) ---
# The console's OWN encrypted backup surface (car 3). An export is the ONE
# place in this server where a credential slot VALUE leaves the process, so
# every rule below exists to keep that fact narrow, explicit and un-loggable:
# the value goes straight from the reader into the response document and
# never into a log line, an exception or a status string.
BACKUP_SCHEMA = "aidog.backup.v1"         # envelope marker (the CKX meta id)
BACKUP_SCHEMA_VERSION = "1"
BACKUP_INCLUDES = ("secrets", "settings", "memory")
# Fixed memory slot names — a restore can NEVER address an arbitrary path.
BACKUP_MEMORY_FILES = ("MEMORY_curated.md", "USER_curated.md",
                       "EVOLUTION_learned.md")
BACKUP_MEMORY_MAX_BYTES = 512 * 1024      # per memory slot, in BOTH directions
BACKUP_MAX_BODY = 4 * 1024 * 1024         # restore body budget: a whole
                                           # payload rides in on one request
# Exportable slots = the 37 catalog slots + the model-key pool slots that
# are NOT catalogued (writable by POST /api/secrets but not listed): the two
# overlap names are dropped, so the union is 45 today. WEB_TOKEN is
# presence-only and therefore can never appear in a backup.
BACKUP_SECRET_SLOTS = tuple(SECRET_SLOTS) + tuple(
    name for name in MODEL_KEY_SLOTS if name not in SECRET_SLOTS)

# ---------------------------------------- activation checklist formats (§1.1) ---
# Format rules reported by GET /api/setup. Each pattern MIRRORS one entry of
# `deploy/aidog_setup.py` SLOTS — the wizard is the source of truth for "is
# this slot filled correctly" — and tests/web_console_p3.py imports the wizard
# module and asserts these strings are byte-equal to the wizard's compiled
# patterns, so the two definitions cannot drift apart.
SLOT_PATTERNS: dict = {
    "WEB_TOKEN": r"^\S{16,}$",
    "MYCORE_TG_TOKEN": r"^\d{4,}:[A-Za-z0-9_-]{20,}$",
    "AIDOG_TG_ALLOWED_USERS": r"^\d+(,\d+)*$",
    "MODEL_API_KEY": r"^\S{8,}$",
}
# The wizard's required set (aidog_setup --check pending semantics).
REQUIRED_SLOTS = ("WEB_TOKEN", "MYCORE_TG_TOKEN", "AIDOG_TG_ALLOWED_USERS")
# Fixed checklist order: (item id, slot name, i18n hint key, English fallback).
# The server sends the KEY for the locale pack plus a short English fallback so
# the row is readable even before the pack carries the translation.
SETUP_ITEMS: tuple = (
    ("web_token", "WEB_TOKEN", "act.hint.web_token",
     "console write token: invent any random string of 16+ chars"),
    ("model_key", "MODEL_API_KEY", "act.hint.model_key",
     "not needed for loopback endpoints"),
    ("tg_token", "MYCORE_TG_TOKEN", "act.hint.tg_token",
     "Telegram bot token from @BotFather (123456789:AA...)"),
    ("tg_allowlist", "AIDOG_TG_ALLOWED_USERS", "act.hint.tg_allowlist",
     "numeric Telegram user ids, comma-separated (fail-closed while empty)"),
)
# Slots whose VALUE is format-checked on a console write (activation car
# §1.9) — a mirrored wizard pattern, extendable per slot. Empty = clear and
# is legal for every slot; the value itself is never echoed in the refusal.
SLOT_WRITE_PATTERNS: dict = {
    "AIDOG_TG_ALLOWED_USERS": SLOT_PATTERNS["AIDOG_TG_ALLOWED_USERS"],
}
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


# ------------------------------------------ backup / restore (CKX-P1) --------

class BackupReadError(RuntimeError):
    """A backup section could not be READ. Carries an HTTP status and a reason
    WORD only — never a path, never a slot value."""

    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


def backup_memory_paths() -> dict:
    """The three backupable memory slots, resolved through the ACTIVE profile
    (default profile = the pre-car-F overlay/memory paths)."""
    slots = profile_paths.memory_slots()
    return {BACKUP_MEMORY_FILES[0]: slots[0],
            BACKUP_MEMORY_FILES[1]: slots[1],
            BACKUP_MEMORY_FILES[2]: profile_paths.learned_path()}


def backup_payload(include) -> dict:
    """Build one export payload for `include` (a subset of BACKUP_INCLUDES).

    Sections absent from `include` are absent from the result. Only existing,
    non-empty memory slots ride; a section that cannot be read honestly raises
    BackupReadError instead of shipping a half-truth. This function writes
    NOTHING to a log — the values it reads stay inside the caller's response."""
    payload: dict = {}
    if "secrets" in include:
        secrets: dict = {}
        for name in BACKUP_SECRET_SLOTS:
            value = read_env_slot(name)
            if value:
                secrets[name] = value
        payload["secrets"] = secrets
    if "settings" in include:
        payload["settings"] = load_settings()
    if "memory" in include:
        mem: dict = {}
        for name, path in backup_memory_paths().items():
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                continue
            except OSError:
                raise BackupReadError(503, "memory slot unreadable") from None
            if not raw:
                continue
            if len(raw) > BACKUP_MEMORY_MAX_BYTES:
                raise BackupReadError(503, "memory slot over the backup cap")
            try:
                mem[name] = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise BackupReadError(503, "memory slot is not utf-8") from None
        payload["memory"] = mem
    return payload


def _backup_secret_reason(name: str, value: str) -> str:
    """The SAME value rules POST /api/secrets enforces (length, one line, no
    edge whitespace, per-slot shape) as a reason WORD — the value itself is
    never echoed into the reason."""
    if len(value) > SECRET_VALUE_MAX:
        return f"value too long (max {SECRET_VALUE_MAX} chars)"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        return "value must be a single line (no control characters)"
    if value != value.strip():
        return "value must not start or end with whitespace"
    fmt_pat = SLOT_WRITE_PATTERNS.get(name)
    if fmt_pat is None and MODEL_KEY_SLOT_RE.match(name):
        fmt_pat = SLOT_PATTERNS["MODEL_API_KEY"]
    if value and fmt_pat and not re.match(fmt_pat, value):
        return "value does not match the slot format"
    return ""


def backup_apply_secrets(doc: dict) -> dict:
    """Apply one secrets section slot BY slot: a refusal never stops the other
    slots (the honest partial report the restore contract promises). Log lines
    carry names and counts only — never a value."""
    names: list[str] = []
    failed: list[dict] = []
    for name, value in doc.items():
        if not isinstance(name, str) or not isinstance(value, str):
            failed.append({"name": str(name)[:64], "reason": "invalid entry"})
            continue
        group = SECRET_SLOTS.get(name)
        if group is None and MODEL_KEY_SLOT_RE.match(name):
            group = "model"            # the 8 pool slots ride a backup too
        if group is None:
            failed.append({"name": name[:64],
                           "reason": ("slot is not writable"
                                      if name in SECRET_PRESENCE_ONLY
                                      else "unknown slot")})
            continue
        reason = _backup_secret_reason(name, value)
        if reason:
            failed.append({"name": name, "reason": reason})
            continue
        try:
            write_env_slot(name, value)
        except OSError as e:  # noqa: BLE001 — type name only, never a value
            failed.append({"name": name,
                           "reason": f"persist failed: {type(e).__name__}"})
            continue
        names.append(name)
    sys.stderr.write(f"backup restore secrets set={len(names)} "
                     f"failed={len(failed)}\n")
    return {"set": len(names), "names": names, "failed": failed}


def backup_apply_settings(doc: dict) -> dict:
    """Apply one settings section through the EXISTING all-or-nothing
    validator (no second settings grammar exists), merged onto the RAW stored
    document exactly like POST /api/settings."""
    clean, errors = validate_settings_patch(doc)
    if errors:
        return {"applied": False, "changed": [], "errors": errors}
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    merged = deep_merge(stored, clean)
    persisted = merged != stored
    if persisted:
        try:
            atomic_write_json(SETTINGS_FILE, merged)
        except OSError as e:  # noqa: BLE001 — type name only
            return {"applied": False, "changed": [],
                    "errors": [f"persist failed: {type(e).__name__}"]}
        sys.stderr.write(f"backup restore settings "
                         f"keys={','.join(sorted(clean))}\n")
        mc_patch = clean.get("mycore")
        if isinstance(mc_patch, dict) and "session_prune_days" in mc_patch:
            session_prune_view(force=True)
        if {"currency", "fx_rate", "usage"} & set(clean):
            with _usage_lock:
                _usage_cache["ts"], _usage_cache["data"] = 0.0, None
    return {"applied": True, "changed": sorted(clean), "persisted": persisted}


def backup_apply_memory(doc: dict) -> dict:
    """Write the three fixed memory slots (never an arbitrary path), each
    capped and written atomically; a refusal is reported per file and the
    remaining files still land."""
    paths = backup_memory_paths()
    files: list[str] = []
    failed: list[dict] = []
    total = 0
    for name, text in doc.items():
        path = paths.get(name) if isinstance(name, str) else None
        if path is None:
            failed.append({"name": str(name)[:64],
                           "reason": "unknown memory slot"})
            continue
        if not isinstance(text, str):
            failed.append({"name": name, "reason": "content must be a string"})
            continue
        data = text.encode("utf-8")
        if len(data) > BACKUP_MEMORY_MAX_BYTES:
            failed.append({"name": name,
                           "reason": f"over the {BACKUP_MEMORY_MAX_BYTES} byte cap"})
            continue
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
            tmp.write_bytes(data)
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        except OSError as e:  # noqa: BLE001 — type name only
            failed.append({"name": name,
                           "reason": f"persist failed: {type(e).__name__}"})
            continue
        files.append(name)
        total += len(data)
    sys.stderr.write(f"backup restore memory files={len(files)} "
                     f"bytes={total} failed={len(failed)}\n")
    return {"files": files, "bytes": total, "failed": failed}


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
                   "targets": {}, "age_sec": None, "last": None, "detail": "",
                   "escalation": {"level": 0, "target": "", "reason": ""}}
    try:
        facts["age_sec"] = round(time.time() - MYCORE_WD_STATE.stat().st_mtime)
        doc = json.loads(MYCORE_WD_STATE.read_text())
        tg = doc.get("targets") if isinstance(doc, dict) else None
        if isinstance(tg, dict):
            for name, entry in tg.items():
                facts["targets"][str(name)[:32]] = (
                    str(entry.get("state") or "unknown")[:16]
                    if isinstance(entry, dict) else "unknown")
        # Car 7 (D11): the escalation verdict the tail row / status panel
        # renders — highest live level across targets (0 = clear).
        esc = {"level": 0, "target": "", "reason": ""}
        if isinstance(tg, dict):
            for name, entry in tg.items():
                if isinstance(entry, dict):
                    lvl = entry.get("esc_level")
                    if isinstance(lvl, int) and not isinstance(lvl, bool) \
                            and lvl > esc["level"]:
                        esc = {"level": lvl, "target": str(name)[:32],
                               "reason": ""}
        recs = doc.get("escalations") if isinstance(doc, dict) else None
        if isinstance(recs, list) and recs and isinstance(recs[-1], dict) \
                and esc["level"] > 0:
            esc["reason"] = str(recs[-1].get("reason") or "")[:120]
        facts["escalation"] = esc
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


def _validate_model_pricing(val) -> str:
    """usage.model_pricing shape check (v0.5.55). Returns "" when clean,
    else the refusal reason (safe: field names and limits only).

    Grammar: {model_id | "default": {"in": number >= 0, "out": number >= 0}}
    with at most 64 entries; keys <= 100 chars; CNY per 1M tokens (the same
    unit mycore.meter.estimate_cost consumes). Empty dict = table off."""
    if not isinstance(val, dict):
        return "must be an object {model_or_default: {in, out}}"
    if len(val) > 64:
        return "at most 64 entries"
    for name, row in val.items():
        if not isinstance(name, str) or not name or len(name) > 100:
            return "keys must be non-empty strings of at most 100 chars"
        if not isinstance(row, dict):
            return f"{name}: must be an object with in/out"
        unknown = [k for k in row if k not in ("in", "out")]
        if unknown:
            return f"{name}: unknown field"
        for side in ("in", "out"):
            if side not in row:
                return f"{name}: missing {side}"
            p = row[side]
            if isinstance(p, bool) or not isinstance(p, (int, float)) \
                    or not 0 <= p <= 1_000_000:
                return f"{name}: {side} must be a number in 0..1000000"
    return ""


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
                if key == "models":
                    # per-task /model whitelist (activation car §1.6): a bounded
                    # list of model IDS; [] = whitelist off (honest). bools are
                    # not lists; entries are single-line, never secrets.
                    if isinstance(val, bool) or not isinstance(val, list):
                        errors.append("mycore.models must be a list of model ids")
                        continue
                    if len(val) > MODELS_MAX:
                        errors.append(
                            f"mycore.models holds at most {MODELS_MAX} entries")
                        continue
                    bad_model: str | None = None
                    for item in val:
                        if not isinstance(item, str):
                            bad_model = "non-string entry"
                            break
                        if not item or item != item.strip() \
                                or len(item) > MODEL_ID_MAX \
                                or any(ord(c) < 32 or ord(c) == 127 for c in item):
                            bad_model = item[:40] or "empty"
                            break
                    if bad_model is not None:
                        errors.append(
                            "mycore.models entries must be non-empty single-line "
                            f"strings of at most {MODEL_ID_MAX} chars "
                            f"(first bad: {bad_model!r})")
                        continue
                    clean_mc[key] = list(val)
                    continue
                if key == "budget":
                    # compaction budget in chars (activation car §1.6): int only
                    # (bool refused), bounded; absent = cli default.
                    if isinstance(val, bool) or not isinstance(val, int) \
                            or not BUDGET_MIN <= val <= BUDGET_MAX:
                        errors.append(
                            f"mycore.budget must be an integer in "
                            f"{BUDGET_MIN}..{BUDGET_MAX}")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "session_prune_days":
                    # X12: opt-in session auto-prune age in days; 0/absent =
                    # off (same opt-in stance as upstream). int only, bounded.
                    if isinstance(val, bool) or not isinstance(val, int) \
                            or not 0 <= val <= PRUNE_DAYS_MAX:
                        errors.append(
                            f"mycore.session_prune_days must be an integer in "
                            f"0..{PRUNE_DAYS_MAX} (0 = off)")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "reasoning_effort":
                    # X15: opt-in per-model request param passthrough. Closed
                    # enum, "" = off = the pre-X15 wire byte-for-byte; raw
                    # hand-edited junk is ignored by cli.resolve_model_config.
                    if not isinstance(val, str) or val not in REASONING_EFFORTS:
                        errors.append(
                            "mycore.reasoning_effort must be one of: "
                            + "|".join(e or "(empty)"
                                      for e in REASONING_EFFORTS))
                        continue
                    clean_mc[key] = val
                    continue
                if key == "sniff":
                    # SNIFF car: the sense board's settings partition.
                    # Grammar lives in mycore.sniff.parse_config (first error,
                    # chat-safe; slot NAMES only — the slot regex refuses a
                    # credential value and the message never echoes one).
                    if not isinstance(val, dict):
                        errors.append("mycore.sniff must be an object")
                        continue
                    try:
                        sniff.parse_config(val)
                    except sniff.SniffError as e:
                        errors.append(f"mycore.sniff: {e}")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "decision":
                    # Car 4 (D6): the decision endpoint row. Grammar lives
                    # here (all-or-nothing like every other mycore key) —
                    # `enabled` must be a boolean, url/model/router_model
                    # are single-line bounded strings with the same shape
                    # rules the router client enforces. NO key field exists,
                    # by design: the credential is the DECISION_API_KEY slot.
                    if not isinstance(val, dict):
                        errors.append("mycore.decision must be an object")
                        continue
                    unknown = [k for k in val if k not in DECISION_KEYS]
                    if unknown:
                        errors.append("unknown mycore.decision key: "
                                      f"{str(unknown[0])[:40]}")
                        continue
                    block: dict = {}
                    bad = ""
                    if "enabled" in val:
                        if not isinstance(val["enabled"], bool):
                            bad = "enabled must be a boolean"
                        else:
                            block["enabled"] = val["enabled"]
                    for field in ("url", "model", "router_model"):
                        if bad or field not in val:
                            continue
                        value = val[field]
                        if value is None:
                            value = ""
                        if (not isinstance(value, str) or len(value) > 200
                                or "\n" in value or "\r" in value):
                            bad = (f"{field} must be a single line of at most "
                                   "200 chars")
                        elif field == "url" and value and not re.match(
                                r"^https?://[^/\s?#]+", value):
                            bad = "url must be an http(s) base URL"
                        elif field != "url" and value and not MYCORE_MODEL_RE.match(value):
                            bad = f"{field} must be a model id"
                        else:
                            block[field] = value
                    if bad:
                        errors.append(f"mycore.decision: {bad}")
                        continue
                    clean_mc[key] = block
                    continue
                if key == "endpoints":
                    # v0.5.55 structured chain: grammar lives in
                    # mycore.modeltier.parse_endpoints (the validator reports
                    # its exact chat-safe reason). A BOTH-non-empty check runs
                    # after the loop (one error, order-independent) — for a
                    # POST patch the 400 refuses the write; for load_settings
                    # (hand-edited document) the errors are ignored and BOTH
                    # tracks survive into clean, matching cli's endpoints-
                    # win precedence instead of silently dropping one side.
                    try:
                        modeltier.parse_endpoints(val)
                    except ValueError as e:
                        errors.append(f"mycore.endpoints: {e}")
                        continue
                    clean_mc[key] = val
                    continue
                if key == "rotation":
                    # candidate ordering: ""/ordered = the historical fixed
                    # order, round_robin = walking start offset.
                    if not isinstance(val, str) or val not in (
                            "", "ordered", "round_robin"):
                        errors.append(
                            "mycore.rotation must be one of: (empty)|ordered|"
                            "round_robin")
                        continue
                    clean_mc[key] = val
                    continue
                if key in ("max_rpm", "max_tpm"):
                    # global sliding-window caps (0 = off). bool is an int
                    # subclass — rejected explicitly (True is not a cap).
                    ceiling = (modeltier.MAX_RPM if key == "max_rpm"
                               else modeltier.MAX_TPM)
                    if isinstance(val, bool) or not isinstance(val, int) \
                            or not 0 <= val <= ceiling:
                        errors.append(
                            f"mycore.{key} must be an integer in 0..{ceiling} "
                            "(0 = off)")
                        continue
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
            # v0.5.55 dual-track mutex: ONE non-empty chain representation per
            # write. A POST carrying both is refused (400, order-independent);
            # load_settings ignores errors, so a hand-edited document keeping
            # both still loads and cli.resolve prefers `endpoints` — the same
            # precedence collect_endpoints() renders.
            _mc_ep = mc.get("endpoints")
            _mc_up = mc.get("upstreams")
            if _mc_ep and isinstance(_mc_up, str) and _mc_up.strip():
                errors.append(
                    "mycore.endpoints and mycore.upstreams are two "
                    "representations of the chain — send only one "
                    "non-empty (the console clears the other on save)")
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
                if key == "model_pricing":
                    # v0.5.55: per-model CNY price table for the self-metered
                    # panel — {model_id|"default": {"in": ¥/1M, "out": ¥/1M}}.
                    # Unknown fields / non-numeric prices are refused here so
                    # estimate_cost() only ever sees clean numbers.
                    err = _validate_model_pricing(val)
                    if err:
                        errors.append(f"usage.model_pricing: {err}")
                        continue
                    clean_usage[key] = val
                    continue
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
    if "ledger" in patch:
        # D2 anonymous usage reporting: {enabled: bool, endpoint: string}.
        # Endpoint is empty (= never sends) or an http(s) URL, capped; the
        # enabled flag must be a real bool so "1"/"yes" can never smuggle
        # the switch on.
        led = patch["ledger"]
        if not isinstance(led, dict):
            errors.append("ledger must be an object")
        else:
            before_led = len(errors)
            clean_led: dict = {}
            for key in led:
                if key not in ("enabled", "endpoint"):
                    errors.append(f"unknown ledger key: {key}")
                    continue
                val = led[key]
                if key == "enabled":
                    if not isinstance(val, bool):
                        errors.append("ledger.enabled must be a boolean")
                        continue
                    clean_led["enabled"] = val
                else:
                    if not isinstance(val, str) or len(val) > ledger.ENDPOINT_MAX:
                        errors.append("ledger.endpoint must be a string of at most "
                                      f"{ledger.ENDPOINT_MAX} chars")
                        continue
                    val = val.strip()
                    if val and not val.startswith(("https://", "http://")):
                        errors.append("ledger.endpoint must be empty or an http(s) URL")
                        continue
                    clean_led["endpoint"] = val
            if len(clean_led) == len(led) and len(errors) == before_led:
                clean["ledger"] = clean_led
    if "evolution" in patch:
        clean_evo, evo_errors = evolution.validate_evolution_settings(
            patch["evolution"])
        errors.extend(evo_errors)
        if clean_evo:
            clean["evolution"] = clean_evo
    if "mission" in patch:
        # Mission partition (0.5.44): grammar lives in mycore.mission — one
        # source of truth for write validation and load (evolution parity).
        clean_mis, mis_errors = mission.validate_mission_settings(patch["mission"])
        errors.extend(mis_errors)
        if clean_mis:
            clean["mission"] = clean_mis
    if "bridge" in patch:
        # User-owned bridge (car 6, D9): the grammar lives in mycore.bridge —
        # one source of truth for the console write path and the runtime read.
        clean_bridge, bridge_errors = bridge.validate(patch["bridge"])
        errors.extend(bridge_errors)
        if clean_bridge:
            clean["bridge"] = clean_bridge
    if "hooks" in patch:
        # Event hooks partition (car 4a): schema + argv[0] existence live in
        # mycore.hooks — one source of truth for write validation and load.
        clean_hooks, hook_errors = hooks.validate_hooks(patch["hooks"])
        errors.extend(hook_errors)
        if clean_hooks:
            clean["hooks"] = clean_hooks
    if "guardrails" in patch:
        # Guardrails partition (car 7, D11 restraint): the grammar lives in
        # mycore.leash — one source of truth for the console write path and
        # the runtime gate. 0 = off; the offending value is never echoed.
        clean_guard, guard_errors = leash.validate_guardrails(patch["guardrails"])
        errors.extend(guard_errors)
        if clean_guard:
            clean["guardrails"] = clean_guard
    if "obedience" in patch:
        # Obedience partition (car 7, D11 obedience): grammar + priority wording
        # live in mycore.assembly — one source of truth for the write path
        # and the stack builder. Entry VALUES are never echoed.
        clean_obs, obs_errors = validate_obedience(patch["obedience"])
        errors.extend(obs_errors)
        if clean_obs:
            clean["obedience"] = clean_obs
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
        # Wave 3 (X6): failover health/cooling — tri-state healthy, in-process
        # scope, zero key material (see collect_modeltier).
        "modeltier": collect_modeltier(),
        "memory": {
            "curated_chars": len(mem.read_text()) if mem.exists() else 0,
            "curated_limit": 2200,
            "user_chars": len(usr.read_text()) if usr.exists() else 0,
            "user_limit": 1375,
        },
        "config": cfg_bits,
        # Car 7 (D11 tail surface): one-glance state, merged into the EXISTING
        # Overview (no second switchboard). `state` is derived, never
        # guessed: paused > budget-blocked > a turn in flight > idle.
        "tail": tail_state(),
    }


# ------------------------------------------------- car 7: control / tail ---
# Console-side in-process turn counter (this process only — the TG leg's
# turns are reported from its own log, honestly labeled).
_active_lock = threading.Lock()
_active_turns = [0]


def _bump_active_turn(delta: int) -> None:
    with _active_lock:
        _active_turns[0] = max(0, _active_turns[0] + delta)


def _service_turn_working() -> bool:
    """True when the mycore service log shows a pipeline TURN_START with no
    later TURN_END/TURN_CRASH — a TG/voice/channel turn is in flight.
    Absent/unparseable log = False (honest: unknown reads as not-working,
    the panel state stays the authority for process health)."""
    lines = _tail_text(MYCORE_SERVICE_LOG, 32768).splitlines()
    last_start = ""
    last_end = ""
    for line in lines:
        m = SERVICE_LINE_RE.match(line)
        if not m:
            continue
        code = m.group(2).upper()
        if code == "TURN_START":
            last_start = m.group(1)
        elif code in ("TURN_END", "TURN_CRASH"):
            last_end = m.group(1)
    return bool(last_start) and last_start > last_end


def tail_state() -> dict:
    """The tail row: state/reason/since + the honest working flags."""
    st = leash.read_state()
    with _active_lock:
        console_turns = _active_turns[0]
    status = {"state": "idle", "reason": "", "since": 0.0,
              "console_turns": console_turns,
              "service_turn": _service_turn_working()}
    if st.get("paused"):
        status.update(state="paused", reason=str(st.get("reason") or ""),
                      since=float(st.get("since") or 0.0))
        return status
    # budget block (only consulted when a cap is set — free otherwise)
    code, _ctx = _budget_code()
    if code:
        status.update(state="blocked", reason=code)
        return status
    if console_turns > 0 or status["service_turn"]:
        status["state"] = "working"
    return status


def _budget_code() -> tuple[str, dict]:
    """(code, ctx) for TODAY's budget gate without re-reading the pause flag:
    "" = allowed, else budget_usd | budget_tokens."""
    status = leash.budget_status(load_settings())
    limits = status["limits"]
    if limits["usd_day"] > 0 and status["usd"]["state"] == "over":
        return "budget_usd", {"used": status["usd"].get("used"),
                               "limit": status["usd"].get("limit")}
    if limits["token_day"] > 0 and status["tokens"]["state"] == "over":
        return "budget_tokens", {"used": status["tokens"].get("used"),
                                  "limit": status["tokens"].get("limit")}
    return "", {}


def collect_control() -> dict:
    """GET /api/control — the leash read face: pause state + budget status
    + what a NEW turn would answer right now. No secrets (numbers only)."""
    st = leash.read_state()
    settings = load_settings()
    budget = leash.budget_status(settings)
    _ok, code, ctx = leash.gate(settings)
    return {
        "paused": bool(st.get("paused")),
        "reason": str(st.get("reason") or ""),
        "since": float(st.get("since") or 0.0),
        "stop_seq": int(st.get("stop_seq") or 0),
        "budget": budget,
        "gate": {"allowed": not code, "code": code,
                 "detail": leash.block_detail(code, ctx) if code else ""},
        "tail_state": tail_state().get("state", "idle"),
        "day": "utc",
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


def collect_usage_self() -> dict:
    """GET /api/usage/self (v0.5.55) — the SELF-METERED panel: rows the
    mycore meter wrote for chat attempts in this profile (last 7 days), plus
    a CNY cost from settings usage.model_pricing.

    Independent of the external metrics source the legacy /api/usage reads,
    so an end user without that infrastructure still sees real numbers.
    Degradations stay honest: inactive meter -> available:false; a model
    with no configured price -> cost null + listed unpriced (never 0);
    display currency conversion reuses the FX machinery of /api/usage."""
    settings = load_settings()
    usage = model_meter.read()
    out: dict = {"available": False}
    if not usage.get("available"):
        return {**out, "reason": usage.get("reason") or "inactive"}
    pricing = (settings.get("usage") or {}).get("model_pricing")
    pricing = pricing if isinstance(pricing, dict) else {}
    cost = model_meter.estimate_cost(usage, pricing)
    out.update({
        "available": True,
        "window_days": usage.get("days") or [],
        "files": usage.get("files", 0),
        "truncated": bool(usage.get("truncated")),
        "requests": usage.get("requests", 0),
        "ok": usage.get("ok", 0),
        "fail": usage.get("fail", 0),
        "in_tokens": usage.get("in_tokens", 0),
        "out_tokens": usage.get("out_tokens", 0),
        "cached_tokens": usage.get("cached", 0),
        # car 5-D7 item 3: the measured prompt-cache hit rate (provider-reported
        # tokens only; null when nothing reported an input count). Observation,
        # not a goal — this gateway still sends no cache directive of its own.
        "cached_ratio": usage.get("cached_ratio"),
        "no_usage": usage.get("no_usage", 0),
        "by_endpoint": usage.get("by_endpoint") or [],
        "by_model": usage.get("by_model") or [],
        "by_day": usage.get("by_day") or {},
        "pricing": {"configured": bool(pricing),
                    "total_cny": cost["total_cny"],
                    "per_model": cost["per_model"],
                    "unpriced": cost["unpriced"]},
        "display": display_fx(settings),
    })
    return out


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


def _wd_escalation_events() -> list[dict]:
    """Car 7 (D11 watch escalation): the journal the watchdog dual-writes
    (state file `escalations`) rendered as console events — the "which
    level / which attempt / why" chain with its REAL timestamp. Absent or
    unreadable journal = no events (honest absence, never a fabrication)."""
    try:
        doc = json.loads(MYCORE_WD_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    recs = doc.get("escalations") if isinstance(doc, dict) else None
    if not isinstance(recs, list):
        return []
    out: list[dict] = []
    for rec in recs[-8:]:
        if not isinstance(rec, dict):
            continue
        try:
            ts = float(rec.get("ts") or 0.0)
        except (TypeError, ValueError):
            continue
        level = rec.get("level")
        if not isinstance(level, int) or isinstance(level, bool):
            continue
        target = str(rec.get("target") or "?")[:32]
        attempt = rec.get("attempt")
        attempt = attempt if isinstance(attempt, int) \
            and not isinstance(attempt, bool) else 0
        reason = sanitize_event_text(str(rec.get("reason") or ""))
        # level -> event severity: 0 = clear/recovered, 1 = down, 2+ = escalate
        ev_level = "info" if level <= 0 else ("warn" if level == 1 else "urgent")
        out.append(_event(ev_level, "watchdog",
                          f"L{level} {target} attempt {attempt}: {reason}",
                          ts, code="wd.escalation"))
    return out


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


# ------------------------- official skills catalog (car 4, 0.5.47) --------
# GET /api/skills/remote pulls the published catalog. The design-book ladder
# (autonomous_ops_design.md 4.4) is fail-soft end to end: remote -> the copy
# shipped with this release -> an honest empty 200; a 5xx is impossible by
# construction. The two knobs below are env seams (operator/test overrides,
# read per request the way lib.profile_paths reads its own) — the shipped
# constant carries no credential of any kind.
SKILLS_REMOTE_URL = "https://aidog.im/skills/catalog.json"
SKILLS_COPY_FILE = REPO / "site" / "skills" / "catalog.json"
SKILLS_REMOTE_TIMEOUT = 5.0   # one outbound attempt, hard ceiling
SKILLS_REMOTE_TTL = 60.0      # in-process reuse window (no stampede)
_skills_lock = threading.Lock()
_skills_cache: dict = {"ts": 0.0, "key": None, "data": None}


def _skill_rows(doc) -> list[dict]:
    """Project a catalog document onto the fixed seven-field schema
    (slug/name/category/description/version/requires/install_status).
    Non-object entries and entries without slug+name are dropped (partial
    success); unknown keys never pass through, so neither source can smuggle
    a local path, an internal id or a credential into the payload."""
    if not isinstance(doc, dict) or not isinstance(doc.get("skills"), list):
        return []
    rows: list[dict] = []
    for entry in doc["skills"]:
        if not isinstance(entry, dict):
            continue
        slug = str(entry.get("slug") or "").strip()
        name = str(entry.get("name") or "").strip()
        if not slug or not name:
            continue
        requires = entry.get("requires")
        rows.append({
            "slug": slug,
            "name": name,
            "category": str(entry.get("category") or ""),
            "description": str(entry.get("description") or ""),
            "version": str(entry.get("version") or ""),
            "requires": [str(r) for r in requires]
                        if isinstance(requires, list) else [],
            "install_status": str(entry.get("install_status") or ""),
        })
    return rows


def _availability_sources() -> tuple[set[str], set[str], set[str]]:
    """Car 5 (0.5.48): the three capability sources a `requires` entry is
    checked against, all name-only:
      1) tools  — the LIVE registry (tools_off applied), the same source
                  GET /api/capabilities reports in `tools`;
      2) slots  — credential-slot PRESENCE from secrets.env (the value is
                  read for presence and dropped; nothing serializes);
      3) routes — channel routes that report `configured`.
    A registry failure yields an empty tool set: nothing can then be
    confirmed, so tool requirements honestly land in `missing`."""
    try:
        reg = build_registry(disabled=resolve_disabled_tools())
        tools = {str(f.get("function", {}).get("name", ""))
                 for f in reg.schema() if isinstance(f, dict)}
        tools.discard("")
    except Exception as e:  # noqa: BLE001 — degrade to "unconfirmed"
        sys.stderr.write(f"skills availability tools failed err={type(e).__name__}\n")
        tools = set()
    slots = set(SECRET_SLOTS) | set(SECRET_PRESENCE_ONLY)
    present = {name for name in slots if read_env_slot(name)}
    routes = set(channel_routes())
    rows = collect_channels().get("channels")
    rows = rows if isinstance(rows, list) else []
    configured = {str(r.get("name")) for r in rows
                  if isinstance(r, dict) and r.get("configured")
                  and r.get("name")}
    return tools, present, configured & routes


def _with_availability(rows: list[dict]) -> list[dict]:
    """Project every catalog row with `available: bool` + `missing: [..]`.

    Classification of one `requires` entry (three classes, in order):
      * a KNOWN CREDENTIAL SLOT -> ok iff that slot is set (presence only);
      * a KNOWN CHANNEL ROUTE   -> ok iff that route reports `configured`;
      * a LIVE TOOL NAME        -> ok (the registry already answers);
      * anything else           -> `missing` (honest: never guessed).
    `available` is simply `not missing`; the seven catalog fields stay
    untouched, so the static site copy (seven fields) and this projection
    remain self-consistent: projection = static + these two computed fields.
    The input rows are never mutated (the fail-soft cache owns them)."""
    tools, present, routes = _availability_sources()
    known_slots = set(SECRET_SLOTS) | set(SECRET_PRESENCE_ONLY)
    known_routes = set(channel_routes())
    out: list[dict] = []
    for row in rows:
        missing: list[str] = []
        for req in row.get("requires") or []:
            if req in known_slots:
                if req not in present:
                    missing.append(req)
            elif req in known_routes:
                if req not in routes:
                    missing.append(req)
            elif req in tools:
                continue
            else:
                missing.append(req)   # unknown requirement: unavailable
        new = dict(row)
        new["available"] = not missing
        new["missing"] = missing
        out.append(new)
    return out


def collect_skills_remote() -> dict:
    """GET /api/skills/remote — the official skills catalog (car 4, 0.5.47).

    Read, loopback, no auth (same contract as GET /api/mission). Priority:
    1) the published catalog over HTTP (5s ceiling, one attempt); 2) the copy
    shipped with this release, honestly marked `source: "cache"`; 3) an
    empty `source: "unavailable"` payload. Never 5xx, never a hang: one
    in-process lock plus a 60s cache keyed by (url, copy path) allows at most
    one outbound fetch per minute per config, and concurrent callers wait out
    that single fetch instead of stacking their own.

    Car 5 (0.5.48): the two computed fields `available` / `missing` are
    projected OUTSIDE the cache on every request, so a slot that was just set
    (or cleared) reflects immediately while the fetch ladder keeps its 60s
    window. An `unavailable` payload carries an empty list — there is simply
    nothing to compute, and no availability claim is made."""
    data = _skills_remote_ladder()
    if not data.get("skills"):
        return data                       # fail-soft rung: no claim at all
    doc = dict(data)
    doc["skills"] = _with_availability(data["skills"])
    return doc


def _skills_remote_ladder() -> dict:
    """The fail-soft fetch ladder itself (cached payload, NO availability
    fields — see collect_skills_remote)."""
    url = (os.environ.get("AIDOG_SKILLS_CATALOG_URL") or "").strip() \
        or SKILLS_REMOTE_URL
    copy_file = Path((os.environ.get("AIDOG_SKILLS_CATALOG_FILE") or "").strip()
                     or SKILLS_COPY_FILE)
    now = time.time()
    key = (url, str(copy_file))
    with _skills_lock:
        cached = _skills_cache
        if (cached["data"] is not None and cached.get("key") == key
                and now - cached["ts"] < SKILLS_REMOTE_TTL):
            return cached["data"]
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        data: dict | None = None
        remote_err: str | None = None
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "aidog-console"})
            with urllib.request.urlopen(req,
                                         timeout=SKILLS_REMOTE_TIMEOUT) as resp:
                doc = json.loads(resp.read().decode("utf-8", "replace"))
            rows = _skill_rows(doc)
            if not rows:
                raise ValueError("remote catalog has no usable entries")
            data = {"source": "remote", "fetched_at": stamp,
                    "count": len(rows), "skills": rows}
        except Exception as e:  # noqa: BLE001 — offline degrades, never 500
            remote_err = type(e).__name__
        if data is None:  # fail-soft: the copy shipped with this release
            copy_err: str | None = None
            try:
                rows = _skill_rows(
                    json.loads(copy_file.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                rows, copy_err = [], type(e).__name__
            if rows:
                data = {"source": "cache", "fetched_at": stamp,
                        "count": len(rows), "skills": rows}
            else:
                # reason only, no path and no URL: an honest type-name trio
                data = {"source": "unavailable", "fetched_at": stamp,
                        "count": 0, "skills": [],
                        "error": f"remote fetch failed ({remote_err}) and the "
                                 f"bundled copy is unusable ({copy_err})"}
        _skills_cache.update(ts=now, key=key, data=data)
        return data


# ------------------------------------------- community discussions feed ----
# Community view (product GitHub Discussions): the console reads the repo's
# discussion list ANONYMOUSLY (no token ever rides this request) and caches
# it module-wide for DISCUSSIONS_TTL so a browser reload never stacks
# upstream reads. Until the repo flips public GitHub answers 404/410 and the
# endpoint honestly reports `available:false` with a category-word `reason` —
# a designed state the UI renders as an honest card.
DISCUSSIONS_URL = "https://api.github.com/repos/AiDog-im/AiDog/discussions?per_page=20"
DISCUSSIONS_TTL = 600.0         # in-process reuse window (seconds)
DISCUSSIONS_TIMEOUT = 8.0       # one outbound attempt, hard ceiling
_discussions_lock = threading.Lock()
_discussions_cache: dict = {"ts": 0.0, "data": None}


def _discussion_rows(doc: object) -> list[dict]:
    """Map GitHub Discussions REST items to the console's six-field rows.
    Nested `category`/`user` objects degrade to "", a non-list payload maps
    to [], and only an https://github.com/ html_url survives as `url` (a
    tampered link degrades to "", which the UI renders as plain text)."""
    out: list[dict] = []
    items = doc if isinstance(doc, list) else []
    for it in items[:20]:
        if not isinstance(it, dict):
            continue
        cat = it.get("category")
        if isinstance(cat, dict):
            cat = cat.get("name") or cat.get("slug") or ""
        user = it.get("user")
        author = user.get("login") if isinstance(user, dict) else (user or "")
        try:
            comments = int(it.get("comments") or 0)
        except (TypeError, ValueError):
            comments = 0
        num = it.get("number")
        url = str(it.get("html_url") or "")
        if not url.startswith("https://github.com/"):
            url = ""
        out.append({
            "number": num if isinstance(num, int) else None,
            "title": str(it.get("title") or ""),
            "category": str(cat or ""),
            "author": str(author or ""),
            "comments": max(0, comments),
            "updated_at": str(it.get("updated_at") or it.get("created_at") or ""),
            "url": url,
        })
    return out


def _fetch_discussions() -> dict:
    """One outbound GitHub read -> the /api/discussions payload. Every
    failure path yields a category WORD (never a URL, never an exception
    string, never a credential), so the payload is always safe to render."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    url = (os.environ.get("AIDOG_DISCUSSIONS_URL") or "").strip() \
        or DISCUSSIONS_URL                 # test seam, same ladder as skills
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "aidog-console",
            "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=DISCUSSIONS_TIMEOUT) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
        if not isinstance(doc, list):
            raise ValueError("payload is not a list")
        return {"available": True, "discussions": _discussion_rows(doc),
                "checked_at": stamp}
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        reason = ("not_public_yet" if code in (401, 404) else
                  "not_enabled" if code == 410 else
                  "rate_limited" if code in (403, 429) else
                  "upstream_error")
        sys.stderr.write(f"discussions fetch failed code={code}\n")
    except Exception as e:  # noqa: BLE001 — offline degrades, never 500
        # ValueError here = a 200 whose body would not parse; URLError and
        # friends = transport. Both are logged as a type/code word only.
        reason = "upstream_error" if isinstance(e, ValueError) else "network"
        sys.stderr.write(f"discussions fetch failed err={type(e).__name__}\n")
    return {"available": False, "discussions": [], "checked_at": stamp,
            "reason": reason}


def collect_discussions(fresh: bool = False) -> dict:
    """GET /api/discussions — the product's GitHub Discussions (community
    panel). Read, loopback, no auth (same contract as GET /api/mission).
    One module fetch per DISCUSSIONS_TTL; the lock is held across the fetch
    so concurrent callers wait out ONE upstream read instead of stacking
    their own. `?fresh=1` (the panel's refresh button) skips the cache."""
    now = time.time()
    with _discussions_lock:
        cached = _discussions_cache
        if (not fresh and cached["data"] is not None
                and now - cached["ts"] < DISCUSSIONS_TTL):
            return cached["data"]
        data = _fetch_discussions()
        _discussions_cache.update(ts=time.time(), data=data)
        return data


# ---------------------------------------- community writes & thread reads ---
# The READ faces below are loopback/no-auth like every other GET; the WRITE
# faces (create/comment) sit behind Bearer WEB_TOKEN. All four share
# mycore.community's classification: a failure is an HTTP status plus one
# category WORD (and, for the reads, an available:false payload) — never an
# exception string, never a URL, never a credential.
REPLIES_TTL = 60.0                # in-process thread-read reuse (seconds)
_replies_lock = threading.Lock()
_replies_cache: dict = {}         # number -> {ts, data}


def _community_status(reason: str) -> int:
    """CommunityError reason word -> HTTP status (the UI reads the word,
    the status only tells a retry from a fix-your-input)."""
    return {"bad_input": 400, "not_found": 404, "no_token": 409,
            "no_scope": 409, "busy": 429, "rate_limited": 429,
            "network": 502, "upstream_error": 502}.get(reason, 502)


def _community_error_payload(err) -> tuple[int, dict]:
    """mycore.community.CommunityError -> (status, body). `retry_s` rides
    the busy/rate_limited pair so the composer can countdown honestly."""
    payload: dict = {"ok": False, "reason": err.reason}
    if err.detail:
        payload["detail"] = err.detail
    if err.reason in ("busy", "rate_limited"):
        from mycore import community as _comm
        payload["retry_s"] = int(_comm.seconds_until_writable()) + 1
    return _community_status(err.reason), payload


def _reply_row(item: object) -> dict | None:
    """One GitHub comment -> the console's five-field reply row. Nested
    objects degrade to "", a bad html_url degrades to "" (the UI renders
    plain text then), and only str/int fields survive."""
    if not isinstance(item, dict):
        return None
    user = item.get("user")
    author = user.get("login") if isinstance(user, dict) else (user or "")
    url = str(item.get("html_url") or "")
    if not url.startswith("https://github.com/"):
        url = ""
    return {
        "id": str(item.get("id") or ""),
        "author": str(author or ""),
        "body": str(item.get("body") or "")[:4000],
        "created_at": str(item.get("created_at") or ""),
        "url": url,
    }


def collect_replies(number: object, fresh: bool = False) -> dict:
    """GET /api/discussions/replies?number=N — one thread's comments
    (anonymous REST read, 60s in-process cache, `?fresh=1` busts it). A bad
    number answers 400 here; an upstream failure answers the same honest
    available:false shape as the list face."""
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        n = int(number)   # bool is an int subclass: reject it explicitly
        if isinstance(number, bool):
            raise TypeError
    except (TypeError, ValueError):
        return {"ok": False, "error": "validation failed",
                "details": ["number must be a positive integer"]}
    if n <= 0:
        return {"ok": False, "error": "validation failed",
                "details": ["number must be a positive integer"]}
    now = time.time()
    with _replies_lock:
        cached = _replies_cache.get(n)
        if (not fresh and cached and now - cached["ts"] < REPLIES_TTL):
            return dict(cached["data"])
    from mycore import community as _comm
    try:
        items = _comm.replies(n)
    except _comm.CommunityError as e:
        return {"available": False, "number": n, "replies": [],
                "reason": e.reason, "checked_at": stamp}
    except Exception as e:  # noqa: BLE001 — offline degrades, never 500
        sys.stderr.write(f"replies fetch failed err={type(e).__name__}\n")
        return {"available": False, "number": n, "replies": [],
                "reason": "network", "checked_at": stamp}
    rows = [r for r in (_reply_row(i) for i in items[:50]) if r]
    data = {"available": True, "number": n, "replies": rows,
            "checked_at": stamp}
    with _replies_lock:
        _replies_cache[n] = {"ts": time.time(), "data": dict(data)}
        while len(_replies_cache) > 20:      # bounded: threads are per-number
            _replies_cache.pop(next(iter(_replies_cache)))
    return data


def bust_replies_cache(number: object = None) -> None:
    """A successful reply invalidates that thread (called by the POST leg so
    the very next GET shows the new comment without waiting out the TTL)."""
    with _replies_lock:
        if number is None:
            _replies_cache.clear()
        else:
            try:
                _replies_cache.pop(int(number), None)
            except (TypeError, ValueError):
                pass


def collect_categories(fresh: bool = False) -> dict:
    """GET /api/discussions/categories — the post composer's category list.
    GraphQL needs the community token, so an unfilled slot is a DESIGN state:
    {available:false, reason:no_token} tells the UI exactly which slot to set.
    mycore.community caches the node ids for 10 minutes."""
    from mycore import community as _comm
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        items = _comm.categories(force=fresh)
    except _comm.CommunityError as e:
        return {"available": False, "categories": [], "reason": e.reason,
                "checked_at": stamp}
    except Exception as e:  # noqa: BLE001 — never 500 on a read face
        sys.stderr.write(f"categories fetch failed err={type(e).__name__}\n")
        return {"available": False, "categories": [], "reason": "network",
                "checked_at": stamp}
    return {"available": True,
            "categories": [{"id": c["id"], "name": c["name"],
                            "slug": c["slug"]} for c in items],
            "checked_at": stamp}


def bust_discussions_cache() -> None:
    """A successful create invalidates the 600s list cache so the panel's
    next GET (with or without ?fresh=1) shows the new discussion."""
    with _discussions_lock:
        _discussions_cache.update(ts=0.0, data=None)


# ------------------------------------------- credential-slot verification ---
# Two check levels, in one table so `verify` stays one honest code path:
#   * FORMAT — a conservative shape rule. Only rules we are sure about are
#     listed; every other slot falls back to the generic single-line rule
#     (copy-paste accidents: trailing spaces, a stray quote, a split line).
#     The expectation text is what ships to the UI — the VALUE never is.
#   * LIVE — a real remote probe with a 5s ceiling, for the four slots whose
#     usability GitHub/Telegram/OpenAI-compatible endpoints can prove.
SLOT_VERIFY_RULES: dict[str, tuple[str, str]] = {
    "WEB_TOKEN": (r"^\S{16,}$", "at least 16 chars, no spaces"),
    "MODEL_API_KEY": (r"^\S{8,}$", "at least 8 chars, no spaces"),
    "MYCORE_TG_TOKEN": (r"^\d{4,}:[A-Za-z0-9_-]{20,}$",
                         "bot token form 123456789:AA..."),
    "AIDOG_TG_ALLOWED_USERS": (r"^\d+(,\d+)*$",
                                "numeric ids, comma-separated"),
    "AIDOG_GH_PAT": (r"^\S{20,}$", "at least 20 chars, no spaces"),
    "CLOUDFLARE_API_TOKEN": (r"^\S{16,}$", "at least 16 chars, no spaces"),
    "GITHUB_TOKEN": (r"^\S{20,}$", "at least 20 chars, no spaces"),
    "AIDOG_COMMUNITY_PAT": (r"^\S{20,}$", "at least 20 chars, no spaces"),
    "MYCORE_WD_CHAT": (r"^-?\d{5,20}$", "a numeric Telegram chat id"),
    "CHANNEL_DINGTALK_WEBHOOK": (r"^https://\S+$", "an https:// webhook URL"),
}
VERIFY_TIMEOUT = 5.0             # one live probe, hard ceiling


def _verify_format(name: str, value: str) -> dict:
    """Shape check for one stored value -> {ok, reason, detail}. Never
    echoes the value; the detail names the EXPECTATION instead."""
    if value != value.strip():
        return {"ok": False, "reason": "bad_format",
                "detail": "value has leading or trailing whitespace — "
                          "retype it without spaces at the edges"}
    if any(ord(ch) < 32 or ch == 127 for ch in value):
        return {"ok": False, "reason": "bad_format",
                "detail": "value contains a control character — retype it "
                          "as one plain line"}
    if len(value) > SECRET_VALUE_MAX:
        return {"ok": False, "reason": "bad_format",
                "detail": f"value is longer than {SECRET_VALUE_MAX} chars"}
    rule = SLOT_VERIFY_RULES.get(name)
    if rule and not re.match(rule[0], value):
        return {"ok": False, "reason": "bad_format",
                "detail": f"expected {rule[1]}"}
    return {"ok": True, "reason": "ok",
            "detail": ("shape ok" if rule else
                       "generic shape ok (no slot-specific rule)")}


def _probe_community_pat(value: str) -> dict:
    """Live: the token authenticates AND can read this repo's discussions —
    exactly what posting needs (mycore.community owns the classification)."""
    from mycore import community as _comm
    res = _comm.verify_token(value)
    if res.get("ok"):
        return {"ok": True, "reason": "ok",
                "detail": f"ok — authenticated as {res.get('login', '?')}, "
                          f"discussions readable"}
    return {"ok": False, "reason": res.get("reason") or "no_scope",
            "detail": res.get("detail") or
                      "token cannot read this repository's discussions"}


def _probe_github_pat(value: str) -> dict:
    """Live: GET /user with the token. The detail may name the ACCOUNT
    (public data — the owner runs two gh identities, so this matters) but
    never the token."""
    from mycore import community as _comm
    try:
        body = _comm._request(f"{_comm.api_base()}/user", token=value)
        login = str(json.loads(body).get("login") or "")
    except _comm.CommunityError as e:
        return {"ok": False, "reason": e.reason,
                "detail": e.detail or "GitHub rejected this token"}
    except (ValueError, AttributeError, TypeError):
        return {"ok": False, "reason": "upstream_error",
                "detail": "unexpected /user payload"}
    if not login:
        return {"ok": False, "reason": "upstream_error",
                "detail": "no login in the /user response"}
    return {"ok": True, "reason": "ok",
            "detail": f"ok — authenticated as {login}"}


def _probe_bot_token(value: str) -> dict:
    """Live: Telegram getMe. The token travels in the URL (Telegram's own
    protocol), so NOTHING from a failed attempt is echoed — not the URL, not
    the exception, only this classification."""
    base = (os.environ.get("AIDOG_TG_API_URL") or "https://api.telegram.org"
            ).rstrip("/")
    url = f"{base}/bot{value}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aidog-console"})
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT) as resp:
            doc = json.loads(resp.read(64 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError:
        return {"ok": False, "reason": "unauthorized",
                "detail": "Telegram rejected this bot token"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "reason": "network",
                "detail": "could not reach Telegram"}
    if isinstance(doc, dict) and doc.get("ok") is True:
        username = str((doc.get("result") or {}).get("username") or "")
        return {"ok": True, "reason": "ok",
                "detail": f"ok — bot @{username}" if username else "ok"}
    return {"ok": False, "reason": "unauthorized",
            "detail": "Telegram rejected this bot token"}


def _probe_model_key(value: str) -> dict:
    """Live: GET {mycore.base_url}/models with the key. The base URL is
    deliberately NEVER echoed (settings allow credentials embedded in it) —
    the detail talks about the endpoint in words only."""
    try:
        base = str((load_settings().get("mycore") or {}).get("base_url") or "").strip()
    except Exception:  # noqa: BLE001 — a broken settings file is not a 500
        base = ""
    if not base:
        return {"ok": False, "reason": "no_endpoint",
                "detail": "no model endpoint configured in settings"}
    url = base.rstrip("/") + "/models"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "aidog-console", "Authorization": f"Bearer {value}"})
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
            resp.read(64 * 1024)
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        if code in (401, 403):
            return {"ok": False, "reason": "unauthorized",
                    "detail": f"the endpoint answered HTTP {code} — check "
                              f"the key"}
        return {"ok": False, "reason": "upstream_error",
                "detail": f"the endpoint answered HTTP {code}"}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {"ok": False, "reason": "network",
                "detail": "could not reach the configured model endpoint"}
    if 200 <= status < 300:
        return {"ok": True, "reason": "ok",
                "detail": "ok — endpoint accepted the key"}
    return {"ok": False, "reason": "upstream_error",
            "detail": f"the endpoint answered HTTP {status}"}


def _probe_cloudflare(value: str) -> dict:
    """Live: GET /zones (READ-ONLY) — the smallest call that proves the token
    authenticates and can see the account. Zone names are public DNS data, so
    only the COUNT is reported. The token never appears in any outcome. The
    AIDOG_CF_API_URL seam keeps the offline suite off the network."""
    base = (os.environ.get("AIDOG_CF_API_URL") or
            "https://api.cloudflare.com/client/v4").rstrip("/")
    req = urllib.request.Request(
        f"{base}/zones?per_page=1",
        headers={"User-Agent": "aidog-console",
                 "Authorization": f"Bearer {value}"})
    try:
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT) as resp:
            doc = json.loads(resp.read(64 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        if code in (401, 403):
            return {"ok": False, "reason": "unauthorized",
                    "detail": f"Cloudflare answered HTTP {code} — the token "
                              "was rejected or cannot read zones"}
        return {"ok": False, "reason": "upstream_error",
                "detail": f"Cloudflare answered HTTP {code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "reason": "network",
                "detail": "could not reach the Cloudflare API"}
    if not isinstance(doc, dict):
        return {"ok": False, "reason": "upstream_error",
                "detail": "unexpected zones payload"}
    if doc.get("success") is not True:
        return {"ok": False, "reason": "no_scope",
                "detail": "the token authenticates but the zone list was "
                          "refused — add the Zone:Read scope"}
    info = doc.get("result_info")
    total = info.get("total_count") if isinstance(info, dict) else None
    res = doc.get("result")
    seen = len(res) if isinstance(res, list) else 0
    n = total if isinstance(total, int) else seen
    if n <= 0:
        return {"ok": True, "reason": "ok",
                "detail": "ok — token accepted, but 0 zones are visible "
                          "(grant Zone:Read if you expected some)"}
    return {"ok": True, "reason": "ok",
            "detail": f"ok — token accepted · {n} zone(s) visible "
                      "(read-only test)"}


def _probe_github_token(value: str) -> dict:
    """Live: GET /user with the Connections slot (car 6). The login is public
    data (the owner runs two identities, so it matters); the token never
    appears. X-OAuth-Scopes turns 'authenticated' into 'authenticated and
    here is what it may do' — fine-grained tokens send no scope list, and
    that is stated instead of guessed. AIDOG_GH_API_URL is the offline seam
    (the same one mycore.community already uses)."""
    base = (os.environ.get("AIDOG_GH_API_URL") or
            "https://api.github.com").rstrip("/")
    req = urllib.request.Request(
        f"{base}/user",
        headers={"User-Agent": "aidog-console",
                 "Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {value}"})
    scopes = ""
    try:
        with urllib.request.urlopen(req, timeout=VERIFY_TIMEOUT) as resp:
            scopes = (resp.headers.get("X-OAuth-Scopes") or "").strip()
            doc = json.loads(resp.read(64 * 1024).decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        code = getattr(e, "code", 0)
        if code in (401, 403):
            return {"ok": False, "reason": "unauthorized",
                    "detail": f"GitHub answered HTTP {code} — the token was "
                              "rejected"}
        return {"ok": False, "reason": "upstream_error",
                "detail": f"GitHub answered HTTP {code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return {"ok": False, "reason": "network",
                "detail": "could not reach the GitHub API"}
    login = str(doc.get("login") or "") if isinstance(doc, dict) else ""
    if not login:
        return {"ok": False, "reason": "upstream_error",
                "detail": "no login in the /user response"}
    if scopes:
        return {"ok": True, "reason": "ok",
                "detail": f"ok — authenticated as {login} · scopes: {scopes}"}
    return {"ok": True, "reason": "ok",
            "detail": f"ok — authenticated as {login} · no scope list "
                      "(fine-grained token; check its repository access)"}


# slot -> live probe (absence = format check only, stated as such)
SLOT_LIVE_PROBES = {
    "AIDOG_COMMUNITY_PAT": _probe_community_pat,
    "AIDOG_GH_PAT": _probe_github_pat,
    "MYCORE_TG_TOKEN": _probe_bot_token,
    "MODEL_API_KEY": _probe_model_key,
    "CLOUDFLARE_API_TOKEN": _probe_cloudflare,
    "GITHUB_TOKEN": _probe_github_token,
}


def _verify_slot(name: str) -> dict:
    """One slot's full check row. Unset -> ok:null/not_set (honest absence);
    a shape failure short-circuits BEFORE any network call; a live probe, if
    the slot has one, decides the final level/ok/reason/detail."""
    group = SECRET_SLOTS.get(name, "core")
    value = read_env_slot(name)
    row: dict = {"name": name, "group": group, "set": value is not None,
                 "level": "format", "ok": None, "reason": "not_set",
                 "detail": "slot is empty — nothing to verify"}
    if value is None:
        return row
    fmt = _verify_format(name, value)
    row.update(fmt)
    if not fmt["ok"]:
        return row                    # fix the shape first, no network call
    probe = SLOT_LIVE_PROBES.get(name)
    if probe is None:
        return row                    # level stays "format", detail says so
    row["level"] = "live"
    try:
        row.update(probe(value))
    except Exception as e:  # noqa: BLE001 — a probe must never 500 the route
        sys.stderr.write(f"slot verify probe failed name={name} "
                         f"err={type(e).__name__}\n")
        row.update(ok=False, reason="upstream_error",
                   detail="verification could not complete")
    return row


def _verify_slots(names: list[str]) -> list[dict]:
    """Rows for every requested slot; live probes run in parallel so
    `verify all` costs one timeout, not N of them."""
    from concurrent.futures import ThreadPoolExecutor
    if not names:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(names))) as pool:
        return list(pool.map(_verify_slot, names))


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
    events += _wd_escalation_events()      # car 7: the escalation chain
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


# Extensions car (Phase B): /api/quotes gets its OWN budget too — a keyless
# market lookup must never spend the STT, /api/say or exchange-probe windows
# (the console can quote a symbol while the microphone is busy). Duplicated
# on purpose: the four windows are independent by contract.
QUOTES_RATE_LIMIT = 10    # keyless quote lookups per sliding minute
QUOTES_RATE_WINDOW = 60.0
_quotes_times: deque = deque()
_quotes_lock = threading.Lock()


def _quotes_rate_ok() -> bool:
    """Independent sliding window for GET /api/quotes — 10 lookups per
    minute on a private deque (never another endpoint's budget)."""
    now = time.time()
    with _quotes_lock:
        while _quotes_times and now - _quotes_times[0] > QUOTES_RATE_WINDOW:
            _quotes_times.popleft()
        if len(_quotes_times) >= QUOTES_RATE_LIMIT:
            return False
        _quotes_times.append(now)
        return True


class QuotesError(Exception):
    """Typed upstream failure for GET /api/quotes: an HTTP status plus an
    honest message. Carries no URL, no header and never a made-up number."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _quotes_get(url: str, headers: dict, timeout: float) -> bytes:
    """One read-only GET for the quote adapters. urllib honors the
    environment proxy, so a restricted network surfaces as an honest
    QuotesError (502/504) instead of a traceback, and a 404 from the
    upstream means the symbol does not exist."""
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise QuotesError(404, "unknown symbol upstream") from None
        raise QuotesError(502, f"quote upstream refused ({e.code})") from None
    except urllib.error.URLError as e:
        reason = str(getattr(e, "reason", "") or "")
        if "timed out" in reason.lower() or isinstance(getattr(e, "reason", None),
                                                        TimeoutError):
            raise QuotesError(504, "quote upstream timed out") from None
        raise QuotesError(502, "quote upstream unreachable") from None
    except TimeoutError:
        raise QuotesError(504, "quote upstream timed out") from None


_QUOTE_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; aidog-quotes/1.0)"}


def quotes_cn(symbol: str, timeout: float = 8.0) -> dict:
    """Sina A-share quote (keyless). The wire is GBK and the host REQUIRES a
    finance Referer; an empty row means the symbol does not exist — 404, never
    a fabricated zero. Row layout: [0] name, [2] previous close, [3] last,
    [30]/[31] Beijing date/time."""
    url = "https://hq.sinajs.cn/list=" + urllib.parse.quote(symbol)
    raw = _quotes_get(url, {**_QUOTE_HEADERS,
                            "Referer": "https://finance.sina.com.cn"}, timeout)
    text = raw.decode("gbk", errors="replace")
    m = re.search(r'"([^"]*)"', text)
    row = m.group(1).strip() if m else ""
    if not row:
        raise QuotesError(404, f"unknown A-share symbol {symbol!r}")
    f = row.split(",")
    if len(f) < 32:
        raise QuotesError(502, "unexpected Sina payload shape")
    try:
        prev = float(f[2])
        price = float(f[3])
    except (ValueError, IndexError):
        raise QuotesError(502, "unexpected Sina payload shape") from None
    change = price - prev
    pct = (change / prev * 100.0) if prev else None
    try:
        ts = int(datetime.strptime(f"{f[30]} {f[31]}", "%Y-%m-%d %H:%M:%S")
                 .replace(tzinfo=timezone(timedelta(hours=8))).timestamp())
    except Exception:  # noqa: BLE001 — odd date = fall back to "now"
        ts = int(time.time())
    return {"ok": True, "market": "cn", "symbol": symbol, "name": f[0],
            "price": price, "change": round(change, 4),
            "pct": (round(pct, 4) if pct is not None else None),
            "currency": "CNY", "ts": ts, "source": "sina"}


def quotes_us(symbol: str, timeout: float = 8.0) -> dict:
    """Yahoo Finance chart quote (keyless). An empty result with an upstream
    error description is an honest 404; a payload without a price is a 502 —
    a missing number is never rendered as 0."""
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?interval=1d&range=1d")
    raw = _quotes_get(url, _QUOTE_HEADERS, timeout)
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise QuotesError(502, "unexpected Yahoo payload shape") from None
    chart = doc.get("chart") if isinstance(doc, dict) else None
    chart = chart if isinstance(chart, dict) else {}
    result = chart.get("result") or []
    if not result:
        err = chart.get("error") if isinstance(chart.get("error"), dict) else {}
        desc = str(err.get("description") or "").strip()[:80]
        note = f" — {desc}" if desc else ""
        raise QuotesError(404, f"unknown US symbol {symbol!r}{note}")
    meta = result[0].get("meta") if isinstance(result[0], dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not isinstance(price, (int, float)):
        raise QuotesError(502, "Yahoo returned no price")
    change = (price - prev) if isinstance(prev, (int, float)) and prev else None
    pct = (change / prev * 100.0) if change is not None else None
    return {"ok": True, "market": "us",
            "symbol": str(meta.get("symbol") or symbol),
            "name": str(meta.get("shortName") or meta.get("longName") or symbol),
            "price": price,
            "change": (round(change, 4) if change is not None else None),
            "pct": (round(pct, 4) if pct is not None else None),
            "currency": str(meta.get("currency") or "USD"),
            "ts": int(meta.get("regularMarketTime") or time.time()),
            "source": "yahoo"}


def quotes_mock(base: str, market: str, symbol: str,
                timeout: float = 5.0) -> dict:
    """Offline seam (env AIDOG_QUOTES_URL): the mock answers with OUR payload
    shape so the tests exercise validation, routing and rendering without the
    internet. A mock error body maps to the same honest status codes."""
    sep = "&" if "?" in base else "?"
    url = base + sep + urllib.parse.urlencode({"market": market,
                                               "symbol": symbol})
    raw = _quotes_get(url, {"User-Agent": "aidog-quotes-test/1.0"}, timeout)
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise QuotesError(502, "mock quote payload is not JSON") from None
    if not isinstance(doc, dict):
        raise QuotesError(502, "mock quote payload is not an object")
    if doc.get("error") and doc.get("ok") is not True:
        msg = str(doc["error"])[:120]
        raise QuotesError(404 if "unknown" in msg.lower() else 502, msg)
    if doc.get("ok") is not True or not isinstance(doc.get("price"),
                                                    (int, float)):
        raise QuotesError(502, "mock quote payload missing ok/price")
    return doc


def _quotes_symbol_ok(market: str, symbol: str) -> bool:
    """Format gate, per market: cn needs the sh|sz|bj prefix (the wire has no
    exchange discriminator), us needs a bare ticker. Checked before any
    network I/O so a bad request can never reach an upstream."""
    if market == "cn":
        return bool(re.fullmatch(r"(sh|sz|bj)\d{6}", symbol))
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z.\-]{0,9}", symbol))


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


def collect_version(fresh: bool = False) -> dict:
    now = time.time()
    with _version_lock:
        if fresh:
            # `GET /api/version?fresh=1` (activation car §1.3): the update
            # banner/button must not wait out the 300s cache — additive query,
            # same payload shape as the cached read.
            _version_cache["ts"] = 0.0
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


def chat_context_usage() -> dict:
    """Wave 3 (X14): read-only context usage for the console session.

    The number is the REAL one the next model call sees: `compact.measure()`
    over (system stack + persisted session turns) — the same metric
    `run_turn()` compacts with, against `chat_budget()` (the exact threshold
    it fires at: `measure > budget`). Every field is derived from files read
    here; when a read fails the block degrades to `available:false` + the
    exception CLASS, and `budget_*` still reports the configured value —
    never a fabricated number. No message content ever leaves this function:
    counts and lengths only."""
    budget = chat_budget()
    out: dict = {"available": False, "used_chars": None, "system_chars": None,
                 "session_chars": None, "messages": None,
                 "budget_chars": budget, "threshold_chars": budget,
                 "compaction_enabled": budget is not None,
                 "over_budget": None}
    try:
        stack = build_system_stack(context_dirs=resolve_context_dirs(),
                                   user_rules=resolve_user_rules())
        history = [m for m in Session(chat_session_path()).load()
                   if m.get("role") != "system"]
        sys_chars = measure(stack)
        ses_chars = measure(history)
    except Exception as e:  # noqa: BLE001 — honest degradation, no numbers
        out["note"] = f"context read failed: {type(e).__name__}"
        return out
    used = sys_chars + ses_chars
    out.update(available=True, used_chars=used, system_chars=sys_chars,
               session_chars=ses_chars, messages=len(stack) + len(history),
               over_budget=(None if budget is None else used > budget))
    return out


def chat_history(limit: int = 200) -> dict:
    """Read-only session view for GET /api/chat/history: user/assistant turns
    only (system constitution and tool payloads never leave the server),
    content capped at CHAT_HISTORY_CAP. Loopback read, no bearer needed.
    `context` (X14) rides along: session-level usage counters, never content."""
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
    return {"messages": out, "count": len(out),
            "context": chat_context_usage()}


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


def chat_model_whitelist() -> list[str]:
    """Wave 4 (X7): allowed ids for POST /api/chat {model} — the SAME rule
    service.ChatService._allowed_models enforces for the TG /model command:
    web/settings.json mycore.models (non-empty strings; empty/absent = the
    configured model only). It reads the very document that function reads
    (see the MYCORE_KEYS note: raw-file consumers and this merged view stay
    in agreement by construction), so a console turn can never accept an id
    the TG pipeline would refuse — one whitelist, no parallel grammar."""
    mc = load_settings().get("mycore")
    raw = mc.get("models") if isinstance(mc, dict) else None
    if isinstance(raw, list):
        out = [m for m in raw if isinstance(m, str) and m]
        if out:
            return out
    return [cli.resolve_model_config().model]


def _rotate_active_session() -> Path:
    """Move the live console session file into
    archive/<stem>.<stamp>[.<n>].jsonl and return where it went. Shared by
    {action:'clear'} and the X20 resume — ONE rotation rule, one archive
    namespace, one collision counter. Raises OSError to the caller (the
    source file is never half-moved: same-directory move is atomic)."""
    src = chat_session_path()
    arch = chat_sessions_dir() / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    dst = arch / f"{CHAT_SESSION_NAME}.{stamp}.jsonl"
    n = 0
    while dst.exists():
        n += 1
        dst = arch / f"{CHAT_SESSION_NAME}.{stamp}.{n}.jsonl"
    shutil.move(str(src), str(dst))
    return dst


def clear_chat_session() -> bool:
    """Archive the console session (POST /api/chat {action:'clear'}).
    Returns True when something was rotated."""
    if not chat_session_path().is_file():
        return False
    _rotate_active_session()
    return True


def resume_chat_session(sid: str) -> tuple[int, dict]:
    """X20 live session switch (POST /api/chat {action:'resume', id}).
    Returns (http_code, payload). Steps in this exact, crash-safe order:
      1. resolve `id` — SAME grammar + resolved-parent double-check export
         uses, scope = sessions root and archive/ only (400 bad grammar /
         404 unknown, id verbatim in details[0]); the id being the LIVE file
         answers an honest no-op 200 {resumed:false, reason:'already active'};
      2. copy the target to a temp name IN THE SESSIONS ROOT (same directory
         as the active file, so the commit rename is same-directory);
      3. rotate the current active session into archive/ — the exact rotation
         {action:'clear'} uses, so the old live session survives as a copy;
      4. rename the temp onto the active path (atomic within the directory);
      5. only NOW delete the target's source copy: it moved into the live
         slot, so the list never shows it twice (a failed unlink degrades to
         an extra listed copy — never data loss, reported as
         `leftover_source:true`).
    Failures at 2/3 leave the active file byte-identical (temp removed); a
    failure at 4 restores the archived copy to the active path first — so the
    live path always ends up either the original file or the new one, never
    half of each. Nothing is deleted before the switch has committed, so the
    operation is non-destructive: the previous live session is in archive/,
    the target's only copy becomes the live session. ONE stderr audit line per
    attempt (sanitized id only — grammar-safe charset, no newlines), zero
    message content, zero credentials."""
    root = chat_sessions_dir()
    active = chat_session_path()
    audit_id = re.sub(r"[^A-Za-z0-9._/-]", "?", str(sid))[:96]

    def _audit(result: str) -> None:
        sys.stderr.write(f"chat session resume id={audit_id} result={result}\n")

    try:
        target = session_file(root, sid)
    except ValueError as e:
        _audit("bad_id")
        return 400, {"error": "validation failed", "details": [str(e)]}
    except FileNotFoundError as e:
        _audit("not_found")
        return 404, {"error": "not found", "details": [str(e)]}
    try:
        same_live = target == active.resolve()
    except OSError:
        same_live = False
    if same_live:
        _audit("noop")
        return 200, {"ok": True, "resumed": False, "id": str(sid),
                     "reason": "already active"}
    def _fail(result: str, detail: str) -> tuple[int, dict]:
        _audit(result)
        return 500, {"error": "resume failed", "details": [detail]}

    # 1. copy the target next to the active file — nothing committed yet
    stamp = int(time.time())
    tmp = root / f"{CHAT_SESSION_NAME}.resume-{stamp}.jsonl.tmp"
    n = 0
    while tmp.exists():
        n += 1
        tmp = root / f"{CHAT_SESSION_NAME}.resume-{stamp}.{n}.jsonl.tmp"
    try:
        shutil.copy2(str(target), str(tmp))
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        return _fail(f"copy_failed:{type(e).__name__}",
                     f"copy failed ({type(e).__name__})")
    # 2. rotate the live session into archive/ (clear semantics)
    archived: Path | None = None
    if active.is_file():
        try:
            archived = _rotate_active_session()
        except OSError as e:
            try:
                tmp.unlink()
            except OSError:
                pass
            return _fail(f"archive_failed:{type(e).__name__}",
                         f"archive failed ({type(e).__name__})")
    # 3. commit: the temp becomes the live session (atomic rename)
    try:
        os.replace(str(tmp), str(active))
    except OSError as e:
        restored = True
        if archived is not None:
            try:
                shutil.move(str(archived), str(active))
                archived = None
            except OSError:
                restored = False
        try:
            tmp.unlink()
        except OSError:
            pass
        if not restored:
            return _fail(f"rename_failed:{type(e).__name__}",
                         f"rename failed ({type(e).__name__}) and the "
                         f"archived copy could not be restored — the live "
                         f"session is in archive/ only")
        return _fail(f"rename_failed:{type(e).__name__}",
                     f"rename failed ({type(e).__name__})")
    # 4. committed — drop the target's now-duplicated source copy
    leftover = False
    try:
        target.unlink()
    except OSError:
        leftover = True
    _audit("ok_leftover" if leftover else "ok")
    payload: dict = {"ok": True, "resumed": True, "id": str(sid),
                     "archived": archived is not None}
    if leftover:
        payload["leftover_source"] = True
    return 200, payload


# ------------------------------------------- Wave 6 delta2: X12 auto-prune ---
# Opt-in session auto-prune (settings mycore.session_prune_days; 0/absent =
# off — the same opt-in stance as upstream). The console applies it on a
# settings write that touches the key (force) and lazily on GET /api/sessions
# at most once per hour, so a read face never becomes a busy deleter. The
# disk advisory is ONE-SHOWN per console process: the first observation of
# free space under DISK_LOW_FREE reports it, then stays silent — a nag that
# never stops is a bug, not a warning.

_PRUNE_LOCK = threading.Lock()
_PRUNE_STATE: dict = {"ran_at": 0.0,
                      "result": {"removed": 0, "kept": 0, "freed": 0,
                                 "errors": 0},
                      "advisory_sent": False}
PRUNE_INTERVAL = 3600.0            # seconds between lazy (GET-triggered) runs


def session_prune_days() -> int:
    """Threshold from settings: a positive int in 1..PRUNE_DAYS_MAX, else 0
    (off). Bools and hand-edited junk read as off — fail-silent, never a
    crash, never a delete."""
    raw = (load_settings().get("mycore") or {}).get("session_prune_days")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return 0
    return raw if 1 <= raw <= PRUNE_DAYS_MAX else 0


def disk_low_advisory(free: int | None) -> str | None:
    """X12 advisory text for `free` bytes; None = healthy or unknown (a
    failed disk_usage must never fake a warning). Pure, so the test suite
    pins both sides without filling a disk."""
    if free is None or free >= DISK_LOW_FREE:
        return None
    gib = max(int(free) // (1024 ** 3), 0)
    return (f"disk space low: {gib} GiB free — sessions and generated images "
            f"keep growing; enable session auto-prune (Settings -> Model) "
            f"or free space")


def session_prune_view(force: bool = False) -> dict:
    """X12 read face: current prune state, running the prune when due
    (force = a settings write just changed the key). Returns {enabled, days,
    removed, kept, freed, errors, ran_at, advisory} — honest zeros before
    the first run, advisory one-shown (see module note). Never raises."""
    days = session_prune_days()
    now = time.time()
    with _PRUNE_LOCK:
        if days > 0 and (force or not _PRUNE_STATE["ran_at"]
                         or now - _PRUNE_STATE["ran_at"] >= PRUNE_INTERVAL):
            try:
                res = prune_old(chat_sessions_dir(), days,
                                keep=(CHAT_SESSION_NAME,))
            except Exception as e:  # noqa: BLE001 — fail-soft, class only
                sys.stderr.write(f"session prune failed "
                                 f"err={type(e).__name__}\n")
                res = {"removed": 0, "kept": 0, "freed": 0, "errors": 1}
            _PRUNE_STATE["ran_at"] = now
            _PRUNE_STATE["result"] = res
            sys.stderr.write(f"session prune days={days} "
                             f"removed={res.get('removed', 0)} "
                             f"freed={res.get('freed', 0)}\n")
        out = {"enabled": days > 0, "days": days,
               "ran_at": _PRUNE_STATE["ran_at"] or None}
        out.update(_PRUNE_STATE["result"])
        if _PRUNE_STATE["advisory_sent"]:
            out["advisory"] = None                 # already had its one shot
            return out
        free: int | None = None
        for probe in (chat_sessions_dir(), mycore_state_dir(), REPO):
            try:
                free = shutil.disk_usage(probe).free
                break
            except OSError:
                continue
        adv = disk_low_advisory(free)
        if adv:
            _PRUNE_STATE["advisory_sent"] = True
        out["advisory"] = adv
        return out


def content_blocks_text(blocks: list) -> str:
    """X16: multimodal content blocks -> plain text: text parts joined, each
    image part an honest '[image]' marker (no base64, no URL ever re-enters
    the session file or an export)."""
    parts: list[str] = []
    for block in blocks if isinstance(blocks, list) else []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif block.get("type") == "image_url":
            parts.append("[image]")
    return " ".join(parts)


def vision_rejection_note(text: str) -> str:
    """X16 honest degradation: an image-attached turn that ended in a model
    error carrying an HTTP 4xx was REFUSED by the endpoint — for an image
    payload that means no vision support, and we say so instead of implying
    an answer exists. Non-4xx failures (transport, timeout) get no note: the
    image was not proven to be the cause."""
    note = text or ""
    if "HTTP 4" in note:
        return note + (" [vision: the endpoint rejected the image input "
                       "with HTTP 4xx — image understanding (vision) "
                       "appears unsupported on this endpoint]")
    return note


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

    stack = build_system_stack(context_dirs=resolve_context_dirs(),
                               user_rules=resolve_user_rules())
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
    development) or violates its contract. Never fabricates rows.
    Every row additionally carries `inbound_health` —
    {healthy: true|false|null, last_ok_age_sec, consec_fails, cursor} — the
    E3 poll-health view (null = never polled / unreadable = honest unknown,
    never a guessed false). The field is OMITTED entirely when the health
    read fails (shape-hide: the console shows nothing rather than a lie);
    existing row fields are never altered.
    Wave 4 (X8): the top-level `webhook` block is READ-ONLY inbound endpoint
    truth — the loopback address of POST /api/channels/webhook plus a
    secret_configured BOOL. The slot VALUE never enters this frame; the route
    itself lives in this file, so the facts answer even when the channel
    module does not."""
    webhook_facts = {
        "url": f"http://{HOST}:{PORT}/api/channels/webhook",
        "secret_configured": bool(read_env_slot("CHANNEL_WEBHOOK_SECRET")),
    }
    try:
        from mycore import channels as _channels
    except Exception as e:  # noqa: BLE001 — module may not exist yet
        sys.stderr.write(f"channels status unavailable err={type(e).__name__}\n")
        return {"channels": [], "note": "channel module unavailable",
                "webhook": webhook_facts}
    try:
        settings = load_settings()
        rows = _channels.status(settings, REPO)
    except Exception as e:  # noqa: BLE001 — status must never take the console down
        sys.stderr.write(f"channels status failed err={type(e).__name__}\n")
        return {"channels": [], "note": "channel status unavailable",
                "webhook": webhook_facts}
    if not isinstance(rows, list):
        return {"channels": [], "note": "channel status unavailable",
                "webhook": webhook_facts}
    try:
        health = _channels.inbound_health_map(settings, REPO)
    except Exception:  # noqa: BLE001 — honest absence, rows stay untouched
        health = {}
    if isinstance(health, dict):
        for row in rows:
            view = health.get(row.get("name")) if isinstance(row, dict) else None
            if isinstance(view, dict):
                row["inbound_health"] = view
    return {"channels": rows, "webhook": webhook_facts}


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
    cfg = None
    try:
        cfg = cli.resolve_model_config()
        model, upstreams_ok = cfg.model, bool(getattr(cfg, "upstreams", ()))
    except Exception as e:  # noqa: BLE001 — catalog must not fail the console
        sys.stderr.write(f"capabilities model config failed err={type(e).__name__}\n")
        model, upstreams_ok = None, False
    # v0.5.55 gateway knobs: rotation mode, global rate caps and the size of
    # the resolved chain — names/counts/booleans only (same catalog contract).
    gateway_blk = {
        "rotation": getattr(cfg, "rotation", ""),
        "max_rpm": getattr(cfg, "max_rpm", 0),
        "max_tpm": getattr(cfg, "max_tpm", 0),
        "endpoints_count": len(getattr(cfg, "upstreams", ()) or ()),
    }
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
    # 0.5.44 mission summary: counts + tier only (fail-soft). Goal text stays
    # off the no-auth catalog — the board lives at GET /api/mission.
    try:
        _mreg = mission.load(mission.default_path())
        _mact = mission.active_mission(_mreg)
        _mblock = settings.get("mission")
        _mtier = _mblock.get("reporting") if isinstance(_mblock, dict) else None
        if _mtier not in mission.REPORTING_TIERS:
            _mtier = mission.DEFAULT_REPORTING
        mission_blk = {"active": 1 if _mact else 0,
                       "running": bool(_mact
                                        and _mact.get("state") == "running"),
                       "total": len(_mreg.get("missions") or []),
                       "reporting": _mtier}
    except Exception as e:  # noqa: BLE001 — probe must never break the catalog
        sys.stderr.write(f"capabilities mission probe failed err={type(e).__name__}\n")
        mission_blk = {"active": 0, "running": False, "total": 0,
                       "reporting": mission.DEFAULT_REPORTING}
    return {
        "version": VERSION,
        "mycore": {"model": model, "upstreams_configured": upstreams_ok,
                   "mcp_servers": mcp_block,
                   "gateway": gateway_blk,
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
        # 0.5.44: mission board summary (active count + reporting tier)
        "mission": mission_blk,
    }



def collect_modeltier() -> dict:
    """GET /api/status -> `modeltier` block (Wave 3 X6): the failover chain's
    health/cooling state, one row per CHAIN endpoint (primary first).

    Facts come from the SAME structures ModelConfig.describe() logs:
    HealthRegistry identities are `<base_url>|<slot NAME>` — slot NAMES only —
    and the row label is sanitize_endpoint() output (no query/userinfo/
    fragment, long path segments dropped), so this block serializes ZERO key
    material by construction (the smoke sweeps it for sk-/key/secret).

    `healthy` is tri-state BY DESIGN: null = never observed in THIS process
    (cooldown state is deliberately not persisted — modeltier's stated scope),
    false = a unit of that endpoint is cooling right now, true = observed and
    not cooling. `scope` names that honesty: this is the console process's
    registry — chat turns executed by the TG service live in THAT process.
    `last_error_class` is deliberately absent: the registry stores counts and
    clocks, not error classes, and inventing one would be a lie.
    Fail-soft like every other status block: never a 500."""
    out: dict = {"available": False, "scope": "console-process",
                 "endpoints": [], "cooling": 0}
    try:
        cfg = cli.resolve_model_config()
        health = modeltier.SHARED_HEALTH
        now = time.time()
        rows: list[dict] = []
        for idx, (base_url, key_env, _model) in enumerate(cfg.chain()):
            slots = tuple(s for s in (key_env or "").split(",") if s) or ("",)
            idents = [modeltier.HealthRegistry.identity(base_url, s)
                      for s in slots]
            seen = any(health.observed(i) for i in idents)
            remaining = max((health.remaining(i) for i in idents), default=0.0)
            healthy = (None if not seen else
                       False if remaining > 0 else True)
            row: dict = {"name": sanitize_endpoint(base_url)
                         or f"endpoint-{idx + 1}",
                         "healthy": healthy, "primary": idx == 0}
            if remaining > 0:
                # registry clocks are monotonic; project the remainder onto
                # wall time so the console can show a countdown.
                row["cooling_remaining_s"] = round(remaining, 1)
                row["cooling_until"] = round(now + remaining, 1)
            rows.append(row)
        out.update(available=True, endpoints=rows,
                   cooling=sum(1 for r in rows if r.get("healthy") is False))
        return out
    except Exception as e:  # noqa: BLE001 — status must never 500 the console
        sys.stderr.write(f"modeltier status failed err={type(e).__name__}\n")
        out["note"] = f"unavailable: {type(e).__name__}"
        return out


def cron_row(job: dict, now: float | None = None) -> dict:
    """One display row for GET /api/cron AND the create response — the SAME
    fixed field whitelist for both, so a console-created job and a CLI/NL job
    are indistinguishable in shape (the scheduler's own display fields plus
    the optional `name` label); no filesystem path can enter the payload by
    construction."""
    from mycore import scheduler as _scheduler   # noqa: PLC0415 — lazy import
    now = time.time() if now is None else now
    return {
        "id": str(job.get("id", "")),
        "type": str(job.get("type", "")),
        "spec": _scheduler.describe(job),
        "name": str(job.get("name", "")),
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
    }


def collect_cron() -> dict:
    """GET /api/cron — read-only view of the natural-language cron registry
    (frontend coverage 1.4). Fail-soft by contract: an absent, unreadable or
    corrupt state file answers {"jobs": [], "count": 0}, never a 500, and
    nothing here mutates the registry (load_jobs hands back fresh dicts).

    Rows are built by `cron_row` — a fixed field whitelist (the scheduler's
    own display fields: id/spec/name/text/origin/due marks) — so no
    filesystem path can enter this payload by construction."""
    try:
        from mycore import scheduler as _scheduler
        jobs = _scheduler.load_jobs(_scheduler.default_jobs_path())
        now = time.time()
        rows = [cron_row(job, now) for job in jobs]
        return {"jobs": rows, "count": len(rows)}
    except Exception as e:  # noqa: BLE001 — catalog must not break the console
        sys.stderr.write(f"cron list failed err={type(e).__name__}\n")
        return {"jobs": [], "count": 0}


# ------------------------------------- agent backup (CKX-P2, car 8) ---------
CKX_JOB_NAME = "ckx-backup"            # the ONE scheduled-backup job label
CKX_DEFAULT_SPEC = "daily 03:00"        # what the console pre-fills

# The model-visible instruction behind `kind: prompt`. It is deliberately
# narrow: the tool already returns metadata only, so this text's job is to
# keep the reply to THAT and to name the two ways a run can come back short.
# Nothing here can cause a payload or a passphrase to be produced — the tool
# has no argument that would allow one.
CKX_SCHEDULE_PROMPT = (
    "Run the `backup_run` tool once (it takes no arguments) and report ONLY "
    "the JSON metadata it returns: ok, file, bytes, sha256, verified, "
    "roundtrip, ms and quota. If the tool answers with an ERROR line, report "
    "that reason verbatim and stop — do not retry more than once. Never "
    "print a backup file's contents, any exported section, or any "
    "passphrase: the tool does not return them and you must not try to read "
    "them. When quota.state is `warn`, say the free-call budget is running "
    "low; when it is `over`, say the meter must be reset in the console "
    "after checking the plan.")


def collect_backup_agent() -> dict:
    """GET /api/backup/agent — the CKX-P2 status face (car 8).

    Fail-soft by contract: a broken registry, an unreadable meter or a
    missing Node runtime each degrade to an honest field, never a 500. This
    payload carries NAMES, NUMBERS AND PRESENCE ONLY — the passphrase slot is
    reported as `set: bool`, never as a value, and no filesystem path enters
    it (the schedule row is built by the same whitelist cron_row uses)."""
    out: dict = {
        "node": {"ok": False, "version": "", "reason": "probe unavailable"},
        "password_slot": {"set": False},
        "quota": {"used": 0, "cap": 0, "warn_at": 0, "state": "unknown"},
        "scheduled": None,
        "mcp": {"enabled": False},
    }
    try:
        from mycore import ckxbackup as _ckx   # noqa: PLC0415 — lazy import
    except Exception as e:  # noqa: BLE001 — class name only
        out["node"]["reason"] = type(e).__name__
        return out
    try:
        st = _ckx.node_status()
        out["node"] = {"ok": bool(st.get("ok")),
                       "version": str(st.get("version") or ""),
                       "reason": str(st.get("reason") or "")}
        out["password_slot"] = {"set": bool(_ckx.password_value())}
        out["quota"] = _ckx.quota_status()
    except Exception as e:  # noqa: BLE001 — class name only
        out["node"]["reason"] = type(e).__name__
    # schedule row: the fixed job label, built from cron_row's whitelist
    try:
        from mycore import scheduler as _sch   # noqa: PLC0415 — lazy import
        for job in _sch.load_jobs(_cron_jobs_path()):
            if str(job.get("name") or "") == CKX_JOB_NAME:
                row = cron_row(job)
                out["scheduled"] = {"id": row["id"], "name": row["name"],
                                    "spec": row["spec"], "kind": row["kind"],
                                    "next": row["next"], "done": row["done"]}
                break
    except Exception:  # noqa: BLE001 — an absent/corrupt registry reads null
        out["scheduled"] = None
    # MCP switch: the EFFECTIVE config (env override > this server's stored
    # value), so an env override can never claim a state the runtime lacks
    # and the read face always matches what mcp_on/mcp_off just wrote.
    try:
        raw = _effective_mcp_text()
        out["mcp"] = {"enabled": any(
            isinstance(e, dict) and e.get("name") == _ckx.MCP_SERVER_NAME
            for e in (json.loads(raw) if raw.strip() else []))}
    except Exception:  # noqa: BLE001 — invalid config reads as off, honestly
        out["mcp"] = {"enabled": False}
    return out


def backup_agent_schedule(spec: str | None) -> tuple[int, dict]:
    """POST action=schedule — every pre-flight gate BEFORE the registry is
    touched, so a refusal never leaves a half-created job behind.

    Gate order (owner contract): Node >= 20 -> passphrase slot present ->
    local call meter not over (D12 quota gate) -> spec grammar -> idempotent
    same-name detection. `origin` stays None on purpose: delivery falls back
    to MYCORE_WD_CHAT, else log-only, exactly like every other console job."""
    from mycore import ckxbackup as _ckx      # noqa: PLC0415 — lazy import
    from mycore import scheduler as _sch      # noqa: PLC0415 — lazy import
    node = _ckx.node_status()
    if not node["ok"]:
        return 409, {"error": "agent backup unavailable",
                     "details": [node["reason"] or "node runtime unavailable"]}
    if not _ckx.password_value():
        return 409, {"error": "agent backup unavailable",
                     "details": [f"{_ckx.PASSWORD_SLOT} slot is not set "
                                 f"(Settings > Credentials)"]}
    quota = _ckx.quota_status()
    if quota["state"] == "over":
        return 409, {"error": "call quota exhausted",
                     "details": [f"{quota['used']}/{quota['cap']} local calls "
                                 f"counted — reset the meter after checking "
                                 f"your plan"]}
    text = spec if isinstance(spec, str) and spec.strip() else CKX_DEFAULT_SPEC
    try:
        jobs = _sch.load_jobs(_cron_jobs_path())
    except OSError as e:  # noqa: BLE001 — type name only
        return 500, {"error": f"registry unreadable: {type(e).__name__}"}
    for job in jobs:
        if str(job.get("name") or "") == CKX_JOB_NAME:
            if _sch.describe(job) == text.strip():
                return 200, {"ok": True, "already": True,
                             "job": cron_row(job)}
            return 409, {"error": "already scheduled",
                         "details": [f"{CKX_JOB_NAME} runs "
                                     f"{_sch.describe(job)} — remove it "
                                     f"before changing the schedule"]}
    try:
        job = _sch.create_job(jobs, text, CKX_SCHEDULE_PROMPT,
                              kind="prompt", source="command",
                              name=CKX_JOB_NAME)
        _sch.save_jobs(_cron_jobs_path(), jobs)
    except _sch.SpecError as e:
        return 400, {"error": "validation failed", "details": [str(e)]}
    except OSError as e:  # noqa: BLE001 — type name only
        return 500, {"error": f"registry not writable: {type(e).__name__}"}
    sys.stderr.write(f"ckx schedule spec={_sch.describe(job)} kind={job['kind']}\n")
    return 200, {"ok": True, "job": cron_row(job)}


def backup_agent_unschedule() -> tuple[int, dict]:
    """POST action=unschedule — delete by the FIXED job id lookup (name),
    never by a client-supplied id: this face owns exactly one job."""
    from mycore import scheduler as _sch      # noqa: PLC0415 — lazy import
    try:
        jobs = _sch.load_jobs(_cron_jobs_path())
    except OSError as e:  # noqa: BLE001 — type name only
        return 500, {"error": f"registry unreadable: {type(e).__name__}"}
    hit = next((j for j in jobs
                if str(j.get("name") or "") == CKX_JOB_NAME), None)
    if hit is None:
        return 404, {"error": "not scheduled"}
    if not _sch.delete_job(jobs, str(hit.get("id") or "")):
        return 404, {"error": "not scheduled"}
    try:
        _sch.save_jobs(_cron_jobs_path(), jobs)
    except OSError as e:  # noqa: BLE001 — type name only
        return 500, {"error": f"registry not writable: {type(e).__name__}"}
    return 200, {"ok": True, "removed": CKX_JOB_NAME}


def _settings_mcp_text() -> str:
    """The RAW stored mycore.mcp string (hand-written keys aside). Reading
    the file, not load_settings(): the merge view cannot be written back
    without dropping what it filtered."""
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    mc = stored.get("mycore") if isinstance(stored, dict) else None
    raw = mc.get("mcp") if isinstance(mc, dict) else None
    return raw if isinstance(raw, str) else ""


def _effective_mcp_text() -> str:
    """The config the runtime will actually use: env MYCORE_MCP when set,
    else the stored mycore.mcp — the SAME precedence mycore.mcp.raw_config
    applies, read through THIS server's settings seam (the file the agent
    face writes), so the read face and the write face can never disagree."""
    env = os.environ.get("MYCORE_MCP", "").strip()
    return env if env else _settings_mcp_text()


def backup_agent_mcp(on: bool) -> tuple[int, dict]:
    """POST action=mcp_on | mcp_off — insert/replace (or remove) ONLY the
    `ckx` entry of mycore.mcp; every other server the operator configured
    survives byte-identical. The result goes through the SAME
    validate_settings_patch + deep_merge-onto-RAW-store write path as the
    settings face, so an unknown hand-written key can never be dropped and a
    rejected value is refused before anything is written."""
    from mycore import ckxbackup as _ckx      # noqa: PLC0415 — lazy import
    if on:
        node = _ckx.node_status()
        if not node["ok"]:
            return 409, {"error": "agent backup unavailable",
                         "details": [node["reason"]
                                     or "node runtime unavailable"]}
        if not _ckx.password_value():
            return 409, {"error": "agent backup unavailable",
                         "details": [f"{_ckx.PASSWORD_SLOT} slot is not set "
                                     f"(Settings > Credentials)"]}
    raw = _settings_mcp_text()
    try:
        entries = json.loads(raw) if raw.strip() else []
    except ValueError:
        return 409, {"error": "mycore.mcp is not valid JSON",
                     "details": ["fix it in Settings > Model first"]}
    if not isinstance(entries, list):
        return 409, {"error": "mycore.mcp must be a JSON array",
                     "details": ["fix it in Settings > Model first"]}
    keep = [e for e in entries
            if not (isinstance(e, dict)
                    and e.get("name") == _ckx.MCP_SERVER_NAME)]
    already = len(keep) != len(entries)
    if on:
        if len(keep) >= mcp.MAX_SERVERS:
            return 409, {"error": "mcp server slots full",
                         "details": [f"at most {mcp.MAX_SERVERS} servers — "
                                     f"remove one first"]}
        keep.append(_ckx.ckx_server(REPO))
        want = True
    else:
        want = False
        if not already:
            return 200, {"ok": True, "changed": False,
                         "mcp": {"enabled": False}}
    text = json.dumps(keep, separators=(",", ":")) if keep else ""
    patch, errors = validate_settings_patch({"mycore": {"mcp": text}})
    if errors:
        return 400, {"error": "validation failed", "details": errors}
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    merged = deep_merge(stored, patch)
    changed = merged != stored
    if changed:
        try:
            atomic_write_json(SETTINGS_FILE, merged)
        except OSError as e:  # noqa: BLE001 — type name only
            return 500, {"error": f"persist failed: {type(e).__name__}"}
        sys.stderr.write(f"settings updated keys=mycore.mcp "
                         f"ckx={'on' if on else 'off'}\n")
    return 200, {"ok": True, "changed": changed, "mcp": {"enabled": want}}


def _repo_relative(path: object) -> str:
    """Repo-relative POSIX path, or the bare file name for a path outside the
    repo — the console never serializes a machine path (the standing rule for
    every payload this server builds)."""
    raw = str(path or "")
    if not raw:
        return ""
    p = Path(raw)
    try:
        return p.resolve().relative_to(REPO.resolve()).as_posix()
    except (OSError, ValueError):
        return p.name


def checkpoint_row(entry: dict) -> dict:
    """One X10 row for GET /api/checkpoints — METADATA ONLY: id, timestamp,
    snapshot path + size + sha256. The snapshot BYTES never leave
    mycore.checkpoint (a checkpoint may cover a credential file), so this row
    is the whole payload, always — no content, no preview, no inline base64."""
    ts = entry.get("ts")
    size = entry.get("size")
    try:
        ts_f = float(ts) if ts is not None else 0.0
    except (TypeError, ValueError):
        ts_f = 0.0
    return {
        "id": int(entry.get("id", -1)),
        "ts": ts_f,
        "when": checkpoint.fmt_when(ts_f),
        "path": _repo_relative(entry.get("path")),
        "file": str(entry.get("file") or ""),
        "size": int(size) if isinstance(size, (int, float)) else 0,
        "sha256": str(entry.get("sha256") or ""),
    }


def collect_checkpoints() -> dict:
    """GET /api/checkpoints — the X10 read face. Fail-soft by contract: an
    absent, unreadable or corrupt index answers {entries: [], count: 0}, never
    a 500 (mycore.checkpoint re-reads the index per call, so this list is live
    and needs no cache)."""
    try:
        entries = checkpoint.list_entries(limit=0)   # rotation caps at 100
    except Exception as e:  # noqa: BLE001 — catalog must not break the console
        sys.stderr.write(f"checkpoint list failed err={type(e).__name__}\n")
        return {"entries": [], "count": 0}
    rows = [checkpoint_row(e) for e in entries]
    return {"entries": rows, "count": len(rows)}


def _state_file_count(state: Path) -> int | None:
    """Files under <profile>/state — the SAME test aidog_persist's rm guard
    applies (empty dirs are scaffolding, not state). None = unreadable, which
    the catalog reports honestly and the UI treats as NOT removable."""
    try:
        return sum(1 for p in state.rglob("*") if p.is_file())
    except OSError:
        return None


def collect_profiles() -> dict:
    """GET /api/profiles — the X11 read face, the console mirror of
    `aidog profile list`. Presence / counts / port ONLY: a slot value or a
    file byte never enters the payload (an absent credential answers
    `secrets: false`, never a redacted string), and the DEFAULT row always
    describes the implicit no-AIDOG_PROFILE profile so a per-profile env can
    never skew the catalog. Fail-soft: one unreadable profile degrades to
    `state_files: null`, never a 500."""
    try:
        active = profile_paths.profile_name()   # "" = the default profile
    except Exception:  # noqa: BLE001 — an invalid env must not 500 a GET
        active = ""

    def build(name: str, state: Path, secrets: Path, settings: Path,
              port: int | None, is_default: bool) -> dict:
        files = _state_file_count(state)
        return {
            "name": name,
            "default": is_default,
            "active": (active == "") if is_default else (name == active),
            "secrets": secrets.is_file(),
            "settings": settings.is_file(),
            "web_port": port,
            "state_dir": _repo_relative(state),
            "state_files": files,
            "can_remove": (not is_default) and files == 0,
        }

    rows = [build("default", REPO / "data" / "mycore_service",
                  REPO / "secrets.env", REPO / "web" / "settings.json",
                  8790, True)]
    root = profile_paths.profiles_root(REPO)
    if root.is_dir():
        for p in sorted(root.iterdir()):
            if not p.is_dir() or not PROFILE_NAME_RE.match(p.name):
                continue      # invalidly-named dirs are listed but never adopted
            meta: dict = {}
            try:
                doc = json.loads((p / "profile.json").read_text(encoding="utf-8"))
                meta = doc if isinstance(doc, dict) else {}
            except (OSError, ValueError):
                meta = {}
            port = meta.get("web_port")
            rows.append(build(p.name, p / "state", p / "secrets.env",
                              p / "settings.json",
                              port if isinstance(port, int) else None, False))
    return {"profiles": rows, "count": len(rows), "active": active,
            "profiles_root": _repo_relative(root)}


def collect_mission() -> dict:
    """GET /api/mission — the mission board (0.5.44). Fail-soft by contract:
    an absent, unreadable or corrupt registry answers the honest empty
    structure ({reporting, active: null, recent: [], count: 0}), never a 500.
    Rows come from mission.public_row — a fixed field whitelist: `goal` is
    user input and echoes back; origin (a personal chat id), the capability
    manifest and every internal path stay out of the payload by construction."""
    try:
        block = load_settings().get("mission")
        tier = block.get("reporting") if isinstance(block, dict) else None
    except Exception:  # noqa: BLE001 — a settings hiccup must not 500 the board
        tier = None
    if tier not in mission.REPORTING_TIERS:
        tier = mission.DEFAULT_REPORTING
    rows: list[dict] = []
    try:
        doc = mission.load(mission.default_path())
        rows = [mission.public_row(m) for m in (doc.get("missions") or [])]
    except Exception as e:  # noqa: BLE001 — catalog must not break the console
        sys.stderr.write(f"mission list failed err={type(e).__name__}\n")
    active = next((r for r in rows if r.get("state") in mission.ACTIVE_STATES), None)
    recent = [r for r in reversed(rows)
              if r.get("state") not in mission.ACTIVE_STATES][:10]
    return {"reporting": tier, "active": active, "recent": recent,
            "count": len(rows)}


def collect_mission_detail(mid: str) -> tuple[dict, int]:
    """GET /api/mission?id=<mid> — one mission for the task board (car 2).
    Same gate as the list (loopback GET, no auth) and the same whitelist
    discipline: public_row PLUS the structured plan (null for a mission that
    predates car 2 / was never planned), the scrubbed tail of the event
    stream (<=20 {ts, kind, text}) and the resolved budget with honest usage.
    Unknown id -> 404. Never credentials, origin, manifest or a path."""
    try:
        path = mission.default_path()
        doc = mission.load(path)
        rec = mission.find(doc.get("missions") or [], mid)
        if rec is None:
            return {"error": "not found", "details": ["unknown mission id"]}, 404
        detail = dict(mission.public_row(rec))
        detail["plan"] = mission.plan_detail(rec)
        detail["events"] = mission.detail_events(path, rec["id"])
        detail["budget_used"] = mission.budget_used(rec)
        return {"mission": detail}, 200
    except Exception as e:  # noqa: BLE001 — a bad record must not 500 the board
        sys.stderr.write(f"mission detail failed err={type(e).__name__}\n")
        return {"error": f"detail failed: {type(e).__name__}"}, 500


# --------------------------------------------------- SNIFF / SENSE board ---
# Read model + the two write actions of the sense board (SNIFF car). The
# whole surface is read-only with respect to the outside world: GET renders
# local state, POST runs a read-only sweep or marks a finding acknowledged.
# mycore.sniff has no actuation code path at all (asserted in core tests).
SNIFF_FINDINGS_PAGE = 50


def collect_bridge_status() -> dict:
    """GET /api/bridge/status — the Bridge card's read model (car 6).

    Non-secret by construction: the `bridge` settings block holds a URL, host
    patterns and a number, and an audit row holds a host, a path, a byte count
    and a reason WORD. No credential can be here, and no audit line ever
    carries a query string."""
    cfg = bridge.config()
    return {
        "enabled": bool(cfg["enabled"]),
        "worker_url": cfg["worker_url"],
        "allow": list(cfg["allow"]),
        "allow_auth": list(cfg["allow_auth"]),
        "max_bytes": cfg["max_bytes"],
        # `routing` = would a request actually be proxied right now? (It needs
        # the switch, the URL AND at least one allowed host — three ways to be
        # off, all reported honestly.)
        "routing": bool(cfg["enabled"] and cfg["worker_url"] and cfg["allow"]),
        "audit": bridge.audit_tail(),
        "audit_file": bridge.audit_file().name,
        "template": "/api/bridge/template",
        "readme": "/api/bridge/template?file=readme",
    }


def collect_sniff() -> dict:
    """GET /api/sniff — the SENSE board read model (loopback, no auth, same
    contract as GET /api/mission). Config comes from settings (a hand-edited
    bad partition fails closed to an honest `config_error`), statuses from
    the sweep state, findings newest-first from the local log.

    No credential material: slot NAMES only, and every finding payload was
    already field-whitelisted + truncated by mycore.sniff."""
    now = time.time()
    try:
        cfg = sniff.parse_config(sniff.settings_block(), strict_rules=False)
        config_error = ""
    except sniff.SniffError as e:
        cfg = sniff.parse_config({}, strict_rules=False)
        config_error = str(e)[:160]
    state = sniff.load_state()
    entries = state.get("sources") if isinstance(state.get("sources"), dict) else {}
    findings = sniff.load_findings()
    findings.sort(key=lambda row: float(row.get("last_seen") or 0.0),
                  reverse=True)
    return {
        "enabled": cfg["enabled"],
        "pause": cfg["pause"],
        "interval_s": cfg["interval_s"],
        "quiet_hours": cfg["quiet_hours"],
        "digest_cap": cfg["digest_cap"],
        "config_error": config_error,
        "last_swept": float(state.get("last_swept") or 0.0),
        "sources": [sniff.source_status(s, entries.get(s["id"]), now)
                    for s in cfg["sources"]],
        "rules": [{k: r[k] for k in ("id", "source", "when", "severity",
                                      "speak")} for r in cfg["rules"]],
        "rule_errors": dict(cfg["rule_errors"]),
        "findings": findings[:SNIFF_FINDINGS_PAGE],
        "findings_total": len(findings),
        "read_only": True,
    }


# ------------------------------------- activation & operations (0.5.40) ----

def endpoint_reachable(base: str, timeout: float = 1.0) -> bool:
    """Is the configured model endpoint answering? Reuses the console's own
    probes (`port_open` / `http_status`, the retry pair inside http_status
    keeps the whole probe at ≈2s): a 2xx on <base>/models or <base>/v1/models
    proves reachability; a refused port is down; an auth-gated gateway that
    refuses the probe still counts as reachable because its PORT is open.
    Never raises — an unparsable URL is simply unreachable."""
    if not isinstance(base, str) or not base:
        return False
    try:
        parts = urllib.parse.urlparse(base)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    probe = base.rstrip("/")
    probe = f"{probe}/models" if probe.endswith("/v1") else f"{probe}/v1/models"
    if http_status(probe, timeout=timeout) is not None:
        return True
    return port_open(port)


# ----------------------------------- upstream connectivity test (Wave 3 X5) -

def sanitize_endpoint(url: object) -> str:
    """Display label for an endpoint: scheme://host[:port]/path with userinfo,
    query and fragment STRIPPED — a key or token parked anywhere in the URL
    must never serialize into a response, a log line or an assertion payload.
    Path segments of 16+ chars are dropped as well (a path-embedded credential
    is the one shape stripping cannot recognize); the short segments stay, so
    `/v1` survives while `/sk-...` does not. Empty string for a non-http(s)
    or hostless value."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        port = None
    path = (parts.path or "").rstrip("/")
    segments = [s for s in path.split("/") if s]
    if any(len(seg) >= 16 for seg in segments):
        # a path-embedded credential is the one shape stripping cannot
        # recognize: drop only the suspicious segment(s), keep the rest
        path = "/" + "/".join(s for s in segments if len(s) < 16)
    return urllib.parse.urlunsplit(
        (parts.scheme, f"{host}:{port}" if port else host, path, "", ""))


def _probe_key(key_env: str) -> str:
    """First populated slot of an endpoint's credential pool -> used for ONE
    probe request header inside this process and dropped immediately. Env
    wins over the secrets.env slot (mycore.model's own resolution order), a
    missing slot degrades to no auth header. The value is never returned to
    a caller other than this in-process probe, never logged, never echoed."""
    for name in (s for s in (key_env or "").split(",") if s):
        try:
            value = os.environ.get(name, "") or (read_env_slot(name) or "")
        except Exception:  # noqa: BLE001 — a failed read = probe without auth
            value = ""
        if value:
            return value
    return ""


def probe_endpoint(base_url: str, key: str = "",
                   timeout: float | None = None) -> dict:
    """ONE connectivity probe: GET <base>/v1/models (the OpenAI-style list
    endpoint every configured gateway answers; <base>/models when the base
    already ends in /v1) with a 5s cap.

    Returns {name, ok, latency_ms, error_class} where `error_class` is an
    EXCEPTION CLASS NAME or "HTTP <status>" and nothing else — the response
    body, the probed query string and `key` never leave this frame (X5 red
    line). `key` rides one Authorization header in-process only; empty = an
    unauthenticated probe of a public endpoint. Never raises: every failure
    class is an honest row."""
    budget = UPSTREAM_TEST_TIMEOUT if timeout is None else timeout
    name = sanitize_endpoint(base_url) or "endpoint"
    base = str(base_url or "").rstrip("/")
    probe = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    headers = {"User-Agent": "aidog-console/1"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(probe, headers=headers, method="GET")
    started = time.monotonic()

    def ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        with urllib.request.urlopen(req, timeout=budget) as resp:
            status = int(getattr(resp, "status", 200) or 200)
            try:
                resp.read(2048)       # drain a little; bytes are never returned
            except Exception:  # noqa: BLE001 — a truncated body says nothing
                pass
    except urllib.error.HTTPError as e:
        # HTTP class only: reason / body / url stay in this frame.
        return {"name": name, "ok": False, "latency_ms": ms(),
                "error_class": f"HTTP {int(e.code)}"}
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        cls = type(reason).__name__ if reason is not None else "URLError"
        return {"name": name, "ok": False, "latency_ms": ms(),
                "error_class": cls}
    except Exception as e:  # noqa: BLE001 — class name only, never the text
        return {"name": name, "ok": False, "latency_ms": ms(),
                "error_class": type(e).__name__}
    if 200 <= status < 300:
        return {"name": name, "ok": True, "latency_ms": ms(),
                "error_class": None}
    return {"name": name, "ok": False, "latency_ms": ms(),
            "error_class": f"HTTP {status}"}


def upstream_probe_targets(url: str | None = None
                           ) -> tuple[list[tuple[str, str]], str | None]:
    """(targets, error_detail) for POST /api/mycore/upstreams/test.

    Each target = (base_url, key value). With `url` given: exactly that
    endpoint (ad-hoc probe, no credential — a URL we did not configure gets
    no slot). With `url` omitted: the EFFECTIVE failover chain WITHOUT the
    primary (env > settings, the same precedence mycore.cli resolves at run
    time; the primary endpoint is already probed live by the status model
    panel). An invalid spec answers its parse reason as the detail — the
    parser never echoes a URL. Key VALUES are read here for one header and
    never travel further (zero-echo)."""
    if url is not None:
        try:
            parts = urllib.parse.urlsplit(url)
        except ValueError:
            return [], "url must be an http(s) endpoint"
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return [], "url must be an http(s) endpoint with a host"
        return [(url, "")], None
    try:
        cfg = cli.resolve_model_config()
    except Exception as e:  # noqa: BLE001 — class name only
        return [], f"model config unavailable: {type(e).__name__}"
    if not cfg.upstreams and cfg.upstreams_note:
        return [], f"mycore.upstreams: {cfg.upstreams_note}"
    return [(base, _probe_key(key_env))
            for base, key_env, _model in cfg.chain()[1:]], None


# ---------------------------------------------- D1 model discovery (round D) --
def _discover_timeout() -> float:
    """Seconds allowed to ONE discovery fetch. Default = the documented 10s
    ceiling; AIDOG_DISCOVER_TIMEOUT is the offline-test seam and is CLAMPED
    to that same ceiling, so a seam can only ever make the test faster — it
    can never ask the product to wait longer than it promises."""
    raw = (os.environ.get("AIDOG_DISCOVER_TIMEOUT") or "").strip()
    try:
        value = float(raw) if raw else DISCOVER_TIMEOUT_MAX
    except ValueError:
        value = DISCOVER_TIMEOUT_MAX
    return min(DISCOVER_TIMEOUT_MAX, max(DISCOVER_TIMEOUT_MIN, value))


def _model_id_ok(value: str) -> bool:
    """The SAME grammar POST /api/settings applies to mycore.models entries:
    a discovery answer offers only ids the save path would accept, so the
    operator can never pick a chip that the whitelist refuses later."""
    return bool(value) and value == value.strip() and len(value) <= MODEL_ID_MAX \
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _discover_echo(base: str) -> str:
    """The submitted endpoint echoed back WITHOUT userinfo, query or
    fragment: settings may carry a credential in any of those, and nothing
    we answer, log or raise may repeat it. The REQUEST is sent as given —
    only the echo is stripped."""
    try:
        parts = urllib.parse.urlsplit(base)
        netloc = parts.netloc.rsplit("@", 1)[-1]   # drop user:pass@host
        return urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, "", ""))
    except ValueError:
        return ""


def _discover_candidates(base: str) -> list[str]:
    """GET targets in order. {base}/models first — this product stores
    mycore.base_url WITH the /v1 suffix (the same convention
    _probe_model_key already uses) — then {base}/v1/models when the path does
    not already end in /v1, so a bare vendor host typed into the field still
    resolves the standard directory. Both candidates are read-only directory
    reads; only 404/405 moves on to the next one."""
    try:
        parts = urllib.parse.urlsplit(base)
    except ValueError:
        return [base.rstrip("/") + "/models"]
    tail = parts.path.rstrip("/")

    def build(suffix: str) -> str:
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, tail + suffix, parts.query, ""))

    out = [build("/models")]
    if not tail.endswith("/v1"):
        out.append(build("/v1/models"))
    return out


def _discover_auth(base: str, api_key: str) -> tuple[str, str]:
    """api_key rule (D1), the three cases, in order:
      1. an inline key travels ONLY as this host's Authorization header;
      2. an empty key against the CONFIGURED endpoint (settings
         mycore.base_url) resolves the saved MODEL_API_KEY slot — handy, and
         the value never leaves this machine;
      3. an empty key against any other endpoint sends NO Authorization, so
         an honest 401 tells the operator a key is needed.
    The returned value lives in the caller's frame only — it is never echoed,
    never logged and never serialized."""
    if api_key:
        return api_key, "key"
    stored = ""
    try:
        mc = load_settings().get("mycore")
        stored = (str(mc.get("base_url") or "").strip()
                  if isinstance(mc, dict) else "")
    except Exception:  # noqa: BLE001 — an unreadable store answers "no slot"
        stored = ""
    if stored and base.rstrip("/") == stored.rstrip("/"):
        try:
            value = (os.environ.get("MODEL_API_KEY")
                     or read_env_slot("MODEL_API_KEY") or "")
        except Exception:  # noqa: BLE001 — an unreadable store reads absent
            value = ""
        if value:
            return value, "slot"
    return "", "none"


def discover_models(base: str, api_key: str = "") -> dict:
    """D1: one read-only GET of {base}/models — a directory read, so zero
    tokens are spent and no completion is ever touched.

    Success -> {ok, base_url (sanitized echo), count, total, truncated, auth,
    models:[{id}]}; failure -> {ok:false, reason} where reason is ONLY an HTTP
    status, an exception class or one of the fixed guard words
    (body-too-large / not-compatible): never the URL, never a query, never a
    body line, never the key. `total` is what the endpoint listed, `count` is
    what we can honestly offer (grammar-clean, key-free, capped at 200), so
    `count < total` — by either cap or filter — is visible instead of silent.
    A reflecting endpoint cannot echo the key back either: an id containing
    it is dropped like a malformed one."""
    key, auth = _discover_auth(base, api_key)
    try:
        parts = urllib.parse.urlsplit(base)
        scheme, host = parts.scheme, parts.hostname
    except ValueError:
        scheme, host = "", None
    if scheme not in ("http", "https") or not host:
        # defense in depth: this function is only ever reached through the
        # handler's 400, and a non-http scheme would otherwise be fetched by
        # urllib's other openers (ftp, file) — refuse here as well.
        return {"ok": False, "reason": "bad-url", "auth": auth}
    headers = {"User-Agent": "aidog-console"}
    if key:
        headers["Authorization"] = "Bearer " + key
    timeout = _discover_timeout()
    candidates = _discover_candidates(base)
    raw = b""
    status = 0
    reason = "HTTP 404"
    for i, url in enumerate(candidates):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                raw = resp.read(DISCOVER_BODY_MAX + 1)
        except urllib.error.HTTPError as e:
            status = int(getattr(e, "code", 0) or 0)
            reason = f"HTTP {status}"
            if status in (404, 405) and i + 1 < len(candidates):
                continue                       # try the standard /v1 path
            return {"ok": False, "reason": reason, "auth": auth}
        except urllib.error.URLError as e:
            inner = getattr(e, "reason", None)
            return {"ok": False, "auth": auth, "reason":
                    type(inner).__name__ if inner is not None else "URLError"}
        except Exception as e:  # noqa: BLE001 — class name only, never text
            return {"ok": False, "reason": type(e).__name__, "auth": auth}
        break                                   # a real response: stop asking
    else:
        return {"ok": False, "reason": reason, "auth": auth}
    if not (200 <= status < 300):
        return {"ok": False, "reason": f"HTTP {status}", "auth": auth}
    if len(raw) > DISCOVER_BODY_MAX:
        return {"ok": False, "reason": "body-too-large", "auth": auth}
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — JSONDecodeError etc.
        return {"ok": False, "reason": type(e).__name__, "auth": auth}
    entries = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return {"ok": False, "reason": "not-compatible", "auth": auth}
    total = len(entries)
    models: list[str] = []
    for item in entries:
        ident = item.get("id") if isinstance(item, dict) else item
        if not isinstance(ident, str) or not _model_id_ok(ident):
            continue                            # a save-refused id: not offered
        if key and key in ident:
            continue                            # zero echo: a reflecting endpoint
        models.append(ident)
        if len(models) >= DISCOVER_MODELS_MAX:
            break
    return {"ok": True, "base_url": _discover_echo(base),
            "count": len(models), "total": total,
            "truncated": len(models) < total, "auth": auth,
            "models": [{"id": m} for m in models]}


def _slot_present(name: str) -> bool:
    """Presence of ONE model-key slot: process env first (operator override
    wins), then the secrets.env slot the console writes. Value never leaves
    this boolean."""
    try:
        return bool(os.environ.get(name) or read_env_slot(name))
    except Exception:  # noqa: BLE001 — an unreadable store reads as absent
        return False


def _decision_row(mc: dict | None = None) -> dict:
    """Car 4 (D6): the Model panel's decision endpoint row. Presence/flags
    only — the key VALUE never serializes, and `url`/`model` echo the RAW
    stored strings (empty = the vendor default) so the form round-trips
    instead of painting the defaults into the inputs. Fail-soft: an unreadable
    client still answers a well-formed row with an honest reason WORD."""
    try:
        from mycore import decision as _deci      # noqa: PLC0415 — lazy import
    except Exception as e:  # noqa: BLE001 — class name only
        return {"enabled": False, "url": "", "model": "", "router_model": "",
                "key_slot": "DECISION_API_KEY", "key_set": False,
                "active": False, "reason": type(e).__name__}
    if mc is None:
        settings = load_settings()
        raw = settings.get("mycore") if isinstance(settings.get("mycore"), dict) else None
        mc = raw if isinstance(raw, dict) else {}
    block = mc.get("decision") if isinstance(mc, dict) else None
    block = block if isinstance(block, dict) else {}
    try:
        cfg = _deci.config()
        reason, active, key_set = cfg["reason"], cfg["active"], cfg["key_set"]
    except Exception as e:  # noqa: BLE001 — class name only
        reason, active, key_set = type(e).__name__, False, False
    return {"enabled": block.get("enabled") is True,
            "url": block.get("url") if isinstance(block.get("url"), str) else "",
            "model": block.get("model") if isinstance(block.get("model"), str) else "",
            "router_model": (block.get("router_model")
                             if isinstance(block.get("router_model"), str) else ""),
            "key_slot": _deci.KEY_ENV, "key_set": key_set,
            "active": active, "reason": reason}


def collect_endpoints() -> dict:
    """GET /api/mycore/endpoints (v0.5.55) — the normalized chain for the
    Model panel. `source` names which track configured it:
    env | endpoints (structured) | upstreams (one-line) | none | invalid.

    Each row is {url, model, keys:[{slot, set}], primary:bool} — slot NAMES
    and presence booleans only, never a value (zero-echo), so the panel can
    render chips and route per-key probes without ever seeing a secret.
    The one-line track parses into the SAME row shape (dual-track UI: an old
    config looks identical to a new one). Fail-soft: a parse problem answers
    200 with source=invalid + note, never a 500."""
    settings = load_settings()
    mc = settings.get("mycore") if isinstance(settings.get("mycore"), \
                                               dict) else {}
    out: dict = {"source": "none", "endpoints": [],
                 "rotation": "", "max_rpm": 0, "max_tpm": 0,
                 "decision": _decision_row(mc), "note": ""}
    if isinstance(mc, dict):
        out["rotation"] = mc.get("rotation") if mc.get("rotation") in (
            "ordered", "round_robin") else ""
        for key, attr, ceiling in (("max_rpm", "max_rpm", modeltier.MAX_RPM),
                                   ("max_tpm", "max_tpm", modeltier.MAX_TPM)):
            val = mc.get(key)
            if type(val) is int and 0 <= val <= ceiling:
                out[attr] = val
    env_spec = (os.environ.get("MYCORE_UPSTREAMS") or "").strip()
    structured = mc.get("endpoints") if isinstance(mc, dict) else None
    line_spec = (mc.get("upstreams") if isinstance(mc, dict) else "") or ""
    chain: list[tuple[str, str, str]] = []
    try:
        cfg = cli.resolve_model_config()
        primary = (cfg.base_url, cfg.api_key_env, cfg.model)
    except Exception as e:  # noqa: BLE001 — class name only
        return {**out, "note": f"model config unavailable: {type(e).__name__}"}
    if env_spec:
        chain = [primary] + list(cfg.upstreams)
        if cfg.upstreams or not cfg.upstreams_note:
            out["source"] = "env"
        else:
            out["source"] = "invalid"
            out["note"] = f"MYCORE_UPSTREAMS: {cfg.upstreams_note}"
    elif isinstance(structured, list) and structured:
        chain = [primary] + list(cfg.upstreams)
        if cfg.upstreams or not cfg.upstreams_note:
            out["source"] = "endpoints"
        else:
            out["source"] = "invalid"
            out["note"] = f"mycore.endpoints: {cfg.upstreams_note}"
    elif line_spec.strip():
        chain = [primary] + list(cfg.upstreams)
        if cfg.upstreams or not cfg.upstreams_note:
            out["source"] = "upstreams"
        else:
            out["source"] = "invalid"
            out["note"] = f"mycore.upstreams: {cfg.upstreams_note}"
    else:
        chain = [primary]
    rows: list[dict] = []
    for idx, item in enumerate(chain):
        # chain mixes the primary (plain 3-tuple) with cfg.upstreams entries
        # (Upstream dataclasses) — normalize to (base, key_env, model).
        if isinstance(item, modeltier.Upstream):
            base, key_env, model = (item.base_url, item.key_env, item.model)
        else:
            base, key_env, model = item
        slots = [s for s in (key_env or "").split(",") if s] or [""]
        rows.append({
            "url": base,
            "model": model,
            "primary": idx == 0,
            "keys": [{"slot": s, "set": _slot_present(s)} if s else
                     {"slot": "", "set": False} for s in slots],
        })
    out["endpoints"] = rows
    # full model-key catalog presence (10 slots) so the panel can render
    # pool chips for slots no endpoint references yet — presence booleans
    # only, values never (zero-echo).
    out["key_presence"] = {name: _slot_present(name)
                           for name in MODEL_KEY_SLOTS}
    return out


def collect_setup() -> dict:
    """GET /api/setup — the activation checklist (loopback READ, no auth).

    Presence/booleans only: every row carries a state word and a hint, never a
    value (the credential values stay inside read_env_slot). Item order is
    fixed (SETUP_ITEMS + model_endpoint + service) and `ready` = every
    REQUIRED row ok + the model endpoint reachable — the wizard's own
    `aidog setup --check` pending semantics, mirrored. The service row is not
    part of `ready`: a bot may be deliberately off (fail-closed pending).
    """
    items: list[dict] = []
    for item_id, slot, hint_key, hint_en in SETUP_ITEMS:
        value = read_env_slot(slot)
        required = slot in REQUIRED_SLOTS
        present = bool(value)
        # An EMPTY value is pending (not a format violation); a non-empty value
        # that breaks the mirrored wizard pattern is invalid (fmt False).
        fmt = (not present) or bool(re.match(SLOT_PATTERNS[slot], value))
        state = "empty" if not present else ("set" if fmt else "invalid")
        ok = (state == "set") if required else (state != "invalid")
        items.append({"id": item_id, "required": required, "ok": ok,
                      "fmt": fmt, "state": state,
                      "hint": hint_key, "hint_en": hint_en})
    base = str((load_settings().get("mycore") or {}).get("base_url") or "")
    url_fmt = bool(re.match(r"^https?://\S+$", base))
    reachable = endpoint_reachable(base) if url_fmt else False
    if not base:
        ep_state = "empty"
    elif not url_fmt:
        ep_state = "invalid"
    else:
        ep_state = "reachable" if reachable else "unreachable"
    items.append({"id": "model_endpoint", "required": True,
                  "ok": bool(url_fmt and reachable), "fmt": url_fmt,
                  "state": ep_state, "hint": "act.hint.model_endpoint",
                  "hint_en": "any http(s) OpenAI-compatible base_url"})
    svc_state = mycore_service_facts()["state"]
    items.append({"id": "service", "required": False,
                  "ok": svc_state == "running", "fmt": None,
                  "state": svc_state, "hint": "act.hint.service",
                  "hint_en": "chat service (Telegram bot) state"})
    ready = all(i["ok"] for i in items if i["required"])
    return {"ready": ready, "items": items, "service": svc_state,
            "model": {"endpoint": base, "reachable": reachable}}


def _tmux_session_exists(name: str) -> bool:
    """tmux session probe for the restart gate — read-only, 5s cap, never
    raises (a missing tmux binary reads as 'no session' = honest 409)."""
    try:
        return subprocess.run(["tmux", "has-session", "-t", name],
                              capture_output=True, timeout=5).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def restart_chat_service() -> tuple[int, dict]:
    """POST /api/service/restart core (§1.2): restart ONLY the chat-service
    tmux session. The web console is NEVER restarted from here — it comes back
    through boot/upgrade paths only (restart it and this request dies mid-flight).

    Session names are the real ones: `aidog_svc` for the default profile
    (deploy/boot_services.sh) and `aidog_svc_<name>` for a named profile, whose
    start interface is `deploy/profile_ctl.sh start <name>` (idempotent: it only
    creates the console session when it is missing, so it never bounces this
    console). Logs carry the session name and a return code — nothing else.
    """
    try:
        profile = profile_paths.profile_name()
    except Exception as e:  # noqa: BLE001 — ProfileError: fail-closed, no guess
        return 503, {"error": f"profile resolution failed: {type(e).__name__}"}
    session = f"aidog_svc_{profile}" if profile else "aidog_svc"
    if not _tmux_session_exists(session):
        return 409, {"error": "chat service not running under tmux",
                     "target": "service"}
    try:
        subprocess.run(["tmux", "kill-session", "-t", session],
                       capture_output=True, timeout=10)   # rc ignored by design
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"service restart kill failed session={session} "
                         f"rc={type(e).__name__}\n")
        return 500, {"error": "session kill failed"}
    if profile:
        cmd = ["bash", str(REPO / "deploy/profile_ctl.sh"), "start", profile]
    else:
        cmd = ["bash", str(REPO / "deploy/boot_services.sh"), "start"]
    rc = -1
    try:
        rc = subprocess.run(cmd, cwd=str(REPO), capture_output=True,
                            timeout=25).returncode
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"service restart requested target=service "
                         f"session={session} rc=timeout\n")
        return 504, {"error": "service start timed out"}
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"service restart requested target=service "
                         f"session={session} rc={type(e).__name__}\n")
        return 500, {"error": "service start failed"}
    sys.stderr.write(f"service restart requested target=service "
                     f"session={session} rc={rc}\n")
    if rc != 0:
        return 500, {"error": "service start failed", "rc": rc}
    return 200, {"ok": True, "target": "service"}


def _cron_jobs_path() -> Path:
    """Cron registry seam (one line so tests can point POST /api/cron at a
    temp registry). Production callers never pass anything."""
    from mycore import scheduler as _scheduler   # noqa: PLC0415 — lazy import
    return _scheduler.default_jobs_path()


def cron_delete(job_id: str) -> tuple[int, dict]:
    """POST /api/cron {action:'del'} — mirrors the CLI `--del` semantics of
    mycore.scheduler exactly: load -> delete_job -> save, unknown id = a plain
    refusal (404 here, rc 1 there) and NOTHING is written when nothing matched."""
    from mycore import scheduler as _scheduler   # noqa: PLC0415 — lazy import
    try:
        jobs = _scheduler.load_jobs(_cron_jobs_path())
        if not _scheduler.delete_job(jobs, job_id):
            return 404, {"error": "unknown job id"}
        _scheduler.save_jobs(_cron_jobs_path(), jobs)
    except OSError as e:  # noqa: BLE001 — type name only, never a path/value
        sys.stderr.write(f"cron del failed err={type(e).__name__}\n")
        return 500, {"error": f"persist failed: {type(e).__name__}"}
    return 200, {"ok": True, "id": job_id, "count": len(jobs)}


def cron_create(name: str, schedule: str, prompt: str,
                kind: str = "message") -> tuple[int, dict]:
    """POST /api/cron {action:'create'} — console-side create through the SAME
    write path as every other entry point (one registry, one grammar):
    load_jobs -> scheduler.create_job -> save_jobs on `_cron_jobs_path()`
    (production: default_jobs_path, the file the CLI and the model tool use).
    `parse_spec` decides the schedule, `create_job` decides name/text/kind —
    its SpecError maps to 400 with the scheduler's own operator-facing
    reason, so no second validation grammar exists. The response row comes
    from `cron_row`, i.e. byte-identical in shape to a GET /api/cron row.
    `source='command'` marks the structured console/CLI family (SOURCES)."""
    from mycore import scheduler as _scheduler   # noqa: PLC0415 — lazy import
    try:
        jobs = _scheduler.load_jobs(_cron_jobs_path())
        job = _scheduler.create_job(jobs, schedule, prompt, kind=kind,
                                    source="command", name=name)
        _scheduler.save_jobs(_cron_jobs_path(), jobs)
    except _scheduler.SpecError as e:
        return 400, {"error": "validation failed", "details": [str(e)]}
    except OSError as e:  # noqa: BLE001 — type name only, never a path/value
        sys.stderr.write(f"cron create failed err={type(e).__name__}\n")
        return 500, {"error": f"persist failed: {type(e).__name__}"}
    sys.stderr.write(f"cron create ok kind={job.get('kind')} "
                     f"type={job.get('type')}\n")
    return 200, {"ok": True, "job": cron_row(job)}


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
        if path == "/api/control":
            # Car 7 (D11): the leash read face — pause state + budget status.
            # No auth (same contract as /api/status: names/numbers, no secrets).
            return self._json(collect_control())
        if path == "/api/config":
            return self._json(read_config_bits())
        if path == "/api/settings":
            return self._json(load_settings())
        if path == "/api/backup/export":
            return self._handle_backup_export()
        if path == "/api/backup/agent":
            return self._handle_backup_agent_get()
        if path == "/api/setup":
            return self._json(collect_setup())
        if path == "/api/usage":
            return self._json(collect_usage(load_settings()))
        if path == "/api/usage/self":
            return self._json(collect_usage_self())
        if path == "/api/mycore/endpoints":
            return self._json(collect_endpoints())
        if path == "/api/events":
            return self._json(collect_events())
        if path == "/api/evolution":
            return self._json(evolution.status_summary())
        if path == "/api/ledger":
            # D2 card status: settings + the local state dir, no secrets and
            # no full install id (prefix only — a local cross-check handle).
            cfg = ledger.effective_settings(load_settings())
            return self._json({
                "enabled": cfg["enabled"],
                "has_endpoint": bool(cfg["endpoint"]),
                "last_report": ledger.last_report_day(MYCORE_STATE_DIR / "ledger"),
                "install_id_prefix": ledger.install_id_prefix_at(
                    MYCORE_STATE_DIR / "ledger"),
            })
        if path == "/api/chat/history":
            return self._json(chat_history())
        if path == "/api/version":
            query = urllib.parse.parse_qs(self.path.split("?", 1)[1]) \
                if "?" in self.path else {}
            fresh = query.get("fresh", [""])[0] == "1"
            return self._json(collect_version(fresh=fresh))
        if path == "/api/skills":
            return self._json(collect_skills())
        if path == "/api/skills/remote":
            return self._json(collect_skills_remote())
        if path == "/api/discussions":
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) \
                if "?" in self.path else {}
            return self._json(collect_discussions(
                fresh=q.get("fresh", [""])[0] == "1"))
        if path == "/api/discussions/categories":
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) \
                if "?" in self.path else {}
            return self._json(collect_categories(
                fresh=q.get("fresh", [""])[0] == "1"))
        if path == "/api/discussions/replies":
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) \
                if "?" in self.path else {}
            raw = (q.get("number") or [None])[0]
            payload = collect_replies(
                raw, fresh=q.get("fresh", [""])[0] == "1")
            if payload.get("ok") is False:      # local validation, not upstream
                return self._json(payload, 400)
            return self._json(payload)
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
        if path == "/api/checkpoints":
            return self._handle_checkpoints_get()
        if path == "/api/profiles":
            return self._json(collect_profiles())
        if path == "/api/sessions":
            return self._handle_sessions_get()
        if path == "/api/artifacts":
            return self._handle_artifacts_get()
        if path == "/api/mission":
            mid = ""
            if "?" in self.path:
                mid = (urllib.parse.parse_qs(
                    self.path.split("?", 1)[1]).get("id") or [""])[0].strip()
            if mid:
                payload, code = collect_mission_detail(mid)
                return self._json(payload, code)
            return self._json(collect_mission())
        if path == "/api/sniff":
            return self._json(collect_sniff())
        if path == "/api/bridge/status":
            return self._json(collect_bridge_status())
        if path == "/api/bridge/template":
            return self._handle_bridge_template()
        if path == "/api/exchange/quote":
            return self._handle_exchange_quote()
        if path == "/api/quotes":
            return self._handle_quotes()
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
        if path == "/api/control":
            return self._handle_control()
        if path == "/api/secrets":
            return self._handle_secrets_write()
        if path == "/api/backup/restore":
            return self._handle_backup_restore()
        if path == "/api/backup/agent":
            return self._handle_backup_agent_post()
        if path == "/api/extensions/intent":
            return self._handle_intent()
        if path == "/api/slots/verify":
            return self._handle_slot_verify()
        if path == "/api/discussions/create":
            return self._handle_discussion_write("create")
        if path == "/api/discussions/comment":
            return self._handle_discussion_write("comment")
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
        if path == "/api/cron":
            return self._handle_cron_post()
        if path == "/api/checkpoints":
            return self._handle_checkpoints_post()
        if path == "/api/profiles":
            return self._handle_profiles_post()
        if path == "/api/mycore/upstreams/test":
            return self._handle_upstreams_test()
        if path == "/api/models/discover":
            return self._handle_models_discover()
        if path == "/api/mission":
            return self._handle_mission_post()
        if path == "/api/sniff":
            return self._handle_sniff_post()
        if path == "/api/service/restart":
            return self._handle_service_restart()
        if path == "/api/update/apply":
            return self._handle_update_apply()
        if path == "/api/doctor/run":
            return self._handle_doctor_run()
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
        if group is None and MODEL_KEY_SLOT_RE.match(name):
            # v0.5.55: the 8 model-key pool slots (MODEL_UP_K1..K8) are
            # writable here but deliberately NOT listed by GET /api/secrets
            # (the fixed catalog stays 35 rows) — the Model endpoints panel
            # shows their presence instead. Same value rules as every slot.
            group = "model"
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
        fmt_pat = SLOT_WRITE_PATTERNS.get(name)
        if fmt_pat is None and MODEL_KEY_SLOT_RE.match(name):
            # same shape rule as MODEL_API_KEY (the wizard's mirror): a model
            # key is at least 8 non-space chars, one line.
            fmt_pat = SLOT_PATTERNS["MODEL_API_KEY"]
        if value and fmt_pat and not re.match(fmt_pat, value):
            # per-slot shape rule (activation car §1.9): mirrors the setup
            # wizard; the stored value is NEVER echoed back in the refusal.
            return self._json({"error": "validation failed",
                               "details": [f"invalid format for {name}: expected "
                                           f"{fmt_pat} (value not shown)"]}, 400)
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

    # ---------------------------------- backup / restore (CKX-P1) ----------
    def _handle_backup_export(self) -> None:
        """GET /api/backup/export — THE ONLY route in this server that returns
        a credential slot VALUE (car 3, D4).

        Gate order: Bearer WEB_TOKEN first (401 / 503 — never a partial read),
        then the `include` allowlist (400). WEB_TOKEN itself is presence-only
        and cannot ride: it is not in BACKUP_SECRET_SLOTS. This response is
        never written to stderr, to the event stream or to any log line; the
        access log carries the request PATH (section names) and nothing else.
        Loopback only, like every other route here."""
        if not self._require_token():
            return
        raw = None
        if "?" in self.path:
            raw = (urllib.parse.parse_qs(
                self.path.split("?", 1)[1]).get("include") or [None])[0]
        if raw is None or not raw.strip():
            include = BACKUP_INCLUDES
        else:
            parts = [p.strip() for p in raw.split(",") if p.strip()]
            unknown = [p for p in parts if p not in BACKUP_INCLUDES]
            if not parts or unknown:
                bad = unknown[0][:64] if unknown else raw[:64]
                return self._json(
                    {"error": "validation failed",
                     "details": ["include must be a comma list of "
                                 f"{','.join(BACKUP_INCLUDES)} (bad: {bad})"]},
                    400)
            include = tuple(dict.fromkeys(parts))
        try:
            payload = backup_payload(include)
        except BackupReadError as e:
            return self._json({"error": "backup read failed",
                               "reason": e.reason}, e.status)
        return self._json({"ok": True, "schema": BACKUP_SCHEMA,
                           "schemaVersion": BACKUP_SCHEMA_VERSION,
                           "created": int(time.time()),
                           "payload": payload})

    def _handle_backup_restore(self) -> None:
        """POST /api/backup/restore — apply a decrypted backup envelope.

        Envelope validation is all-or-nothing (400: schema / version / shape —
        nothing is written before every check passes). Section CONTENT problems
        are honest PARTIAL results: 200 when everything requested landed, 503
        with per-slot / per-file reasons when any section refused. The log
        lines below carry names and counts only — never a value."""
        if not self._require_token():
            return
        doc = self._read_json_body(limit=BACKUP_MAX_BODY)
        if doc is None:
            return
        details: list[str] = []
        if doc.get("schema") != BACKUP_SCHEMA:
            details.append(f"schema must be {BACKUP_SCHEMA}")
        if doc.get("schemaVersion") != BACKUP_SCHEMA_VERSION:
            details.append(f"schemaVersion must be {BACKUP_SCHEMA_VERSION}")
        payload = doc.get("payload")
        if not isinstance(payload, dict):
            details.append("payload must be an object")
        apply = doc.get("apply")
        if not isinstance(apply, dict):
            details.append("apply must be an object")
            apply = {}
        for key in apply:
            if key not in BACKUP_INCLUDES:
                details.append(f"unknown apply section: {str(key)[:32]}")
            elif not isinstance(apply[key], bool):
                details.append(f"apply.{key} must be a boolean")
        if isinstance(payload, dict):
            for key in BACKUP_INCLUDES:
                if apply.get(key) is True and not isinstance(payload.get(key),
                                                              dict):
                    details.append(f"payload.{key} must be an object")
        if details:
            return self._json({"error": "validation failed",
                               "details": details}, 400)
        if not any(apply.get(key) is True for key in BACKUP_INCLUDES):
            return self._json(
                {"error": "validation failed",
                 "details": ["apply must select at least one section"]}, 400)
        out: dict = {}
        if apply.get("secrets") is True:
            out["secrets"] = backup_apply_secrets(payload.get("secrets") or {})
        if apply.get("settings") is True:
            out["settings"] = backup_apply_settings(payload.get("settings") or {})
        if apply.get("memory") is True:
            out["memory"] = backup_apply_memory(payload.get("memory") or {})
        ok = True
        if "secrets" in out:
            ok = ok and not out["secrets"]["failed"]
        if "settings" in out:
            ok = ok and out["settings"].get("applied") is True
        if "memory" in out:
            ok = ok and not out["memory"]["failed"]
        return self._json({"ok": ok, **out}, 200 if ok else 503)

    # ---------------------- agent backup control (CKX-P2, car 8) ----------
    def _handle_backup_agent_get(self) -> None:
        """GET /api/backup/agent — the CKX-P2 status face. NO auth (same
        contract as /api/status and /api/cron: names, numbers and presence
        only), never a slot value, never a file path, never a payload."""
        return self._json(collect_backup_agent())

    def _handle_backup_agent_post(self) -> None:
        """POST /api/backup/agent {action[, spec]} — Bearer WEB_TOKEN: this
        face WRITES (a cron job, the MCP config, the call meter), so it sits
        behind the same gate as every other write here. The value of the
        passphrase slot never enters this frame's response, its stderr line
        or an exception."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action == "schedule":
            spec = doc.get("spec")
            if spec is not None and (not isinstance(spec, str)
                                     or len(spec) > 120
                                     or any(ord(c) < 32 for c in spec)):
                return self._json(
                    {"error": "validation failed",
                     "details": ["spec must be a single line of at most "
                                 "120 chars"]}, 400)
            code, body = backup_agent_schedule(spec)
            return self._json(body, code)
        if action == "unschedule":
            code, body = backup_agent_unschedule()
            return self._json(body, code)
        if action == "quota_reset":
            from mycore import ckxbackup as _ckx  # noqa: PLC0415 — lazy import
            try:
                quota = _ckx.quota_reset()
            except _ckx.CkxError as e:
                return self._json({"error": str(e)}, 500)
            sys.stderr.write("ckx quota reset\n")
            return self._json({"ok": True, "quota": quota})
        if action in ("mcp_on", "mcp_off"):
            if os.environ.get("MYCORE_MCP", "").strip():
                return self._json(
                    {"error": "mcp configured by environment",
                     "details": ["MYCORE_MCP overrides settings — unset it "
                                 "to switch MCP from the console"]}, 409)
            code, body = backup_agent_mcp(action == "mcp_on")
            return self._json(body, code)
        return self._json(
            {"error": "validation failed",
             "details": ["action must be one of: schedule | unschedule | "
                         "quota_reset | mcp_on | mcp_off"]}, 400)

    def _handle_intent(self) -> None:
        """POST /api/extensions/intent — car 4 (D6) call site 1: which
        Extensions tab owns this request.

        Bearer WEB_TOKEN is required because this is the ONE route that
        forwards operator-typed text to the configured decision endpoint —
        it is never anonymous and never automatic (the operator presses the
        button). Every non-success is an honest 200 {available:false, reason:
        WORD} so the UI can say "no fast path" instead of inventing a tab;
        a bad body is a 400 and a missing token a 401/503, the usual ladder.
        The text itself never reaches a log line."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        text = doc.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "validation failed",
                               "details": ["text must be a non-empty string"]}, 400)
        if len(text) > INTENT_TEXT_MAX:
            return self._json(
                {"error": "validation failed",
                 "details": [f"text too long (max {INTENT_TEXT_MAX} chars)"]}, 400)
        try:
            from mycore import decision as _deci   # noqa: PLC0415 — lazy import
        except Exception as e:  # noqa: BLE001 — class name only
            return self._json({"available": False, "reason": type(e).__name__})
        try:
            got = _deci.route_intent(text)
        except _deci.DecisionError as e:
            return self._json({"available": False, "reason": e.reason})
        except Exception as e:  # noqa: BLE001 — class name only, never the text
            return self._json({"available": False, "reason": type(e).__name__})
        return self._json({"available": True, **got})

    # -------------------------------- community writes (0.5.54) -----------
    def _handle_discussion_write(self, action: str) -> None:
        """POST /api/discussions/create|comment — Bearer WEB_TOKEN, then one
        mycore.community write. Validation answers 400 before any outbound
        call; every upstream failure lands on the shared ladder (404/409/429/
        502) with a category WORD plus an upstream-safe detail — never the
        token, never a traceback. A successful write refreshes the list
        cache and (for a reply) that thread's cache so the very next GET
        shows the new content without waiting out the TTL."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        from mycore import community as _comm
        details: list[str] = []
        body = doc.get("body")
        if not isinstance(body, str) or not body.strip():
            details.append("body is required")
        elif len(body) > _comm.BODY_MAX:
            details.append(f"body too long (max {_comm.BODY_MAX} chars)")
        if action == "create":
            title = doc.get("title")
            if not isinstance(title, str) or not title.strip():
                details.append("title is required")
            elif len(title) > _comm.TITLE_MAX:
                details.append(f"title too long (max {_comm.TITLE_MAX} chars)")
            category = doc.get("category")
            if not isinstance(category, str) or not category.strip():
                details.append("category is required")
        else:
            number = doc.get("number")
            if isinstance(number, bool) or not isinstance(number, int) \
                    or number <= 0:
                details.append("number must be a positive integer")
        if details:
            return self._json({"error": "validation failed",
                               "details": details}, 400)
        try:
            if action == "create":
                res = _comm.create(doc.get("title"), body,
                                   doc.get("category"))
                bust_discussions_cache()
            else:
                res = _comm.comment(doc.get("number"), body)
                bust_replies_cache(res.get("number"))
        except _comm.CommunityError as e:
            status, payload = _community_error_payload(e)
            return self._json(payload, status)
        except Exception as e:  # noqa: BLE001 — honest degradation, no internals
            sys.stderr.write(f"discussion write failed err={type(e).__name__}\n")
            return self._json({"ok": False, "reason": "upstream_error"}, 502)
        return self._json({"ok": True, **res})

    # -------------------------------- slot verification (0.5.54) -----------
    def _handle_slot_verify(self) -> None:
        """POST /api/slots/verify {name}|{all:true} — Bearer WEB_TOKEN, then
        one check row per requested slot: {name, group, set, level, ok,
        reason, detail}. Two levels, both honest about what they proved:
          format — the stored VALUE's shape against a conservative rule
                   (never echoed back, only the rule's expectation);
          live   — a real remote probe for the six slots that have one
                   (community PAT -> Discussions scope, GH PAT -> /user,
                   bot token -> getMe, model key -> GET {base}/models,
                   Cloudflare token -> GET /zones (read-only),
                   connections GitHub token -> GET /user + scope list).
        Unset slots answer ok:null / reason not_set — absence is information,
        not a failure. Every live probe runs with a 5s ceiling and reports
        a category word only; the model key probe never echoes the base URL
        (it may carry embedded credentials) nor the key itself."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        name = doc.get("name")
        all_slots = doc.get("all") is True
        if all_slots:
            names = list(SECRET_SLOTS) + list(SECRET_PRESENCE_ONLY)
        elif isinstance(name, str) and name:
            if name in SECRET_SLOTS or name in SECRET_PRESENCE_ONLY:
                names = [name]
            else:
                return self._json({"error": "validation failed",
                                   "details": [f"unknown slot: {name[:64]}"]},
                                  400)
        else:
            return self._json({"error": "validation failed",
                               "details": ["pass a slot name or all:true"]},
                              400)
        # one row per name; live probes run in parallel so `all` stays snappy
        rows = _verify_slots(names)
        return self._json({"results": rows,
                           "checked_at": datetime.now(timezone.utc)
                           .isoformat(timespec="seconds")})

    # -------------------------------------- bridge (car 6, line 5 / D9) ----
    BRIDGE_TEMPLATES: dict = {
        "worker": ("deploy/bridge/worker.js", "text/javascript; charset=utf-8"),
        "readme": ("deploy/bridge/README.md", "text/markdown; charset=utf-8"),
    }

    def _handle_bridge_template(self) -> None:
        """GET /api/bridge/template[?file=worker|readme] — the guided Worker
        template straight from the repo, so the console, the docs and the
        shipped file can never drift into two versions of the same script.
        Read-only and unauthenticated like every other GET here; the file
        carries no credential by construction."""
        which = "worker"
        if "?" in self.path:
            which = (urllib.parse.parse_qs(
                self.path.split("?", 1)[1]).get("file") or ["worker"])[0]
        entry = self.BRIDGE_TEMPLATES.get(which)
        if entry is None:
            return self._json({"error": "not found"}, 404)
        rel, mime = entry
        try:
            data = (REPO / rel).read_bytes()
        except OSError:
            return self._json({"error": "template unavailable"}, 503)
        return self._send(200, data, mime)

    # ------------------------------------ activation & operations (0.5.40) ---
    def _handle_service_restart(self) -> None:
        """POST /api/service/restart {target:'service'} — Bearer gate, then
        the target check, then restart_chat_service(). The chat service only:
        this console never restarts itself. 400 bad target · 409 no tmux
        session (honest) · 503/401 from the standard gate."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        if doc.get("target") != "service":
            return self._json({"error": "validation failed",
                               "details": ["target must be 'service'"]}, 400)
        code, out = restart_chat_service()
        return self._json(out, code)

    def _handle_update_apply(self) -> None:
        """POST /api/update/apply — queue `aidog_update.py apply` in a
        DETACHED child and answer 202 immediately: apply ends with
        `boot_services.sh restart`, so this console may die mid-flight (the
        design). stdout+stderr append to data/update_apply.log; the request
        body is drained but never logged. The script's own dirty-worktree
        guard decides whether anything actually runs."""
        if not self._require_token():
            return
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return self._json({"error": f"body too large (max {MAX_BODY} bytes)"},
                              413)
        if length:
            self.rfile.read(length)          # drained, never inspected/logged
        log = REPO / "data" / "update_apply.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            fh = open(log, "ab")
            try:
                fh.write(("--- update apply queued "
                          f"{datetime.now(timezone.utc).isoformat()} "
                          "(console) ---\n").encode("utf-8"))
                fh.flush()
                subprocess.Popen(
                    [sys.executable, "deploy/aidog_update.py", "apply"],
                    cwd=str(REPO), stdin=subprocess.DEVNULL,
                    stdout=fh, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True)
            finally:
                fh.close()
        except OSError as e:  # noqa: BLE001 — type name only
            sys.stderr.write(f"update apply queue failed err={type(e).__name__}\n")
            return self._json({"error": f"queue failed: {type(e).__name__}"}, 500)
        sys.stderr.write("update apply queued\n")
        return self._json({"ok": True, "queued": True}, 202)

    def _handle_doctor_run(self) -> None:
        """POST /api/doctor/run — run deploy/aidog_doctor.py once (60s cap),
        parse its own summary line and pass up to 80 output lines through
        verbatim. Doctor's contract: values are never printed, so the payload
        carries no credential material. A NON-ZERO exit is not an error —
        FAIL/WARN counts are honest output and still answer 200."""
        if not self._require_token():
            return
        try:
            proc = subprocess.run([sys.executable, "deploy/aidog_doctor.py"],
                                  cwd=str(REPO), capture_output=True, text=True,
                                  timeout=60)
        except subprocess.TimeoutExpired:
            return self._json({"error": "doctor run timed out (60s)"}, 504)
        except OSError as e:  # noqa: BLE001
            return self._json({"error": f"launch failed: {type(e).__name__}"}, 500)
        text = (proc.stdout or "")
        if (proc.stderr or "").strip():
            text += ("\n" if text and not text.endswith("\n") else "") + proc.stderr
        lines = text.splitlines()
        m = re.search(r"(\d+) checks · (\d+) FAIL · (\d+) WARN", text)
        summary = m.group(0) if m else None
        return self._json({
            "checks": int(m.group(1)) if m else None,
            "fail": int(m.group(2)) if m else None,
            "warn": int(m.group(3)) if m else None,
            "summary": summary,
            "rc": proc.returncode,
            "lines": lines[:80],
        })

    def _handle_cron_post(self) -> None:
        """POST /api/cron {action:'del', id} | {action:'create', name,
        schedule, prompt, kind?} — Bearer gate, then either the CLI `--del`
        semantics (404 unknown id) or `cron_create` (400 on a bad spec/name/
        size, 200 + the same row shape GET /api/cron serves). Any other
        action is refused with the allowed set."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action == "create":
            return self._cron_create(doc)
        if action != "del":
            return self._json({
                "error": "validation failed",
                "details": ["action must be one of: create | del"]}, 400)
        job_id = doc.get("id")
        if not isinstance(job_id, str) or not job_id.strip():
            return self._json({"error": "validation failed",
                               "details": ["id must be a non-empty string"]},
                              400)
        code, out = cron_delete(job_id.strip())
        if code != 200:
            sys.stderr.write(f"cron del refused action=del rc={code}\n")
        else:
            sys.stderr.write(f"cron del ok count={out.get('count')}\n")
        return self._json(out, code)

    def _cron_create(self, doc: dict) -> None:
        """The create leg: shape/type checks here (all problems reported in
        one `details` list), grammar/limits inside scheduler.create_job —
        one source of truth for what a valid job IS."""
        name = doc.get("name")
        schedule = doc.get("schedule")
        prompt = doc.get("prompt")
        kind = doc.get("kind", "message")
        details: list[str] = []
        if not isinstance(name, str) or not name.strip():
            details.append("name must be a non-empty string")
        if not isinstance(schedule, str) or not schedule.strip():
            details.append("schedule must be one of: every 30m | daily 08:00 | "
                           "weekly mon 08:00 | once 2026-10-26 09:00")
        if not isinstance(prompt, str) or not prompt.strip():
            details.append("prompt must be a non-empty string")
        elif len(prompt) > CRON_PROMPT_MAX:
            details.append(f"prompt too long (max {CRON_PROMPT_MAX} chars)")
        if details:
            return self._json({"error": "validation failed",
                               "details": details}, 400)
        # name format / spec grammar / job limits / kind all belong to
        # scheduler.create_job — one grammar, mapped straight to 400.
        code, out = cron_create(name.strip(), schedule.strip(), prompt, kind)
        if code != 200:
            sys.stderr.write(f"cron create refused rc={code}\n")
        return self._json(out, code)

    # -------------------------------------- Wave 5 delta1: X10 checkpoints ---
    def _handle_checkpoints_get(self) -> None:
        """GET /api/checkpoints[?id=<n>] — the X10 read face (loopback, no
        auth, same contract as every GET). `?id=` answers ONE row (400 on a
        non-numeric id, honest 404 on an unknown one); without it the whole
        catalog. Metadata only: the snapshot bytes never leave
        mycore.checkpoint, so a credential file's content cannot render."""
        payload = collect_checkpoints()
        entries = payload.get("entries") or []
        query = urllib.parse.parse_qs(self.path.split("?", 1)[1]) \
            if "?" in self.path else {}
        if "id" not in query:
            return self._json(payload)
        raw = (query.get("id") or [""])[0].strip()
        if not raw.isdigit():
            return self._json({"error": "validation failed",
                               "details": ["id must be a non-negative integer"]},
                              400)
        eid = int(raw)
        row = next((r for r in entries if r.get("id") == eid), None)
        if row is None:
            return self._json({"error": "not found",
                               "details": [f"no checkpoint with id {eid}"]}, 404)
        return self._json({"entry": row})

    def _handle_checkpoints_post(self) -> None:
        """POST /api/checkpoints {action:"restore", id} — Bearer gate, then the
        CLI `--restore` semantics mirrored one for one: bad shape -> 400, an
        unknown id -> 404 (checked BEFORE the write, same message the CLI
        prints), a missing/corrupt snapshot or an io failure -> 409 with
        mycore.checkpoint's own honest message in details[0]. Every attempt —
        refused or successful — writes exactly one audit line to stderr."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        if doc.get("action") != "restore":
            return self._json({"error": "validation failed",
                               "details": ["action must be one of: restore"]},
                              400)
        raw = doc.get("id")
        sid = (str(raw).strip()
               if isinstance(raw, (int, str)) and not isinstance(raw, bool)
               else "")
        if not sid.isdigit():
            return self._json({"error": "validation failed",
                               "details": ["id must be a non-negative integer"]},
                              400)
        entry_id = int(sid)
        known = any(e.get("id") == entry_id
                    for e in (collect_checkpoints().get("entries") or []))
        if not known:
            sys.stderr.write(f"checkpoint restore refused id={entry_id} "
                             f"reason=unknown\n")
            return self._json({"error": "not found",
                               "details": [f"no checkpoint with id {entry_id}"]},
                              404)
        try:
            entry = checkpoint.restore(entry_id)
        except checkpoint.CheckpointError as e:   # honest module message
            sys.stderr.write(f"checkpoint restore failed id={entry_id} "
                             f"detail={e}\n")
            return self._json({"error": "restore failed",
                               "details": [str(e)]}, 409)
        except OSError as e:  # noqa: BLE001 — type name only, never a path
            sys.stderr.write(f"checkpoint restore failed id={entry_id} "
                             f"err={type(e).__name__}\n")
            return self._json({"error": f"restore failed: {type(e).__name__}"},
                              500)
        sys.stderr.write(f"checkpoint restore ok id={entry_id} "
                         f"size={entry.get('size')} snap={entry.get('file')}\n")
        return self._json({"ok": True, "entry": checkpoint_row(entry)})

    # ------------------------------------------ Wave 5 delta1: X11 profiles ---
    def _handle_profiles_post(self) -> None:
        """POST /api/profiles {action:"create"|"rm", name} — Bearer gate, then
        the SAME rules `aidog profile create|rm` enforces: shape/name checks
        here (400 with the CLI's own wording) and the rest delegated to
        deploy/aidog_persist.py so there is ONE source of truth for the name
        grammar, the reserved default and the state-file guard. Its first
        output line travels up VERBATIM as details[0].

        Red lines: the reserved default is refused BEFORE any subprocess runs,
        `--force` is never passed (a profile with state files can never be
        removed from the console), and a refusal touches no profile byte."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action not in ("create", "rm"):
            sys.stderr.write("profile post refused reason=action\n")
            return self._json({"error": "validation failed",
                               "details": ["action must be one of: create | rm"]},
                              400)
        name = doc.get("name")
        if not isinstance(name, str) or not name.strip():
            sys.stderr.write(f"profile {action} refused name= "
                             f"reason=name\n")
            return self._json({"error": "validation failed",
                               "details": ["name must be a non-empty string"]},
                              400)
        name = name.strip()
        if not PROFILE_NAME_RE.match(name):
            sys.stderr.write(f"profile {action} refused name={name} "
                             f"reason=grammar\n")
            return self._json({
                "error": "validation failed",
                "details": [f"invalid profile name {name!r}: expected "
                            f"[a-z0-9][a-z0-9_-]{{0,31}}"]}, 400)
        if name in PROFILE_RESERVED:
            detail = (f"{name!r} is reserved for the default profile — "
                      f"pick another name" if action == "create" else
                      "the default profile has no directory and can never "
                      f"be removed")
            sys.stderr.write(f"profile {action} refused name={name} "
                             f"reason=reserved\n")
            return self._json({"error": "validation failed",
                               "details": [detail]}, 400)
        try:
            proc = subprocess.run(
                [sys.executable, "deploy/aidog_persist.py",
                 "profile", action, name],
                cwd=str(REPO), capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:
            return self._json({"error": "profile op timed out (60s)"}, 504)
        except OSError as e:  # noqa: BLE001 — type name only
            return self._json({"error": f"launch failed: {type(e).__name__}"},
                              500)
        lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        if proc.returncode != 0:
            err = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
            details = (lines or err or
                       [f"profile {action} failed (rc {proc.returncode})"])[:3]
            sys.stderr.write(f"profile {action} refused name={name} "
                             f"rc={proc.returncode} detail={details[0]}\n")
            return self._json({"error": "validation failed",
                               "details": details}, 400)
        sys.stderr.write(f"profile {action} ok name={name}\n")
        # success output carries no credential (only slot NAMES and next-step
        # hints); the repo prefix is stripped so no machine path serializes.
        rel = [ln.replace(str(REPO) + os.sep, "").replace(str(REPO), "")
               for ln in lines[:12]]
        return self._json({"ok": True, "action": action, "name": name,
                           "lines": rel})

    def _handle_upstreams_test(self) -> None:
        """POST /api/mycore/upstreams/test {url?, slot?} — Bearer gate, then
        ONE 5s probe per target, answered as a bare JSON array
        [{name, ok, latency_ms, error_class}].

        Target selection (v0.5.55):
          {}            -> the configured failover chain (unchanged);
          {url}         -> that endpoint, NO credential (unchanged);
          {url, slot}   -> that endpoint with the slot's KEY VALUE for one
                           probe header (per-key TEST from the Model panel).
                           `slot` must be a console MODEL slot — the value
                           never travels beyond this frame (same X5 rule:
                           no body, no query, no echo), and the capability
                           is no more than save-then-test: the same admin
                           could store the pair in settings and probe it.
          {slot} (bare) -> 400: a key probe always needs its endpoint.
        `error_class` is an exception class name or 'HTTP <status>' and
        NOTHING else: never the probed query/userinfo, never a response
        body, never a key."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        url = doc.get("url")
        slot = doc.get("slot")
        if slot is not None:
            if not isinstance(slot, str) or not slot.strip():
                return self._json({"error": "validation failed",
                                   "details": ["slot must be a non-empty "
                                               "string"]}, 400)
            slot = slot.strip()
            if not MODEL_KEY_SLOT_RE.match(slot):
                # defense in depth: only model slots are probeable — any
                # other credential name is refused before a value resolves.
                return self._json({"error": "validation failed",
                                   "details": ["slot must be a model key "
                                               "slot (MODEL_API_KEY|"
                                               "OPENAI_API_TOKEN|MODEL_UP_K*)"]},
                                  400)
            if url is None:
                return self._json({"error": "validation failed",
                                   "details": ["slot requires url (a key "
                                               "probe needs its endpoint)"]},
                                  400)
        if url is not None:
            if not isinstance(url, str) or not url.strip():
                return self._json({"error": "validation failed",
                                   "details": ["url must be a non-empty string"]},
                                  400)
            if len(url) > UPSTREAM_TEST_URL_MAX:
                return self._json({"error": "validation failed",
                                   "details": [f"url too long (max "
                                               f"{UPSTREAM_TEST_URL_MAX} chars)"]},
                                  400)
            url = url.strip()
        targets, detail = upstream_probe_targets(url)
        if detail:
            return self._json({"error": "validation failed",
                               "details": [detail]}, 400)
        if slot is not None and url is not None:
            key = os.environ.get(slot, "") or (read_env_slot(slot) or "")
            if not key:
                return self._json({"error": "validation failed",
                                   "details": [f"slot {slot} is not set"]}, 400)
            targets = [(url, key)]           # value lives in this frame only
        results = [probe_endpoint(base, key=key) for base, key in targets]
        # counts only — URL, key and body never enter this line
        sys.stderr.write("upstream test n=%d ok=%d slot=%s\n" %
                         (len(results),
                          sum(1 for r in results if r.get("ok")),
                          "yes" if slot else "no"))
        return self._json(results)

    def _handle_models_discover(self) -> None:
        """POST /api/models/discover {base_url, api_key?} — Bearer gate, then
        ONE read-only GET of the endpoint's model directory (D1): the
        OpenAI-compatible /models list, so a discovery spends **zero tokens**
        and never reaches a completion.

        Same trust posture as /api/mycore/upstreams/test: the console Bearer
        IS the owner, and the existing hook editor can already write argv, so
        an owner-gated outbound fetch sits inside the established boundary.
        The guards below are still enforced (comment kept with the promise):
        http(s) only · <=10s · body <=1MB · at most 200 models · counts only
        in the log line.

        api_key never echoes, never logs, never enters an exception: it
        leaves this frame as one Authorization header for that host and
        nowhere else (see discover_models/_discover_auth for the three
        rules). Validation failures answer 400 with an expectation, never
        with a value; probe failures answer 200 {ok:false, reason} with an
        HTTP status or an exception class only — the same honest shape the
        upstream probe uses."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        base = doc.get("base_url")
        if not isinstance(base, str) or not base.strip():
            return self._json({"error": "validation failed",
                               "details": ["base_url must be a non-empty "
                                           "string"]}, 400)
        if len(base) > UPSTREAM_TEST_URL_MAX:
            return self._json({"error": "validation failed",
                               "details": [f"base_url too long (max "
                                           f"{UPSTREAM_TEST_URL_MAX} "
                                           f"chars)"]}, 400)
        base = base.strip()
        try:
            parts = urllib.parse.urlsplit(base)
            host, scheme = parts.hostname, parts.scheme
        except ValueError:
            host, scheme = None, ""
        if scheme not in ("http", "https") or not host:
            return self._json({"error": "validation failed",
                               "details": ["base_url must be an http(s) "
                                           "endpoint with a host"]}, 400)
        key = doc.get("api_key", "")
        if key is None:
            key = ""
        if not isinstance(key, str):
            return self._json({"error": "validation failed",
                               "details": ["api_key must be a string"]}, 400)
        if len(key) > DISCOVER_KEY_MAX:
            return self._json({"error": "validation failed",
                               "details": [f"api_key too long (max "
                                           f"{DISCOVER_KEY_MAX} chars)"]}, 400)
        result = discover_models(base, key.strip())
        # counts + a boolean only — endpoint, key and body stay out of the log
        sys.stderr.write("models discover ok=%s n=%s total=%s auth=%s\n" %
                         ("yes" if result.get("ok") else "no",
                          result.get("count", 0), result.get("total", 0),
                          result.get("auth", "none")))
        return self._json(result)

    def _handle_mission_post(self) -> None:
        """POST /api/mission {action: start|stop|pause|resume, goal?, id?,
        reporting?, budget?, deadline?, redlines?, deliverable?} — Bearer
        gate, then the mycore.mission state machine.
        400 bad action/goal/reporting/constraint · 404 unknown id · 409
        conflict (an active mission exists, or the move does not fit the
        state — a console pause while an order awaits approval stays 409).
        The three car-3 start constraints are optional: deadline = ISO8601
        in the future, redlines <= 500 chars, deliverable <= 200 chars —
        validated in mycore.mission.create (one grammar, every path).
        This route only writes the registry under the board lock: the chat
        service's mission leg executes slices — a console action never runs
        a model turn, and stop seals with evidence (F4: no silent ends)."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action not in ("start", "stop", "pause", "resume"):
            return self._json({"error": "validation failed",
                               "details": ["action must be one of: "
                                           "start|stop|pause|resume"]}, 400)
        reporting = doc.get("reporting", None)
        if reporting is not None and reporting not in mission.REPORTING_TIERS:
            return self._json({"error": "validation failed",
                               "details": ["reporting must be one of: "
                                           + "|".join(mission.REPORTING_TIERS)]},
                              400)
        path = mission.default_path()
        with _mission_lock:
            reg = mission.load(path)
            # NOTE: load() always sets the key — never `or []` here, that
            # would detach the very list create() appends to (empty-list bug).
            missions = reg["missions"]
            now = time.time()
            if action == "start":
                goal = doc.get("goal")
                if not isinstance(goal, str) or not goal.strip() \
                        or len(goal) > mission.MAX_GOAL_CHARS:
                    return self._json({"error": "validation failed",
                                       "details": [
                                           f"goal must be a non-empty string of "
                                           f"at most {mission.MAX_GOAL_CHARS} chars"]},
                                      400)
                active = mission.active_mission(reg)
                if active is not None:
                    return self._json({"error": "mission already active",
                                       "mission_id": active.get("id"),
                                       "state": active.get("state")}, 409)
                if len(missions) >= mission.MAX_MISSIONS:
                    return self._json({"error": "mission history is full",
                                       "details": [f"at most {mission.MAX_MISSIONS} "
                                                   "missions — retire old ones first"]},
                                      409)
                settings = load_settings()
                # F1 capability manifest frozen at start: tools after the
                # tools_off set + probe gates, enabled channels, credential
                # slot key names with a presence BOOL (never a value), web3.
                manifest = mission.build_manifest(
                    settings, list(SECRET_SLOTS),
                    lambda name: read_env_slot(name) is not None)
                block = settings.get("mission")
                tier = block.get("reporting") if isinstance(block, dict) else None
                if tier not in mission.REPORTING_TIERS:
                    tier = mission.DEFAULT_REPORTING
                # Car 2 budget: settings defaults at THIS moment, overlaid by
                # the optional per-start override; the resolved values are
                # validated and frozen into the mission record (later settings
                # edits never touch a running mission).
                budget_doc = doc.get("budget")
                if budget_doc is not None and not isinstance(budget_doc, dict):
                    return self._json({"error": "validation failed",
                                       "details": ["budget must be an object"]},
                                      400)
                try:
                    eff_budget = mission.validate_budget(
                        {**mission.settings_budget(block),
                         **(budget_doc or {})})
                except mission.MissionError as e:
                    return self._json({"error": "validation failed",
                                       "details": [str(e)]}, 400)
                try:
                    rec = mission.create(missions, goal,
                                         reporting=reporting or tier,
                                         budget=eff_budget,
                                         origin=None, manifest=manifest,
                                         # car 3 A-face constraints (all
                                         # optional; validated inside create)
                                         deadline=doc.get("deadline"),
                                         redlines=doc.get("redlines"),
                                         deliverable=doc.get("deliverable"),
                                         now=now)
                    mission.transition(rec, "running", now,
                                       reason="console start")
                    mission.save(path, reg)
                except mission.MissionError as e:
                    return self._json({"error": "validation failed",
                                       "details": [str(e)]}, 400)
                except OSError as e:  # noqa: BLE001 — type name only
                    return self._json({"error": f"persist failed: {type(e).__name__}"},
                                      500)
                mission.append_event(path, rec["id"], "state",
                                     {"to": "running", "via": "api"})
                sys.stderr.write(f"mission start id={rec['id']} "
                                 f"tier={rec.get('reporting')}\n")
                return self._json({"ok": True,
                                   "mission": mission.public_row(rec)})
            # stop / pause / resume: id first, then the state gate
            mid = doc.get("id")
            if not isinstance(mid, str) or not mid.strip():
                return self._json({"error": "validation failed",
                                   "details": ["id must be a non-empty string"]},
                                  400)
            rec = mission.find(missions, mid.strip())
            if rec is None:
                return self._json({"error": "not found",
                                   "details": ["unknown mission id"]}, 404)
            state = rec.get("state")
            if action == "pause" and state not in ("running", "degraded"):
                return self._json({"error": "conflict",
                                   "details": [f"pause: state {state} cannot "
                                               "pause — allowed states: "
                                               "running, degraded"]}, 409)
            if action == "resume" and state != "paused_user":
                return self._json({"error": "conflict",
                                   "details": [f"resume: state {state} cannot "
                                               "resume — allowed state: "
                                               "paused_user"]}, 409)
            if action == "stop" and state in mission.TERMINAL_STATES:
                return self._json({"error": "conflict",
                                   "details": [f"stop: mission already {state} "
                                               "— allowed from any non-terminal "
                                               "state (planned, running, "
                                               "paused_user, degraded, "
                                               "paused_approve)"]}, 409)
            target = {"pause": "paused_user", "resume": "running",
                      "stop": "sealed"}[action]
            evidence = ["stopped via the console"] if action == "stop" else None
            try:
                mission.transition(rec, target, now, evidence=evidence,
                                   reason=f"{action} via api")
                mission.save(path, reg)
            except mission.MissionError as e:
                return self._json({"error": "conflict",
                                   "details": [str(e)]}, 409)
            except OSError as e:  # noqa: BLE001 — type name only
                return self._json({"error": f"persist failed: {type(e).__name__}"},
                                  500)
            mission.append_event(path, rec["id"], "state",
                                 {"to": target, "via": "api"})
            sys.stderr.write(f"mission {action} id={rec['id']} "
                             f"state={target}\n")
            return self._json({"ok": True,
                               "mission": mission.public_row(rec)})

    # ------------------------------------------ Wave 6 delta2: X13/X17 faces ---
    def _handle_artifacts_get(self) -> None:
        """GET /api/artifacts[?limit=&offset=] — the X17 gallery catalog:
        metadata rows for generated images in MEDIA_DIR, newest first,
        bounded pages. Loopback read like every GET. An unreadable directory
        is an honest 503; an empty one is a truthful {images: [], count: 0} —
        never a fabricated sample row."""
        query = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                 if "?" in self.path else {})

        def page_of(key: str, default: int) -> int | None:
            raw = (query.get(key) or [""])[0].strip()
            if not raw:
                return default
            return int(raw) if raw.isdigit() else None

        limit = page_of("limit", ARTIFACTS_LIMIT)
        offset = page_of("offset", 0)
        if limit is None or offset is None or not 1 <= limit <= ARTIFACTS_MAX \
                or not 0 <= offset <= 100000:
            return self._json({
                "error": "validation failed",
                "details": [f"limit must be 1..{ARTIFACTS_MAX} and offset a "
                            "non-negative integer"]}, 400)
        try:
            entries: list[tuple] = []
            for p in MEDIA_DIR.iterdir():
                if not p.is_file() or not IMAGE_NAME_RE.match(p.name):
                    continue
                if p.suffix.lower() not in MEDIA_MIME:
                    continue
                st = p.stat()
                entries.append((st.st_mtime, st.st_size, p.name))
        except OSError as e:  # noqa: BLE001 — type name only
            sys.stderr.write(f"artifacts list failed err={type(e).__name__}\n")
            return self._json({"error": "media directory unavailable"}, 503)
        entries.sort(reverse=True)
        total = len(entries)
        page = entries[offset:offset + limit]
        images = [{"name": name, "url": "/api/media/" + name, "size": size,
                   "when": round(mtime, 3),
                   "ext": name.rsplit(".", 1)[-1].lower()}
                  for mtime, size, name in page]
        return self._json({"images": images, "count": total, "limit": limit,
                           "offset": offset,
                           "truncated": offset + len(images) < total})

    def _handle_sessions_get(self) -> None:
        """GET /api/sessions[?q=&limit=] | ?id=<sid>&format=json|md — the X13
        read face (loopback, no bearer: same contract as every GET). Content
        is the chat content /api/chat/history already renders — never a
        credential slot value; summaries strip control chars. ?id= answers
        ONE export with an attachment content-type (400 bad id/format, 404
        unknown id). The list additionally runs the X12 prune view (opt-in,
        at most hourly) so the console can show prune state + the one-shown
        disk advisory without a write route."""
        query = (urllib.parse.parse_qs(self.path.split("?", 1)[1])
                 if "?" in self.path else {})
        sid = (query.get("id") or [""])[0].strip()
        if sid:
            fmt = (query.get("format") or ["json"])[0]
            try:
                text, mime = export_session(chat_sessions_dir(), sid, fmt)
            except ValueError as e:
                return self._json({"error": "validation failed",
                                   "details": [str(e)]}, 400)
            except FileNotFoundError as e:
                return self._json({"error": "not found",
                                   "details": [str(e)]}, 404)
            except OSError as e:  # noqa: BLE001 — type name only
                sys.stderr.write(f"session export failed "
                                 f"err={type(e).__name__}\n")
                return self._json({"error": "export unavailable"}, 503)
            return self._send(200, text.encode("utf-8"), mime)
        q = (query.get("q") or [""])[0]
        if len(q) > SESSIONS_Q_MAX:
            return self._json({"error": "validation failed",
                               "details": [f"q must be at most "
                                           f"{SESSIONS_Q_MAX} chars"]}, 400)
        raw_limit = (query.get("limit") or [""])[0].strip()
        if raw_limit and not raw_limit.isdigit():
            return self._json({"error": "validation failed",
                               "details": ["limit must be a positive integer"]},
                              400)
        limit = int(raw_limit) if raw_limit else SESSION_LIST_LIMIT
        if not 1 <= limit <= SESSION_LIST_MAX:
            return self._json({"error": "validation failed",
                               "details": [f"limit must be 1.."
                                           f"{SESSION_LIST_MAX}"]}, 400)
        try:
            payload = list_sessions(chat_sessions_dir(), q=q or None,
                                    limit=limit)
        except Exception as e:  # noqa: BLE001 — fail-soft, class only
            sys.stderr.write(f"session list failed err={type(e).__name__}\n")
            return self._json({"error": "sessions unavailable"}, 503)
        payload["prune"] = session_prune_view()
        return self._json(payload)

    # ------------------------------------------------------ evolution legs ---
    # ------------------------------------------------------- sniff board ---
    def _handle_sniff_post(self) -> None:
        """POST /api/sniff {action: sweep|ack, signature?} — Bearer gate.
        `sweep` runs ONE read-only sweep now; `ack` marks a finding
        acknowledged in the local log. Neither action can touch a device or
        a vehicle: mycore.sniff carries no actuation surface at all (the
        core suite asserts the absence)."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action == "sweep":
            report = sniff.tick(force=True)
            return self._json({"ok": True, "sweep": report,
                               "sniff": collect_sniff()})
        if action == "ack":
            sig = doc.get("signature")
            if not isinstance(sig, str) or not sig or len(sig) > 64:
                return self._json({"error": "validation failed",
                                   "details": ["signature must be a non-empty "
                                               "string of at most 64 chars"]},
                                  400)
            rows = sniff.load_findings()
            if not sniff.ack_finding(rows, sig):
                return self._json({"error": "not found",
                                   "details": ["unknown finding signature"]},
                                  404)
            try:
                sniff.save_findings(rows)
            except OSError as e:  # noqa: BLE001
                return self._json(
                    {"error": f"persist failed: {type(e).__name__}"}, 500)
            return self._json({"ok": True, "sniff": collect_sniff()})
        return self._json({"error": "validation failed",
                           "details": ["action must be one of: sweep|ack"]},
                          400)

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

    def _read_json_body(self, limit: int = MAX_BODY) -> dict | None:
        """Length/JSON/dict validation with responses on failure (None = a
        response was already sent). `limit` lets a caller that expects an
        image attachment (X16: /api/chat -> CHAT_BODY_MAX) raise the body
        budget without moving any other route's cap."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json({"error": "bad content-length"}, 400)
            return None
        if length <= 0:
            self._json({"error": "empty body"}, 400)
            return None
        if length > limit:
            self._json({"error": f"body too large (max {limit} bytes)"}, 413)
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
        contract as the TG pipeline), or {action:'clear'} to archive,
        or {action:'resume', id} (X20) to switch the live session.

        Events: meta {model} · delta {text} · tool {name, ok} ·
                progress {server, tool, message, progress} (MCP progress,
                message truncated to 200 chars — never long free text) ·
                done {text, stop, iterations, tool_calls} · error {error}.
        `done` always carries the authoritative final text — deltas are only
        a live preview the browser replaces wholesale.

        Wave 4 (X7): an OPTIONAL `model` field applies the whitelist gate
        BEFORE anything else runs — unknown id answers 400 with the allowed
        list verbatim, so the console picker and the TG /model command share
        one accept/reject rule. A valid id rides into `_run_chat_turn` for
        that turn only."""
        if not self._require_token():
            return
        doc = self._read_json_body(CHAT_BODY_MAX)
        if doc is None:
            return
        model = doc.get("model")
        if model is not None:
            if not isinstance(model, str) or not model.strip():
                return self._json({"error": "validation failed",
                                   "details": ["model must be a non-empty string"]},
                                  400)
            model = model.strip()
            allowed = chat_model_whitelist()
            if model not in allowed:
                return self._json({"error": "validation failed",
                                   "details": ["model must be one of: "
                                               + ", ".join(allowed)]}, 400)
        if doc.get("action") == "clear":
            return self._json({"ok": True, "archived": clear_chat_session()})
        if doc.get("action") == "resume":
            # X20 live session switch — dispatched BEFORE the text gate (like
            # clear): it carries `id`, never `text`. Same bearer gate as every
            # /api/chat write; 400/404/500 come back as honest JSON.
            sid = doc.get("id")
            if not isinstance(sid, str) or not sid.strip():
                sys.stderr.write("chat session resume id=- result=no_id\n")
                return self._json({"error": "validation failed",
                                   "details": ["id is required (a session "
                                               "id from GET /api/sessions)"]},
                                  400)
            code, payload = resume_chat_session(sid.strip())
            return self._json(payload, code)
        text = doc.get("text")
        if not isinstance(text, str) or not text.strip():
            return self._json({"error": "validation failed",
                               "details": ["text is required"]}, 400)
        text = text.strip()
        if len(text) > CHAT_MAX_CHARS:
            return self._json({"error": "validation failed",
                               "details": [f"text too long (max {CHAT_MAX_CHARS} chars)"]},
                              400)
        image = doc.get("image")
        if image is not None:
            # X16: strict shape gate BEFORE the stream opens. The data URL is
            # bounded and suffix-whitelisted; a non-string, a script URL or a
            # truncated base64 blob is a 400, never a half-turn.
            if not isinstance(image, str) or len(image) > VISION_MAX_CHARS \
                    or not VISION_URL_RE.match(image):
                return self._json({"error": "validation failed",
                                   "details": ["image must be a "
                                               "data:image/(png|jpeg|webp);"
                                               "base64 URL of 64.."
                                               f"{VISION_MAX_CHARS} chars"]},
                                  400)

        # Car 7 (D11 restraint): the leash gate — pause and budget refuse a NEW
        # turn BEFORE the stream opens, with the same {error, details} shape
        # the composer already renders. 409 = held (pause), 429 = over-limit.
        allowed, code, ctx = leash.gate(load_settings())
        if not allowed:
            return self._json({"error": code,
                               "details": [leash.block_detail(code, ctx)],
                               "params": ctx},
                              409 if code == "paused" else 429)

        self._sse_start()
        try:
            ok = self._run_chat_turn(text, model, image=image)
        except Exception as e:  # noqa: BLE001 — never leak internals to the stream
            sys.stderr.write(f"chat turn failed err={type(e).__name__}\n")
            self._sse("error", {"error": "internal"})
            return
        if not ok:
            return

    def _run_chat_turn(self, text: str, model: str | None = None,
                       image: str | None = None) -> bool:
        """Drive one turn over the console session and stream its events.
        Returns False when the client disconnected (observers must stop).
        `model` (X7) is a whitelist member already validated by the caller:
        only cfg.model moves for THIS turn — endpoint and failover chain stay
        put, exactly like the TG per-chat override does.
        `image` (X16) is one validated data-URL: it rides the outgoing user
        message as an image_url block for THIS turn only — the session file
        keeps a textified '[image]' marker (no base64 persists, nothing gets
        resent next turn), and a 4xx refusal gets vision_rejection_note's
        honest sentence in the final `done.text`."""
        cfg = cli.resolve_model_config()
        if model:
            cfg.model = model
        else:
            # Car 4 (D6) call site 4 — the model router (console leg). An
            # explicit picker choice (X7 whitelist id) always wins; every
            # other outcome — router off, Jev said main, weak calibration,
            # unreachable — leaves cfg exactly as resolved above, and the
            # routed model is what the `meta` event reports.
            decision.apply_router(cfg, {"source": "console",
                                        "chars": len(text or ""),
                                        "has_image": bool(image)})
        path = chat_session_path()
        sess = Session(path)
        history = sess.load()
        if not history and not sess.meta():
            sess = Session.create(path, meta={"model": cfg.model})
            history = []
        history = [m for m in history if m.get("role") != "system"]

        stack = build_system_stack(context_dirs=resolve_context_dirs(),
                                   user_rules=resolve_user_rules())
        messages = stack + history
        pre_ids = {id(m) for m in messages}
        content = text
        if image:
            content = [{"type": "text", "text": text},
                       {"type": "image_url", "image_url": {"url": image}}]
        messages.append({"role": "user", "content": content})

        # Car 7 (D11): STOP baseline captured BEFORE the turn — only a later
        # POST /api/control {action:stop} can abort THIS turn, and the
        # active-turn counter feeds the Overview tail row.
        stop_base = leash.stop_baseline()
        _bump_active_turn(1)
        alive = [True]
        try:
            if not self._sse("meta", {"model": cfg.model}):
                return False

            return self._drive_chat_turn(
                messages, pre_ids, cfg, image, content, sess, alive,
                stop_base)
        finally:
            _bump_active_turn(-1)

    def _drive_chat_turn(self, messages, pre_ids, cfg, image, content, sess,
                         alive, stop_base) -> bool:
        """The model/observer half of _run_chat_turn (split so the
        active-turn counter in the caller covers the whole turn in a
        finally — a crash must not leave the tail stuck at "working")."""

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
                       budget_chars=chat_budget(), on_delta=on_delta,
                       should_stop=lambda: leash.stop_requested(stop_base))

        delta = [m for m in messages
                 if id(m) not in pre_ids and m.get("role") != "system"]
        if image:
            # X16: persist the MARKER, not the bytes — the session stays
            # small, later turns never resend the image, and chat history /
            # export render an honest '[image]' instead of vanishing.
            for m in delta:
                if isinstance(m.get("content"), list):
                    m["content"] = content_blocks_text(m["content"])
            if res.stop == "model_error":
                res.text = vision_rejection_note(res.text)
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
        messages = build_system_stack(context_dirs=resolve_context_dirs(),
                                      user_rules=resolve_user_rules())
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
        # Car 7 (D11): the /v1 gateway issues NEW turns too — same pause /
        # budget gate (OpenAI-shaped bodies: 429 held-or-over, honest text).
        allowed, code, ctx = leash.gate(load_settings())
        if not allowed:
            return self._openai_error(429, leash.block_detail(code, ctx),
                                      "rate_limit_error")
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
        # Car 7 (D11): an inbound webhook turn is a NEW turn too — the same
        # pause/budget gate applies (409 held / 429 over-limit, honest detail).
        allowed, code, ctx = leash.gate(load_settings())
        if not allowed:
            return self._json({"error": code,
                               "details": [leash.block_detail(code, ctx)]},
                              409 if code == "paused" else 429)
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

    def _handle_quotes(self) -> None:
        """GET /api/quotes?market=cn|us&symbol=<sym> — Extensions car (Phase B):
        keyless READ-ONLY public quotes. cn -> Sina (GBK + mandatory Referer),
        us -> Yahoo chart JSON; AIDOG_QUOTES_URL swaps the ADAPTER for a mock
        (offline smoke). Answers 200 {ok, market, symbol, name, price, change,
        pct, currency, ts, source} with honest failures — 400 invalid
        market/format (rejected before any network I/O), 404 unknown symbol,
        502 upstream refused or shapeless, 504 upstream timeout — and never a
        fabricated number. Budget: its OWN 10/60s window -> 429, spent only by
        requests that actually reach an upstream (a 400 is free local work),
        so this route can never drain the STT, /api/say or exchange-probe
        windows."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        market = (query.get("market") or [""])[0].strip().lower()
        raw_symbol = (query.get("symbol") or [""])[0].strip()
        if market not in ("cn", "us"):
            return self._json(
                {"error": "invalid market",
                 "details": ["market must be one of cn|us"]}, 400)
        symbol = raw_symbol.lower() if market == "cn" else raw_symbol
        if not _quotes_symbol_ok(market, symbol):
            want = ("sh600519 — prefix sh|sz|bj plus 6 digits" if market == "cn"
                    else "AAPL — 1..10 letters, dots or dashes")
            return self._json(
                {"error": "invalid symbol",
                 "details": [f"symbol must look like {want}"]}, 400)
        mock = os.environ.get("AIDOG_QUOTES_URL", "").strip()
        if not _quotes_rate_ok():
            return self._json({"error": "rate limited"}, 429)
        try:
            if mock:
                doc = quotes_mock(mock, market, symbol)
            elif market == "cn":
                doc = quotes_cn(symbol)
            else:
                doc = quotes_us(symbol)
        except QuotesError as e:
            return self._json({"error": e.message}, e.code)
        except Exception as e:  # noqa: BLE001 — a probe never answers 500
            sys.stderr.write(f"quotes failed market={market} "
                             f"err={type(e).__name__}\n")
            return self._json({"error": "quote fetch failed"}, 502)
        return self._json(doc)

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

    def _handle_control(self) -> None:
        """POST /api/control {action: stop|pause|resume[, reason]} — Bearer
        gate (it changes runtime state). Car 7 / D11 restraint:
        * stop   — moves the STOP counter: the CURRENT turn aborts at its
                   next checkpoint (iteration top / delta / pre-dispatch);
                   turns started later are untouched.
        * pause  — the pause switch ON: new turns and the service's
                   automatic legs are held with an honest line until a human
                   resumes (fail-closed, no process killed).
        * resume — the one human click that clears a pause (and rewrites a
                   corrupt control file back to a valid one)."""
        if not self._require_token():
            return
        doc = self._read_json_body()
        if doc is None:
            return
        action = doc.get("action")
        if action not in ("stop", "pause", "resume"):
            return self._json({
                "error": "validation failed",
                "details": ["action must be one of: stop | pause | resume"]},
                400)
        reason = doc.get("reason")
        if reason is not None and (
                not isinstance(reason, str) or len(reason) > 200
                or any(ord(c) < 32 and c not in "\t" for c in reason)):
            return self._json({
                "error": "validation failed",
                "details": ["reason must be a single line of at most 200 chars"]},
                400)
        try:
            if action == "stop":
                state = leash.request_stop()
            elif action == "pause":
                state = leash.pause(str(reason or "manual"))
            else:
                state = leash.resume()
        except OSError:
            return self._json({"error": "control state write failed"}, 500)
        sys.stderr.write(f"control action={action} paused={int(state['paused'])} "
                         f"stop_seq={state['stop_seq']}\n")
        return self._json({"ok": True, "action": action, "control": {
            "paused": state["paused"], "reason": state["reason"],
            "since": state["since"], "stop_seq": state["stop_seq"]}})

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
        # Merge onto the RAW stored document — never onto load_settings()'s
        # filtered view (bug fix §1.7): an unknown hand-written key must
        # survive a console save. GET keeps the validated view (fail-closed
        # display); only the validated patch is written.
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        merged = deep_merge(stored, clean)
        changed = merged != stored
        if changed:
            try:
                atomic_write_json(SETTINGS_FILE, merged)
            except OSError as e:  # noqa: BLE001
                return self._json({"error": f"persist failed: {type(e).__name__}"}, 500)
            sys.stderr.write(f"settings updated keys={','.join(sorted(clean))}\n")
            mc_patch = clean.get("mycore")
            if isinstance(mc_patch, dict) and "session_prune_days" in mc_patch:
                # X12: applying the switch prunes now (force beats the hourly
                # lazy cadence — the operator just asked for it).
                session_prune_view(force=True)
            # display currency / fx / usage paths affect the next usage render
            if {"currency", "fx_rate", "usage"} & set(clean):
                with _usage_lock:
                    _usage_cache["ts"], _usage_cache["data"] = 0.0, None
        return self._json({"ok": True, "changed": changed,
                           "settings": load_settings()})

    def log_message(self, fmt: str, *args) -> None:  # quiet default
        sys.stderr.write("%s %s\n" % (datetime.now().strftime("%H:%M:%S"), fmt % args))


def main() -> None:
    # v0.5.55: console-run chat legs meter into the same state-dir log the
    # service writes (per-day files, O_APPEND-safe across both processes).
    model_meter.activate()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"I'm AiDog console -> http://{HOST}:{PORT}/  (loopback only, {VERSION})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
