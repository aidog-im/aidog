"""mycore.mcp — minimal MCP client (car 2-D5): stdio + HTTP transports.

Borrows external tool ecosystems without a single third-party dependency:
JSON-RPC 2.0 over (a) a spawned stdio subprocess (newline-delimited messages,
a reader thread routes responses by id) or (b) a plain HTTP POST (Streamable
HTTP single-shot: JSON body or `text/event-stream` response, both parsed;
the `Mcp-Session-Id` response header is captured and replayed).

Lifecycle (fail-soft everywhere): build_registry() connects each configured
server, runs the `initialize` handshake, lists tools, applies the per-server
allow/deny name filters, and registers survivors as `mcp_<server>_<tool>`
handlers. A server that times out or errors is SKIPPED with an honest note on
registry.mcp_notes — the core tools never depend on any MCP server, and a
broken config never crashes startup (mirrors the modeltier fail-soft rule).

Config (empty = off, zero behavior change):
  env MYCORE_MCP (raw JSON) > settings web/settings.json mycore.mcp > off
  JSON array of server objects:
    {"name": "demo",                 # [A-Za-z0-9][A-Za-z0-9_.-]{0,31}
     "transport": "stdio" | "http",
     "command": "python3 -u s.py",   # stdio: shlex-split argv (<=512 chars)
     "cwd": "/optional/workdir",     # stdio only
     "url": "http://127.0.0.1:3000/mcp",   # http only
     "env": {"API_KEY": "$MY_SLOT"},       # stdio child env, $SLOT from
     "headers": {"Authorization": "$TOK"}, # http headers,   os.environ
     "tools": ["search_*"],          # allow patterns (fnmatch), default all
     "deny":  ["*delete*"]}          # deny patterns, applied after allow
  Secrets stay env SLOT NAMES only (car 1 modeltier discipline): a $SLOT that
  is not set fails that server with an honest note — never a literal secret
  in settings.

Trust model: an MCP tool runs with whatever rights its own server has —
the allowed-roots policy of our path tools does NOT apply to remote tools
(docs/features/mcp.md says so plainly). Allow/deny filters bound WHICH tools
the model can see; they are not a sandbox.

Stdlib only; English comments (repo i18n policy).
"""
from __future__ import annotations

import atexit
import fnmatch
import json
import os
import queue
import re
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parent.parent

MAX_SERVERS = 4                # parity with modeltier's bounded chain
MAX_CONFIG_CHARS = 4000
CONNECT_TIMEOUT = 10           # initialize handshake + tools/list budget
CALL_TIMEOUT = 30              # one tools/call
MAX_PAGES = 5                  # tools/list pagination bound
MAX_PATTERNS = 16
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_SLOT_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)$")
_ENVKEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOOL_PREFIX = "mcp_"

PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "imaidog-mcp-client"

# Every session ever opened, for best-effort atexit shutdown (child processes
# must not outlive us even on a crash path); explicit close() is the norm.
_SESSIONS: list["Session"] = []


class McpError(Exception):
    """Honest transport/protocol failure (rendered as an ERROR: tool line)."""


# ---------------------------------------------------------------- config ----

@dataclass
class ServerConfig:
    name: str
    transport: str                       # "stdio" | "http"
    command: tuple[str, ...] = ()
    cwd: str = ""
    url: str = ""
    env: dict = field(default_factory=dict)       # child env: KEY -> "$SLOT"/literal
    headers: dict = field(default_factory=dict)   # http: header -> "$SLOT"/literal
    allow: tuple[str, ...] = ()          # () = all tools
    deny: tuple[str, ...] = ()


def _patterns(raw, field_name: str, name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_PATTERNS:
        raise ValueError(f"mcp server '{name}': {field_name} must be an array "
                         f"of at most {MAX_PATTERNS} glob patterns")
    out = []
    for p in raw:
        if not isinstance(p, str) or not p or len(p) > 64 or "\x00" in p:
            raise ValueError(f"mcp server '{name}': {field_name} entries must "
                             "be non-empty strings of at most 64 chars")
        out.append(p)
    return tuple(out)


def _kv(raw, field_name: str, name: str) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or len(raw) > 8:
        raise ValueError(f"mcp server '{name}': {field_name} must be an object "
                         "of at most 8 entries")
    out = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k or len(k) > 64:
            raise ValueError(f"mcp server '{name}': {field_name} keys must be "
                             "short non-empty strings")
        if not isinstance(v, str) or len(v) > 256 or "\x00" in v or "\n" in v:
            raise ValueError(f"mcp server '{name}': {field_name}.{k} must be a "
                             "single-line string of at most 256 chars")
        out[k] = v
    return out


def parse_config(raw: str | None) -> list[ServerConfig]:
    """Parse the settings/env MCP config. Returns [] for empty/off; raises
    ValueError with the exact reason otherwise (web/server maps it to 400)."""
    if raw is None or not str(raw).strip():
        return []
    text = str(raw)
    if len(text) > MAX_CONFIG_CHARS:
        raise ValueError(f"mycore.mcp must be at most {MAX_CONFIG_CHARS} chars")
    if "\x00" in text:
        raise ValueError("mycore.mcp contains a NUL byte")
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise ValueError(f"mycore.mcp is not valid JSON: {e}") from None
    if not isinstance(doc, list):
        raise ValueError("mycore.mcp must be a JSON array of server objects")
    if len(doc) > MAX_SERVERS:
        raise ValueError(f"mycore.mcp: at most {MAX_SERVERS} servers")
    out: list[ServerConfig] = []
    seen: set[str] = set()
    for i, entry in enumerate(doc, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"mcp server #{i} must be an object")
        unknown = set(entry) - {"name", "transport", "command", "cwd", "url",
                                "env", "headers", "tools", "deny"}
        if unknown:
            raise ValueError(f"mcp server #{i}: unknown field(s): "
                             f"{', '.join(sorted(unknown))}")
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME_RE.match(name):
            raise ValueError(f"mcp server #{i}: name must match "
                             f"{_NAME_RE.pattern}")
        if name in seen:
            raise ValueError(f"mcp server '{name}': duplicate name")
        seen.add(name)
        transport = entry.get("transport")
        if transport not in ("stdio", "http"):
            raise ValueError(f"mcp server '{name}': transport must be "
                             "stdio or http")
        cfg = ServerConfig(name=name, transport=transport)
        if transport == "stdio":
            cmd = entry.get("command")
            if not isinstance(cmd, str) or not cmd.strip() or len(cmd) > 512:
                raise ValueError(f"mcp server '{name}': stdio needs a command "
                                 "string of at most 512 chars")
            try:
                argv = tuple(shlex.split(cmd))
            except ValueError as e:
                raise ValueError(f"mcp server '{name}': bad command quoting "
                                 f"({e})") from None
            if not argv:
                raise ValueError(f"mcp server '{name}': empty command")
            cfg.command = argv
            cwd = entry.get("cwd", "")
            if cwd is not None and not isinstance(cwd, str):
                raise ValueError(f"mcp server '{name}': cwd must be a string")
            cfg.cwd = cwd or ""
        else:
            url = entry.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")) \
                    or len(url) > 512 or any(c.isspace() for c in url):
                raise ValueError(f"mcp server '{name}': http needs a url "
                                 "starting with http:// or https:// "
                                 "(max 512 chars, no spaces)")
            cfg.url = url
        cfg.env = _kv(entry.get("env"), "env", name)
        cfg.headers = _kv(entry.get("headers"), "headers", name)
        if transport == "stdio" and entry.get("headers"):
            raise ValueError(f"mcp server '{name}': headers are http-only "
                             "(use env for stdio)")
        if transport == "http" and entry.get("env"):
            raise ValueError(f"mcp server '{name}': env is stdio-only "
                             "(use headers for http)")
        cfg.allow = _patterns(entry.get("tools"), "tools", name)
        cfg.deny = _patterns(entry.get("deny"), "deny", name)
        out.append(cfg)
    return out


def resolve_slot(value: str, server: str, field_name: str) -> str:
    """Resolve one `$SLOT` reference from the environment. A literal (no $
    prefix) passes through; a missing slot is an honest per-server failure —
    never a default, never a logged value."""
    m = _SLOT_RE.match(value)
    if not m:
        return value
    slot = m.group(1)
    got = os.environ.get(slot)
    if got is None or got == "":
        raise McpError(f"{field_name} slot '{slot}' is not set "
                       f"(mcp server '{server}')")
    return got


def raw_config(repo: str | Path | None = None) -> str:
    """Effective raw config: env MYCORE_MCP (when non-empty) > settings
    mycore.mcp > "" (off). Mirrors the modeltier precedence rule."""
    env = os.environ.get("MYCORE_MCP", "").strip()
    if env:
        return env
    try:
        stored = json.loads((Path(repo or REPO) / "web" / "settings.json")
                            .read_text(encoding="utf-8"))
        mc = stored.get("mycore") if isinstance(stored, dict) else None
        if isinstance(mc, dict) and isinstance(mc.get("mcp"), str):
            return mc["mcp"]
    except (OSError, ValueError):
        pass
    return ""


# -------------------------------------------------------------- sessions ----

class Session:
    """One connected server: JSON-RPC request/response + handshake. Concrete
    transports implement _send/_recv/_close; this base owns id routing,
    timeouts, and the initialized flag."""

    def __init__(self, cfg: ServerConfig) -> None:
        self.cfg = cfg
        self._id = 0
        self._lock = threading.Lock()     # serializes requests (id + write)
        self.tools: list[dict] = []

    # -- transport primitives (per subclass) --
    def _send(self, msg: dict) -> None: ...
    def _recv(self, timeout: float) -> dict: ...
    def _close(self) -> None: ...

    def request(self, method: str, params: dict | None = None,
                timeout: float = CONNECT_TIMEOUT) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            try:
                self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                            "params": params or {}})
                msg = self._recv(timeout)
            except McpError:
                raise
            except Exception as e:  # noqa: BLE001 — transport detail -> honest class
                raise McpError(f"{method}: {type(e).__name__}") from None
        if msg.get("id") != rid:
            raise McpError(f"{method}: response id mismatch "
                           f"({msg.get('id')!r} != {rid})")
        if "error" in msg:
            err = msg["error"] or {}
            code = err.get("code", "?") if isinstance(err, dict) else "?"
            text = err.get("message", "") if isinstance(err, dict) else str(err)
            text = str(text)[:300]
            raise McpError(f"{method}: server error {code}: {text}")
        if "result" not in msg:
            raise McpError(f"{method}: response has neither result nor error")
        return msg["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._id += 1
            try:
                self._send({"jsonrpc": "2.0", "method": method,
                            "params": params or {}})
            except McpError:
                raise
            except Exception as e:  # noqa: BLE001
                raise McpError(f"{method}: {type(e).__name__}") from None

    def handshake(self) -> None:
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": CLIENT_NAME, "version": _client_version()}})
        self.notify("notifications/initialized")

    def list_tools(self) -> list[dict]:
        out: list[dict] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            params = {"cursor": cursor} if cursor else {}
            res = self.request("tools/list", params)
            tools = res.get("tools") if isinstance(res, dict) else None
            if not isinstance(tools, list):
                raise McpError("tools/list: result.tools is missing")
            out.extend(t for t in tools if isinstance(t, dict))
            cursor = (res.get("nextCursor") if isinstance(res, dict) else None)
            if not cursor:
                break
        else:
            raise McpError(f"tools/list: more than {MAX_PAGES} pages "
                           "(pagination bound)")
        return out

    def call_tool(self, name: str, args: dict) -> str:
        res = self.request("tools/call",
                           {"name": name, "arguments": args},
                           timeout=CALL_TIMEOUT)
        if not isinstance(res, dict):
            raise McpError("tools/call: malformed result")
        content = res.get("content")
        parts: list[str] = []
        other = 0
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" \
                        and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                else:
                    other += 1
        text = "\n".join(parts)
        if other:
            text += (f"\n[{other} non-text content part(s) omitted]"
                     if text else f"[{other} non-text content part(s) omitted]")
        if res.get("isError"):
            raise McpError(f"tool '{name}' failed: {(text or 'no detail')[:500]}")
        return text or "(tool returned no text content)"

    def close(self) -> None:
        try:
            self._close()
        except Exception:  # noqa: BLE001 — close is best-effort by contract
            pass
        if self in _SESSIONS:
            _SESSIONS.remove(self)


class StdioSession(Session):
    """Newline-delimited JSON-RPC over a spawned child process. A daemon
    reader thread routes stdout lines to per-id queues (so concurrent calls
    from delegate threads stay matched); stderr is drained into a bounded
    ring so a chatty server can never fill the pipe and deadlock."""

    def __init__(self, cfg: ServerConfig) -> None:
        super().__init__(cfg)
        env = os.environ.copy()
        for k, v in cfg.env.items():
            if not _ENVKEY_RE.match(k):
                raise McpError(f"env key '{k}' is invalid "
                               f"(mcp server '{cfg.name}')")
            env[k] = resolve_slot(v, cfg.name, "env")
        try:
            self.proc = subprocess.Popen(
                cfg.command, cwd=cfg.cwd or str(REPO), env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as e:
            raise McpError(f"cannot spawn '{cfg.command[0]}' "
                           f"({type(e).__name__})") from None
        self._pending: dict[int, queue.Queue] = {}
        self._stderr_ring: deque[str] = deque(maxlen=40)
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _read_loop(self) -> None:
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue            # non-JSON chatter: ignore, never crash
                if not isinstance(msg, dict):
                    continue
                rid = msg.get("id")
                with self._lock:
                    q = self._pending.get(rid) if rid is not None else None
                if q is not None:
                    q.put(msg)
        finally:
            with self._lock:
                self._alive = False
                for q in self._pending.values():
                    q.put(None)         # wake every waiter on EOF

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr_ring.append(line.rstrip()[:200])

    def _send(self, msg: dict) -> None:
        if not self._alive:
            raise McpError(self._dead_reason())
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise McpError(f"write failed ({type(e).__name__})") from None

    def _recv(self, timeout: float) -> dict:
        # Id is registered by request() BEFORE _send under the same lock, so
        # the reader thread can always find this queue.
        q: queue.Queue = self._pending[self._id]
        try:
            msg = q.get(timeout=timeout)
        except queue.Empty:
            raise McpError(f"timed out after {timeout:.0f}s") from None
        if msg is None:
            raise McpError(self._dead_reason())
        return msg

    def request(self, method, params=None, timeout=CONNECT_TIMEOUT) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            q: queue.Queue = queue.Queue(maxsize=1)
            self._pending[rid] = q
        try:
            with self._lock:
                if not self._alive:
                    raise McpError(self._dead_reason())
                try:
                    self.proc.stdin.write(
                        json.dumps({"jsonrpc": "2.0", "id": rid,
                                    "method": method, "params": params or {}})
                        + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError) as e:
                    raise McpError(f"write failed ({type(e).__name__})") from None
            try:
                msg = q.get(timeout=timeout)
            except queue.Empty:
                raise McpError(f"{method}: timed out after {timeout:.0f}s") \
                    from None
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        if msg is None:
            raise McpError(f"{method}: {self._dead_reason()}")
        if msg.get("id") != rid:
            raise McpError(f"{method}: response id mismatch")
        if "error" in msg:
            err = msg["error"] or {}
            code = err.get("code", "?") if isinstance(err, dict) else "?"
            text = str(err.get("message", ""))[:300] if isinstance(err, dict) \
                else str(err)[:300]
            raise McpError(f"{method}: server error {code}: {text}")
        if "result" not in msg:
            raise McpError(f"{method}: response has neither result nor error")
        return msg["result"]

    def _dead_reason(self) -> str:
        rc = self.proc.poll()
        tail = "".join(list(self._stderr_ring)[-3:]).strip()
        reason = f"server exited (rc={rc})" if rc is not None else "server closed"
        return f"{reason}{': ' + tail[:200] if tail else ''}"

    def _close(self) -> None:
        self._alive = False
        try:
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


class HttpSession(Session):
    """Streamable HTTP: one POST per JSON-RPC message, response read as
    either application/json or a single-shot text/event-stream. A test can
    inject `post` (url, headers, body, timeout) -> (status, headers, body)."""

    def __init__(self, cfg: ServerConfig,
                 post: Callable | None = None) -> None:
        super().__init__(cfg)
        self.headers = {"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"}
        for k, v in cfg.headers.items():
            self.headers[k] = resolve_slot(v, cfg.name, "headers")
        self._session_id = ""
        self._post = post or _http_post

    def _send(self, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        hdrs = dict(self.headers)
        if self._session_id:
            hdrs["Mcp-Session-Id"] = self._session_id
        try:
            status, rhdrs, raw = self._post(self.cfg.url, hdrs, body,
                                            CONNECT_TIMEOUT)
        except Exception as e:  # noqa: BLE001 — url/ssl/timeout -> honest class
            raise McpError(f"http post failed ({type(e).__name__})") from None
        if status == 202:               # notification accepted, no body
            self._pending_raw = None
            return
        if status >= 400:
            raise McpError(f"http {status} from server")
        sid = rhdrs.get("mcp-session-id") or rhdrs.get("Mcp-Session-Id")
        if sid:
            self._session_id = sid
        self._pending_raw = (rhdrs.get("content-type", ""), raw)

    def _recv(self, timeout: float) -> dict:
        got = getattr(self, "_pending_raw", None)
        if got is None:
            raise McpError("http: no response body")
        ctype, raw = got
        self._pending_raw = None
        return _parse_http_payload(ctype, raw)

    def _close(self) -> None:
        pass                            # stateless POSTs leave nothing open


def _parse_http_payload(ctype: str, raw: bytes) -> dict:
    text = raw.decode("utf-8", errors="replace")
    if "event-stream" in ctype:
        # SSE: take the last complete `data:` JSON object that is a message.
        payload = ""
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
        if not payload:
            raise McpError("http: empty event-stream response")
        try:
            msg = json.loads(payload)
        except ValueError:
            raise McpError("http: event-stream data is not JSON") from None
    else:
        try:
            msg = json.loads(text)
        except ValueError:
            raise McpError("http: response body is not JSON") from None
    if not isinstance(msg, dict):
        raise McpError("http: response is not a JSON-RPC object")
    return msg


def _http_post(url: str, headers: dict, body: bytes,
               timeout: float) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return (resp.status,
                    {k.lower(): v for k, v in resp.headers.items()},
                    resp.read(4_000_000))
    except urllib.error.HTTPError as e:
        return (e.code, {k.lower(): v for k, v in e.headers.items()},
                e.read(4_000_000) or b"")


def _client_version() -> str:
    try:
        return (REPO / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


def connect(cfg: ServerConfig,
            post: Callable | None = None) -> Session:
    """Spawn/open + handshake. Raises McpError with the honest reason; the
    caller (attach) skips this server and records the note."""
    session: Session = HttpSession(cfg, post=post) if cfg.transport == "http" \
        else StdioSession(cfg)
    try:
        session.handshake()
        session.tools = session.list_tools()
    except Exception:
        session.close()
        raise
    _SESSIONS.append(session)
    return session


def close_all() -> None:
    """Best-effort shutdown of every live session (atexit + tests)."""
    for s in list(_SESSIONS):
        s.close()


atexit.register(close_all)


# ------------------------------------------------------------- attachment ---

def _tool_allowed(name: str, cfg: ServerConfig) -> bool:
    if cfg.allow and not any(fnmatch.fnmatchcase(name, p) for p in cfg.allow):
        return False
    if any(fnmatch.fnmatchcase(name, p) for p in cfg.deny):
        return False
    return True


def _registered_name(server: str, tool: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", tool)
    return f"{TOOL_PREFIX}{server}_{safe}"[:64]


def attach(registry, raw: str, post: Callable | None = None) -> list[str]:
    """Connect every configured server and register its filtered tools on
    `registry`. Returns the honest notes (also left on registry.mcp_notes):
    one line per server — success counts or the failure reason. Never raises
    for server/config problems (config ValueError is re-raised: the CALLER
    decides — web/server turns it into 400, build_registry turns it into a
    note)."""
    cfgs = parse_config(raw)
    notes: list[str] = []
    for cfg in cfgs:
        try:
            session = connect(cfg, post=post)
        except McpError as e:
            notes.append(f"mcp '{cfg.name}': SKIPPED — {e}")
            continue
        added = 0
        skipped = 0
        for tool in session.tools:
            tname = tool.get("name")
            if not isinstance(tname, str) or not tname:
                skipped += 1
                continue
            if not _tool_allowed(tname, cfg):
                skipped += 1
                continue
            reg_name = _registered_name(cfg.name, tname)
            if reg_name in registry.names():
                skipped += 1
                continue
            schema = tool.get("inputSchema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                schema = {"type": "object"}
            desc = (f"[mcp:{cfg.name}] "
                    + (str(tool.get("description") or tname)[:400]))
            registry.register(reg_name, desc, schema,
                              _make_handler(session, cfg.name, tname))
            added += 1
        notes.append(f"mcp '{cfg.name}': connected, {added} tool(s)"
                     + (f", {skipped} filtered/skipped" if skipped else ""))
    registry.mcp_notes = notes
    registry.mcp_sessions = [s for s in _SESSIONS]
    return notes


def _make_handler(session: Session, server: str, tool: str):
    def handler(**kwargs) -> str:
        from .tools import ToolError      # local: no import cycle at load time
        try:
            return session.call_tool(tool, kwargs)
        except McpError as e:
            raise ToolError(f"mcp {server}/{tool}: {e}") from None
    return handler
