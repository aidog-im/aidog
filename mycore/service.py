"""mycore.service — production Telegram chat service (P5-1, design section 14.3).

    python3 -m mycore.service [--once] [--dry-run]

Pipeline per incoming update:

    getUpdates -> allowlist auth (fail-closed) -> command? -> chat turn
        -> assembly.build_system_stack + per-chat JSONL session
        -> loop.run_turn (budgeted) -> reply via TgClient.send_message

Startup gates (in this order — each refuses to start before anything is
consumed or claimed):

    1. AIDOG_TG_ALLOWED_USERS unset/empty  -> exit 3  (fail-closed: an
       unauthenticated chat must never be answered)
    2. pidfile held by a LIVE process      -> exit 2  (single instance only —
       two getUpdates consumers on one token steal each other's updates,
       the same hazard class as the tg.py token guard)
    3. MYCORE_TG_TOKEN hard guard (tg.polling_token()) -> exit 4 (dedicated
       second bot required; the gateway's token is refused)

State directory (default <repo>/data/mycore_service, env AIDOG_MC_STATE):

    mycore_service.pid        single-instance lock (pid liveness, best effort)
    lang.json                 persisted reply language ({"lang": "zh"}); absent
                              or invalid -> default pack (en), fail-soft
    service.log               append log, size-rotate ~1MB keep 1; chat ids
                              redacted to id:<last4>; never token/secret/
                              machine-path values
    sessions/<chat_id>.jsonl  per-chat history (mycore.session)
    sessions/<chat_id>.model  per-chat /model override (session.py writes meta
                              only at file creation, so the override lives in
                              a small sidecar file instead of the JSONL meta)
    evolution_state.json      last run per reflect window (cron leg — fires
                              only when web/settings.json evolution.enabled;
                              the poll loop checks is_due() once a minute,
                              catch-up after downtime, one run per window)
    sessions/archive/         sessions rotated by /reset

Commands (parsed only when message text starts with "/", and only after the
allowlist gate — unauthenticated /commands are ignored like any message):
    /help   list commands with one-line descriptions
    /reset  archive this chat's session file, start fresh
    /model  show model id + endpoint host (no key); /model <id> sets the
            per-chat override (allowed ids: web/settings.json mycore.models
            when present, else only the currently configured id)
    /status session turn count, current model, service uptime
    /lang   show the reply language; /lang <code> switches it service-wide and
            persists to <state>/lang.json (packs discovered in
            overlay/locales/, default en)
    /learn  one experience -> an institutional artifact (evolution loop):
            /learn memory <text> | /learn skill <slug> :: <desc> :: <body>
            the lint gate + skill_route pre-screen run BEFORE any write;
            rejects change nothing and are logged to service.log only
    /reflect [daily|weekly|monthly]  generate the period skeleton (facts only;
            the Human conclusion section stays the owner's to write)
    /evolution  loop status: schedule, last runs, counts, log tail
    /cron       natural-language cron job registry (Phase B2):
            /cron  list jobs (id · spec · kind · next fire · text preview)
            /cron add [message|prompt] <spec> :: <text>  create a job
                    (spec: every 30m | daily 08:00 | weekly mon 08:00 |
                     once YYYY-MM-DD HH:MM — Beijing time; message = the
                     text is delivered as-is, prompt = one model turn runs
                     and its result is delivered)
            /cron del <id>  remove a job
            Delivery on fire: the origin chat that created the job, else
            env MYCORE_WD_CHAT, else log-only (never an error — the fire
            still runs and is recorded as CRON_FIRE in service.log).

Modes:
    --once    fetch at most ONE getUpdates batch (short poll), handle at most
              one update, exit 0 (E2E-friendly; exits 0 with nothing pending)
    --dry-run run the FULL loop and skip only OUTBOUND SENDS. Precisely: it
              is a real getUpdates consumer (pidfile + token guard apply),
              auth/commands/turns all evaluate and log, offsets ADVANCE —
              fetched updates are acknowledged and will not be redelivered.
              Each would-be reply is printed to stdout (path-scrubbed preview)
              and logged as DRYRUN_SEND instead of sendMessage. Use it only
              when consuming pending updates on the second bot is acceptable.

Channel inbound (C car): alongside the Telegram legs, run() evaluates
_maybe_poll_channels() at most once per CHANNEL_POLL_SEC (30s). Each route
from channels.load_channels() is polled once; every inbound message passes
an allowlist gate (web/settings.json channels.<route>.allow — empty or
absent = deny all, fail-closed, CHANNEL_DENY logged at the deny throttle)
before it enters the SAME command/pipeline path. The conversation key is
the string Inbound.key ("route:sender") — sessions live at
sessions/<key>.jsonl, /reset archives them like any TG session, and the
reply goes back through the SAME route's send(target, reply). Dry-run
evaluates and logs channel replies without transmitting them, exactly like
TG sends; any channel failure costs one CHANNEL_* log line, never a dead
loop.

Credentials: the poll token comes from MYCORE_TG_TOKEN at call time through
TgClient.polling_token() (README contract 2 — never logged, never echoed).
Observation scrubbing: chat replies never carry stack traces, absolute
machine paths, or engine names — failures map to short honest lines.
English code/comments per repo i18n policy; every user-visible string comes
from the locale packs (overlay/locales/, loader lib/i18n.py, default en).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.parse
from pathlib import Path

from . import channels as channels_mod
from . import checkpoint
from . import cli
from . import evolution
from . import hooks as hooks_mod
from . import scheduler
from .assembly import build_system_stack, resolve_context_dirs
from .loop import run_turn
from .model import ChatClient
from .session import Session
from .tg import POLL_BOT_ENV, TgClient, TgError
from .tools import build_registry

REPO = Path(__file__).resolve().parent.parent

# Locale loader: stdlib-only sibling module under lib/. The repo root is put
# at the FRONT of sys.path (never merely appended) so this vendored copy
# always wins — same convention as the tools.py router import.
if str(REPO) in sys.path:
    sys.path.remove(str(REPO))
sys.path.insert(0, str(REPO))
from lib import i18n  # noqa: E402  (overlay/locales/* = single source of truth)

ALLOW_ENV = "AIDOG_TG_ALLOWED_USERS"   # comma-separated TG user ids (fail-closed)
STATE_ENV = "AIDOG_MC_STATE"           # state dir override
PIDFILE_NAME = "mycore_service.pid"
LOG_NAME = "service.log"
SESSIONS_DIR = "sessions"
ARCHIVE_DIR = "archive"
MODEL_EXT = ".model"
LANG_FILE_NAME = "lang.json"        # persisted reply language (service-wide)

LOG_MAX_BYTES = 1_000_000              # size-rotate threshold, keep 1 backup
DENY_WARN_THROTTLE = 60.0              # seconds between WARN lines per chat
MAX_POLL_ERRORS = 20                   # bounded backoff, then give up (tg.py parity)
DEFAULT_POLL_TIMEOUT = 30              # long-poll seconds (0 in --once mode)
REFLECT_CHECK_SEC = 60.0               # evolution cron evaluation cadence
TYPING_INTERVAL_SEC = 4.0               # TG typing heartbeat (5s Telegram TTL)
CHANNEL_POLL_SEC = 30                   # channel inbound leg cadence (seconds)
VOICE_MAX_SECONDS = 60                  # TG voice/audio cap before STT (car 4c)
VOICE_MAX_BYTES = 5_000_000             # download cap before STT (5MB, car 4c)

# Honest one-line replies. The English constants stay importable (tests and
# tooling compare against them); live replies resolve the SAME keys through
# the locale packs (default en), so a language switch never weakens the
# honesty rule: no engine names, no paths, no exception internals in chat.
NO_OUTPUT_LINE = i18n.get(i18n.DEFAULT_LANG, "err.no_output",
                          "(no output — see logs)")
MODEL_ERR_LINE = i18n.get(i18n.DEFAULT_LANG, "err.model",
                          "(model error — the turn stopped; see service logs)")

# Cron delivery target: env MYCORE_WD_CHAT (int chat id; the same slot the
# watchdog alerts use — operator-provisioned). Empty/garbage -> the fire
# still runs and is logged, only the send is skipped (honest log-only).
WD_CHAT_ENV = "MYCORE_WD_CHAT"
WD_CHAT_RE = re.compile(r"^-?[0-9]+$")
INTERNAL_ERR_LINE = i18n.get(i18n.DEFAULT_LANG, "err.internal",
                             "(internal error — the turn stopped; see service logs)")

# Absolute machine paths are stripped from dry-run stdout previews only
# (operational convenience; the chat itself never receives raw tool output).
_PATH_RE = re.compile(
    r"/(?:mnt|home|root|etc|var|opt|usr|srv|tmp|dev|proc|sys|run|media)"
    r"(?:/[^\s\"'`)\]]+)*")


# ----------------------------------------------------------------- helpers ---

def state_dir() -> Path:
    """State root: env AIDOG_MC_STATE, else <repo>/data/mycore_service."""
    raw = os.environ.get(STATE_ENV, "").strip()
    return Path(raw) if raw else REPO / "data" / "mycore_service"


def parse_allowlist(raw: str | None) -> frozenset[int]:
    """Comma-separated digits -> ids. Non-numeric entries never match anyone
    (fail-closed); all-invalid input yields the empty set -> start refused."""
    out: set[int] = set()
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if tok and tok.isascii() and tok.isdigit():
            out.add(int(tok))
    return frozenset(out)


def redact_chat(value: object) -> str:
    """Chat id for logs: id:<last4> only — full ids never enter service.log."""
    s = str(value)
    return "id:" + (s[-4:] if len(s) >= 4 else s)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:            # exists, owned by another user
        return True
    except (OverflowError, ValueError, OSError):
        return False
    return True


def _read_pid(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0


def acquire_pidfile(path: str | Path) -> tuple[bool, int]:
    """Single-instance lock -> (acquired, holder_pid).

    O_CREAT|O_EXCL claim; a pre-existing file is taken over only when its pid
    is dead (stale). Refuses when the pid is alive (holder_pid then identifies
    it). Best effort against concurrent starts — the loser of a true race
    retries once after the winner's pid becomes readable; operational safety
    still comes from one launcher (boot phase P5-2).
    """
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    for _ in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{os.getpid()}\n")
            return True, 0
        except FileExistsError:
            holder = _read_pid(p)
            if holder and _pid_alive(holder):
                return False, holder
            try:
                p.unlink()             # stale/garbage -> take over, retry claim
            except OSError:
                return False, holder
    return False, 0


def release_pidfile(path: str | Path) -> None:
    """Remove the lock only when it is ours (never another instance's)."""
    p = Path(path)
    try:
        if p.read_text(encoding="utf-8").strip() == str(os.getpid()):
            p.unlink()
    except OSError:
        pass


def endpoint_host(base_url: str) -> str:
    """host[:port] of the model endpoint — never the key, never user-info."""
    try:
        parts = urllib.parse.urlparse(base_url)
    except ValueError:
        return "unknown"
    host = parts.hostname or "unknown"
    return f"{host}:{parts.port}" if parts.port else host


def fmt_uptime(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m}m{s}s"
    if m:
        return f"{m}m{s}s"
    return f"{s}s"


def _scrub_paths(text: str) -> str:
    return _PATH_RE.sub("[path]", text)


# ------------------------------------------------------------------- log ----

class ServiceLog:
    """Append log at <state>/service.log; size-rotate ~1MB keep 1.

    One line per event: <ISO-8601 UTC> <EVENT> [k=v ...]. Chat ids arrive
    already redacted; callers must never pass token/secret/path values.
    Logging failures never take the service down.
    """

    def __init__(self, path: str | Path, now=lambda: time.time()) -> None:
        self.path = Path(path)
        self._now = now
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def write(self, event: str, *fields: str) -> None:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))
        tail = " ".join(str(f) for f in fields if f)
        line = f"{ts} {event}" + (f" {tail}" if tail else "") + "\n"
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)
            self._rotate()
        except OSError:
            pass

    def _rotate(self) -> None:
        try:
            if self.path.stat().st_size > LOG_MAX_BYTES:
                backup = self.path.with_name(self.path.name + ".1")
                try:
                    backup.unlink()                 # keep exactly 1 backup
                except OSError:
                    pass
                self.path.rename(backup)
        except OSError:
            pass

    def read_text(self) -> str:
        """Test/ops helper: current log contents (empty when absent)."""
        try:
            return self.path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


# --------------------------------------------------------------- service ----

class ChatService:
    """One getUpdates consumer: auth -> command/turn -> reply.

    Injection seams (offline tests, no network/credentials):
        transport=  TgClient transport stub (token still via polling_token())
        chat=       override for the model callable (same pattern as
                    loop.run_turn's `chat` argument); when set, no ChatClient
                    is constructed and no endpoint is contacted
        repo=       repo root used to read web/settings.json (defaults to
                    mycore.cli.REPO — production and cli agree)
        channels=   injected route adapters (dict[str, Channel]); when None
                    the map is built from web/settings.json on each poll
        now/sleep=  clock and sleep for deterministic tests
    """

    def __init__(self, state_dir: str | Path, allowlist,
                 *, dry_run: bool = False,
                 transport=None, chat=None, repo: str | Path | None = None,
                 channels=None,
                 now=lambda: time.time(), sleep=time.sleep,
                 max_poll_errors: int = MAX_POLL_ERRORS) -> None:
        allow = frozenset(allowlist)
        if not allow:
            # Defense in depth: main() refuses earlier with exit 3; this guard
            # keeps direct construction from ever answering an open chat.
            raise ValueError("empty allowlist — fail-closed, refusing to run")
        self.state = Path(state_dir)
        try:
            self.state.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.sessions = self.state / SESSIONS_DIR
        self.allowlist = allow
        self.dry_run = dry_run
        self.chat = chat                       # injected model callable or None
        self.channels = channels               # injected route adapters; None = built from settings
        self.repo = Path(repo) if repo is not None else Path(cli.REPO)
        self._now = now
        self._sleep = sleep
        self.max_poll_errors = max_poll_errors
        self._started = now()
        self._stop = False
        self._deny_stamp: dict[str, float] = {}
        self._reflect_check = 0.0       # last evolution cron evaluation (clock)
        self._cron_check = 0.0          # last scheduler evaluation (clock)
        self._chan_check = 0.0          # last channel inbound poll (clock)
        self.log = ServiceLog(self.state / LOG_NAME, now=now)
        self.lang = self._load_lang()
        # HARD token gate (tg.py): dedicated MYCORE_TG_TOKEN required here too.
        self.tg = TgClient(token=TgClient.polling_token(),
                           token_env=POLL_BOT_ENV, transport=transport)
        self.registry = build_registry(state_dir=self.state)
        # Car 2-D5: surface MCP startup notes (success counts / honest skip
        # reasons) in the service log — the model never sees a silent miss.
        for note in getattr(self.registry, "mcp_notes", []):
            self.log.write("MCP", _scrub_paths(note)[:240])

    # ------------------------------------------------------------ control ---
    def stop(self) -> None:
        """Request a graceful stop (current turn finishes first)."""
        self._stop = True

    def install_signal_handlers(self) -> None:
        import signal

        def _handle(signum, _frame) -> None:
            name = getattr(signal, "Signals")(signum).name
            self.log.write("SIGNAL", f"sig={name}")
            self.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, _handle)
            except (ValueError, OSError):       # non-main thread (tests)
                pass

    # ------------------------------------------------------------- locale ---
    def _load_lang(self) -> str:
        """Persisted reply language (<state>/lang.json) -> pack code.

        Fail-soft by design: absent/garbage/unknown code falls back to the
        default pack (en) and never blocks startup; a rejected persisted
        value is logged once as LANG_FALLBACK (no secret/path detail).
        """
        code = ""
        try:
            raw = json.loads((self.state / LANG_FILE_NAME).read_text(
                encoding="utf-8"))
            code = (str(raw.get("lang", "")).strip().lower()
                    if isinstance(raw, dict) else "")
        except (OSError, ValueError):
            code = ""
        if not code:
            return i18n.DEFAULT_LANG
        if not i18n.has_lang(code):
            self.log.write("LANG_FALLBACK", f"value={code[:16]}",
                           f"using={i18n.DEFAULT_LANG}")
            return i18n.DEFAULT_LANG
        return code

    def _t(self, key: str, **kw) -> str:
        """Localized user-visible string for the service-wide language."""
        return i18n.fmt(self.lang, key, **kw)

    # -------------------------------------------------------------- loop ----
    def run(self, once: bool = False) -> int:
        """getUpdates loop with offset bookkeeping + bounded backoff.

        Stops on self._stop (SIGTERM/SIGINT finish the current turn first),
        on --once after at most one handled update (or one empty fetch), or
        by raising TgError after max_poll_errors consecutive failures.
        Returns the number of handled updates.
        """
        offset: int | None = None
        processed = 0
        errors = 0
        self.log.write("LOOP_START", f"mode={'once' if once else 'poll'}")
        try:
            while not self._stop:
                timeout = 0 if once else DEFAULT_POLL_TIMEOUT
                try:
                    updates = self.tg.get_updates(offset=offset, timeout=timeout)
                except TgError as e:
                    if once:
                        self.log.write("POLL_ERR", "mode=once", f"err={str(e)[:120]}")
                        raise
                    errors += 1
                    self.log.write("POLL_ERR", f"n={errors}", f"err={str(e)[:120]}")
                    if errors >= self.max_poll_errors:
                        raise
                    if self._stop:
                        break
                    self._sleep(min(30.0, 1.5 ** min(errors, 10)))
                    continue
                errors = 0
                self._maybe_reflect()          # evolution cron leg (no-op when off)
                self._maybe_fire_cron()        # scheduler leg (no-op with no due jobs)
                self._maybe_poll_channels()    # channel leg (throttled, fail-soft)
                if once and not updates:
                    break                          # nothing pending: exit 0
                for upd in updates:
                    if not isinstance(upd, dict):
                        continue
                    uid = upd.get("update_id")
                    if isinstance(uid, int):
                        offset = uid + 1          # ack bookkeeping
                    self.handle_update(upd)
                    processed += 1
                    if once or self._stop:
                        break
                if once:
                    break
        finally:
            self.log.write("LOOP_END", f"processed={processed}")
        return processed

    # ------------------------------------------------------------- auth -----
    def _warn_denied(self, chat_id: object) -> None:
        """Throttled WARN for unauthorized senders. Never replies to them."""
        key = str(chat_id)
        nowt = self._now()
        last = self._deny_stamp.get(key)
        if last is None or nowt - last >= DENY_WARN_THROTTLE:
            self._deny_stamp[key] = nowt
            self.log.write("DENY", f"chat={redact_chat(chat_id)}",
                           "reason=not-in-allowlist")

    def handle_update(self, upd: dict) -> None:
        """One update -> (auth gate) -> command or chat turn -> single send."""
        if not isinstance(upd, dict):
            return
        msg = upd.get("message")
        if not isinstance(msg, dict):
            self.log.write("NOTE", "kind=non_message_update ignored")
            return
        frm = msg.get("from") or {}
        uid = frm.get("id")
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if not isinstance(cid, int):
            self.log.write("NOTE", "kind=missing_chat_id ignored")
            return
        if uid not in self.allowlist:
            self._warn_denied(cid)
            return                                   # no reply, ever
        text = msg.get("text")
        # Forum topic (car 4a-M3): a threaded message keeps ITS OWN pipeline
        # session under "{cid}:{tid}" so two topics never share context;
        # commands (/cron, /checkpoint, /model, ...) stay chat-level by
        # design — their state is chat-wide. The reply threads back via
        # message_thread_id when the update carried one (tg.send_message).
        # Computed BEFORE the voice branch so voice replies thread identically.
        tid = msg.get("message_thread_id")
        if not isinstance(tid, int) or isinstance(tid, bool):
            tid = None
        if not isinstance(text, str) or not text.strip():
            # Car 4c: a voice/audio note (no usable text) -> STT -> the very
            # same command/pipeline path below. Anything else without text
            # is still ignored, exactly as before.
            med = msg.get("voice")
            if not isinstance(med, dict):
                med = msg.get("audio")
            if isinstance(med, dict):
                self._handle_voice(cid, med, tid)
            return
        text = text.strip()
        try:
            if text.startswith("/"):
                reply = self._command(cid, text)
            else:
                skey = f"{cid}:{tid}" if tid is not None else cid
                reply = self._pipeline(cid, text, session_key=skey)
        except Exception as e:  # noqa: BLE001 — one bad update must not kill the loop
            self.log.write("UPDATE_ERR", f"chat={redact_chat(cid)}",
                           f"err={type(e).__name__}")
            reply = self._t("err.internal")
        if reply:
            self._send(cid, reply, thread_id=tid)
        self._drain_photos(cid, tid)      # text first, photos after (car 4c)

    def _handle_voice(self, chat_id: int, med: dict,
                      tid: int | None) -> None:
        """Voice/audio note -> getFile -> download -> STT -> the SAME text
        path as a typed message (car 4c).

        Fail-soft by contract: every failure replies with an honest locale
        line (voice.stt.*) and NEVER raises into the poll loop. Logs carry
        chat/ms/lang/chars only — never transcript content, never a URL
        (download/getFile URLs embed the token; TgError already sanitizes)."""
        try:
            dur = med.get("duration")
            if (isinstance(dur, (int, float)) and not isinstance(dur, bool)
                    and dur > VOICE_MAX_SECONDS):
                self._send(chat_id,
                           self._t("voice.stt.tooLong", n=int(dur)),
                           thread_id=tid)
                return
            file_id = med.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise TgError("missing file_id")
            meta = self.tg.get_file(file_id)
            size = meta.get("file_size") if isinstance(meta, dict) else None
            if (isinstance(size, int) and not isinstance(size, bool)
                    and size > VOICE_MAX_BYTES):
                # Oversize BEFORE downloading: honest failure, no wasted pull.
                reason = "file too large (5MB limit)"
                self.log.write("STT_FAIL", f"chat={redact_chat(chat_id)}",
                               f"err={reason}")
                self._send(chat_id,
                           self._t("voice.stt.failed", reason=reason),
                           thread_id=tid)
                return
            file_path = meta.get("file_path") if isinstance(meta, dict) else None
            if not isinstance(file_path, str) or not file_path:
                raise TgError("missing file_path")
            t0 = self._now()
            data = self.tg.download_file(file_path)
            from . import stt        # delayed: media module is optional (4c-A)
            res = stt.transcribe(data)
        except Exception as e:  # noqa: BLE001 — one bad note must not kill the loop
            reason = _scrub_paths(str(e)).strip()[:120] or type(e).__name__
            self.log.write("STT_FAIL", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}:{reason[:80]}")
            self._send(chat_id,
                       self._t("voice.stt.failed", reason=reason),
                       thread_id=tid)
            return
        out = res.get("text") if isinstance(res, dict) else ""
        reply = str(out or "").strip()
        if not reply:
            self.log.write("STT_FAIL", f"chat={redact_chat(chat_id)}",
                           "err=empty transcript")
            self._send(chat_id, self._t("voice.stt.empty"), thread_id=tid)
            return
        lang = str((res.get("language") if isinstance(res, dict) else "") or "?")
        self.log.write("STT_OK", f"chat={redact_chat(chat_id)}",
                       f"ms={int(max(0.0, self._now() - t0) * 1000)}",
                       f"lang={lang[:16]}", f"chars={len(reply)}")
        # From here: the EXACT typed-text path (command, session key rule,
        # single _send) — voice is only another input method.
        try:
            if reply.startswith("/"):
                out_text = self._command(chat_id, reply)
            else:
                skey = f"{chat_id}:{tid}" if tid is not None else chat_id
                out_text = self._pipeline(chat_id, reply, session_key=skey)
        except Exception as e:  # noqa: BLE001 — one bad turn must not kill the loop
            self.log.write("UPDATE_ERR", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}")
            out_text = self._t("err.internal")
        if out_text:
            self._send(chat_id, out_text, thread_id=tid)
        self._drain_photos(chat_id, tid)

    def _drain_photos(self, chat_id: int, tid: int | None) -> None:
        """Send images generated during this turn (car 4c): text reply
        FIRST, photos after; a photo failure only logs — it can never touch
        the delivered text. dry-run previews without transmitting.

        imagegen is optional (car 4c-A): an import failure is a silent no-op,
        so a partially-landed tree never breaks the loop."""
        try:
            from . import imagegen
        except Exception:  # noqa: BLE001 — media module absent = no-op
            return
        try:
            items = imagegen.drain_outbox()
        except Exception as e:  # noqa: BLE001 — drain must never bubble up
            self.log.write("PHOTO_DRAIN_FAIL", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}")
            return
        if not items:
            return
        for item in items:
            try:
                path, prompt = item[0], str(item[1])
                name = Path(path).name
            except Exception:  # noqa: BLE001 — malformed entry: skip, keep going
                continue
            if self.dry_run:
                self.log.write("DRYRUN_SEND_PHOTO", f"chat={redact_chat(chat_id)}",
                               f"name={name}")
                continue
            try:
                self.tg.send_photo(chat_id, Path(path), caption=prompt[:1000],
                                   thread_id=tid)
                self.log.write("SEND_PHOTO_OK", f"chat={redact_chat(chat_id)}",
                               f"name={name}")
            except Exception as e:  # noqa: BLE001 — photo failure never harms text
                self.log.write("SEND_PHOTO_FAIL", f"chat={redact_chat(chat_id)}",
                               f"name={name}",
                               f"err={type(e).__name__}:"
                               f"{_scrub_paths(str(e))[:80]}")

    def _send(self, chat_id: int, text: str,
              thread_id: int | None = None) -> bool:
        """The single Telegram outbound point (dry-run stops here; channel
        replies go through _channel_send with the same dry-run contract).
        `thread_id` threads the reply back into its forum topic (None =
        plain chat reply, unchanged payload)."""
        if self.dry_run:
            preview = _scrub_paths(text)[:120]
            self.log.write("DRYRUN_SEND", f"chat={redact_chat(chat_id)}",
                           f"chars={len(text)}")
            print(f"[dry-run] would send chat={redact_chat(chat_id)} "
                  f"chars={len(text)} preview={preview!r}")
            return False
        try:
            self.tg.send_message(chat_id, text, thread_id=thread_id)
            self.log.write("SEND_OK", f"chat={redact_chat(chat_id)}",
                           f"chars={len(text)}")
            return True
        except TgError as e:
            self.log.write("SEND_FAIL", f"chat={redact_chat(chat_id)}",
                           f"err={str(e)[:120]}")
            return False

    # ---------------------------------------------------------- commands ----
    def _settings_doc(self) -> dict:
        """The raw web/settings.json document. {} on any read/parse failure
        (callers fail closed on absence, never crash — same contract the
        mycore block reader has always had)."""
        try:
            doc = json.loads(
                (self.repo / "web" / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return doc if isinstance(doc, dict) else {}

    def _settings_block(self) -> dict:
        mc = self._settings_doc().get("mycore")
        return mc if isinstance(mc, dict) else {}

    def _budget(self) -> int | None:
        """Compaction budget: web/settings.json mycore.budget when present,
        else the cli default (same number the one-shot runner uses)."""
        raw = self._settings_block().get("budget")
        if isinstance(raw, bool):
            raw = None
        if isinstance(raw, int):
            return raw if raw > 0 else None
        if isinstance(raw, str) and raw.isascii() and raw.isdigit():
            return int(raw) if int(raw) > 0 else None
        return cli.DEFAULT_BUDGET_CHARS

    def _allowed_models(self) -> list[str]:
        """Configured /model picker list: web/settings.json mycore.models when
        present, else only the currently configured model id."""
        models = self._settings_block().get("models")
        if isinstance(models, list):
            out = [m for m in models if isinstance(m, str) and m]
            if out:
                return out
        return [cli.resolve_model_config().model]

    def _session_path(self, chat_id: int | str) -> Path:
        return self.sessions / f"{chat_id}.jsonl"

    def _override_path(self, chat_id: int | str) -> Path:
        return self.sessions / f"{chat_id}{MODEL_EXT}"

    def _read_override(self, chat_id: int | str) -> str | None:
        try:
            val = self._override_path(chat_id).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return val or None

    def _write_override(self, chat_id: int | str, model: str) -> None:
        self.sessions.mkdir(parents=True, exist_ok=True)
        self._override_path(chat_id).write_text(model + "\n", encoding="utf-8")

    def _model_for_chat(self, chat_id: int | str):
        """Effective ModelConfig for this chat: cli resolution + override."""
        cfg = cli.resolve_model_config()
        override = self._read_override(chat_id)
        if override:
            cfg.model = override
        return cfg

    def _archive_session(self, chat_id: int | str) -> bool:
        """Rotate the chat's session file into sessions/archive/ (/reset)."""
        src = self._session_path(chat_id)
        if not src.is_file():
            return False
        arch = self.sessions / ARCHIVE_DIR
        arch.mkdir(parents=True, exist_ok=True)
        stamp = int(self._now())
        dst = arch / f"{chat_id}.{stamp}.jsonl"
        n = 0
        while dst.exists():                        # same-second collisions
            n += 1
            dst = arch / f"{chat_id}.{stamp}.{n}.jsonl"
        shutil.move(str(src), str(dst))
        self.log.write("ARCHIVE", f"chat={redact_chat(chat_id)}")
        return True

    def _command(self, chat_id: int | str, text: str) -> str:
        parts = text.split()
        name = parts[0][1:].split("@", 1)[0].lower()   # strip "/" and "@botname"
        arg = " ".join(parts[1:]).strip()
        self.log.write("CMD", f"chat={redact_chat(chat_id)}", f"cmd={name}")

        if name == "help":
            return (self._t("cmd.help.title") + "\n"
                    + self._t("cmd.help.body"))
        if name == "reset":
            rotated = self._archive_session(chat_id)
            return (self._t("cmd.reset.archived") if rotated
                    else self._t("cmd.reset.empty"))
        if name == "model":
            cfg = self._model_for_chat(chat_id)
            if not arg:
                return self._t("cmd.model.show", model=cfg.model,
                               host=endpoint_host(cfg.base_url))
            if arg not in self._allowed_models():
                return self._t("cmd.model.unknown",
                               ids=", ".join(self._allowed_models()))
            try:
                self._write_override(chat_id, arg)
            except OSError:
                self.log.write("MODEL_SET_ERR", f"chat={redact_chat(chat_id)}",
                               "err=write_failed")
                return self._t("cmd.model.save_err")
            self.log.write("MODEL_SET", f"chat={redact_chat(chat_id)}",
                           f"model={arg}")
            return self._t("cmd.model.set", model=arg)
        if name == "status":
            turns = len(Session(self._session_path(chat_id)).load())
            cfg = self._model_for_chat(chat_id)
            up = fmt_uptime(self._now() - self._started)
            return self._t("cmd.status.line", turns=turns, model=cfg.model,
                           uptime=up)
        if name == "lang":
            return self._lang_command(chat_id, arg)
        if name == "learn":
            return self._learn_command(arg)
        if name == "reflect":
            return self._reflect_command(arg)
        if name == "evolution":
            return self._evolution_command()
        if name == "cron":
            return self._cron_command(chat_id, arg)
        if name == "checkpoint":
            return self._checkpoint_command(chat_id, arg)
        return self._t("cmd.unknown")

    def _lang_command(self, chat_id: int | str, arg: str) -> str:
        """`/lang` (show) and `/lang <code>` (switch, persisted service-wide).

        Only discovered packs are switchable — an unknown code changes
        nothing and answers with the honest available list (fail-closed).
        """
        codes = ", ".join(i18n.languages())
        if not arg:
            return self._t("cmd.lang.show", lang=self.lang, codes=codes)
        code = arg.strip().lower()
        if not i18n.has_lang(code):
            return self._t("cmd.lang.bad", codes=codes)
        self.lang = code
        try:
            (self.state / LANG_FILE_NAME).write_text(
                json.dumps({"lang": code}) + "\n", encoding="utf-8")
        except OSError:
            self.log.write("LANG_SET_ERR", f"chat={redact_chat(chat_id)}",
                           "err=write_failed")
            return self._t("cmd.lang.save_err")
        self.log.write("LANG_SET", f"chat={redact_chat(chat_id)}",
                       f"lang={code}")
        return self._t("cmd.lang.set", lang=code)

    # ---------------------------------------------------------- evolution ---
    def _rel_path(self, path: object) -> str:
        """Repo-relative POSIX path for chat replies (machine paths never
        appear in a reply — observation scrubbing rule)."""
        p = Path(str(path))
        try:
            return p.resolve().relative_to(self.repo.resolve()).as_posix()
        except (OSError, ValueError):
            return p.name

    def _rule_labels(self, rules: list[str]) -> str:
        return ", ".join(i18n.fmt(self.lang, f"rule.{r}", r) for r in rules)

    def _wd(self, value: object) -> str:
        """Weekday code -> localized name (wd.mon..wd.sun; unknown value
        passes through untouched so an odd config still reads honestly)."""
        raw = str(value)
        return i18n.fmt(self.lang, "wd." + raw, raw)

    def _evolution_cfg(self) -> dict:
        """Effective evolution settings (fail-closed: absent/invalid = off)."""
        try:
            doc = json.loads((self.repo / "web" / "settings.json").read_text(
                encoding="utf-8"))
        except (OSError, ValueError):
            doc = {}
        return evolution.evolution_settings(doc if isinstance(doc, dict) else {})

    def _evo_state_path(self) -> Path:
        return self.state / evolution.STATE_FILE_NAME

    def _learn_command(self, arg: str) -> str:
        """`/learn memory <text>` and `/learn skill <slug> :: <desc> :: <body>`.
        Everything runs through the evolution gate — no write without lint +
        pre-screen, no log line for a reject (service.log carries that)."""
        usage = self._t("cmd.learn.usage")
        words = arg.strip().split(None, 1)
        if not words:
            return usage
        kind = words[0].lower()
        rest = words[1].strip() if len(words) > 1 else ""
        if kind == "memory":
            if not rest:
                return usage
            res = evolution.learn_memory(rest, repo=self.repo)
        elif kind == "skill":
            segs = rest.split("::", 2)
            if len(segs) < 3 or not all(s.strip() for s in segs):
                return usage
            res = evolution.learn_skill(segs[0].strip(), segs[1].strip(),
                                        segs[2].strip(), repo=self.repo)
        else:
            return usage
        if not res.ok:
            self.log.write("LEARN_REJECT", f"kind={kind}",
                           "rules=" + ",".join(res.errors))
            return self._t("cmd.learn.rejected",
                           details=self._rule_labels(res.errors))
        self.log.write("LEARN_OK", f"kind={kind}",
                       f"path={self._rel_path(res.path)}")
        key = "cmd.learn.ok_mem" if kind == "memory" else "cmd.learn.ok_skill"
        msg = self._t(key, path=self._rel_path(res.path))
        if res.prescreen:
            msg += "\n" + self._t("cmd.learn.prescreen",
                                  notes="; ".join(res.prescreen))
        return msg

    def _reflect_command(self, arg: str) -> str:
        """`/reflect [daily|weekly|monthly]` — explicit trigger, same code
        path as the cron leg (the window key is marked done either way)."""
        period = (arg.strip() or "daily").lower()
        if period not in evolution.PERIODS:
            return self._t("cmd.reflect.bad")
        try:
            res = evolution.reflect(period, now=self._now(), repo=self.repo,
                                     state_path=self._evo_state_path())
        except (OSError, ValueError):
            self.log.write("REFLECT_ERR", f"period={period}",
                           "trigger=command")
            return self._t("cmd.reflect.fail")
        self.log.write("REFLECT", f"period={period}",
                       f"created={int(res.created)}", f"file={res.path.name}",
                       "trigger=command")
        if res.created:
            return self._t("cmd.reflect.ok", period=period,
                           path=self._rel_path(res.path),
                           skills=len(res.facts.get("skills") or []),
                           entries=sum((res.facts.get("entries") or {}).values()))
        return self._t("cmd.reflect.exists", period=period,
                       path=self._rel_path(res.path))

    def _evolution_command(self) -> str:
        """`/evolution` — loop status (observed facts only)."""
        cfg = self._evolution_cfg()
        sp = self._evo_state_path()
        st = evolution.load_state(sp)
        summ = evolution.status_summary(repo=self.repo, cfg=cfg,
                                        state_path=sp, now=self._now())
        onoff = self._t("common.on") if cfg.get("enabled") else self._t("common.off")
        lines = [self._t("cmd.evolution.head", enabled=onoff,
                         daily_at=cfg["daily_at"],
                         weekly_day=self._wd(cfg["weekly_day"]),
                         monthly_day=cfg["monthly_day"])]
        if summ.get("due_now"):
            lines.append(self._t("cmd.evolution.due",
                                 periods=", ".join(summ["due_now"])))
        runs = " ".join(f"{p}={st.get(p) or '-'}" for p in evolution.PERIODS)
        lines.append(self._t("cmd.evolution.last", runs=runs))
        lines.append(self._t("cmd.evolution.counts",
                             skills=summ["skills"],
                             learned=f"{summ['learned_bytes']} B",
                             entries=summ["log_entries"],
                             drafts=len(summ["drafts"])))
        tail = summ["log_tail"][-5:]
        if tail:
            lines.append(self._t("cmd.evolution.log"))
            lines += [f"  {e['ts'][:16]} {e['kind']} {e['summary']}"
                      for e in tail]
        else:
            lines.append(self._t("cmd.evolution.log_empty"))
        return "\n".join(lines)

    def _maybe_reflect(self) -> None:
        """Evolution cron leg (dual-driven loop, design differentiation #3): evaluation is
        throttled to REFLECT_CHECK_SEC; off unless evolution.enabled (fail
        closed); each reflect() marks its window, so a period fires once per
        window — with catch-up after downtime."""
        nowt = self._now()
        if nowt - self._reflect_check < REFLECT_CHECK_SEC:
            return
        self._reflect_check = nowt
        try:
            cfg = self._evolution_cfg()
            if not cfg.get("enabled"):
                return
            sp = self._evo_state_path()
            due = evolution.is_due(cfg, nowt, evolution.load_state(sp))
            for period in due:
                res = evolution.reflect(period, now=nowt, repo=self.repo,
                                        state_path=sp)
                self.log.write("REFLECT", f"period={period}",
                               f"created={int(res.created)}",
                               f"file={res.path.name}", "trigger=cron")
        except Exception as e:  # noqa: BLE001 — a reflect failure never kills the loop
            self.log.write("REFLECT_ERR", f"err={type(e).__name__}")

    def _cron_command(self, chat_id: int | str, arg: str) -> str:
        """`/cron` (list) · `/cron add [kind] <spec> :: <text>` · `/cron del <id>`.

        The structured leg of the dual-driven scheduler (the model `cron`
        tool shares this grammar and registry). Creation stamps the origin
        chat so a fired job knows where to answer."""
        words = arg.strip().split(None, 1)
        sub = (words[0].lower() if words else "list")
        rest = words[1].strip() if len(words) > 1 else ""
        path = self.state / scheduler.STATE_FILE_NAME
        if sub in ("list", "ls"):
            try:
                jobs = scheduler.load_jobs(path)
            except OSError:
                return self._t("cmd.cron.fail")
            if not jobs:
                return self._t("cmd.cron.empty")
            now = self._now()
            lines = [self._t("cmd.cron.list_head", n=len(jobs))]
            for j in jobs:
                state = self._t("cmd.cron.done") if j.get("done") else ""
                nf = scheduler.next_fire(j, now)
                delta = scheduler.fmt_delta(nf, now)
                lines.append(
                    f"  {j['id']} · {scheduler.describe(j)} · {j.get('kind')} · "
                    f"{self._t('cmd.cron.next')} "
                    f"{scheduler.fmt_beijing(nf)}"
                    f"{' (' + delta + ')' if delta else ''}"
                    f"{state} · {str(j.get('text', ''))[:40]}")
            return "\n".join(lines)
        if sub in ("add", "new"):
            kind = "message"
            body = rest
            head, sep, tail = rest.partition("::")
            if not sep:
                return self._t("cmd.cron.usage")
            head_words = head.split()
            if head_words and head_words[0].lower() in scheduler.KINDS:
                kind = head_words[0].lower()
                head = " ".join(head_words[1:])
            try:
                job = None
                jobs = scheduler.load_jobs(path)
                job = scheduler.create_job(jobs, head.strip(), tail, kind=kind,
                                           source="command", origin=chat_id,
                                           now=self._now())
                scheduler.save_jobs(path, jobs)
            except scheduler.SpecError as e:
                self.log.write("CRON_REJECT", f"chat={redact_chat(chat_id)}",
                               f"reason={str(e)[:60]}")
                return self._t("cmd.cron.bad_spec", detail=str(e))
            except OSError:
                self.log.write("CRON_ERR", "op=add", "err=write_failed")
                return self._t("cmd.cron.fail")
            self.log.write("CRON_ADD", f"chat={redact_chat(chat_id)}",
                           f"id={job['id']}", f"kind={job['kind']}")
            return self._t("cmd.cron.added", id=job['id'],
                           spec=scheduler.describe(job), kind=job['kind'],
                           nxt=scheduler.fmt_beijing(
                               scheduler.next_fire(job, self._now())))
        if sub in ("del", "delete", "rm"):
            job_id = rest.split()[0] if rest.split() else ""
            if not job_id:
                return self._t("cmd.cron.usage")
            try:
                jobs = scheduler.load_jobs(path)
                removed = scheduler.delete_job(jobs, job_id)
                if removed:
                    scheduler.save_jobs(path, jobs)
            except OSError:
                self.log.write("CRON_ERR", "op=del", "err=write_failed")
                return self._t("cmd.cron.fail")
            if not removed:
                return self._t("cmd.cron.del_miss", id=job_id)
            self.log.write("CRON_DEL", f"chat={redact_chat(chat_id)}",
                           f"id={job_id}")
            return self._t("cmd.cron.deleted", id=job_id)
        return self._t("cmd.cron.usage")

    def _checkpoint_command(self, chat_id: int | str, arg: str) -> str:
        """`/checkpoint` (list) · `/checkpoint restore <id>` (rollback) —
        the write safety net (car 1-D2): write_file/edit_file snapshot the
        previous bytes and mycore.checkpoint owns the store. A bare numeric
        arg is accepted as restore. Replies scrub machine paths through
        _rel_path like every other reply."""
        words = arg.strip().split(None, 1)
        sub = words[0].lower() if words else "list"
        rest = words[1].strip() if len(words) > 1 else ""
        if sub in ("list", "ls"):
            entries = checkpoint.list_entries(self.state, limit=10)
            if not entries:
                return self._t("cmd.checkpoint.empty")
            lines = [self._t("cmd.checkpoint.head", n=len(entries))]
            for e in entries:
                lines.append(f"  #{e['id']} · {self._rel_path(e.get('path', ''))} · "
                             f"{checkpoint.fmt_when(float(e.get('ts', 0.0)))}")
            return "\n".join(lines)
        token = rest.split()[0] if rest.split() else ""
        if sub.isdigit():                     # /checkpoint 7 accepted as restore
            token = sub
        elif sub not in ("restore", "undo", "get"):
            return self._t("cmd.checkpoint.usage")
        if not token.isdigit():
            return self._t("cmd.checkpoint.usage")
        entry_id = int(token)
        try:
            entry = checkpoint.restore(entry_id, self.state, now=self._now())
        except checkpoint.CheckpointError as e:
            if str(e).startswith("no checkpoint"):
                return self._t("cmd.checkpoint.miss", id=entry_id)
            self.log.write("CHECKPOINT_ERR", f"chat={redact_chat(chat_id)}",
                           f"id={entry_id}", f"err={str(e)[:60]}")
            return self._t("cmd.checkpoint.fail")
        self.log.write("CHECKPOINT_RESTORE", f"chat={redact_chat(chat_id)}",
                       f"id={entry_id}")
        return self._t("cmd.checkpoint.restored", id=entry_id,
                       path=self._rel_path(entry.get("path", "")),
                       size=entry.get("size", 0))

    def _wd_chat_target(self) -> int | None:
        """Fallback delivery chat: env MYCORE_WD_CHAT when it is a plain
        integer; unset/garbage -> None (log-only, by design)."""
        raw = os.environ.get(WD_CHAT_ENV, "").strip()
        return int(raw) if WD_CHAT_RE.fullmatch(raw) else None

    def _cron_deliver(self, job: dict, text: str) -> str:
        """One fired job's output -> origin chat, else MYCORE_WD_CHAT, else
        log-only. Returns the delivery label for CRON_FIRE (observed fact).

        A string origin is a channel endpoint `route:target` (the job was
        created inside a channel turn): it answers on the same route, and an
        unknown route or an honest send failure falls back to the existing
        int-origin > MYCORE_WD_CHAT > log-only chain."""
        origin = job.get("origin")
        self._fire_hooks("cron_fire",
                         origin if isinstance(origin, (int, str)) else None)
        if isinstance(origin, str):
            route, sep, dest = origin.partition(":")
            if sep and dest:
                try:
                    chan = self._channel_map().get(route)
                except Exception as e:  # noqa: BLE001 — fallback below, loop lives
                    self.log.write("CHANNEL_ERR", "phase=cron",
                                   f"err={type(e).__name__}")
                    chan = None
                if chan is not None and \
                        self._channel_send(chan, route, dest, text):
                    return f"channel:{route}"
        target = origin if isinstance(origin, int) else self._wd_chat_target()
        if not isinstance(target, int):
            return "none"
        self._send(target, text)
        return "origin" if target == origin else "wd"

    def _cron_prompt_text(self, job: dict) -> str:
        """kind=prompt: run ONE fresh model turn (constitution stack + the
        stored text; tools enabled, no session persistence) and return the
        reply — honest short lines on any stop, same mapping as _pipeline."""
        cfg = cli.resolve_model_config()
        chat_fn = self.chat or ChatClient(cfg).chat
        messages = build_system_stack(context_dirs=resolve_context_dirs()) \
            + [{"role": "user",
                "content": job.get("text", "")}]
        try:
            res = run_turn(messages, chat_fn, self.registry,
                           budget_chars=self._budget())
        except Exception as e:  # noqa: BLE001 — run_turn never raises; belt anyway
            self.log.write("CRON_ERR", f"id={job.get('id', '?')}",
                           f"err={type(e).__name__}")
            return self._t("err.internal")
        if res.stop == "final":
            return res.text.strip() or self._t("err.no_output")
        if res.stop == "max_iters":
            return self._t("err.max_iters", n=res.iterations)
        self.log.write("CRON_ERR", f"id={job.get('id', '?')}",
                       "err=model", f"detail={res.text[:160]}")
        return self._t("err.model")

    def _maybe_fire_cron(self) -> None:
        """Scheduler leg (Phase B2): throttled to CRON cadence like the
        evolution leg; mark-first-then-execute (a crash loses one delivery,
        never duplicates one); delivery target = origin > MYCORE_WD_CHAT >
        log-only. A fire failure never kills the poll loop."""
        nowt = self._now()
        if nowt - self._cron_check < REFLECT_CHECK_SEC:
            return
        self._cron_check = nowt
        path = self.state / scheduler.STATE_FILE_NAME
        try:
            jobs = scheduler.load_jobs(path)
            due = scheduler.due_jobs(jobs, nowt)
            if not due:
                return
            for job in due:
                scheduler.mark_fired(job, nowt)
                scheduler.save_jobs(path, jobs)   # persist BEFORE execution
                if job.get("kind") == "prompt":
                    text = self._cron_prompt_text(job)
                else:
                    text = str(job.get("text", ""))
                label = self._cron_deliver(job, text)
                preview = _scrub_paths(text)[:80]
                self.log.write("CRON_FIRE", f"id={job.get('id', '?')}",
                               f"kind={job.get('kind', '?')}",
                               f"type={job.get('type', '?')}",
                               f"delivered={label}", f"preview={preview}")
        except Exception as e:  # noqa: BLE001 — a fire failure never kills the loop
            self.log.write("CRON_ERR", f"err={type(e).__name__}")

    # ------------------------------------------------------------ channels --
    def _channel_map(self) -> dict:
        """Route adapters for this evaluation: the injected dict (tests) or
        freshly built from web/settings.json with the SERVICE state root, so
        cursors, sessions and the log share one directory. Settings values
        are only read, never logged."""
        if self.channels is not None:
            return self.channels
        cfg = dict(self._settings_doc())
        cfg["state_dir"] = str(self.state)
        return channels_mod.load_channels(self.repo, cfg)

    @staticmethod
    def _channel_allow(route_cfg: object) -> frozenset[str]:
        """`channels.<route>.allow` -> accepted senders. Fail-closed: a
        missing block, a non-list, or non-string entries all yield the empty
        set, which denies every inbound message (same rule as ALLOW_ENV)."""
        if not isinstance(route_cfg, dict):
            return frozenset()
        raw = route_cfg.get("allow")
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(v for v in raw if isinstance(v, str))

    def _channel_send(self, chan, route: str, target: str, text: str) -> bool:
        """One outbound channel reply — the dry-run contract mirrors _send:
        evaluated, previewed and logged, never transmitted. Returns True on
        an accepted delivery (or a dry-run), False on an honest failure."""
        who = f"route={route} chat={redact_chat(target)}"
        if self.dry_run:
            preview = _scrub_paths(text)[:120]
            self.log.write("DRYRUN_SEND", who, f"chars={len(text)}")
            print(f"[dry-run] would send {who} chars={len(text)} "
                  f"preview={preview!r}")
            return True                       # dry-run counts as delivered
        try:
            ok = bool(chan.send(target, text))
        except Exception as e:  # noqa: BLE001 — adapters promise never to raise
            self.log.write("CHANNEL_SEND_FAIL", who, f"err={type(e).__name__}")
            return False
        if ok:
            self.log.write("CHANNEL_SEND_OK", who, f"chars={len(text)}")
        else:
            self.log.write("CHANNEL_SEND_FAIL", who)
        return ok

    def _handle_channel_inbound(self, route: str, chan, inbound,
                                allow: frozenset[str]) -> None:
        """One inbound message -> allowlist gate -> the SAME command/pipeline
        path as Telegram -> reply through the SAME route. The conversation
        key is Inbound.key (str, `route:sender`): session jsonl, /model
        override and cron origins all live under that string."""
        sender = str(getattr(inbound, "sender", "") or "")
        key = str(getattr(inbound, "key", None) or f"{route}:{sender}")
        if sender not in allow:
            nowt = self._now()
            last = self._deny_stamp.get(key)
            if last is None or nowt - last >= DENY_WARN_THROTTLE:
                self._deny_stamp[key] = nowt
                self.log.write("CHANNEL_DENY", f"route={route}",
                               f"sender={redact_chat(sender)}",
                               "reason=not-in-allowlist")
            return                                   # no reply, ever
        text = str(getattr(inbound, "text", "") or "").strip()
        if not text:
            return
        target = str(getattr(inbound, "target", "") or "")
        self.log.write("CHANNEL_IN", f"route={route}",
                       f"sender={redact_chat(sender)}", f"chars={len(text)}")
        try:
            if text.startswith("/"):
                reply = self._command(key, text)
            else:
                reply = self._pipeline(key, text)
        except Exception as e:  # noqa: BLE001 — one bad message, one line
            self.log.write("CHANNEL_ERR", f"route={route}",
                           f"chat={redact_chat(key)}", f"err={type(e).__name__}")
            reply = self._t("err.internal")
        if reply:
            self._channel_send(chan, route, target, reply)

    def _maybe_poll_channels(self) -> None:
        """Channel inbound leg: throttled to CHANNEL_POLL_SEC; poll every
        route once, gate each message on its allowlist (fail-closed), answer
        in place. Every failure mode maps to one CHANNEL_* line and a skip —
        a broken route, a bad settings file or a crashing turn never kills
        the main loop (the reflect/cron legs' philosophy)."""
        nowt = self._now()
        if nowt - self._chan_check < CHANNEL_POLL_SEC:
            return
        self._chan_check = nowt
        try:
            route_map = self._channel_map()
            cfg_doc = self._settings_doc().get("channels")
        except Exception as e:  # noqa: BLE001 — a config error costs one line
            self.log.write("CHANNEL_ERR", "phase=load", f"err={type(e).__name__}")
            return
        block = cfg_doc if isinstance(cfg_doc, dict) else {}
        for route, chan in route_map.items():
            try:
                items = chan.poll()
            except Exception as e:  # noqa: BLE001 — one bad route, one line
                self.log.write("CHANNEL_ERR", f"route={route}", "phase=poll",
                               f"err={type(e).__name__}")
                continue
            if not isinstance(items, list):
                continue
            allow = self._channel_allow(block.get(route))
            for inbound in items:
                try:
                    self._handle_channel_inbound(route, chan, inbound, allow)
                except Exception as e:  # noqa: BLE001 — one bad message, one line
                    self.log.write("CHANNEL_ERR", f"route={route}",
                                   f"err={type(e).__name__}")

    # ------------------------------------------------------------ turn ------
    def _typing_heartbeat(self, chat_id: int | str):
        """Start a best-effort `typing` indicator for one pipeline turn and
        return the stop callable (always `finally`-stopped by _pipeline).

        Contract: silent by design — dry-run sends nothing, and any failure
        (no token, offline, rate limit) stops the loop immediately without a
        log line: a lost heartbeat must never cost a reply. The first beat
        fires on the daemon thread right away (the model call overlaps it),
        then repeats every TYPING_INTERVAL_SEC until the stop callable runs.
        """
        if self.dry_run or not isinstance(chat_id, int):
            # Channel turns carry str session keys: typing is a Telegram-only
            # action (numeric chat id) and never a channel reply path.
            return lambda: None
        stop = threading.Event()

        def beat() -> None:
            while True:
                try:
                    self.tg.send_chat_action(chat_id, "typing")
                except Exception:  # noqa: BLE001 — silent stop, by design
                    return
                if stop.wait(TYPING_INTERVAL_SEC):
                    return

        threading.Thread(target=beat, name="typing-heartbeat",
                         daemon=True).start()
        return stop.set

    def _fire_hooks(self, event: str, chat_id: int | str | None = None,
                    tool: str | None = None) -> None:
        """Event hook (car 4a): fire the settings `hooks` block for `event`
        on a background thread — never blocks, never raises, never echoes
        output (mycore.hooks contract). `chat_id` is redacted here (service
        log discipline) before it reaches the hook environment."""
        try:
            hooks_mod.fire(event, repo=self.repo,
                           chat=redact_chat(chat_id) if chat_id is not None else None,
                           tool=tool, log=self.log.write)
        except Exception:  # noqa: BLE001 — a hook problem never breaks a turn
            pass

    def _pipeline(self, chat_id: int | str, text: str,
                  session_key: int | str | None = None) -> str:
        """Session load -> body stack -> run_turn -> persisted delta -> reply.

        Returns the final reply text (honest short lines on failure); the
        caller performs the single send. Commands never enter here, so the
        typing heartbeat covers exactly the long part (model + tools), and
        the scheduler origin is pinned for the whole turn (a `cron` tool
        call inside it records this chat as the job's delivery target).

        `session_key` (car 4a-M3) overrides ONLY which session file the turn
        reads/persists — the forum-topic key "{cid}:{tid}". Chat identity
        (typing beat, cron origin, hooks, log lines, /model override) stays
        `chat_id`; None = chat-level session (unchanged behavior). Cross-topic
        context transfer is v1 out of scope: topics are isolated on purpose.
        """
        stop_beat = self._typing_heartbeat(chat_id)
        scheduler.set_origin(chat_id)   # cron tool remembers where it was asked
        self._fire_hooks("turn_start", chat_id)
        try:
            return self._pipeline_body(chat_id, text, session_key=session_key)
        finally:
            stop_beat()
            scheduler.clear_origin()
            self._fire_hooks("turn_end", chat_id)

    def _pipeline_body(self, chat_id: int, text: str,
                       session_key: int | str | None = None) -> str:
        cfg = self._model_for_chat(chat_id)
        path = self._session_path(session_key if session_key is not None
                                  else chat_id)
        sess = Session(path)
        history = sess.load()
        if not history and not sess.meta():        # fresh (or garbage-only) file
            sess = Session.create(path, meta={"model": cfg.model})
            history = []
        history = [m for m in history if m.get("role") != "system"]

        stack = build_system_stack(context_dirs=resolve_context_dirs())
        messages = stack + history
        pre_ids = {id(m) for m in messages}
        messages.append({"role": "user", "content": text})

        chat_fn = self.chat or ChatClient(cfg).chat
        self.log.write("TURN_START", f"chat={redact_chat(chat_id)}",
                       f"model={cfg.model}", f"history={len(history)}")

        def on_event(kind: str, data: dict) -> None:
            # Names only — tool ARGUMENTS may carry machine paths, never logged.
            if kind == "tool_call":
                self.log.write("TOOL", f"chat={redact_chat(chat_id)}",
                               f"name={data.get('name', '?')}")
                self._fire_hooks("tool_call", chat_id,
                                 tool=str(data.get("name", ""))[:64])
            elif kind == "compact":
                self.log.write("COMPACT", f"folded={data.get('folded', 0)}")
                self._fire_hooks("compact", chat_id)

        try:
            res = run_turn(messages, chat_fn, self.registry,
                           on_event=on_event, budget_chars=self._budget())
        except Exception as e:  # noqa: BLE001 — run_turn never raises; belt anyway
            self.log.write("TURN_CRASH", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}")
            self._fire_hooks("error", chat_id)
            return self._t("err.internal")

        # Persist only this turn's new messages (identity-based: compaction
        # rewrites list slots, so an index slice would misalign). System lines
        # and compaction markers are excluded — session.py drops them on load.
        delta = [m for m in messages
                 if id(m) not in pre_ids and m.get("role") != "system"]
        try:
            sess.append_many(delta)
        except OSError as e:
            self.log.write("SESSION_APPEND_ERR", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}")

        self.log.write("TURN_END", f"chat={redact_chat(chat_id)}",
                       f"stop={res.stop}", f"iters={res.iterations}",
                       f"tools={res.tool_calls}")
        if res.stop == "final":
            return res.text.strip() or self._t("err.no_output")
        if res.stop == "max_iters":
            return self._t("err.max_iters", n=res.iterations)
        # model_error: detail goes to the log only — the chat gets one honest
        # line with no engine name, exception internals, or paths.
        self.log.write("MODEL_ERR", f"chat={redact_chat(chat_id)}",
                       f"detail={res.text[:160]}")
        return self._t("err.model")


# ------------------------------------------------------------------ CLI -----

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="mycore.service",
        description="mycore production Telegram chat service (P5-1)")
    ap.add_argument("--once", action="store_true",
                    help="handle at most one pending update, then exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="run the full loop but skip outbound sends "
                         "(offsets still advance — see module docstring)")
    args = ap.parse_args(argv)

    # Gate 1: fail-closed allowlist — checked before pidfile, token, anything.
    allow = parse_allowlist(os.environ.get(ALLOW_ENV))
    if not allow:
        print(f"mycore.service: {ALLOW_ENV} is unset or empty — refusing to "
              "start (fail-closed: an unauthenticated chat must never be "
              "answered)", file=sys.stderr)
        return 3

    state = state_dir()
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"mycore.service: state dir unavailable ({type(e).__name__})",
              file=sys.stderr)
        return 1

    # Gate 2: single instance (one getUpdates consumer per token).
    pidfile = state / PIDFILE_NAME
    try:
        acquired, holder = acquire_pidfile(pidfile)
    except OSError as e:
        print(f"mycore.service: pidfile unavailable ({type(e).__name__})",
              file=sys.stderr)
        return 1
    if not acquired:
        who = f"pid {holder}" if holder else "an unknown owner"
        print(f"mycore.service: pidfile already held by {who} — refusing to "
              "start (single instance only)", file=sys.stderr)
        return 2

    svc: ChatService | None = None
    try:
        # Gate 3: dedicated second-bot token (TgError message is secret-free).
        svc = ChatService(state, allow, dry_run=args.dry_run)
        svc.install_signal_handlers()
        svc.log.write("START", f"pid={os.getpid()}",
                      f"dry_run={int(bool(args.dry_run))}",
                      f"once={int(bool(args.once))}",
                      f"allow={len(allow)}")
        svc.run(once=args.once)
        svc.log.write("EXIT", "rc=0")
        return 0
    except TgError as e:
        print(f"mycore.service: {e}", file=sys.stderr)
        gate = "startup" if svc is None else "poll"
        ServiceLog(state / LOG_NAME).write("ABORT", f"gate={gate}",
                                           f"err={str(e)[:160]}")
        return 4 if svc is None else 1
    finally:
        release_pidfile(pidfile)


if __name__ == "__main__":
    sys.exit(main())
