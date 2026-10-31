"""mycore/hooks — event hooks fired from chat-service turns (car 4a).

Configuration lives in web/settings.json under the top-level `hooks` block:

    {"turn_end": [{"cmd": ["scripts/notify.sh", "arg"], "timeout": 5}]}

Contract (docs/features/event_hooks.md):
  * events whitelist: turn_start, turn_end, tool_call, compact, cron_fire,
    error — unknown event keys are refused at settings-write time (400);
  * `cmd` is an argv array executed DIRECTLY via subprocess (never through a
    shell): shell metacharacters in arguments are literal text, never
    syntax. argv[0] is resolved before exec — a path (contains "/" or starts
    with "." or "~") resolves against the repo root, a bare name resolves
    through PATH — and must exist at settings-write time (400 otherwise);
  * timeout 1..30 s per hook (default 5); a timeout kills the child
    (subprocess.run contract);
  * every fire runs on ONE background daemon thread; the registered hooks of
    an event run sequentially, in registration order — a hook can never
    block or fail a conversation;
  * environment is a minimal whitelist: PATH, HOME (when present), plus
    AIDOG_HOOK_EVENT, optional AIDOG_HOOK_CHAT (pre-redacted by the caller,
    id:<last4> only — redact_chat discipline) and AIDOG_HOOK_TOOL (tool name
    only, never arguments);
  * hook output is NEVER echoed — not into chat replies, not into log lines
    (zero-echo: a hook printing a secret leaks nowhere). A failure becomes
    one HOOK_FAIL line: event name + status/exit code + duration.

stdlib only. English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent

# Event whitelist — converged to the service's REAL wiring points (v1):
# turn_start/turn_end/tool_call/compact/error live in ChatService, cron_fire
# in _cron_deliver. `checkpoint` is deliberately NOT in the list: snapshots
# happen inside the tool layer (not this car's partition), and a restore-only
# hook would be misleading — see docs/features/event_hooks.md.
EVENTS = ("turn_start", "turn_end", "tool_call", "compact", "cron_fire", "error")

DEFAULT_TIMEOUT = 5
MIN_TIMEOUT = 1
MAX_TIMEOUT = 30
MAX_HOOKS_PER_EVENT = 16
MAX_CMD_ARGS = 32
MAX_ARG_CHARS = 4096
HOOK_KEYS = ("cmd", "timeout")

# Minimal child environment: nothing inherits by default. PATH/HOME only —
# no API keys, no console tokens, no channel credentials ever cross into a
# hook process (README contract 2 discipline).
ENV_WHITELIST = ("PATH", "HOME")


def _resolve_argv0(arg0: object, repo: str | Path = REPO) -> str | None:
    """Resolve argv[0] to an absolute executable path, or None if missing.

    Path-shaped names (containing "/", or starting with "." / "~") resolve
    against `repo` when relative; bare names resolve through PATH via
    shutil.which(). Existence only — no shell, no search heuristics beyond
    PATH (see module docstring)."""
    if not isinstance(arg0, str) or not arg0:
        return None
    if "/" in arg0 or arg0.startswith((".", "~")):
        p = Path(arg0).expanduser()
        if not p.is_absolute():
            p = Path(repo) / p
        try:
            return str(p) if p.is_file() else None
        except OSError:
            return None
    return shutil.which(arg0)


def validate_hooks(doc: object, repo: str | Path = REPO) -> tuple[dict, list[str]]:
    """Schema validation for the settings `hooks` partition (all-or-nothing
    per patch: any error -> the whole write is refused with 400). Returns
    (clean, errors); `clean` keeps the valid entries with defaults filled so
    the settings loader can also use it for partial hand-edited configs."""
    if not isinstance(doc, dict):
        return {}, ["hooks must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for event, entries in doc.items():
        if event not in EVENTS:
            errors.append(f"unknown hooks event: {event} "
                          f"(allowed: {', '.join(EVENTS)})")
            continue
        if not isinstance(entries, list):
            errors.append(f"hooks.{event} must be a list of hook objects")
            continue
        if len(entries) > MAX_HOOKS_PER_EVENT:
            errors.append(f"hooks.{event} holds at most "
                          f"{MAX_HOOKS_PER_EVENT} hooks")
            continue
        out: list[dict] = []
        for i, entry in enumerate(entries):
            where = f"hooks.{event}[{i}]"
            if not isinstance(entry, dict):
                errors.append(f"{where} must be an object")
                continue
            bad = [k for k in entry if k not in HOOK_KEYS]
            if bad:
                errors.append(f"{where} unknown key: {bad[0]} "
                              f"(allowed: {', '.join(HOOK_KEYS)})")
                continue
            cmd = entry.get("cmd")
            ok = True
            if not isinstance(cmd, list) or not cmd:
                errors.append(f"{where}.cmd must be a non-empty array of strings")
                ok = False
            elif len(cmd) > MAX_CMD_ARGS:
                errors.append(f"{where}.cmd holds at most {MAX_CMD_ARGS} arguments")
                ok = False
            else:
                for j, arg in enumerate(cmd):
                    if (not isinstance(arg, str) or not arg
                            or len(arg) > MAX_ARG_CHARS
                            or "\x00" in arg or "\n" in arg or "\r" in arg):
                        errors.append(
                            f"{where}.cmd[{j}] must be a single-line non-empty "
                            f"string of at most {MAX_ARG_CHARS} chars")
                        ok = False
                        break
            if ok and _resolve_argv0(cmd[0], repo) is None:
                errors.append(f"{where}.cmd[0] not found: "
                              f"{str(cmd[0])[:80]}")
                ok = False
            timeout = entry.get("timeout", DEFAULT_TIMEOUT)
            if (isinstance(timeout, bool) or not isinstance(timeout, int)
                    or not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT):
                errors.append(f"{where}.timeout must be an integer in "
                              f"{MIN_TIMEOUT}..{MAX_TIMEOUT}")
                ok = False
            if ok:
                out.append({"cmd": list(cmd), "timeout": timeout})
        clean[event] = out
    return clean, errors


def load_hooks(repo: str | Path | None = None) -> tuple[dict, list[str]]:
    """Read web/settings.json `hooks` -> (clean, errors). Fail-soft: any read
    or parse failure yields ({}, []) — no hooks configured, never a crash."""
    root = Path(repo) if repo is not None else REPO
    try:
        doc = json.loads((root / "web" / "settings.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}, []
    if not isinstance(doc, dict):
        return {}, []
    block = doc.get("hooks")
    if not isinstance(block, dict):
        return {}, []
    return validate_hooks(block, root)


def fire(event: str, *, repo: str | Path | None = None,
         chat: str | None = None, tool: str | None = None,
         log: Callable[..., None] | None = None) -> threading.Thread | None:
    """Fire every registered hook for `event` on a background thread.

    Returns the started Thread (tests join it), or None when nothing is
    configured for the event. Never raises: config problems become one
    HOOK_FAIL status=config_invalid line (reason lives at settings-write
    time), execution problems become HOOK_FAIL lines inside the thread.
    `chat` must arrive PRE-REDACTED (redact_chat: id:<last4>) and `tool` is a
    bare tool name — arguments never cross the boundary."""
    if event not in EVENTS:
        return None
    try:
        clean, errors = load_hooks(repo)
    except Exception:  # noqa: BLE001 — fire must never raise
        clean, errors = {}, []
    event_errs = [e for e in errors if e.startswith(f"hooks.{event}[")]
    specs = clean.get(event) or []
    if event_errs and log is not None:
        try:
            log("HOOK_FAIL", f"event={event}", "status=config_invalid")
        except Exception:  # noqa: BLE001
            pass
    if not specs:
        return None
    root = Path(repo) if repo is not None else REPO
    env_chat = chat if isinstance(chat, str) and chat else None
    if env_chat and ("\n" in env_chat or "\r" in env_chat or "\x00" in env_chat):
        env_chat = None                     # one line only, fail-soft
    env_tool = tool if isinstance(tool, str) and tool else None
    if env_tool and ("\n" in env_tool or "\x00" in env_tool):
        env_tool = None
    th = threading.Thread(target=_run_hooks,
                          args=(event, specs, str(root), env_chat, env_tool, log),
                          daemon=True, name=f"hooks-{event}")
    th.start()
    return th


def _run_hooks(event: str, specs: list[dict], repo: str,
               chat: str | None, tool: str | None,
               log: Callable[..., None] | None) -> None:
    """Sequential execution of one event's hooks, registration order."""
    for i, spec in enumerate(specs):
        try:
            _run_one(event, i, spec, repo, chat, tool, log)
        except Exception as e:  # noqa: BLE001 — one bad hook, one line, next
            _fail(log, event, i, f"error:{type(e).__name__}", None, 0.0)


def _run_one(event: str, idx: int, spec: dict, repo: str,
             chat: str | None, tool: str | None,
             log: Callable[..., None] | None) -> None:
    """Run ONE hook to completion; log HOOK_FAIL on any non-zero outcome.
    Output (stdout+stderr) is captured and discarded — zero echo."""
    cmd = list(spec.get("cmd") or [])
    timeout = spec.get("timeout", DEFAULT_TIMEOUT)
    if not cmd:
        _fail(log, event, idx, "missing", None, 0.0)
        return
    resolved = _resolve_argv0(cmd[0], repo)
    if resolved is None:
        _fail(log, event, idx, "missing", str(cmd[0]), 0.0)
        return
    cmd[0] = resolved
    env = {k: os.environ[k] for k in ENV_WHITELIST if k in os.environ}
    env["AIDOG_HOOK_EVENT"] = event
    if chat:
        env["AIDOG_HOOK_CHAT"] = chat
    if tool:
        env["AIDOG_HOOK_TOOL"] = tool
    t0 = time.monotonic()
    status = "ok"
    try:
        proc = subprocess.run(cmd, cwd=repo, env=env, capture_output=True,
                              text=True, timeout=timeout)
        if proc.returncode != 0:
            status = f"exit:{proc.returncode}"
    except subprocess.TimeoutExpired:
        status = f"timeout:{timeout}s"      # child killed by subprocess.run
    except FileNotFoundError:
        status = "missing"
    except OSError as e:
        status = f"oserror:{type(e).__name__}"
    except Exception as e:  # noqa: BLE001 — last-resort containment
        status = f"error:{type(e).__name__}"
    dur = time.monotonic() - t0
    if status != "ok":
        _fail(log, event, idx, status, resolved, dur)


def _fail(log: Callable[..., None] | None, event: str, idx: int,
          status: str, cmd0: str | None, dur: float) -> None:
    """One HOOK_FAIL line: event + status/exit code + duration. No hook
    output content, ever (zero-echo); only argv[0]'s basename for grepping."""
    if log is None:
        return
    try:
        log("HOOK_FAIL", f"event={event}", f"idx={idx}",
            f"status={status}",
            f"cmd={Path(cmd0).name}" if cmd0 else "",
            f"dur={dur:.2f}s")
    except Exception:  # noqa: BLE001 — logging never breaks a hook
        pass
