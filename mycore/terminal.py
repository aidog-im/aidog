"""mycore.terminal — configured terminal backend for run_bash (car 4b-B).

One settings key (`mycore.shell`, one line in web/settings.json) selects where
`run_bash` executes its command:

    "" or "local"                       local shell in the repo root (default)
    "docker:image=<name>"               docker run --rm <image> sh -c <cmd>
    "docker:container=<name>"           docker exec <name> sh -c <cmd>
    "ssh:<user>@<host>[:port]"          ssh BatchMode -> sh -c <cmd>
    "ssh:<user>@<host>[:port];identity=<abs path>"

Contracts (car 4b-B):
  * parse() is the ONE grammar — web/server.py's settings validator and
    tools._run_bash both delegate here, so they can never drift. Errors are
    English, describe the EXPECTED shape only, and never echo user input
    (injection-echo hygiene).
  * run() reproduces the historical _run_bash output contract byte for byte:
    `exit=<code>\n<stdout>[\n--- stderr ---\n<stderr>]` (trailing whitespace
    rstripped), timeout -> error. A non-zero exit / refused connection /
    missing container is a RESULT (exit=N line), never an exception; only
    backend-level problems (missing binary, timeout) raise TerminalError,
    which tools._run_bash maps to ToolError so the tool layer keeps one
    honest error contract.
  * No per-call output truncation here — the tool cap (MAX_RESULT_CHARS)
    lives in tools.finalize_result and applies to every backend uniformly,
    exactly as it always did for run_bash.
  * stdlib only; source English (repo i18n policy).
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MAX_SPEC_CHARS = 256                 # one settings line (server validator cap)

_DOCKER_RE = re.compile(r"docker:(image|container)=([A-Za-z0-9._:/-]{1,120})\Z")
_SSH_RE = re.compile(r"([A-Za-z0-9_.-]{1,32})@([A-Za-z0-9.-]{1,253})"
                     r"(?::([0-9]{1,10}))?\Z")
_EXPECT = ('expected "", "local", "docker:image=<name>", '
           '"docker:container=<name>", or "ssh:<user>@<host>[:port]" '
           'with optional ";identity=<abs path>"')


class TerminalError(RuntimeError):
    """Backend-level failure (missing binary, timeout, bad call). tools.py
    catches it and re-raises as ToolError — one honest tool error shape."""


@dataclass(frozen=True)
class Backend:
    """A parsed mycore.shell value. kind: local | docker | ssh;
    mode: docker only (image | container); name: docker target."""
    kind: str
    mode: str = ""
    name: str = ""
    user: str = ""
    host: str = ""
    port: int = 22
    identity: str = ""


def parse(spec) -> Backend:
    """Parse one mycore.shell settings line into a Backend.

    Raises ValueError (English, shape-only — the message never contains the
    input itself) for anything outside the documented grammar. "" / "local"
    select the default local backend: zero behavior change.
    """
    if not isinstance(spec, str):
        raise ValueError(f"shell spec must be a string ({_EXPECT})")
    if len(spec) > MAX_SPEC_CHARS:
        raise ValueError(f"shell spec must be at most {MAX_SPEC_CHARS} chars")
    s = spec.strip()
    if s in ("", "local"):
        return Backend(kind="local")
    if s.startswith("docker:"):
        m = _DOCKER_RE.fullmatch(s)
        if not m:
            raise ValueError('docker spec must be "docker:image=<name>" or '
                             '"docker:container=<name>" (name: [A-Za-z0-9._:/-], '
                             '1..120 chars, exactly one key)')
        return Backend(kind="docker", mode=m.group(1), name=m.group(2))
    if s.startswith("ssh:"):
        return _parse_ssh(s[4:])
    raise ValueError(f"invalid shell spec: {_EXPECT}")


def _parse_ssh(body: str) -> Backend:
    """Parse the part after 'ssh:': user@host[:port][;identity=<abs path>]."""
    parts = body.split(";")
    if len(parts) > 2:
        raise ValueError('ssh spec allows only one ";identity=<abs path>" '
                         'suffix (passwords or other fields are never accepted)')
    identity = ""
    if len(parts) == 2:
        if not parts[1].startswith("identity="):
            raise ValueError('ssh spec suffix must be ";identity=<abs path>" '
                             '(passwords or other fields are never accepted)')
        identity = parts[1][len("identity="):]
        if (not identity.startswith("/") or len(identity) > 256
                or any(c in identity for c in "\x00\n\r")):
            raise ValueError("ssh identity must be an absolute path of at most "
                             "256 chars (key file only, never a passphrase)")
    m = _SSH_RE.fullmatch(parts[0])
    if not m:
        raise ValueError('ssh spec must be "ssh:<user>@<host>[:port]" '
                         "(user: [A-Za-z0-9_.-] 1..32 chars and required, "
                         "host: [A-Za-z0-9.-] 1..253 chars, port: 1..65535)")
    port = 22
    if m.group(3):
        port = int(m.group(3))
        if not 1 <= port <= 65535:
            raise ValueError("ssh port must be 1..65535")
    return Backend(kind="ssh", user=m.group(1), host=m.group(2),
                   port=port, identity=identity)


def run(backend: Backend, command: str, timeout: int) -> str:
    """Execute `command` on `backend` for up to `timeout` seconds.

    Returns the exact historical run_bash contract:
    `exit=<code>\n<stdout>` with an optional `\n--- stderr ---\n<stderr>`
    block, trailing whitespace rstripped. Non-zero exits are results, not
    errors; TerminalError covers only missing binaries, timeouts, and bad
    arguments.
    """
    if not isinstance(command, str) or not command.strip():
        raise TerminalError("command must be a non-empty string")
    if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
        raise TerminalError("timeout must be a positive integer")
    argv, use_shell = _invocation(backend, command)
    try:
        proc = subprocess.run(argv, shell=use_shell, cwd=str(REPO),
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TerminalError(f"command timed out after {timeout}s") from None
    except FileNotFoundError:
        raise TerminalError(f"{argv[0]} not found in PATH (install it or set "
                            "mycore.shell to local)") from None
    out = (proc.stdout or "") + (("\n--- stderr ---\n" + proc.stderr) if proc.stderr else "")
    header = f"exit={proc.returncode}"
    return f"{header}\n{out}".rstrip() or f"{header} (no output)"


def _invocation(backend: Backend, command: str):
    """-> (argv_or_command, use_shell). use_shell=True is the historical local
    `subprocess.run(..., shell=True)` — byte-identical old behavior; use_shell
   =False runs the argv list directly with no local shell in between.

    ssh joins its post-destination argv with plain spaces before the remote
    shell re-parses it, so the `sh -c` payload is passed shlex-quoted: one
    argv element survives the join and arrives as a single -c script argument.
    """
    if backend.kind == "local":
        # Historical run_bash: local /bin/sh, cwd = repo root. Unchanged.
        return command, True
    if backend.kind == "docker":
        if shutil.which("docker") is None:
            raise TerminalError("docker not found in PATH (install docker or "
                                "set mycore.shell to local)")
        # No cwd migration: containers keep their own workdir (spec: container
        # root). No -e proxy passthrough either — minimal surface.
        if backend.mode == "image":
            return (["docker", "run", "--rm", backend.name,
                     "sh", "-c", command], False)
        return (["docker", "exec", backend.name, "sh", "-c", command], False)
    if backend.kind == "ssh":
        if shutil.which("ssh") is None:
            raise TerminalError("ssh not found in PATH (install openssh-client "
                                "or set mycore.shell to local)")
        # BatchMode=yes: never interactively prompt (missing key = honest fast
        # failure). accept-new: pin new host keys, still fail on change.
        argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "BatchMode=yes"]
        if backend.identity:
            argv += ["-i", backend.identity]
        argv += ["-p", str(backend.port), f"{backend.user}@{backend.host}",
                 "--", "sh", "-c", shlex.quote(command)]
        return argv, False
    raise TerminalError(f"unknown terminal backend kind: {backend.kind!r}")
