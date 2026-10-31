"""mycore.ckxbackup — agent-driven encrypted backup (CKX-P2, car 8).

One deterministic path turns this console's own export into an encrypted
`.ckx` artifact WITHOUT the model ever holding a passphrase or a plaintext
byte:

  1. export  — GET /api/backup/export over 127.0.0.1 (Bearer WEB_TOKEN),
               `ProxyHandler({})` so a host proxy can never intercept the
               loopback call; the response bytes are the payload, exactly
               what the browser card encrypts (same envelope, same restore).
  2. encrypt — vendored `deploy/ckx/ckx.mjs` reading the payload from STDIN
               and writing the container to `<state>/backups/`. The
               passphrase enters the child ONLY as an environment variable
               (`--password-env`), never argv, never a log line.
  3. verify  — `ckx.mjs verify --in <file>` (password-free structural check).
  4. roundtrip — `ckx.mjs decrypt --in <file> --out -` returns the plaintext
               on STDOUT; we compare its sha256 against the bytes we fed in
               and drop both. The plaintext NEVER touches the filesystem and
               NEVER enters a return value.

Faithfulness rules (design §8, extended):
  * the passphrase is read here (env > profile secrets.env slot) and handed
    to ONE child environment; it is never returned, never logged, never in
    argv;
  * `run_backup()` returns METADATA ONLY — file name, size, sha256, booleans,
    elapsed ms and the quota counter;
  * every failure is a short honest `CkxError` reason word with no path, no
    passphrase and no payload in it (dispatch renders it as `ERROR: <line>`).

Quota (design §2.4): the free plan is 1000 calls and `GET /v1/ckx/usage`
answers 405 unauthenticated, so the upstream count cannot be read from here.
We keep OUR OWN conservative meter — one line per initiated encrypt, warn at
800, refuse at 1000, resettable by the operator from the console. The
upstream billing record stays authoritative (deploy/ckx/VENDOR.md).

Stdlib only; English source per repo i18n policy.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (profile path seam)

CKX_CLI = REPO / "deploy" / "ckx" / "ckx.mjs"
CKX_MCP = REPO / "deploy" / "ckx" / "ckx-mcp.mjs"
PASSWORD_SLOT = "AIDOG_CKX_PASSWORD"
MCP_SERVER_NAME = "ckx"

NODE_MIN_MAJOR = 20            # upstream agent tooling is Node >= 20 ESM
PROBE_TIMEOUT = 5              # `node --version`
RUN_TIMEOUT = 120              # one encrypt / verify / decrypt of a full export
EXPORT_TIMEOUT = 20            # loopback GET /api/backup/export

QUOTA_CAP = 1000               # upstream free plan (calls)
QUOTA_WARN = 800               # warn before the cap, never at it
QUOTA_FILE = "ckx_calls.jsonl" # one JSON line per INITIATED encrypt
QUOTA_MAX_BYTES = 4 * 1024 * 1024   # fail-closed bound on the meter file
BACKUP_DIR = "backups"         # <state>/backups, mode 0700
FILE_MODE = 0o600
DIR_MODE = 0o700

# The MCP allow-list is DENY-by-default: verify/meta/capabilities/estimate/
# encrypt can only ever return structure or ciphertext. `ckx_decrypt*`,
# the recipient-keypair family and `ckx_generate*` are named in `deny` as
# belt-and-braces — a decryption or a private key must never be able to
# reach the model context (design §8; recovery stays on the P1 browser card).
MCP_ALLOW = ("ckx_verify_integrity", "ckx_read_meta", "ckx_capabilities",
             "ckx_estimate", "ckx_encrypt")
MCP_DENY = ("ckx_decrypt*", "ckx_generate*", "*recipient*", "ckx_encrypt_to")

_STAMP_RE = re.compile(r"^[A-Za-z0-9TZ-]{8,40}$")


class CkxError(Exception):
    """Honest, short failure reason — no path, no passphrase, no payload."""


# --------------------------------------------------------------- test seams --

_Runner = Callable[..., "subprocess.CompletedProcess"]
_Exporter = Callable[[], bytes]

_RUNNER: _Runner | None = None
_EXPORTER: _Exporter | None = None


def set_runner_for_tests(fn: _Runner | None) -> None:
    """Replace the subprocess runner (None restores the real one)."""
    global _RUNNER
    _RUNNER = fn


def set_export_for_tests(fn: _Exporter | None) -> None:
    """Replace the loopback export fetch (None restores the real one)."""
    global _EXPORTER
    _EXPORTER = fn


def _run(argv: list[str], *, env: dict | None = None,
         data: bytes | None = None, timeout: int = RUN_TIMEOUT):
    """One child process. stdout/stderr are captured, never printed and never
    folded into an error string — upstream messages are not ours to echo."""
    if _RUNNER is not None:
        return _RUNNER(argv, env=env, data=data, timeout=timeout)
    return subprocess.run(argv, env=env, input=data, capture_output=True,
                          timeout=timeout)


# ------------------------------------------------------------ capability ----

def node_status() -> dict:
    """Probe the host Node runtime -> {ok, major, version, reason}.

    `reason` is "" when ok. Any probe failure is an honest `ok: false` with a
    short reason — the caller (tool / console) degrades to the P1 manual
    card instead of pretending a capability exists."""
    exe = shutil.which("node")
    if not exe:
        return {"ok": False, "major": 0, "version": "",
                "reason": "node not found on PATH"}
    try:
        proc = _run([exe, "--version"], timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "major": 0, "version": "",
                "reason": "node could not be executed"}
    out = (proc.stdout or b"").decode("utf-8", "replace").strip()
    m = re.search(r"v(\d+)", out)
    major = int(m.group(1)) if m else 0
    if major < NODE_MIN_MAJOR:
        return {"ok": False, "major": major, "version": out,
                "reason": f"node >= {NODE_MIN_MAJOR} required "
                          f"(found {out or 'an unreadable version'})"}
    return {"ok": True, "major": major, "version": out, "reason": ""}


# ------------------------------------------------------------ credentials ----

def password_value() -> str:
    """The passphrase: process env first, then the profile secrets.env slot.

    The value stays inside this frame — callers hand it to a child
    environment and nothing else. Empty string = not configured (the caller
    refuses honestly)."""
    got = os.environ.get(PASSWORD_SLOT, "").strip()
    if got:
        return got
    from . import envslots   # local import: keeps the module graph acyclic
    return envslots.slot_value(PASSWORD_SLOT).strip()


def _web_token() -> str:
    """Bearer token for the loopback export: env first, then secrets.env."""
    got = os.environ.get("WEB_TOKEN", "").strip()
    if got:
        return got
    from . import envslots
    return envslots.slot_value("WEB_TOKEN").strip()


# ------------------------------------------------------------------ quota ----

def _state_root(state_dir: str | Path | None = None) -> Path:
    """State root: the build_registry state dir when given (tests), else the
    profile/repo state dir resolved at CALL time (AIDOG_MC_STATE aware)."""
    return Path(state_dir) if state_dir is not None else pp.state_dir(REPO)


def _quota_path(state_dir: str | Path | None = None) -> Path:
    return _state_root(state_dir) / QUOTA_FILE


def quota_status(state_dir: str | Path | None = None) -> dict:
    """Local call meter -> {used, cap, warn_at, state}.

    state: `ok` < warn_at <= `warn` < cap <= `over`. Fail-closed: an
    unreadable or oversized meter file counts as `over` — we never let a
    broken counter look like a free lane."""
    cap = QUOTA_CAP
    path = _quota_path(state_dir)
    try:
        size = path.stat().st_size
        if size > QUOTA_MAX_BYTES:
            return {"used": cap + 1, "cap": cap, "warn_at": QUOTA_WARN,
                    "state": "over"}
        used = 0
        remaining = size
        with path.open("rb") as fh:
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                used += chunk.count(b"\n")
                remaining -= len(chunk)
    except FileNotFoundError:
        used = 0
    except OSError:
        return {"used": cap + 1, "cap": cap, "warn_at": QUOTA_WARN,
                "state": "over"}
    state = "over" if used >= cap else ("warn" if used >= QUOTA_WARN else "ok")
    return {"used": used, "cap": cap, "warn_at": QUOTA_WARN, "state": state}


def quota_note(state_dir: str | Path | None = None) -> None:
    """Count ONE initiated encrypt. Called only after every pre-flight check
    passed and immediately before the child is spawned — a pre-flight failure
    never burns a counted call, a launched encrypt always does (conservative:
    an upstream that bills a failed attempt is still covered)."""
    path = _quota_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": int(time.time()), "kind": "backup"},
                                separators=(",", ":")) + "\n")
    except OSError as e:
        raise CkxError(f"call meter is not writable ({type(e).__name__})") from None


def quota_reset(state_dir: str | Path | None = None) -> dict:
    """Operator action: zero the local meter. The upstream billing record is
    authoritative — reset only after checking the plan yourself."""
    path = _quota_path(state_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    except OSError as e:
        raise CkxError(f"call meter is not writable ({type(e).__name__})") from None
    return quota_status(state_dir)


# ----------------------------------------------------------------- export ----

def _loopback_export() -> bytes:
    """GET /api/backup/export over 127.0.0.1 -> raw response bytes.

    The proxy handler is EXPLICITLY empty: this host sets HTTP_PROXY, and a
    loopback call must never leave the machine. Only the HTTP status enters
    an error reason — never a body, never the token."""
    token = _web_token()
    if not token:
        raise CkxError("WEB_TOKEN slot is not set (console export gate)")
    try:
        port = int(os.environ.get("AIDOG_WEB_PORT", "8790") or 8790)
    except ValueError:
        raise CkxError("AIDOG_WEB_PORT is not a number") from None
    url = f"http://127.0.0.1:{port}/api/backup/export"
    req = urllib.request.Request(
        url, headers={"Authorization": "Bearer " + token,
                      "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=EXPORT_TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        raise CkxError(f"console export refused (http {e.code})") from None
    except (urllib.error.URLError, OSError):
        raise CkxError("console is not answering on the loopback export") from None
    if not body:
        raise CkxError("console export returned an empty document")
    return body


def export_bytes() -> bytes:
    """The payload to encrypt: the seam when tests install one, else the real
    loopback fetch. Always bytes — no re-serialization, so the round-trip
    digest is meaningful."""
    if _EXPORTER is not None:
        return _EXPORTER()
    return _loopback_export()


# -------------------------------------------------------------- encryption ---

def _node_exe() -> str:
    return shutil.which("node") or "node"


def _label(stamp: str) -> str:
    return f"aidog-{stamp}" if _STAMP_RE.match(stamp) else "aidog-backup"


def _next_out(backups: Path, stamp: str) -> Path:
    out = backups / f"aidog-{stamp}.ckx"
    n = 2
    while out.exists():
        out = backups / f"aidog-{stamp}-{n}.ckx"
        n += 1
    return out


def _rel(path: Path) -> str:
    """Repo-relative POSIX path, else the bare name — the model gets a useful
    handle, never a machine layout (the console's own serialization rule)."""
    try:
        return path.resolve().relative_to(REPO.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def run_backup(state_dir: str | Path | None = None) -> dict:
    """Run ONE full backup and return metadata only.

    Raises CkxError with a short reason on any failure; the partial
    container (if one was created) is removed so a failed run never leaves a
    file that looks like a good backup."""
    t0 = time.monotonic()
    node = node_status()
    if not node["ok"]:
        raise CkxError(node["reason"] or "node runtime unavailable")
    pw = password_value()
    if not pw:
        raise CkxError(f"{PASSWORD_SLOT} slot is not set "
                       f"(set it in Settings > Credentials)")
    quota = quota_status(state_dir)
    if quota["state"] == "over":
        raise CkxError(f"ckx free-call quota reached ({quota['cap']}) — "
                       f"reset the call meter in the console after checking "
                       f"your plan")
    payload = export_bytes()            # pre-flight: a failure costs no call

    root = _state_root(state_dir)
    backups = root / BACKUP_DIR
    try:
        backups.mkdir(parents=True, exist_ok=True)
        os.chmod(backups, DIR_MODE)
    except OSError as e:
        raise CkxError(f"backup directory is not writable "
                       f"({type(e).__name__})") from None

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = _next_out(backups, stamp)
    child_env = dict(os.environ)
    child_env[PASSWORD_SLOT] = pw       # the ONLY place the passphrase goes

    quota_note(state_dir)               # counted from here on
    argv = [_node_exe(), str(CKX_CLI), "encrypt", "--in", "-", "--out",
            str(out), "--password-env", PASSWORD_SLOT, "--label",
            _label(stamp), "--medium", "file"]
    try:
        proc = _run(argv, env=child_env, data=payload, timeout=RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        raise CkxError("ckx encrypt could not be started") from None
    if proc.returncode != 0 or not out.is_file():
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        raise CkxError(f"ckx encrypt failed (exit {proc.returncode})")
    try:
        os.chmod(out, FILE_MODE)
    except OSError:
        pass

    try:
        # --- 3. password-free structural self-check
        try:
            proc = _run([_node_exe(), str(CKX_CLI), "verify", "--in", str(out)],
                        timeout=RUN_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            raise CkxError("ckx verify could not be started") from None
        if proc.returncode != 0:
            raise CkxError("container self-check failed")
        try:
            doc = json.loads((proc.stdout or b"").decode("utf-8", "replace"))
        except ValueError:
            doc = {}
        if not isinstance(doc, dict) or doc.get("ok") is not True:
            raise CkxError("container self-check failed")

        # --- 4. round-trip in memory: plaintext on STDOUT, compared, dropped
        try:
            proc = _run([_node_exe(), str(CKX_CLI), "decrypt", "--in",
                         str(out), "--out", "-", "--password-env",
                         PASSWORD_SLOT],
                        env=child_env, timeout=RUN_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            raise CkxError("ckx decrypt could not be started") from None
        if proc.returncode != 0:
            raise CkxError("round-trip decrypt failed")
        back = proc.stdout or b""
        got = hashlib.sha256(back).hexdigest()
        del back, proc
        if got != hashlib.sha256(payload).hexdigest():
            raise CkxError("round-trip digest mismatch — backup not accepted")
    except Exception:
        try:
            out.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    size = out.stat().st_size
    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    return {
        "ok": True,
        "file": _rel(out),
        "bytes": size,
        "sha256": sha,
        "verified": True,
        "roundtrip": True,
        "ms": int((time.monotonic() - t0) * 1000),
        "quota": quota_status(state_dir),
    }


# ------------------------------------------------------------ MCP registration --

def ckx_server(repo: Path | str | None = None) -> dict:
    """The `ckx` MCP server entry (a parse_config-legal object).

    Built from this checkout's vendored path; `command[0]` resolves node at
    SPAWN time through PATH, so a node upgrade never invalidates settings.
    The env value is the SLOT NAME `$AIDOG_CKX_PASSWORD` — the child gets the
    value through mcp.resolve_slot, the settings file only ever holds a name.
    """
    root = Path(repo) if repo is not None else REPO
    exe = shutil.which("node") or "node"
    return {
        "name": MCP_SERVER_NAME,
        "transport": "stdio",
        "command": f"{shlex.quote(exe)} "
                   f"{shlex.quote(str(root / 'deploy' / 'ckx' / 'ckx-mcp.mjs'))}",
        "env": {PASSWORD_SLOT: "$" + PASSWORD_SLOT},
        "tools": list(MCP_ALLOW),
        "deny": list(MCP_DENY),
    }


def mcp_entry(repo: Path | str | None = None) -> str:
    """A single-server `mycore.mcp` config string holding only the ckx entry
    (mcp.parse_config round-trippable). `mcp_on` merges `ckx_server()` into
    whatever the operator already configured — it never replaces it."""
    return json.dumps([ckx_server(repo)], separators=(",", ":"))
