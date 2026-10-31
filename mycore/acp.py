"""mycore.acp — Agent Client Protocol (ACP) v1 server over stdio.

    python3 -m mycore.acp     # spawn by an ACP client; NDJSON JSON-RPC 2.0

Protocol facts come from the official ACP v1 JSON schema (schema/v1 in the
agent-client-protocol repository; fetched and grepped before implementation —
method names, field names, error codes and enums below are schema-exact):

  * framing  : newline-delimited JSON, one JSON-RPC 2.0 message per line;
               stdout carries ONLY protocol frames, logs go to stderr
  * methods  : initialize (request) -> session/new -> session/prompt;
               session/cancel (client notification); session/update
               (agent->client notification: agent_message_chunk, tool_call,
               tool_call_update)
  * fs proxy : agent->client requests fs/read_text_file / fs/write_text_file
               (the schema puts fs capabilities on the CLIENT side — the
               agent proxies file tools only when the client advertised
               fs.readTextFile / fs.writeTextFile in initialize)
  * version  : protocolVersion must be 1; anything else is an honest
               JSON-RPC error (-32602), no downgrade dance

Concurrency model: the read loop owns stdin; `session/prompt` runs on a
worker thread so responses to OUR outbound requests (fs/*), client
notifications (session/cancel) and further client requests are all processed
WHILE a turn is in flight. Outbound requests register a pending-id future;
the read loop routes client responses to them (response-vs-request is
distinguished by the presence of `method`, so id spaces never collide).

Declared capabilities are exactly what is implemented (no terminal — we
never send terminal/*; no session/load — loadSession=false; prompts are
text + resource_link baseline only). Config comes from the same precedence
as mycore.cli (env MYCORE_* > web/settings.json mycore block > defaults).
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

from . import checkpoint, cli
from .assembly import build_system_stack, resolve_context_dirs
from .loop import run_turn
from .model import ChatClient
from .tools import ToolError, build_registry, resolve_inside

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (car F: per-profile settings)

PROTOCOL_VERSION = 1
FS_TIMEOUT_DEFAULT = 10.0     # seconds to wait for a client fs/* response
JOIN_TIMEOUT = 8.0            # bounded join of prompt workers at EOF
MAX_PROMPT_TEXT = 200_000     # hard cap on extracted prompt text

# JSON-RPC / ACP error codes (schema ErrorCode enum)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
RESOURCE_NOT_FOUND = -32002

# ToolKind mapping for session/update tool_call frames (schema ToolKind).
TOOL_KINDS = {"read_file": "read", "write_file": "edit", "edit_file": "edit",
              "grep": "search", "glob": "search", "skill_route": "search",
              "run_bash": "execute", "execute_code": "execute",
              "delegate": "execute", "http_fetch": "fetch", "cron": "other"}

USAGE = ("usage: python3 -m mycore.acp\n"
         "  ACP v1 stdio server (NDJSON JSON-RPC on stdin/stdout).\n"
         "  Model config: env MYCORE_* > web/settings.json mycore > defaults.")


class _PromptCancelled(Exception):
    """Raised from on_event boundaries when session/cancel arrived."""


class _Waiter:
    """Pending outbound request slot (reader thread resolves, worker waits)."""
    __slots__ = ("event", "value", "error")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.value: object = None
        self.error: str | None = None


class AcpSession:
    """In-memory session state (no cross-restart persistence: loadSession
    is declared false, sessions live only for this process)."""

    def __init__(self, sid: str, cwd: str, roots: tuple[str, ...]) -> None:
        self.sid = sid
        self.cwd = cwd
        self.roots = roots
        self.messages: list[dict] = []       # non-system history
        self.cancel = threading.Event()
        self.busy = False
        self.registry = None                 # built at session/new
        self.msg_seq = 0                     # messageId rotation (per model call)
        self.tool_seq = 0                    # toolCallId counter
        self.tool_q: list[str] = []          # in-flight toolCallIds (FIFO)
        self.stream_mid = "m1"               # current chunk messageId
        self.stream_buf: list[str] = []      # deltas of the current message
        self.last_result = ""                # last dispatch output (previews)


def _budget() -> int | None:
    """Compaction budget: web/settings.json mycore.budget (service._budget
    parity); absent -> cli default, <=0 -> never compact."""
    try:
        doc = json.loads(pp.settings_path(REPO).read_text(
            encoding="utf-8"))
        mc = doc.get("mycore") if isinstance(doc, dict) else None
        raw = mc.get("budget") if isinstance(mc, dict) else None
    except (OSError, ValueError):
        raw = None
    if isinstance(raw, bool):
        raw = None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        return int(raw) if int(raw) > 0 else None
    return cli.DEFAULT_BUDGET_CHARS


def _prompt_text(blocks: object) -> str:
    """Extract user text from a ContentBlock list (baseline: Text and
    ResourceLink; anything else was not declared in promptCapabilities and
    is skipped honestly)."""
    if not isinstance(blocks, list):
        return ""
    parts: list[str] = []
    for blk in blocks:
        if not isinstance(blk, dict):
            continue
        kind = blk.get("type")
        if kind == "text" and isinstance(blk.get("text"), str):
            parts.append(blk["text"])
        elif kind == "resource_link":
            uri = blk.get("uri")
            if isinstance(uri, str) and uri:
                parts.append(f"[resource: {uri}]")
    return "\n".join(parts)[:MAX_PROMPT_TEXT]


def _cap_args(args: object) -> object:
    """rawInput for tool_call frames: shallow cap so a 30k write payload
    does not bloat the update stream (client owns the real data anyway)."""
    if not isinstance(args, dict):
        return args
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 2000:
            out[k] = v[:2000] + "…"
        else:
            out[k] = v
    return out


class AcpServer:
    """One stdio connection. `lines` yields input lines (stdin), `write`
    receives one outgoing frame dict per call (serialized NDJSON), `chat`
    overrides the model callable (offline tests — same seam as ChatService),
    `fs_timeout` bounds client fs/* waits, `log` receives stderr messages."""

    def __init__(self, lines: Iterable[str], write: Callable[[dict], None],
                 *, chat: Callable[..., dict] | None = None,
                 fs_timeout: float = FS_TIMEOUT_DEFAULT,
                 log: Callable[[str], None] | None = None) -> None:
        self.lines = lines
        self._write = write
        self._chat = chat
        self.fs_timeout = fs_timeout
        self._log_fn = log
        self._wlock = threading.Lock()
        self._closed = False
        self._initialized = False
        self.fs_read = False                  # client advertised fs.readTextFile
        self.fs_write = False                 # client advertised fs.writeTextFile
        self._pending: dict[int, _Waiter] = {}
        self._pending_lock = threading.Lock()
        self._rid = 0                         # OUR outbound request ids
        self._sid = 0                         # session id counter
        self.sessions: dict[str, AcpSession] = {}
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------- output --
    def _log(self, msg: str) -> None:
        if self._log_fn is not None:
            try:
                self._log_fn(msg)
            except Exception:  # noqa: BLE001 — logging never breaks the loop
                pass

    def _send(self, frame: dict) -> None:
        if self._closed:
            return
        with self._wlock:
            try:
                self._write(frame)
            except (BrokenPipeError, OSError, ValueError):
                # Client gone mid-write: keep serving reads until EOF.
                self._closed = True
                self._log("write failed (client gone) — output closed")

    def _send_result(self, rid: object, result: dict) -> None:
        self._send({"jsonrpc": "2.0", "id": rid, "result": result})

    def _send_err(self, rid: object, code: int, message: str) -> None:
        self._send({"jsonrpc": "2.0", "id": rid,
                    "error": {"code": code, "message": message}})

    def _update(self, sid: str, update: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": "session/update",
                    "params": {"sessionId": sid, "update": update}})

    def _next_rid(self) -> int:
        self._rid += 1
        return self._rid

    # -------------------------------------------------------------- loop ---
    def serve(self) -> int:
        """Read loop until EOF; returns 0 (clean) — never raises."""
        try:
            for raw in self.lines:
                line = raw.strip() if isinstance(raw, str) else ""
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    self._send_err(None, PARSE_ERROR,
                                   "parse error (invalid JSON line)")
                    continue
                if not isinstance(msg, dict):
                    self._send_err(None, INVALID_REQUEST,
                                   "invalid request (not an object)")
                    continue
                try:
                    self._route(msg)
                except Exception as e:  # noqa: BLE001 — one bad frame, one line
                    self._log(f"dispatch error: {type(e).__name__}")
                    if isinstance(msg.get("id"), (str, int)) and \
                            isinstance(msg.get("method"), str):
                        self._send_err(msg["id"], INTERNAL_ERROR,
                                       f"internal error: {type(e).__name__}")
        except KeyboardInterrupt:
            self._log("interrupted — shutting down")
        finally:
            self._shutdown()
        return 0

    def _route(self, msg: dict) -> None:
        method = msg.get("method")
        if isinstance(method, str):
            if "id" in msg:                       # client request
                self._handle_request(method, msg.get("id"), msg.get("params"))
            else:                                 # client notification
                self._handle_notify(method, msg.get("params"))
            return
        if "id" in msg and ("result" in msg or "error" in msg):
            self._route_response(msg)
            return
        self._send_err(msg.get("id") if isinstance(msg.get("id"), (str, int))
                       else None, INVALID_REQUEST, "invalid request")

    def _route_response(self, msg: dict) -> None:
        rid = msg.get("id")
        with self._pending_lock:
            waiter = self._pending.pop(rid, None)
        if waiter is None:
            self._log(f"response for unknown id {rid!r} ignored")
            return
        err = msg.get("error")
        if isinstance(err, dict):
            waiter.error = str(err.get("message", "client error"))[:200]
        else:
            waiter.value = msg.get("result")
        waiter.event.set()

    def _handle_notify(self, method: str, params: object) -> None:
        if method == "session/cancel":
            sid = params.get("sessionId") if isinstance(params, dict) else None
            session = self.sessions.get(sid) if isinstance(sid, str) else None
            if session is not None:
                session.cancel.set()
                self._log(f"session/cancel sid={sid}")
            else:
                self._log(f"session/cancel for unknown sid {sid!r} ignored")
            return
        self._log(f"notification {method} ignored")

    def _handle_request(self, method: str, rid: object,
                        params: object) -> None:
        if method == "initialize":
            self._m_initialize(rid, params)
        elif method == "session/new":
            self._m_new_session(rid, params)
        elif method == "session/prompt":
            self._m_prompt(rid, params)
        else:
            self._send_err(rid, METHOD_NOT_FOUND,
                           f"method not found: {method}")

    # ---------------------------------------------------------- messages ---
    def _m_initialize(self, rid: object, params: object) -> None:
        if not isinstance(params, dict):
            self._send_err(rid, INVALID_PARAMS, "initialize params must be an object")
            return
        pv = params.get("protocolVersion")
        if isinstance(pv, bool) or not isinstance(pv, int) or pv != PROTOCOL_VERSION:
            self._send_err(rid, INVALID_PARAMS,
                           f"unsupported protocolVersion: {pv!r} "
                           f"(this agent implements protocol version "
                           f"{PROTOCOL_VERSION})")
            return
        caps = params.get("clientCapabilities")
        fs = caps.get("fs") if isinstance(caps, dict) and isinstance(caps.get("fs"), dict) else {}
        self.fs_read = bool(fs.get("readTextFile"))
        self.fs_write = bool(fs.get("writeTextFile"))
        self._initialized = True
        info = params.get("clientInfo")
        if isinstance(info, dict):
            self._log(f"initialize client={str(info.get('name', '?'))[:40]} "
                      f"v{str(info.get('version', '?'))[:20]} "
                      f"fs_read={int(self.fs_read)} fs_write={int(self.fs_write)}")
        try:
            version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
        except OSError:
            version = "0.0.0"
        self._send_result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {"image": False, "audio": False,
                                       "embeddedContext": False},
                "mcpCapabilities": {"http": False, "sse": False},
                "sessionCapabilities": {},
                "auth": {},
            },
            "authMethods": [],
            "agentInfo": {"name": "aidog", "version": version},
        })

    def _m_new_session(self, rid: object, params: object) -> None:
        if not isinstance(params, dict):
            self._send_err(rid, INVALID_PARAMS, "session/new params must be an object")
            return
        cwd = params.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            self._send_err(rid, INVALID_PARAMS, "session/new requires cwd")
            return
        if not os.path.isabs(cwd):
            self._send_err(rid, INVALID_PARAMS,
                           "session/new cwd must be an absolute path")
            return
        extra = params.get("additionalDirectories")
        if extra is None:
            extra = []
        if not isinstance(extra, list) or any(
                not (isinstance(d, str) and os.path.isabs(d)) for d in extra):
            self._send_err(rid, INVALID_PARAMS,
                           "session/new additionalDirectories must be "
                           "absolute paths")
            return
        # Client-provided mcpServers are accepted and ignored in v1 (our MCP
        # comes from our own settings) — documented in docs/features/acp.md.
        self._sid += 1
        sid = f"s{self._sid}"
        roots = tuple(dict.fromkeys([cwd, *extra]))
        session = AcpSession(sid, cwd, roots)
        try:
            session.registry = self._build_registry(session)
        except Exception as e:  # noqa: BLE001 — build_registry is fail-soft
            self._send_err(rid, INTERNAL_ERROR,
                           f"registry build failed: {type(e).__name__}")
            return
        self.sessions[sid] = session
        self._log(f"session/new sid={sid} roots={len(roots)}")
        self._send_result(rid, {"sessionId": sid})

    def _m_prompt(self, rid: object, params: object) -> None:
        if not isinstance(params, dict):
            self._send_err(rid, INVALID_PARAMS, "session/prompt params must be an object")
            return
        sid = params.get("sessionId")
        session = self.sessions.get(sid) if isinstance(sid, str) else None
        if session is None:
            self._send_err(rid, RESOURCE_NOT_FOUND,
                           f"unknown session: {sid!r}")
            return
        blocks = params.get("prompt")
        if not isinstance(blocks, list):
            self._send_err(rid, INVALID_PARAMS,
                           "session/prompt prompt must be an array "
                           "of content blocks")
            return
        text = _prompt_text(blocks)
        if not text.strip():
            self._send_err(rid, INVALID_PARAMS,
                           "prompt contains no supported text content")
            return
        if session.busy:
            self._send_err(rid, INVALID_PARAMS,
                           "another prompt is already running for this session")
            return
        session.busy = True
        session.cancel.clear()
        th = threading.Thread(target=self._prompt_worker,
                              args=(session, rid, text),
                              daemon=True, name=f"acp-prompt-{sid}")
        self._threads.append(th)
        th.start()

    # ---------------------------------------------------------- prompting --
    def _prompt_worker(self, session: AcpSession, rid: object,
                       text: str) -> None:
        t0 = time.monotonic()
        stop = "internal_error"
        try:
            stack = build_system_stack(context_dirs=resolve_context_dirs())
            history = [m for m in session.messages if m.get("role") != "system"]
            messages = stack + history
            messages.append({"role": "user", "content": text})
            session.msg_seq = 1
            session.stream_mid = "m1"
            session.stream_buf = []
            session.tool_q = []
            chat_fn = self._chat or ChatClient(cli.resolve_model_config()).chat
            try:
                res = run_turn(messages, chat_fn, session.registry,
                               on_event=self._make_on_event(session),
                               budget_chars=_budget(),
                               on_delta=self._make_on_delta(session))
            finally:
                # Full non-system list (stack + history + this turn), keeping
                # compacted/folded forms — identity-based rebuild is unsafe
                # after compaction rewrites list slots (service lesson).
                try:
                    session.messages = [m for m in messages
                                        if m.get("role") != "system"]
                except Exception:  # noqa: BLE001 — history keeps last good state
                    pass
            if session.cancel.is_set():
                stop = "cancelled"
            elif res.stop == "final":
                # Authoritative text: flush only when the live stream did not
                # already deliver exactly this (fake/non-streaming chats).
                if res.text and res.text != "".join(session.stream_buf):
                    session.msg_seq += 1
                    self._chunk(session, res.text, f"m{session.msg_seq}")
                stop = "end_turn"
            elif res.stop == "max_iters":
                # Honest stop reason per schema; no internal note is echoed.
                stop = "max_turn_requests"
            else:  # model_error: schema has no error stop reason -> JSON-RPC
                # error (message vetted, no endpoint/key detail).
                self._log(f"prompt model_error sid={session.sid} "
                          f"iters={res.iterations}")
                self._send_err(rid, INTERNAL_ERROR,
                               "model call failed (see agent logs)")
                return
            self._send_result(rid, {"stopReason": stop})
            self._log(f"prompt done sid={session.sid} stop={stop} "
                      f"iters={res.iterations} tools={res.tool_calls} "
                      f"dur={time.monotonic() - t0:.1f}s")
        except _PromptCancelled:
            self._send_result(rid, {"stopReason": "cancelled"})
            self._log(f"prompt cancelled sid={session.sid} "
                      f"dur={time.monotonic() - t0:.1f}s")
        except Exception as e:  # noqa: BLE001 — honest error, no traceback
            self._log(f"prompt internal error sid={session.sid}: "
                      f"{type(e).__name__}")
            self._send_err(rid, INTERNAL_ERROR,
                           f"internal error: {type(e).__name__}")
        finally:
            session.busy = False

    def _make_on_event(self, session: AcpSession):
        def on_event(kind: str, data: dict) -> None:
            if session.cancel.is_set():
                raise _PromptCancelled()
            if kind == "model_call":
                # New assistant message: rotate messageId, reset stream buffer.
                session.msg_seq += 1
                session.stream_mid = f"m{session.msg_seq}"
                session.stream_buf = []
            elif kind == "tool_call":
                session.tool_seq += 1
                tcid = f"t{session.tool_seq}"
                session.tool_q.append(tcid)
                name = str(data.get("name", ""))
                self._update(session.sid, {
                    "sessionUpdate": "tool_call",
                    "toolCallId": tcid,
                    "title": name,
                    "name": name or None,
                    "kind": TOOL_KINDS.get(name, "other"),
                    "status": "in_progress",
                    "rawInput": _cap_args(data.get("args")),
                })
            elif kind == "tool_result":
                ok = bool(data.get("ok"))
                tcid = session.tool_q.pop(0) if session.tool_q else None
                if tcid:
                    preview = session.last_result
                    if len(preview) > 160:
                        preview = preview[:160] + "…"
                    self._update(session.sid, {
                        "sessionUpdate": "tool_call_update",
                        "toolCallId": tcid,
                        "status": "completed" if ok else "failed",
                        "content": [{"type": "content",
                                     "content": {"type": "text",
                                                 "text": preview}}],
                        "rawOutput": preview,
                    })
        return on_event

    def _make_on_delta(self, session: AcpSession):
        def on_delta(text: str) -> None:
            if session.cancel.is_set() or not text:
                return                       # never raise inside chat()
            session.stream_buf.append(text)
            self._chunk(session, text, session.stream_mid)
        return on_delta

    def _chunk(self, session: AcpSession, text: str, mid: str) -> None:
        self._update(session.sid, {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": text},
            "messageId": mid,
        })

    # ------------------------------------------------------- fs proxy ------
    def _build_registry(self, session: AcpSession):
        """Session registry: allowed roots = cwd + additionalDirectories.
        When the client advertised BOTH fs caps, the file trio's handlers are
        re-routed through the client proxy (schema direction: agent ->
        client). Handlers are replaced IN PLACE on the fresh per-session
        registry (build_registry makes new Tool objects per call, so this
        never leaks across sessions) — in-place matters because execute_code
        and delegate capture this same registry object at build time, so
        steps dispatched through them hit the proxy too. Dispatch is wrapped
        once so every result lands in session.last_result (tool preview)."""
        reg = build_registry(allowed_roots=session.roots)
        if self.fs_read and self.fs_write:
            proxies = {
                "read_file": lambda path, offset=0, limit=200:
                    self._proxy_read(session, path, offset, limit),
                "write_file": lambda path, content:
                    self._proxy_write(session, path, content),
                "edit_file": lambda path, old_text, new_text:
                    self._proxy_edit(session, path, old_text, new_text),
            }
            for name, handler in proxies.items():
                reg._tools[name].handler = handler   # fresh per-session Tool
        base_dispatch = reg.dispatch

        def dispatch(name: str, arguments: dict) -> str:
            out = base_dispatch(name, arguments)
            session.last_result = out            # preview for tool_call_update
            return out

        reg.dispatch = dispatch                  # instance shadow (same object)
        return reg

    def _resolve_path(self, session: AcpSession, path: object) -> Path:
        """Absolute path inside session roots (contract-4 style policy even
        for proxied ops — defense in depth on top of the client's own FS
        policy). Raises ToolError -> honest ERROR string for the model."""
        if not isinstance(path, str) or not path.strip():
            raise ToolError("path must be a non-empty string")
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = Path(session.cwd) / p
        return resolve_inside(str(p), session.roots)

    def _request(self, method: str, params: dict, session: AcpSession) -> dict:
        """Send an agent->client request, wait for the reader thread to route
        the response (bounded by fs_timeout; cancel/EOF abort honestly)."""
        if self._closed:
            raise ToolError(f"{method}: connection closed")
        waiter = _Waiter()
        rid = self._next_rid()
        with self._pending_lock:
            self._pending[rid] = waiter
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params})
        deadline = time.monotonic() + self.fs_timeout
        while not waiter.event.is_set():
            if session.cancel.is_set():
                with self._pending_lock:
                    self._pending.pop(rid, None)
                raise ToolError(f"{method}: session cancelled while waiting "
                                f"for the client")
            if self._closed or time.monotonic() >= deadline:
                with self._pending_lock:
                    self._pending.pop(rid, None)
                raise ToolError(f"{method}: no response from the client "
                                f"within {self.fs_timeout:g}s")
            waiter.event.wait(0.1)
        if waiter.error is not None:
            raise ToolError(f"{method}: client refused: {waiter.error}")
        result = waiter.value
        return result if isinstance(result, dict) else {}

    def _proxy_read(self, session: AcpSession, path: object,
                    offset: int = 0, limit: int = 200) -> str:
        p = self._resolve_path(session, path)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ToolError("offset must be a non-negative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) \
                or not 1 <= limit <= 2000:
            raise ToolError("limit must be an integer in 1..2000")
        res = self._request("fs/read_text_file",
                            {"sessionId": session.sid, "path": str(p),
                             "line": offset + 1, "limit": limit},
                            session)
        content = res.get("content")
        if not isinstance(content, str):
            raise ToolError("fs/read_text_file returned no content")
        lines = content.splitlines()
        if not lines:
            return f"[empty: {p.name}]"
        return "\n".join(f"{i + 1 + offset:>6}\t{ln}"
                         for i, ln in enumerate(lines))

    def _snapshot_note(self, p: Path) -> str:
        """Pre-write checkpoint on the LOCAL view of the path: preserved for
        the common stdio co-located deployment (client FS = agent FS). A
        path not visible locally (remote client) has nothing to snapshot —
        documented in docs/features/acp.md."""
        if not p.is_file():
            return ""
        try:
            entry = checkpoint.snapshot(p, None)
        except Exception:  # noqa: BLE001 — safety net, never blocks a write
            entry = None
        if entry is not None:
            return f" · checkpoint #{entry['id']}"
        return " · previous file not checkpointed (too large or unreadable)"

    def _proxy_write(self, session: AcpSession, path: object,
                     content: object) -> str:
        p = self._resolve_path(session, path)
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        note = self._snapshot_note(p)
        self._request("fs/write_text_file",
                      {"sessionId": session.sid, "path": str(p),
                       "content": content},
                      session)
        return f"wrote {len(content)} chars to {p}{note}"

    def _proxy_edit(self, session: AcpSession, path: object,
                    old_text: object, new_text: object) -> str:
        p = self._resolve_path(session, path)
        if not isinstance(old_text, str) or not old_text:
            raise ToolError("old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise ToolError("new_text must be a string")
        res = self._request("fs/read_text_file",
                            {"sessionId": session.sid, "path": str(p)},
                            session)
        text = res.get("content")
        if not isinstance(text, str):
            raise ToolError("fs/read_text_file returned no content")
        n = text.count(old_text)
        if n == 0:
            raise ToolError("old_text not found in file")
        if n > 1:
            raise ToolError(f"old_text matches {n} times; must be unique "
                            f"(add context)")
        note = self._snapshot_note(p)
        self._request("fs/write_text_file",
                      {"sessionId": session.sid, "path": str(p),
                       "content": text.replace(old_text, new_text, 1)},
                      session)
        return f"edited {p} (1 replacement){note}"

    # ---------------------------------------------------------- teardown ---
    def _shutdown(self) -> None:
        """EOF (or interrupt): fail pending fs waiters, cancel sessions,
        join prompt workers bounded — then the process exits cleanly."""
        self._closed = True
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending.clear()
        for w in waiters:
            w.error = "connection closed (EOF)"
            w.event.set()
        for s in self.sessions.values():
            s.cancel.set()
        alive = [t for t in self._threads if t.is_alive()]
        for t in alive:
            t.join(timeout=JOIN_TIMEOUT)
        still = sum(1 for t in alive if t.is_alive())
        self._log(f"eof — shutdown ({len(alive)} prompt worker(s), "
                  f"{still} still running)")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(USAGE, file=sys.stderr)
        return 0 if args[0] in ("-h", "--help") else 2

    def _write(frame: dict) -> None:
        sys.stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    def _log(msg: str) -> None:
        print(f"[acp] {msg}", file=sys.stderr, flush=True)

    server = AcpServer(lines=sys.stdin, write=_write, log=_log)
    return server.serve()


if __name__ == "__main__":
    sys.exit(main())
