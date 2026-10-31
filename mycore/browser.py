"""mycore.browser — local headless browser automation over CDP (car 4b).

Pure stdlib (repo iron rule): a minimal RFC 6455 WebSocket client speaks the
Chrome DevTools Protocol to a locally launched Chromium/Chrome. No Playwright
or Selenium — the Python packages are not installed and the free toolchain
must not grow third-party runtime dependencies.

Capability honesty: `available()` only checks that a browser BINARY can be
discovered (env AIDOG_BROWSER_BIN > playwright cache glob > PATH); it never
launches anything, so build_registry() can decide in milliseconds whether to
register the six browser_* tools at all (missing binary -> a note on
registry.browser_note, never fake tools). Every runtime failure raises
ToolError carrying the real cause (status line, exception type, exit code,
stderr tail) — never a silent fake result.

URL policy: http/https only. chrome://, about:, file:, data:, javascript: and
friends are refused with the reason (they reach local files or the browser UI).

Lifecycle: one singleton session behind a thread lock, a 300s inactivity
watchdog thread, atexit cleanup (process + temp profile dir), and output/snapshot
caps (snapshot <= 12000 chars).
"""
from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .tools import ToolError  # tools imports this module only inside build_registry

REPO = Path(__file__).resolve().parent.parent

SNAPSHOT_MAX = 12000      # snapshot output cap (context budget discipline)
IDLE_TIMEOUT = 300.0      # seconds of inactivity before the session auto-closes
STARTUP_TIMEOUT = 25.0    # wait for DevTools /json/version
NAV_TIMEOUT = 30.0        # Page.navigate + loadEventFired
LOAD_TIMEOUT = 30.0       # post-click settle wait

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


# ------------------------------------------------------------ discovery ---

def find_binary() -> tuple[str | None, list[str]]:
    """Locate the browser binary. Order: env AIDOG_BROWSER_BIN (authoritative:
    a set-but-missing value is a hard, honest failure — the operator's
    override is never silently ignored), then the Playwright cache glob, then
    PATH. Returns (path | None, attempts) for honest error reporting."""
    attempts: list[str] = []
    env = os.environ.get("AIDOG_BROWSER_BIN", "").strip()
    if env:
        attempts.append(f"$AIDOG_BROWSER_BIN={env}")
        if Path(env).is_file() and os.access(env, os.X_OK):
            return env, attempts
        return None, attempts          # explicit override wins — bad value fails loudly
    cache = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "ms-playwright"
    hits = sorted(cache.glob("chromium-*/chrome-linux64/chrome"))
    attempts.append(f"glob {cache}/chromium-*/chrome-linux64/chrome -> {len(hits)} hit(s)")
    for cand in reversed(hits):        # newest build first
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand), attempts
    for name in ("chrome", "chromium", "chromium-browser", "google-chrome",
                 "google-chrome-stable"):
        found = shutil.which(name)
        attempts.append(f"PATH {name} -> {found or 'miss'}")
        if found:
            return found, attempts
    return None, attempts


def available() -> bool:
    """Fast capability probe for build_registry(): discovery only, never launches."""
    try:
        return find_binary()[0] is not None
    except OSError:
        return False


# ---------------------------------------------------- minimal RFC 6455 ws ---

class _WSClosed(Exception):
    """Server sent a close frame (or the handshake ended early)."""


class _WS:
    """Minimal RFC 6455 client: local hosts only, client masking, text frames,
    fragmentation, ping/pong, close handshake. All parse state lives on the
    instance (byte buffer + fragment registers) so a read timeout is always
    resumable — a later read continues exactly where it stopped."""

    def __init__(self, url: str) -> None:
        self.dead = False
        self._buf = bytearray()
        self._frag_op: int | None = None
        self._frag_parts: list[bytes] = []
        p = urllib.parse.urlsplit(url)
        if p.scheme != "ws":
            raise ToolError(f"unsupported websocket scheme {p.scheme!r} (ws:// only)")
        host = p.hostname or ""
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ToolError(f"refusing websocket to non-local host {host!r}")
        port = p.port or 80
        try:
            self.sock = socket.create_connection((host, port), timeout=10)
        except OSError as e:
            raise ToolError(f"websocket connect to {host}:{port} failed: {type(e).__name__}") from None
        self.sock.settimeout(10)
        key = base64.b64encode(os.urandom(16)).decode()
        path = (p.path or "/") + (f"?{p.query}" if p.query else "")
        req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        try:
            self.sock.sendall(req.encode())
            head = bytearray()
            while b"\r\n\r\n" not in head:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ToolError("websocket handshake: peer closed before response")
                head += chunk
                if len(head) > 65536:
                    raise ToolError("websocket handshake: oversized response")
        except OSError as e:
            raise ToolError(f"websocket handshake failed: {type(e).__name__}") from None
        raw, rest = bytes(head).split(b"\r\n\r\n", 1)
        lines = raw.split(b"\r\n")
        if b" 101" not in lines[0]:
            raise ToolError(f"websocket handshake rejected: {lines[0][:80]!r}")
        accept = b""
        for ln in lines[1:]:
            if ln.lower().startswith(b"sec-websocket-accept:"):
                accept = ln.split(b":", 1)[1].strip()
        expect = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest())
        if accept != expect:
            raise ToolError("websocket handshake: bad Sec-WebSocket-Accept")
        self._buf += rest

    # -- framing -------------------------------------------------------
    def set_timeout(self, seconds: float) -> None:
        try:
            self.sock.settimeout(max(seconds, 0.05))
        except OSError:
            pass

    def _fill(self, n: int) -> None:
        while len(self._buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except TimeoutError:
                raise
            except OSError as e:
                self.dead = True
                raise ConnectionError(f"socket read failed: {type(e).__name__}") from None
            if not chunk:
                self.dead = True
                raise ConnectionError("websocket closed by peer")
            self._buf += chunk

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        hdr = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        hdr += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        try:
            self.sock.sendall(bytes(hdr))
        except OSError as e:
            self.dead = True
            raise ConnectionError(f"socket write failed: {type(e).__name__}") from None

    def send_text(self, text: str) -> None:
        if self.dead:
            raise ConnectionError("websocket marked dead")
        self._send_frame(0x1, text.encode("utf-8"))

    def read_message(self) -> str:
        """Next complete data message (control frames handled transparently).
        State is fully instance-side: a TimeoutError mid-frame resumes cleanly."""
        while True:
            self._fill(2)
            b0, b1 = self._buf[0], self._buf[1]
            fin = b0 & 0x80
            op = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:                       # 16-bit extended length
                self._fill(4)
                ln = struct.unpack(">H", bytes(self._buf[2:4]))[0]
                off = 4
            elif ln == 127:                     # 64-bit extended length
                self._fill(10)
                ln = struct.unpack(">Q", bytes(self._buf[2:10]))[0]
                off = 10
            else:
                off = 2
            total = off + (4 if masked else 0) + ln
            self._fill(total)
            frame = bytes(self._buf[:total])
            del self._buf[:total]
            pos = off
            mask = frame[pos:pos + 4] if masked else b""
            if masked:
                pos += 4
            payload = bytearray(frame[pos:pos + ln])
            if masked:
                for i in range(ln):
                    payload[i] ^= mask[i % 4]
            data = bytes(payload)
            if op == 0x8:                                   # close
                self._frag_op, self._frag_parts = None, []
                try:
                    self._send_frame(0x8, data[:2])
                except (ConnectionError, OSError):
                    pass
                raise _WSClosed("server sent close frame")
            if op == 0x9:                                   # ping -> pong
                self._send_frame(0xA, data)
                continue
            if op == 0xA:                                   # pong
                continue
            if op in (0x1, 0x2):                            # new message
                if self._frag_op is not None:
                    raise ToolError("websocket: data frame while a fragmented message is open")
                self._frag_op, self._frag_parts = op, [data]
            elif op == 0x0:                                 # continuation
                if self._frag_op is None:
                    raise ToolError("websocket: continuation frame without a start")
                self._frag_parts.append(data)
            else:
                raise ToolError(f"websocket: unsupported opcode {op}")
            if fin:
                msg_op, parts = self._frag_op, self._frag_parts
                self._frag_op, self._frag_parts = None, []
                blob = b"".join(parts)
                return blob.decode("utf-8", errors="replace") if msg_op == 0x1 else blob.hex()

    def close(self) -> None:
        if not self.dead:
            try:
                self._send_frame(0x8, struct.pack(">H", 1000))
                self.set_timeout(1.0)
                try:
                    self.read_message()          # await the close echo
                except Exception:                # noqa: BLE001 — best effort
                    pass
            except (ConnectionError, OSError, ToolError):
                pass
        try:
            self.sock.close()
        except OSError:
            pass
        self.dead = True


# ------------------------------------------------------------ CDP session ---

class _CDP:
    """One in-flight command at a time (callers hold the session lock).
    Non-matching traffic (events, stale responses) is buffered for wait_event."""

    def __init__(self, ws: _WS) -> None:
        self.ws = ws
        self._next = 0
        self.events: list[dict] = []

    def _buffer(self, data: dict) -> None:
        self.events.append(data)
        if len(self.events) > 1000:
            del self.events[:500]

    def call(self, method: str, params: dict | None = None,
             session: str | None = None, timeout: float = 30.0) -> dict:
        if self.ws.dead:
            raise ToolError("browser connection lost — call browser_open to restart")
        self._next += 1
        mid = self._next
        msg: dict[str, Any] = {"id": mid, "method": method}
        if params:
            msg["params"] = params
        if session:
            msg["sessionId"] = session
        try:
            self.ws.send_text(json.dumps(msg))
            self.ws.set_timeout(timeout)
            while True:
                data = json.loads(self.ws.read_message())
                if data.get("id") == mid:
                    if "error" in data:
                        err = data["error"] or {}
                        raise ToolError(f"CDP {method} error {err.get('code', '?')}: "
                                        f"{err.get('message', '?')}")
                    return data.get("result") or {}
                if "method" in data:
                    self._buffer(data)
                # stale/unknown response id: ignore and keep reading
        except TimeoutError:
            raise ToolError(f"CDP timeout: {method} got no response within "
                            f"{timeout:g}s (browser unresponsive)") from None
        except _WSClosed as e:
            self.ws.dead = True
            raise ToolError(f"browser closed the DevTools connection ({e}) — "
                            "call browser_open to restart") from None
        except ConnectionError as e:
            self.ws.dead = True
            raise ToolError(f"browser connection lost: {e}") from None
        except OSError as e:
            self.ws.dead = True
            raise ToolError(f"browser socket error: {type(e).__name__}") from None
        except json.JSONDecodeError:
            raise ToolError(f"CDP {method}: malformed response frame") from None

    def _pop_event(self, name: str, session: str | None) -> dict | None:
        for i, ev in enumerate(self.events):
            if ev.get("method") == name and (session is None
                                              or ev.get("sessionId") == session):
                return self.events.pop(i)
        return None

    def wait_event(self, name: str, session: str | None,
                   timeout: float) -> dict | None:
        """First drain buffered events, then block until `name` or timeout
        (None = honest timeout, no fake success)."""
        ev = self._pop_event(name, session)
        if ev is not None:
            return ev
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                return None
            try:
                self.ws.set_timeout(rem)
                raw = self.ws.read_message()
            except TimeoutError:
                return None
            except (_WSClosed, ConnectionError) as e:
                self.ws.dead = True
                raise ToolError(f"browser connection lost while waiting for {name}: {e}") from None
            except OSError as e:
                self.ws.dead = True
                raise ToolError(f"browser socket error: {type(e).__name__}") from None
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if "id" in data:
                continue                      # unexpected response — ignore
            if data.get("method") == name and (session is None
                                                or data.get("sessionId") == session):
                return data
            self._buffer(data)


# ---------------------------------------------------- page script snippets ---

_JS_HELPERS = r"""
  var SEL = 'a[href],button,input,select,textarea,summary,[role=button],' +
            '[role=link],[role=tab],[role=checkbox],[role=radio],[role=menuitem],' +
            '[role=option],[onclick]';
  function interactive() {
    var out = [];
    Array.prototype.forEach.call(document.querySelectorAll(SEL), function (el) {
      var r = el.getBoundingClientRect();
      if ((r.width > 0 || r.height > 0) || el.getClientRects().length > 0) out.push(el);
    });
    return out;
  }
  function label(el) {
    var t = el.innerText || el.getAttribute('aria-label') || el.title ||
            el.getAttribute('placeholder') || el.getAttribute('name') || el.id ||
            el.value || '';
    return String(t).replace(/\s+/g, ' ').trim().slice(0, 80);
  }
  function describe(el, i) {
    var tag = el.tagName.toLowerCase();
    var s = '@e' + i + ' [' + tag;
    if (tag === 'input' && el.type && el.type !== 'text') s += ':' + el.type;
    var role = el.getAttribute('role');
    if (role && role !== tag) s += '/' + role;
    s += '] "' + label(el) + '"';
    if (tag === 'a' && el.href) s += ' -> ' + String(el.href).slice(0, 120);
    if (tag === 'input' || tag === 'textarea') {
      s += ' value="' + String(el.value || '').slice(0, 60) + '"';
    }
    if (tag === 'select' && el.options.length) {
      var o = el.options[el.selectedIndex];
      s += ' selected="' + (o ? String(o.text).slice(0, 60) : '') + '"';
    }
    return s;
  }
"""

_JS_SNAPSHOT = ("(function () {" + _JS_HELPERS + r"""
  var els = interactive();
  var items = els.map(function (el, i) { return describe(el, i + 1); });
  var text = (document.body && document.body.innerText) || '';
  return JSON.stringify({url: location.href, title: document.title,
                         text: text.slice(0, 8000), items: items.slice(0, 200),
                         total: items.length});
})()""")

_JS_CLICK = ("(function () {" + _JS_HELPERS + r"""
  var els = interactive();
  var el = els[{N} - 1];
  if (!el) return JSON.stringify({ok: false, reason: 'notfound'});
  var out = {ok: true, tag: el.tagName.toLowerCase(), label: label(el)};
  try { el.focus({preventScroll: true}); } catch (e) {}
  try { el.click(); } catch (e) {
    return JSON.stringify({ok: false, reason: 'clickerror', error: String(e)});
  }
  return JSON.stringify(out);
})()""")

_JS_FOCUS = ("(function () {" + _JS_HELPERS + r"""
  var els = interactive();
  var el = els[{N} - 1];
  if (!el) return JSON.stringify({ok: false, reason: 'notfound'});
  var tag = el.tagName.toLowerCase();
  var ok = false;
  if (tag === 'textarea') ok = true;
  else if (el.isContentEditable) ok = true;
  else if (tag === 'input') {
    ok = ['text', 'search', 'url', 'tel', 'email', 'password', 'number']
         .indexOf(el.type || 'text') >= 0;
  }
  if (!ok) return JSON.stringify({ok: false, reason: 'noteditable',
                                  tag: tag, type: el.type || ''});
  el.focus({preventScroll: true});
  return JSON.stringify({ok: true, tag: tag, type: el.type || ''});
})()""")


# --------------------------------------------------------- session object ---

class _Browser:
    def __init__(self, binary: str, port: int, profile: Path,
                 proc: subprocess.Popen, cdp: _CDP, sid: str,
                 err_path: Path) -> None:
        self.binary = binary
        self.port = port
        self.profile = profile
        self.proc = proc
        self.cdp = cdp
        self.sid = sid
        self.err_path = err_path

    def close(self) -> None:
        try:
            if not self.cdp.ws.dead:
                try:
                    self.cdp.call("Browser.close", timeout=3)   # polite shutdown
                except ToolError:
                    pass
            self.cdp.ws.close()
        finally:
            try:
                self.proc.wait(timeout=5)
            except Exception:                                    # noqa: BLE001
                for stop in (self.proc.terminate, self.proc.kill):
                    try:
                        stop()
                        self.proc.wait(timeout=3)
                        break
                    except Exception:                            # noqa: BLE001
                        continue
            shutil.rmtree(self.profile, ignore_errors=True)


_LOCK = threading.RLock()
_BROWSER: _Browser | None = None
_last_used = 0.0
_watchdog_running = False
_shot_seq = 0


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _stderr_tail(path: Path, n: int = 400) -> str:
    try:
        return path.read_bytes()[-n:].decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _start() -> _Browser:
    binary, attempts = find_binary()
    if not binary:
        raise ToolError("browser binary not found; tried: " + " · ".join(attempts))
    port = _free_port()
    profile = Path(tempfile.mkdtemp(prefix="aidog-browser-"))
    err_path = profile / "chrome.stderr.log"
    cmd = [binary, "--headless", "--no-sandbox", "--disable-gpu",
           "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
           "--disable-background-networking", "--disable-component-update",
           "--disable-sync", "--disable-default-apps", "--remote-allow-origins=*",
           f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
           "about:blank"]
    try:
        with err_path.open("wb") as err_f:
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err_f)
    except OSError as e:
        shutil.rmtree(profile, ignore_errors=True)
        raise ToolError(f"cannot launch {binary}: {type(e).__name__}: {e}") from None
    # DevTools readiness via GET /json/version (NOT /json/new — modern Chrome
    # requires PUT there; navigation goes through the websocket instead).
    ws_url = ""
    last = ""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline and not ws_url:
        if proc.poll() is not None:
            tail = _stderr_tail(err_path)
            shutil.rmtree(profile, ignore_errors=True)
            raise ToolError(f"browser exited with code {proc.returncode} during startup"
                            + (f"; stderr tail: {tail}" if tail else ""))
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
                info = json.loads(resp.read().decode("utf-8", "replace"))
            ws_url = str(info.get("webSocketDebuggerUrl") or "")
            if not ws_url:
                last = "no webSocketDebuggerUrl in /json/version"
        except Exception as e:                                   # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        if not ws_url:
            time.sleep(0.2)
    if not ws_url:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:                                        # noqa: BLE001
            pass
        shutil.rmtree(profile, ignore_errors=True)
        raise ToolError(f"browser DevTools endpoint not ready after "
                        f"{STARTUP_TIMEOUT:g}s ({last})")
    try:
        cdp = _CDP(_WS(ws_url))
        res = cdp.call("Target.getTargets", timeout=10)
        pages = [t for t in (res.get("targetInfos") or []) if t.get("type") == "page"]
        if pages:
            tid = pages[0]["targetId"]
        else:
            tid = cdp.call("Target.createTarget", {"url": "about:blank"},
                           timeout=10)["targetId"]
        sid = cdp.call("Target.attachToTarget", {"targetId": tid, "flatten": True},
                       timeout=10)["sessionId"]
        cdp.call("Page.enable", session=sid, timeout=10)
    except Exception as e:                                       # noqa: BLE001
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:                                        # noqa: BLE001
            pass
        shutil.rmtree(profile, ignore_errors=True)
        if isinstance(e, ToolError):
            raise
        raise ToolError(f"CDP handshake failed: {type(e).__name__}: {e}") from None
    return _Browser(binary, port, profile, proc, cdp, sid, err_path)


def _close_locked() -> None:
    global _BROWSER
    b, _BROWSER = _BROWSER, None
    if b is not None:
        try:
            b.close()
        except Exception:                                        # noqa: BLE001
            pass


def _watchdog_loop() -> None:
    while True:
        time.sleep(15)
        with _LOCK:
            if (_BROWSER is not None
                    and time.monotonic() - _last_used >= IDLE_TIMEOUT):
                _close_locked()


def _ensure_watchdog() -> None:
    global _watchdog_running
    if _watchdog_running:
        return
    _watchdog_running = True
    threading.Thread(target=_watchdog_loop, name="aidog-browser-idle",
                     daemon=True).start()


def _ensure() -> _Browser:
    """Start (or reuse) the singleton session. Call with _LOCK held."""
    global _BROWSER, _last_used
    if _BROWSER is not None:
        if _BROWSER.cdp.ws.dead:
            _close_locked()
        elif time.monotonic() - _last_used > IDLE_TIMEOUT:
            _close_locked()              # watchdog may have been late
        else:
            _last_used = time.monotonic()
            return _BROWSER
    _BROWSER = _start()
    _last_used = time.monotonic()
    _ensure_watchdog()
    return _BROWSER


def _require() -> _Browser:
    """Existing live session or an honest ToolError (never auto-starts)."""
    global _last_used
    with _LOCK:
        if _BROWSER is None:
            raise ToolError("no browser session — call browser_open first")
        if _BROWSER.cdp.ws.dead:
            _close_locked()
            raise ToolError("browser connection lost — call browser_open to restart")
        if time.monotonic() - _last_used > IDLE_TIMEOUT:
            _close_locked()
            raise ToolError("browser session was idle >300s and was closed — "
                            "call browser_open again")
        _last_used = time.monotonic()
        return _BROWSER


def _atexit() -> None:
    try:
        with _LOCK:
            _close_locked()
    except Exception:                                            # noqa: BLE001
        pass


atexit.register(_atexit)


# ------------------------------------------------------------- page helpers ---

def _evaluate(expr: str, timeout: float = 20.0) -> Any:
    b = _require()
    res = b.cdp.call("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True, "awaitPromise": False},
                     session=b.sid, timeout=timeout)
    if "exceptionDetails" in res:
        d = res["exceptionDetails"] or {}
        msg = ((d.get("exception") or {}).get("description")
               or d.get("text") or "evaluation failed")
        raise ToolError(f"page script failed: {msg}")
    return (res.get("result") or {}).get("value")


def _as_obj(val: Any) -> dict:
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except json.JSONDecodeError:
            raise ToolError("page script returned malformed JSON") from None
    if not isinstance(val, dict):
        raise ToolError("page script returned no data (page not loaded? — "
                        "call browser_open first)")
    return val


def _ref_index(ref: Any) -> int:
    if not isinstance(ref, str):
        raise ToolError("ref must be a string like @e1")
    m = re.fullmatch(r"\s*@?e?(\d+)\s*", ref)
    if not m or int(m.group(1)) < 1:
        raise ToolError(f"bad ref {ref!r} — expected an @eN ref from the latest "
                        "browser_snapshot")
    return int(m.group(1))


def _check_url(url: Any) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ToolError("url must be a non-empty string")
    u = url.strip()
    parts = urllib.parse.urlsplit(u)
    scheme = (parts.scheme or "").lower()
    if scheme in ("chrome", "chrome-extension", "about", "file", "data",
                  "javascript", "blob", "devtools", "view-source"):
        raise ToolError(f"blocked URL scheme '{scheme}:' — the browser tool only "
                        "opens http:// and https:// URLs (chrome:/about:/file:/data: "
                        "can reach browser UI or local files)")
    if scheme not in ("http", "https"):
        raise ToolError(f"only http:// and https:// URLs are allowed "
                        f"(got {scheme + ':' if scheme else 'no scheme'})")
    if not parts.netloc:
        raise ToolError(f"URL has no host: {u}")
    return u


def _wait_settled(b: _Browser, what: str, timeout: float = LOAD_TIMEOUT) -> None:
    if b.cdp.wait_event("Page.loadEventFired", b.sid, timeout=timeout) is not None:
        return
    ready = _evaluate("String(document.readyState)")
    if ready == "complete":               # event missed/buffered — DOM says done
        return
    raise ToolError(f"{what} did not finish within {timeout:g}s "
                    f"(readyState={ready})")


def _next_shot_path() -> Path:
    global _shot_seq
    _shot_seq += 1
    d = REPO / "data" / "browser"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"shot-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{_shot_seq}.png"


# -------------------------------------------------------------- tool entries ---

def open_url(url: str) -> str:
    """browser_open: navigate the singleton session to an http(s) URL."""
    u = _check_url(url)
    with _LOCK:
        b = _ensure()
        res = b.cdp.call("Page.navigate", {"url": u}, session=b.sid,
                         timeout=NAV_TIMEOUT)
        if res.get("errorText"):
            raise ToolError(f"navigation to {u} failed: {res['errorText']}")
        _wait_settled(b, f"navigation to {u}", NAV_TIMEOUT)
        title = _evaluate("String(document.title || '')") or ""
    return f"loaded {u} · title={title[:120]}"


def snapshot() -> str:
    """browser_snapshot: page text + interactive elements as positional @eN refs."""
    with _LOCK:
        _require()
        val = _as_obj(_evaluate(_JS_SNAPSHOT))
    lines = [f"url: {val.get('url', '')}",
             f"title: {val.get('title', '')}",
             "refs are positional (@e1, @e2, ...) — re-snapshot after any DOM change.",
             "", "text:", str(val.get("text", "")),
             "", f"interactive ({val.get('total', 0)}):"]
    items = [str(x) for x in (val.get("items") or [])]
    lines += items
    total = int(val.get("total", len(items)) or 0)
    if total > len(items):
        lines.append(f"... [{total - len(items)} more interactive elements omitted]")
    out = "\n".join(lines)
    if len(out) > SNAPSHOT_MAX:
        out = out[:SNAPSHOT_MAX] + f"\n... [truncated at {SNAPSHOT_MAX} chars]"
    return out


def click(ref: str) -> str:
    """browser_click: click the element at a positional @eN ref."""
    n = _ref_index(ref)
    with _LOCK:
        _require()
        d = _as_obj(_evaluate(_JS_CLICK.replace("{N}", str(n))))
        if not d.get("ok"):
            if d.get("reason") == "notfound":
                raise ToolError(f"ref not found, re-snapshot (@e{n} does not exist "
                                "in the current DOM — refs are positional)")
            if d.get("reason") == "clickerror":
                raise ToolError(f"click on @e{n} raised {d.get('error')}")
            raise ToolError(f"cannot click @e{n}: {d.get('reason')}")
        time.sleep(0.05)                 # let a navigation commit
        b = _require()
        if _evaluate("String(document.readyState)") != "complete":
            _wait_settled(b, f"click on @e{n}")
    return f"clicked @e{n} [{d.get('tag', '')}] \"{d.get('label', '')}\""


def type_text(ref: str, text: str, submit: bool = False) -> str:
    """browser_type: focus the text field at a ref, insert text, optional Enter."""
    n = _ref_index(ref)
    if not isinstance(text, str):
        raise ToolError("text must be a string")
    with _LOCK:
        _require()
        d = _as_obj(_evaluate(_JS_FOCUS.replace("{N}", str(n))))
        if not d.get("ok"):
            if d.get("reason") == "notfound":
                raise ToolError(f"ref not found, re-snapshot (@e{n} does not exist "
                                "in the current DOM — refs are positional)")
            extra = f" ({d.get('tag')}{':' + d.get('type') if d.get('type') else ''})"
            raise ToolError(f"@e{n} is not a text field{extra} — use browser_click")
        b = _require()
        b.cdp.call("Input.insertText", {"text": text}, session=b.sid, timeout=15)
        if submit:
            for kind in ("keyDown", "keyUp"):
                params: dict[str, Any] = {
                    "type": kind, "key": "Enter", "code": "Enter",
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
                if kind == "keyDown":
                    params["text"] = "\r"
                b.cdp.call("Input.dispatchKeyEvent", params, session=b.sid, timeout=15)
            time.sleep(0.05)
            if _evaluate("String(document.readyState)") != "complete":
                _wait_settled(b, f"submit after typing into @e{n}")
    note = " + Enter" if submit else ""
    return f"typed {len(text)} chars into @e{n} [{d.get('tag', '')}]{note}"


def screenshot() -> str:
    """browser_screenshot: PNG on disk; returns the absolute path + size."""
    with _LOCK:
        b = _require()
        res = b.cdp.call("Page.captureScreenshot", {"format": "png"},
                         session=b.sid, timeout=30)
        data = res.get("data")
        if not data:
            raise ToolError("Page.captureScreenshot returned no data")
        try:
            raw = base64.b64decode(data)
        except (ValueError, TypeError) as e:
            raise ToolError(f"screenshot payload undecodable: {type(e).__name__}") from None
        path = _next_shot_path()
        path.write_bytes(raw)
    size = path.stat().st_size
    if size <= 0:
        raise ToolError(f"screenshot file is empty: {path}")
    return f"{path} ({size} bytes)"


def close() -> str:
    """browser_close: kill the session and remove its temp profile."""
    with _LOCK:
        if _BROWSER is None:
            return "no browser session"
        _close_locked()
    return "browser closed (process and temp profile removed)"
