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
import time
import urllib.parse
from pathlib import Path

from . import cli
from . import evolution
from .assembly import build_system_stack
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

# Honest one-line replies. The English constants stay importable (tests and
# tooling compare against them); live replies resolve the SAME keys through
# the locale packs (default en), so a language switch never weakens the
# honesty rule: no engine names, no paths, no exception internals in chat.
NO_OUTPUT_LINE = i18n.get(i18n.DEFAULT_LANG, "err.no_output",
                          "(no output — see logs)")
MODEL_ERR_LINE = i18n.get(i18n.DEFAULT_LANG, "err.model",
                          "(model error — the turn stopped; see service logs)")
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
        now/sleep=  clock and sleep for deterministic tests
    """

    def __init__(self, state_dir: str | Path, allowlist,
                 *, dry_run: bool = False,
                 transport=None, chat=None, repo: str | Path | None = None,
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
        self.repo = Path(repo) if repo is not None else Path(cli.REPO)
        self._now = now
        self._sleep = sleep
        self.max_poll_errors = max_poll_errors
        self._started = now()
        self._stop = False
        self._deny_stamp: dict[str, float] = {}
        self._reflect_check = 0.0       # last evolution cron evaluation (clock)
        self.log = ServiceLog(self.state / LOG_NAME, now=now)
        self.lang = self._load_lang()
        # HARD token gate (tg.py): dedicated MYCORE_TG_TOKEN required here too.
        self.tg = TgClient(token=TgClient.polling_token(),
                           token_env=POLL_BOT_ENV, transport=transport)
        self.registry = build_registry()

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
        if not isinstance(text, str) or not text.strip():
            return
        text = text.strip()
        try:
            if text.startswith("/"):
                reply = self._command(cid, text)
            else:
                reply = self._pipeline(cid, text)
        except Exception as e:  # noqa: BLE001 — one bad update must not kill the loop
            self.log.write("UPDATE_ERR", f"chat={redact_chat(cid)}",
                           f"err={type(e).__name__}")
            reply = self._t("err.internal")
        if reply:
            self._send(cid, reply)

    def _send(self, chat_id: int, text: str) -> bool:
        """The single outbound point (dry-run stops here)."""
        if self.dry_run:
            preview = _scrub_paths(text)[:120]
            self.log.write("DRYRUN_SEND", f"chat={redact_chat(chat_id)}",
                           f"chars={len(text)}")
            print(f"[dry-run] would send chat={redact_chat(chat_id)} "
                  f"chars={len(text)} preview={preview!r}")
            return False
        try:
            self.tg.send_message(chat_id, text)
            self.log.write("SEND_OK", f"chat={redact_chat(chat_id)}",
                           f"chars={len(text)}")
            return True
        except TgError as e:
            self.log.write("SEND_FAIL", f"chat={redact_chat(chat_id)}",
                           f"err={str(e)[:120]}")
            return False

    # ---------------------------------------------------------- commands ----
    def _settings_block(self) -> dict:
        try:
            doc = json.loads(
                (self.repo / "web" / "settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        mc = doc.get("mycore") if isinstance(doc, dict) else None
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

    def _session_path(self, chat_id: int) -> Path:
        return self.sessions / f"{chat_id}.jsonl"

    def _override_path(self, chat_id: int) -> Path:
        return self.sessions / f"{chat_id}{MODEL_EXT}"

    def _read_override(self, chat_id: int) -> str | None:
        try:
            val = self._override_path(chat_id).read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return val or None

    def _write_override(self, chat_id: int, model: str) -> None:
        self.sessions.mkdir(parents=True, exist_ok=True)
        self._override_path(chat_id).write_text(model + "\n", encoding="utf-8")

    def _model_for_chat(self, chat_id: int):
        """Effective ModelConfig for this chat: cli resolution + override."""
        cfg = cli.resolve_model_config()
        override = self._read_override(chat_id)
        if override:
            cfg.model = override
        return cfg

    def _archive_session(self, chat_id: int) -> bool:
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

    def _command(self, chat_id: int, text: str) -> str:
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
        return self._t("cmd.unknown")

    def _lang_command(self, chat_id: int, arg: str) -> str:
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
                         weekly_day=cfg["weekly_day"],
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

    # ------------------------------------------------------------ turn ------
    def _pipeline(self, chat_id: int, text: str) -> str:
        """Session load -> body stack -> run_turn -> persisted delta -> reply.

        Returns the final reply text (honest short lines on failure); the
        caller performs the single send.
        """
        cfg = self._model_for_chat(chat_id)
        path = self._session_path(chat_id)
        sess = Session(path)
        history = sess.load()
        if not history and not sess.meta():        # fresh (or garbage-only) file
            sess = Session.create(path, meta={"model": cfg.model})
            history = []
        history = [m for m in history if m.get("role") != "system"]

        stack = build_system_stack()               # constitution/memory first
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
            elif kind == "compact":
                self.log.write("COMPACT", f"folded={data.get('folded', 0)}")

        try:
            res = run_turn(messages, chat_fn, self.registry,
                           on_event=on_event, budget_chars=self._budget())
        except Exception as e:  # noqa: BLE001 — run_turn never raises; belt anyway
            self.log.write("TURN_CRASH", f"chat={redact_chat(chat_id)}",
                           f"err={type(e).__name__}")
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
