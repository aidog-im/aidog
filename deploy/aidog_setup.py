#!/usr/bin/env python3
"""aidog_setup — interactive install/configuration wizard for I'm AiDog.

  aidog setup               # interactive: guide every credential slot, then
                            # (optionally) run doctor/boot at the end
  aidog setup --check       # non-interactive: report slot status, exit 0/2/1
  aidog setup --yes         # non-interactive: run deploy/install.sh (idempotent,
                            # the pre-wizard behavior — provision + boot + verify)
  aidog setup --yes --no-boot  # provision + verify only, never boot

Contract (B3, owner-approved):
  - guides each slot of secrets.env in order — WEB_TOKEN, MYCORE_TG_TOKEN,
    AIDOG_TG_ALLOWED_USERS, MODEL_API_KEY, AIDOG_GH_PAT, MYCORE_WD_CHAT —
    plus the model endpoint (web/settings.json mycore.base_url / model);
  - already-filled slots are REPORTED (name + length) and never overwritten;
  - values are read with hidden input when stdin is a TTY (echo suppressed)
    and are NEVER printed — output carries slot names and lengths only;
  - every write is read back and syntax-checked (own-line KEY=value rule for
    secrets.env, JSON re-parse for settings.json) before the wizard reports ok;
  - invalid input is retried up to 3 times with the reason (never echoing the
    value), then the slot is skipped (left empty) — pending stays fail-closed;
  - finally offers to run deploy/install.sh (doctor + boot + verify) unless
    --no-boot; exit codes follow install.sh: 0 ready / 2 pending / 1 error.

Input sourcing: interactive prompts read stdin (getpass on a TTY, plain line
read otherwise so scripted/CI runs are testable). EOF ends prompting gracefully
(summary still printed). Secrets are never written to argv or logs.
English output per repo i18n policy.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))  # `from mycore import localmodels` (zero-key)
SECRETS = REPO / "secrets.env"
EXAMPLE = REPO / "secrets.env.example"
SETTINGS = REPO / "web" / "settings.json"
SETTINGS_EXAMPLE = REPO / "web" / "settings.example.json"
INSTALL_SH = REPO / "deploy" / "install.sh"

MAX_TRIES = 3

# name, prompt, validator, secret(hidden input), help(where to get it)
SLOTS: list[tuple[str, str, "re.Pattern[str]", bool, str]] = [
    ("WEB_TOKEN",
     "Console write-API token",
     re.compile(r"^\S{16,}$"), True,
     "any long random string you invent (>=16 chars, no spaces)"),
    ("MYCORE_TG_TOKEN",
     "Telegram bot token (@BotFather)",
     re.compile(r"^\d{4,}:[A-Za-z0-9_-]{20,}$"), True,
     "format 123456789:AA... from @BotFather"),
    ("AIDOG_TG_ALLOWED_USERS",
     "Allowed Telegram user ids",
     re.compile(r"^\d+(,\d+)*$"), False,
     "your numeric user id, comma-separated for several (fail-closed while empty)"),
    ("MODEL_API_KEY",
     "Model endpoint API key",
     re.compile(r"^\S{8,}$"), True,
     "leave EMPTY if your endpoint needs no key (local gateway default)"),
    ("AIDOG_GH_PAT",
     "GitHub token for 'aidog update'",
     re.compile(r"^\S{20,}$"), True,
     "PAT with read access to the product repo (optional, >=20 chars)"),
    ("MYCORE_WD_CHAT",
     "Watchdog/cron alert chat id",
     re.compile(r"^-?\d{5,20}$"), False,
     "Telegram chat id for alerts (optional; empty = log-only)"),
]

URL_RE = re.compile(r"^https?://\S+$")
MODEL_RE = re.compile(r"^\S+$")


def say(msg: str) -> None:
    print(f"[setup] {msg}")


# ---------------------------------------------------------------- secrets.env

def env_line_value(text: str, key: str) -> str | None:
    """Own-line rule: KEY=VALUE is a value only when it is the line's whole body."""
    m = re.search(rf"(?m)^{re.escape(key)}=(\S+)$", text)
    return m.group(1) if m else None


def read_secrets() -> str:
    try:
        return SECRETS.read_text(encoding="utf-8")
    except OSError:
        return ""


def provision_secrets() -> bool:
    """Create secrets.env from the template when missing. Never overwrites."""
    if SECRETS.exists():
        return False
    if not EXAMPLE.is_file():
        say(f"FAIL: {EXAMPLE.name} template missing — cannot provision")
        sys.exit(1)
    shutil.copyfile(EXAMPLE, SECRETS)
    try:
        os.chmod(SECRETS, 0o600)
    except OSError:  # 9p/CIFS mounts reject chmod (harmless, doctor knows)
        pass
    say("created secrets.env from template (mode 0600 best-effort)")
    return True


def format_violations(text: str) -> list[int]:
    """Line numbers (1-based) that are neither blank, comment, nor own-line KEY=value."""
    bad: list[int] = []
    for i, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$", line):
            bad.append(i)
    return bad


def write_slot(key: str, value: str) -> tuple[bool, str]:
    """Replace or append one KEY=value line, keeping every other byte intact.
    Returns (ok, readback_detail). Values are never included in the detail."""
    text = read_secrets()
    if re.search(rf"(?m)^{re.escape(key)}=\S*$", text):
        # replacement via lambda: user values may contain backslashes
        new = re.sub(rf"(?m)^{re.escape(key)}=\S*$",
                     lambda _m: f"{key}={value}", text)
    else:
        new = text.rstrip("\n") + "\n" if text.strip() else ""
        new += f"{key}={value}\n"
    try:
        tmp = SECRETS.with_name(SECRETS.name + ".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, SECRETS)
        try:
            os.chmod(SECRETS, 0o600)
        except OSError:
            pass
    except OSError as e:
        return False, f"write failed ({type(e).__name__})"
    back = env_line_value(read_secrets(), key)
    if back != value:
        return False, "read-back mismatch (value not echoed)"
    return True, f"written {key} (len {len(value)}) — read back ok"


# ------------------------------------------------------------------ settings

def read_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_settings(data: dict) -> tuple[bool, str]:
    try:
        tmp = SETTINGS.with_name(SETTINGS.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        os.replace(tmp, SETTINGS)
        json.loads(SETTINGS.read_text(encoding="utf-8"))  # syntax re-check
    except (OSError, ValueError) as e:
        return False, f"settings write failed ({type(e).__name__})"
    return True, "web/settings.json written — JSON re-parsed ok"


# ------------------------------------------------------------------ prompting

def read_value(prompt: str, hidden: bool) -> str | None:
    """One answer. None = EOF/abort. Hidden input on a TTY; plain line read
    otherwise (scripted runs)."""
    try:
        if hidden and sys.stdin.isatty():
            return getpass.getpass(prompt + ": ").strip()
        print(f"{prompt}: ", end="", flush=True)
        line = sys.stdin.readline()
        if line == "":
            print()
            return None
        return line.strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def guide_slot(text: str, key: str, label: str, pat: "re.Pattern[str]",
               hidden: bool, help_txt: str) -> tuple[str, str | None]:
    """Prompt one slot. Returns (action, value):
    action = 'kept' | 'skipped' | 'written' | 'failed' | 'aborted'; value only
    for 'written'."""
    current = env_line_value(text, key)
    if current is not None:
        say(f"{key}: set (len {len(current)}) — kept, never overwritten")
        return "kept", None
    say(f"{key}: EMPTY — {label} ({help_txt})")
    for attempt in range(1, MAX_TRIES + 1):
        raw = read_value(f"  {key} [{label}]", hidden)
        if raw is None:
            return "aborted", None
        if raw == "":
            if key in ("MODEL_API_KEY", "AIDOG_GH_PAT", "MYCORE_WD_CHAT"):
                say(f"{key}: left empty (optional — pending is fine)")
                return "skipped", None
            say(f"{key}: required — a value is needed (attempt {attempt}/{MAX_TRIES})")
            continue
        if not pat.match(raw):
            say(f"{key}: invalid format — expected: {help_txt} "
                f"(attempt {attempt}/{MAX_TRIES})")
            continue
        ok, detail = write_slot(key, raw)
        if ok:
            say(detail)
            return "written", raw
        say(f"{key}: {detail}")
        return "failed", None
    say(f"{key}: skipped after {MAX_TRIES} attempts (stays empty — fail-closed)")
    return "skipped", None


# --------------------------------------------------------------- status/check

def slot_report() -> tuple[int, list[str]]:
    """Print slot status (names + lengths only). Returns (pending_required, notes)."""
    text = read_secrets()
    if not text:
        say("secrets.env: ABSENT (run 'aidog setup' to provision)")
        return 2, ["secrets.env absent"]
    pending: list[str] = []
    for key, label, pat, secret, help_txt in SLOTS:
        v = env_line_value(text, key)
        required = key in ("WEB_TOKEN", "MYCORE_TG_TOKEN", "AIDOG_TG_ALLOWED_USERS")
        if v is None:
            mark = "EMPTY (required)" if required else "empty (optional)"
            if required:
                pending.append(key)
        elif not pat.match(v):
            mark = f"set (len {len(v)}) — INVALID format, fix by hand"
            if required:
                # a required slot that violates its format is NOT ready —
                # same pending semantics as empty (fail-closed), never 0.
                pending.append(key)
        else:
            mark = f"set (len {len(v)})"
        say(f"{key:<26} {mark}  [{label}]")
    bad = format_violations(text)
    if bad:
        say(f"secrets.env format: {len(bad)} line(s) violate own-line KEY=value: "
            f"line(s) {', '.join(map(str, bad))} — fix by hand (values not shown)")
        return 1, ["format"]  # broken file is an error, not merely pending
    say("secrets.env format: ok (own-line KEY=value)")
    if SETTINGS.exists():
        try:
            json.loads(SETTINGS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            say("web/settings.json: INVALID JSON — fix by hand (error)")
            return 1, ["settings.json invalid"]
    mc = read_settings().get("mycore") or {}
    base = mc.get("base_url", "")
    model = mc.get("model", "")
    if not URL_RE.match(str(base)):
        say(f"settings mycore.base_url: MISSING/invalid — set any http(s):// OpenAI-compatible endpoint")
        pending.append("mycore.base_url")
    else:
        say(f"settings mycore.base_url: {base} (model: {model or 'unset'})")
    if pending:
        say(f"pending: {', '.join(pending)}")
        return 2, pending
    say("all required slots filled")
    return 0, []


# ----------------------------------------------------------------------- main

def cmd_check() -> int:
    rc, _ = slot_report()
    return rc


def detect_local_model() -> dict | None:
    """Zero-key onboarding step (car 4b): probe local model servers, offer the
    hits, and return the chosen entry {kind, base_url, model} — or None to fall
    through to the manual base_url/model prompts. Interactive wizard only
    (--check/--yes never reach it); never raises. The miss line is printed only
    when the probe module was actually importable (honesty: never claim probes
    that did not run)."""
    try:
        from mycore import localmodels  # noqa: PLC0415 — REPO is on sys.path
    except Exception:
        return None
    try:
        found = localmodels.probe()
    except Exception:
        found = []
    if not isinstance(found, list) or not found:
        override = os.environ.get("AIDOG_LOCAL_MODEL_PROBES") or ""
        tried = (", ".join(u.strip() for u in override.split(",") if u.strip())
                 if override else
                 "ollama :11434, llama-server :8080, lm-studio :1234")
        say(f"no local model server detected (tried {tried})")
        return None
    say("local model server detected:")
    for i, line in enumerate(localmodels.describe(found), 1):
        say(f"  {i}. {line}")
    raw = read_value(
        f"use which? [enter = 1, 2..{len(found)}, n = manual setup]", False)
    if raw is None:
        return None
    pick = raw.strip() or "1"
    if pick.isdigit() and 1 <= int(pick) <= len(found):
        entry = found[int(pick) - 1]
        if isinstance(entry, dict) and entry.get("base_url") and entry.get("model"):
            return entry
    return None  # 'n' / invalid answer -> manual setup flow


def cmd_wizard(boot: bool) -> int:
    provision_secrets()
    say("I'm AiDog setup wizard — values are hidden while typing and never shown back")
    say("(existing values are never overwritten; edit secrets.env by hand to rotate)")
    text = read_secrets()
    aborted = False
    for key, label, pat, secret, help_txt in SLOTS:
        if aborted:
            cur = env_line_value(text, key)
            if cur is None:
                say(f"{key}: not asked (input ended) — stays empty")
            continue
        action, _val = guide_slot(text, key, label, pat, secret, help_txt)
        text = read_secrets()  # re-read: later slots see earlier writes
        if action == "aborted":
            aborted = True

    # model endpoint (settings.json — non-secret, shown)
    if not SETTINGS.exists() and SETTINGS_EXAMPLE.is_file():
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SETTINGS_EXAMPLE, SETTINGS)
        say("created web/settings.json from template")
    # zero-key onboarding (interactive only): local model server detection,
    # inserted between settings.json creation and the manual base_url prompt
    local_choice = detect_local_model()
    if local_choice is not None:
        data = read_settings()
        prev = data.get("mycore")
        prev = prev if isinstance(prev, dict) else {}
        data["mycore"] = {**prev, "base_url": str(local_choice["base_url"]),
                          "model": str(local_choice["model"])}
        ok, detail = write_settings(data)
        say(f"using local model server: {local_choice['base_url']} "
            f"(model: {local_choice['model']}) — no API key needed")
        say(detail)
        if not ok:
            return 1
    else:
        mc = read_settings().get("mycore")
        if not isinstance(mc, dict):
            mc = {}
        cur_base = str(mc.get("base_url") or "http://127.0.0.1:3460/v1")
        cur_model = str(mc.get("model") or "mimo-v2.6-flash")
        new_base, new_model = cur_base, cur_model
        for attempt in range(1, MAX_TRIES + 1):
            raw = read_value(f"model endpoint base_url [{cur_base}]", False)
            if raw is None:
                break
            new_base = raw or cur_base
            if URL_RE.match(new_base):
                break
            say(f"base_url: invalid — expected http(s)://... (attempt {attempt}/{MAX_TRIES})")
            new_base = cur_base
        for attempt in range(1, MAX_TRIES + 1):
            raw = read_value(f"model name [{cur_model}]", False)
            if raw is None:
                break
            new_model = raw or cur_model
            if MODEL_RE.match(new_model):
                break
            say(f"model: invalid — expected a non-empty token (attempt {attempt}/{MAX_TRIES})")
            new_model = cur_model
        if (new_base, new_model) != (cur_base, cur_model):
            data = read_settings()
            data["mycore"] = {**mc, "base_url": new_base, "model": new_model}
            ok, detail = write_settings(data)
            say(detail)
            if not ok:
                return 1

    # summary (names + lengths only) + format check
    say("— summary —")
    rc, _ = slot_report()

    if rc != 0:
        say("pending items remain — the chat service stays fail-closed until filled")
    if boot:
        answer = read_value("run doctor + boot + verify now (deploy/install.sh)? [y/N]",
                            False)
        if answer is not None and answer.lower() in ("y", "yes"):
            say("running deploy/install.sh …")
            return subprocess.call(["bash", str(INSTALL_SH)], cwd=str(REPO))
        say("skipped boot — run 'aidog setup' again (or bash deploy/install.sh) when ready")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="aidog setup",
        description="Interactive setup/config wizard (fill credential slots, "
                    "validate, optionally boot). --check reports status only; "
                    "--yes runs the idempotent installer without prompts.")
    ap.add_argument("--check", action="store_true",
                    help="report slot status only (exit 0 ready / 2 pending / 1 error)")
    ap.add_argument("--yes", action="store_true",
                    help="non-interactive: run deploy/install.sh (provision + verify)")
    ap.add_argument("--no-boot", action="store_true",
                    help="with --yes: provision + verify only, never boot")
    args = ap.parse_args(argv)

    if args.check:
        return cmd_check()
    if args.yes:
        if args.no_boot:
            if not INSTALL_SH.is_file():
                say("FAIL: deploy/install.sh missing")
                return 1
            # provision (templates only when missing) + slot report, no boot
            provision_secrets()
            if SETTINGS_EXAMPLE.is_file() and not SETTINGS.exists():
                SETTINGS.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(SETTINGS_EXAMPLE, SETTINGS)
                say("created web/settings.json from template")
            rc, _ = slot_report()
            say("boot skipped (--no-boot) — re-run without --no-boot to boot")
            return rc
        return subprocess.call(["bash", str(INSTALL_SH)], cwd=str(REPO))
    return cmd_wizard(boot=not args.no_boot)


if __name__ == "__main__":
    sys.exit(main())
