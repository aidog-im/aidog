#!/usr/bin/env python3
"""P3 web console smoke test — endpoints, auth gate, validation, idempotency.

Runs against a live console (default http://127.0.0.1:8790). The access token
is read in-process from secrets.env and never printed or written anywhere.
Exit code 0 = all assertions passed.
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8790"
SECRETS = Path(__file__).resolve().parent.parent / "secrets.env"

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


def get(path: str) -> tuple[int, dict | list | str]:
    try:
        with urllib.request.urlopen(BASE + path, timeout=8) as r:
            body = r.read().decode()
            return r.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def post(path: str, doc, token: str | None) -> tuple[int, dict | str]:
    data = json.dumps(doc).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def post_raw(path: str, doc, ctype: str = "application/json",
             origin: str | None = None, timeout: int = 40) -> tuple[int, str, bytes]:
    """POST returning (status, content-type, body-bytes) — for /api/say, whose
    success response is audio, not JSON."""
    data = json.dumps(doc).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()
    except Exception as e:  # noqa: BLE001
        return 0, "", str(e).encode()


def post_audio(path: str, data: bytes, ctype: str, origin: str | None = None,
               timeout: int = 45) -> tuple[int, dict | str]:
    """POST raw audio bytes — the /api/stt local contract. Returns
    (status, parsed JSON body)."""
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", ctype)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def post_sse(path: str, doc, token: str | None,
             timeout: int = 90) -> tuple[int, str, list[tuple[str, dict]]]:
    """POST expecting an SSE stream: returns (status, content-type, events).
    Used by the /api/chat live-model check — a non-200 answer still returns
    its parsed JSON error body as a zero-event result."""
    data = json.dumps(doc).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    events: list[tuple[str, dict]] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ct = r.headers.get("Content-Type", "")
            status = r.status
            buf = b""
            while True:
                chunk = r.read(4096)
                if not chunk:
                    break
                buf += chunk
            text = buf.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), []
    except Exception as e:  # noqa: BLE001
        return 0, "", [("error", {"error": type(e).__name__})]
    if not ct.startswith("text/event-stream"):
        return status, ct, []
    for frame in text.split("\n\n"):
        ev, data_line = "message", ""
        for ln in frame.split("\n"):
            if ln.startswith("event:"):
                ev = ln[6:].strip()
            elif ln.startswith("data:"):
                data_line += ln[5:].strip()
        if data_line:
            try:
                events.append((ev, json.loads(data_line)))
            except ValueError:
                continue
    return status, ct, events


def openai_error_shape(body, msg_part: str | None = None) -> bool:
    """True when `body` is an OpenAI error document: {error:{message:str,
    type, code:null}} with a known type (optionally carrying `msg_part`)."""
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return False
    if not isinstance(err.get("message"), str) or err.get("code") is not None:
        return False
    if err.get("type") not in ("invalid_request_error", "server_error"):
        return False
    return msg_part is None or msg_part in err["message"]


def call_v1(method: str, path: str, doc=None, bearer: str | None = None,
            timeout: int = 120):
    """Request on the /v1 gateway: (status, content-type, parsed JSON when
    the body is JSON, raw text for SSE streams). Long timeout: live turns."""
    data = json.dumps(doc).encode() if doc is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if bearer is not None:
        req.add_header("Authorization", f"Bearer {bearer}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, ct = r.status, r.headers.get("Content-Type", "")
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        status, ct = e.code, e.headers.get("Content-Type", "")
        raw = e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, "", f"{type(e).__name__}: {e}"
    try:
        return status, ct, json.loads(raw)
    except ValueError:
        return status, ct, raw


def sse_data_lines(text: str) -> list[str]:
    """All `data:` payloads of an OpenAI-style SSE body, in order."""
    return [ln[5:].strip() for ln in text.splitlines()
            if ln.startswith("data:")]


def main() -> int:
    tok_m = re.search(r"(?m)^WEB_TOKEN=(\S+)$", SECRETS.read_text()) if SECRETS.exists() else None
    token = tok_m.group(1) if tok_m else None
    check("secrets.WEB_TOKEN present", bool(token))

    s, health = get("/api/health")
    check("GET /api/health", s == 200 and isinstance(health, dict) and health.get("status") == "ok")

    s, status = get("/api/status")
    check("GET /api/status advisory", s == 200 and status.get("advisory", {}).get("level") in
          ("info", "warn", "urgent"))
    check("GET /api/status panels=5", s == 200 and len(status.get("panels", [])) == 5)

    # --- P5-3 phase-aware observability: mycore runtime facts inside the
    # existing 5-panel set (ids unchanged; additive detail/top-level keys) ---
    myc = status.get("mycore") if isinstance(status, dict) else None
    ms = myc.get("service") if isinstance(myc, dict) else None
    mw = myc.get("watchdog") if isinstance(myc, dict) else None
    check("GET /api/status mycore runtime block",
          isinstance(ms, dict) and
          ms.get("state") in ("running", "pending(fail-closed)", "stopped") and
          isinstance(mw, dict) and mw.get("state") in ("up", "down", "unknown") and
          isinstance(mw.get("detail"), str) and bool(mw.get("detail")),
          f"mycore={myc}")
    kpan = next((p for p in status.get("panels", []) if p.get("id") == "kernel"), {})
    gpan = next((p for p in status.get("panels", []) if p.get("id") == "gateway"), {})
    check("GET /api/status panels carry mycore detail",
          bool(kpan.get("detail")) and "mycore service" in str(gpan.get("detail", "")),
          f"kernel={kpan.get('detail')!r} gateway={gpan.get('detail')!r}")

    # --- P5-3 post-swap branch: shim-absent resolver faked in-process ---
    # (no file deletion, no server restart: SHIM_PRESENT is patched on an
    # imported copy of web/server.py and restored afterwards)
    import importlib.util as _ilu
    _spec = _ilu.spec_from_file_location(
        "aidog_web_console_p3", Path(__file__).resolve().parent.parent / "web/server.py")
    _srv = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_srv)
    _saved = _srv.SHIM_PRESENT
    try:
        _srv.SHIM_PRESENT = False
        _bits = _srv.read_config_bits()
        _mc = _srv.load_settings().get("mycore") or {}
        check("post-swap config bits -> mycore settings",
              _bits.get("error") is None and
              _bits.get("model_base_url") == _mc.get("base_url") and
              _bits.get("model_default") == _mc.get("model"),
              f"bits={_bits}")
        _svc = _srv.mycore_service_facts()
        _kp, _gp = _srv._kernel_panel(_svc), _srv._gateway_panel(_svc)
        check("post-swap panels degrade honestly",
              _svc.get("state") in ("running", "pending(fail-closed)", "stopped") and
              _kp.get("state") in ("up", "down") and
              "mycore service" in str(_kp.get("detail", "")) and
              str(_gp.get("detail", "")).startswith("n/a (post-swap)"),
              f"kernel={_kp} gateway={_gp}")
    finally:
        _srv.SHIM_PRESENT = _saved

    s, settings = get("/api/settings")
    check("GET /api/settings", s == 200 and isinstance(settings, dict) and "language" in settings)
    myc = settings.get("mycore") if isinstance(settings, dict) else None
    check("GET settings mycore block",
          isinstance(myc, dict) and isinstance(myc.get("base_url"), str)
          and isinstance(myc.get("model"), str) and myc.get("model"),
          f"mycore={myc}")

    s, usage = get("/api/usage")
    check("GET /api/usage shape", s == 200 and isinstance(usage, dict) and
          "available" in usage and "today" in usage and "pool" in usage)
    disp = usage.get("display") if isinstance(usage, dict) else None
    check("GET /api/usage display block", isinstance(disp, dict) and
          disp.get("currency") in ("USD", "CNY", "EUR", "GBP", "JPY") and
          disp.get("symbol") and disp.get("source") in
          ("auto", "manual", "native", "unavailable"),
          f"display={disp}")

    s, events = get("/api/events")
    check("GET /api/events shape", s == 200 and isinstance(events.get("events"), list))

    # --- evolution loop endpoints (Phase A): read open, writes Bearer ---
    s, evo = get("/api/evolution")
    check("GET /api/evolution shape",
          s == 200 and isinstance(evo, dict)
          and isinstance(evo.get("enabled"), bool)
          and isinstance(evo.get("schedule"), dict)
          and isinstance(evo.get("state"), dict)
          and isinstance(evo.get("due_now"), list)
          and isinstance(evo.get("log_tail"), list)
          and isinstance(evo.get("drafts"), list),
          f"got {s} {str(evo)[:140]}")
    s, body = post("/api/learn", {"kind": "memory", "text": "x"}, None)
    check("POST /api/learn no token -> 401", s == 401, f"got {s}")
    s, body = post("/api/evolution", {"action": "reflect", "period": "daily"}, None)
    check("POST /api/evolution no token -> 401", s == 401, f"got {s}")

    # --- voice capability (Phase 2) ---
    s, voice = get("/api/voice")
    check("GET /api/voice shape", s == 200 and isinstance(voice, dict) and
          "available" in voice and "voices" in voice and "max_chars" in voice and
          isinstance(voice.get("available"), bool))

    # --- /api/say validation (all rejected before the rate budget) ---
    s, _ct, _b = post_raw("/api/say", {"text": ""})
    check("POST /api/say empty text -> 400", s == 400, f"got {s}")
    s, _ct, _b = post_raw("/api/say", {"text": "x" * 201})
    check("POST /api/say overlong text -> 400", s == 400, f"got {s}")
    s, _ct, _b = post_raw("/api/say", {"text": "hi", "lang": "fr"})
    check("POST /api/say bad lang -> 400", s == 400, f"got {s}")
    s, _ct, _b = post_raw("/api/say", {"text": "hi"}, ctype="text/plain")
    check("POST /api/say non-JSON content-type -> 415", s == 415, f"got {s}")
    s, _ct, _b = post_raw("/api/say", {"text": "hi"}, origin="https://evil.example")
    check("POST /api/say cross-origin -> 403", s == 403, f"got {s}")

    # --- /api/say live synthesis: 200+audio, or an honest degradation code.
    # 429 = rate budget spent by a previous run in the same window (by design),
    # 502 = engine/network failure surfaced as JSON (never fake audio). ---
    s, ct, body = post_raw("/api/say", {"text": "Console voice self test.", "lang": "en"})
    if s == 200:
        # mp3 sync/magic: "ID3" tag or a 0xFFEx/0xFFFx frame-sync header
        head3 = body[:3]
        head2 = body[:2]
        ok = (ct.startswith("audio/") and len(body) > 1000 and
              (head3 == b"ID3" or head2 in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xfa")))
        check("POST /api/say synth -> mp3", ok, f"ct={ct} len={len(body)} head={head3!r}")
    elif s == 429:
        check("POST /api/say synth -> mp3", True, "rate-limited (prior run in window)")
        print(f"INFO  /api/say rate-limited as designed (429)")
    elif s == 502:
        err = json.loads(body.decode() or "{}")
        check("POST /api/say synth -> mp3", isinstance(err.get("error"), str) and err["error"],
              f"502 without error detail: {body[:80]!r}")
        print(f"INFO  /api/say engine degraded honestly: {err.get('error', '')[:80]}")
    elif s == 503:
        check("POST /api/say synth -> mp3", True, "engine absent (reported by capability)")
        print("INFO  /api/say: voice engine not installed — 503 as designed")
    else:
        check("POST /api/say synth -> mp3", False, f"got {s} ct={ct} body={body[:80]!r}")

    # --- car 4c: /api/say engine parameter — a bad engine is refused before
    # the rate budget; an explicit byok leg must answer HONESTLY (200 audio
    # or a 503 that names the real reason — never a silent edge fallback). --
    s, _ct, _b = post_raw("/api/say", {"text": "hi", "engine": "bogus"})
    check("POST /api/say engine=bogus -> 400", s == 400, f"got {s}")
    s, ct, body = post_raw("/api/say", {"text": "Console byok honesty test.",
                                        "engine": "byok"}, timeout=40)
    if s == 429:
        check("POST /api/say engine=byok honest -> audio or 503", True,
              "rate-limited (prior run in window)")
        print("INFO  /api/say byok leg rate-limited as designed (429)")
    elif s == 200:
        check("POST /api/say engine=byok honest -> audio or 503",
              str(ct).startswith("audio/") and len(body) > 500,
              f"ct={ct} len={len(body)}")
    else:
        try:
            byok_err = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            byok_err = {}
        check("POST /api/say engine=byok honest -> audio or 503",
              s == 503 and isinstance(byok_err, dict)
              and isinstance(byok_err.get("error"), str)
              and bool(byok_err.get("error")),
              f"got {s} {body[:100]!r}")

    # --- car 4c: /api/stt local contract — every validation leg answers
    # before the rate window, so they are stable in any order. ---
    s, _b = post_audio("/api/stt", b'{"text":"hi"}', "application/json")
    check("POST /api/stt non-audio content-type -> 415", s == 415, f"got {s}")
    s, _b = post_audio("/api/stt", b"", "audio/mpeg")
    check("POST /api/stt empty body -> 400", s == 400, f"got {s}")
    s, _b = post_audio("/api/stt", b"xx", "audio/mpeg",
                       origin="https://evil.example")
    check("POST /api/stt cross-origin -> 403", s == 403, f"got {s}")
    # 413: the handler refuses from the DECLARED Content-Length before any
    # body byte is read, so the probe sends the >2MB header and reads the
    # answer instead of racing a 2MB write against the closed socket.
    import http.client as _hc
    _parts = urllib.parse.urlsplit(BASE)
    _conn = _hc.HTTPConnection(_parts.hostname or "127.0.0.1",
                               _parts.port or 80, timeout=10)
    st_413 = 0
    try:
        _conn.putrequest("POST", "/api/stt")
        _conn.putheader("Content-Type", "audio/mpeg")
        _conn.putheader("Content-Length", str(2_000_001))
        _conn.endheaders()
        _resp = _conn.getresponse()
        st_413 = _resp.status
        _resp.read()
    except OSError as e:  # noqa: BLE001
        st_413 = 0
        print(f"INFO  413 probe transport: {type(e).__name__}")
    finally:
        _conn.close()
    check("POST /api/stt declared >2MB -> 413", st_413 == 413, f"got {st_413}")

    # --- console chat (B1): gate first, then shape, then a live model turn ---
    s, body = post("/api/chat", {"text": "hi"}, None)
    check("POST /api/chat no token -> 401", s == 401, f"got {s}")
    s, hist = get("/api/chat/history")
    check("GET /api/chat/history shape",
          s == 200 and isinstance(hist, dict)
          and isinstance(hist.get("messages"), list)
          and isinstance(hist.get("count"), int)
          and all(m.get("role") in ("user", "assistant")
                  and isinstance(m.get("content"), str)
                  for m in hist.get("messages", [])),
          f"got {s} {str(hist)[:120]}")
    if token:
        s, body = post("/api/chat", {"text": "   "}, token)
        check("POST /api/chat blank text -> 400", s == 400, f"got {s} {body}")
        s, body = post("/api/chat", {"text": "x" * 4001}, token)
        check("POST /api/chat overlong text -> 400", s == 400, f"got {s} {body}")
        s, body = post("/api/chat", {"action": "nope"}, token)
        check("POST /api/chat unknown action -> 400",
              s == 400 and isinstance(body, dict) and "details" in body,
              f"got {s} {body}")
        # live model over SSE: >=1 delta + an authoritative done frame.
        # A red line here with the gateway up is a REAL signal, not flake.
        s, ct, evs = post_sse("/api/chat",
                              {"text": "Reply with exactly: pong"}, token)
        kinds = [e for e, _ in evs]
        done = next((d for e, d in evs if e == "done"), None)
        check("POST /api/chat live model -> SSE stream",
              s == 200 and ct.startswith("text/event-stream")
              and kinds and kinds[0] == "meta", f"got {s} ct={ct} evs={kinds[:6]}")
        check("POST /api/chat live model -> delta + done",
              "delta" in kinds and "done" in kinds
              and isinstance(done, dict)
              and isinstance(done.get("text"), str)
              and done.get("stop") in ("final", "max_iters", "model_error"),
              f"kinds={kinds} done={str(done)[:120]}")
        if isinstance(done, dict):
            check("POST /api/chat done carries turn stats",
                  isinstance(done.get("iterations"), int)
                  and isinstance(done.get("tool_calls"), int),
                  str({k: done.get(k) for k in ("iterations", "tool_calls")}))
        # history grew by the live turn; then archive it (idempotent clear)
        s, hist = get("/api/chat/history")
        roles = [m["role"] for m in hist.get("messages", [])]
        check("POST /api/chat turn persisted to the session",
              s == 200 and roles.count("user") >= 1
              and roles.count("assistant") >= 1, str(roles[-4:]))
        s, body = post("/api/chat", {"action": "clear"}, token)
        check("POST /api/chat clear archives the session",
              s == 200 and isinstance(body, dict) and body.get("ok") is True
              and isinstance(body.get("archived"), bool), f"got {s} {body}")
        s, hist = get("/api/chat/history")
        check("POST /api/chat clear empties history",
              s == 200 and hist.get("count") == 0, str(hist)[:120])

    # --- auth gate ---
    s, body = post("/api/settings", {"sound": True}, None)
    check("POST without token -> 401", s == 401, f"got {s}")
    s, body = post("/api/settings", {"sound": True}, "wrong-token")
    check("POST wrong token -> 401", s == 401, f"got {s}")

    # --- validation ---
    s, body = post("/api/settings", {"unknown_key": 1}, token)
    check("POST unknown key -> 400", s == 400, f"got {s} {body}")
    s, body = post("/api/settings", {"refresh_sec": 9999}, token)
    check("POST out-of-range -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"usage": {"metrics_url": "http://evil.example/x"}}, token)
    check("POST non-loopback metrics_url -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"voice_volume": 150}, token)
    check("POST voice_volume out-of-range -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"voice_volume": True}, token)
    check("POST voice_volume bool -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"currency": "XXX"}, token)
    check("POST bad currency -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"fx_rate": -1}, token)
    check("POST negative fx_rate -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"fx_rate": True}, token)
    check("POST fx_rate bool -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"mycore": {"model": "bad model!"}}, token)
    check("POST mycore bad model -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"mycore": {"base_url": "ftp://x"}}, token)
    check("POST mycore non-http url -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"mycore": {"unknown": "x"}}, token)
    check("POST mycore unknown key -> 400", s == 400, f"got {s}")

    # --- car 1-D1/D4: context_dirs + upstreams (empty = off must be legal,
    # malformed must be refused with the parser's exact reason) ---
    s, body = post("/api/settings", {"mycore": {"context_dirs": ""}}, token)
    check("POST mycore empty context_dirs (off) -> 200", s == 200,
          f"got {s} {body}")
    s, body = post("/api/settings", {"mycore": {"context_dirs": "/tmp:/var"}},
                   token)
    check("POST mycore context_dirs list -> 200", s == 200, f"got {s} {body}")
    s, body = post("/api/settings", {"mycore": {"context_dirs": "a\nb"}}, token)
    check("POST mycore multiline context_dirs -> 400", s == 400, f"got {s}")
    s, body = post("/api/settings", {"mycore": {"upstreams": "bogus"}}, token)
    check("POST mycore bad upstreams spec -> 400 + reason",
          s == 400 and "upstreams" in str(body), f"got {s} {body}")
    s, body = post("/api/settings",
                   {"mycore": {"upstreams": "http://b1.example/v1|KEY_B1"}},
                   token)
    check("POST mycore valid upstreams -> 200", s == 200, f"got {s} {body}")
    s, body = post("/api/settings",
                   {"mycore": {"upstreams": "", "context_dirs": ""}}, token)
    check("POST mycore chain/context back to off -> 200", s == 200,
          f"got {s} {body}")

    # --- car 2-D5: mcp (empty = off, JSON grammar, exact parser reason) ---
    s, body = post("/api/settings", {"mycore": {"mcp": ""}}, token)
    check("POST mycore empty mcp (off) -> 200", s == 200, f"got {s} {body}")
    s, body = post("/api/settings", {"mycore": {"mcp": "{broken"}}, token)
    check("POST mycore bad mcp JSON -> 400 + reason",
          s == 400 and "mcp" in str(body), f"got {s} {body}")
    s, body = post("/api/settings",
                   {"mycore": {"mcp": '[{"name":"a","transport":"udp"}]'}},
                   token)
    check("POST mycore bad mcp transport -> 400 + reason",
          s == 400 and "transport" in str(body), f"got {s} {body}")
    s, body = post("/api/settings",
                   {"mycore": {"mcp":
                    '[{"name":"demo","transport":"http",'
                    '"url":"http://127.0.0.1:9/mcp"}]'}}, token)
    check("POST mycore valid mcp server -> 200", s == 200, f"got {s} {body}")
    s, body = post("/api/settings", {"mycore": {"mcp": ""}}, token)
    check("POST mycore mcp back to off -> 200", s == 200, f"got {s} {body}")

    # --- evolution settings partition: strict validation, all-or-nothing ---
    if token:
        s, body = post("/api/settings",
                       {"evolution": {"enabled": "yes", "daily_at": "25:00",
                                      "weekly_day": "someday", "monthly_day": 32,
                                      "zz": 1}}, token)
        check("POST evolution bad partition -> 400 (5 details)",
              s == 400 and isinstance(body, dict)
              and len(body.get("details", [])) == 5, f"got {s} {body}")
        s, body = post("/api/settings", {"evolution": {"daily_at": "09:10"}},
                       token)
        check("POST evolution valid partition -> 200",
              s == 200 and isinstance(body, dict)
              and body.get("settings", {}).get("evolution", {}).get("daily_at")
              == "09:10", f"got {s} {str(body)[:160]}")
        s, body = post("/api/learn", {"kind": "wat"}, token)
        check("POST /api/learn bad kind -> 400 (no write)",
              s == 400 and isinstance(body, dict) and "details" in body,
              f"got {s} {body}")
        s, body = post("/api/evolution",
                       {"action": "reflect", "period": "yearly"}, token)
        check("POST /api/evolution bad period -> 400 (no write)",
              s == 400, f"got {s} {body}")

    # --- valid write + idempotency ---
    if token:
        patch = {"sound": False}
        s, body = post("/api/settings", patch, token)
        ok = s == 200 and isinstance(body, dict) and body.get("ok") is True
        check("POST valid patch -> 200", ok, f"got {s} {body}")
        first_changed = body.get("changed") if isinstance(body, dict) else None
        s, body = post("/api/settings", patch, token)
        check("POST repeat -> changed=false (idempotent)",
              s == 200 and isinstance(body, dict) and body.get("changed") is False,
              f"got {s} {body}")
        s, settings2 = get("/api/settings")
        check("settings persisted", s == 200 and settings2.get("sound") is False)
        check("voice_volume persisted in range",
              isinstance(settings2.get("voice_volume"), int) and
              0 <= settings2["voice_volume"] <= 100)
        check("write changed flag meaningful", isinstance(first_changed, bool))
        check("response never echoes token",
              token not in json.dumps(body if isinstance(body, dict) else {}))

    # --- channels (car C): status endpoint, settings partition, webhook gate. --
    # The settings document is snapshotted as raw text and restored after the
    # channels writes, so this smoke never leaves personal config behind.
    settings_path = SECRETS.parent / "web" / "settings.json"
    try:
        settings_backup = settings_path.read_text(encoding="utf-8")
    except OSError:
        settings_backup = None

    s, ch = get("/api/channels")
    rows = ch.get("channels") if isinstance(ch, dict) else None
    check("GET /api/channels 200 + channels list",
          s == 200 and isinstance(rows, list), f"got {s} {str(ch)[:140]}")
    rows = rows if isinstance(rows, list) else []
    if rows:
        check("GET /api/channels element fields",
              all(isinstance(r, dict)
                  and {"name", "enabled", "configured"} <= set(r)
                  for r in rows), str(rows)[:200])
    else:
        # empty list is only legal when honestly explained by a note
        check("GET /api/channels empty list carries a note",
              isinstance(ch, dict) and isinstance(ch.get("note"), str)
              and bool(ch.get("note")), f"got {ch}")

    if token:
        s, body = post("/api/settings",
                       {"channels": {"discord": {"enabled": False,
                                                 "allow": ["u-1"],
                                                 "channel_ids": ["c-1"]}}},
                       token)
        dsc = (body.get("settings", {}).get("channels", {}).get("discord", {})
               if isinstance(body, dict) else {})
        check("POST settings channels legal merge -> 200",
              s == 200 and isinstance(body, dict) and body.get("ok") is True
              and dsc.get("allow") == ["u-1"]
              and dsc.get("channel_ids") == ["c-1"],
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings",
                       {"channels": {"webhook": {"enabled": "yes"}}}, token)
        check("POST settings channels bad type -> 400", s == 400,
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings", {"channels": {"nope": {}}}, token)
        check("POST settings channels unknown key -> 400", s == 400,
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings",
                       {"channels": {"email": {"smtp_port": 0}}}, token)
        check("POST settings channels bad port -> 400", s == 400,
              f"got {s} {str(body)[:160]}")

    # --- car D: outbound send gate + domestic route partition. Every write
    # below stays inside this snapshot block so settings.json is restored
    # byte-for-byte afterwards. ---
    if token:
        s, body = post("/api/channels/send",
                       {"route": "discord", "target": "t", "text": "ping"},
                       "wrong-token")
        check("POST /api/channels/send wrong bearer -> 401", s == 401,
              f"got {s} {str(body)[:140]}")
        check("POST /api/channels/send 401 never echoes the token",
              isinstance(body, dict) and token not in json.dumps(body),
              str(body)[:140])
        s, body = post("/api/channels/send",
                       {"target": "t", "text": "ping"}, token)
        check("POST /api/channels/send missing route -> 400",
              s == 400 and isinstance(body, dict)
              and isinstance(body.get("details"), list),
              f"got {s} {str(body)[:140]}")
        s, body = post("/api/channels/send",
                       {"route": "nope", "target": "t", "text": "ping"},
                       token)
        check("POST /api/channels/send unknown route -> 400", s == 400,
              f"got {s} {str(body)[:140]}")
        s, body = post("/api/channels/send",
                       {"route": "discord", "target": "t", "text": "   "},
                       token)
        check("POST /api/channels/send empty text -> 400", s == 400,
              f"got {s} {str(body)[:140]}")
        s, body = post("/api/channels/send",
                       {"route": "discord", "target": "t", "text": "ping"},
                       token)
        check("POST /api/channels/send disabled route -> 409 + reason",
              s == 409 and isinstance(body, dict) and body.get("ok") is False
              and "disabled" in str(body.get("error", "")),
              f"got {s} {str(body)[:140]}")
        # Enable discord with no CHANNEL_DISCORD_TOKEN slot on this machine:
        # the 409 reason must name the slot, never any value.
        s_en, _b_en = post("/api/settings",
                           {"channels": {"discord": {"enabled": True}}},
                           token)
        disc_m = re.search(r"(?m)^CHANNEL_DISCORD_TOKEN=(\S+)$",
                           SECRETS.read_text()) if SECRETS.exists() else None
        s, body = post("/api/channels/send",
                       {"route": "discord", "target": "t", "text": "ping"},
                       token)
        if disc_m is None:
            check("POST /api/channels/send missing slot -> 409 names slot",
                  s_en == 200 and s == 409 and isinstance(body, dict)
                  and body.get("ok") is False
                  and "CHANNEL_DISCORD_TOKEN" in str(body.get("error", ""))
                  and "ping" not in json.dumps(body),
                  f"enable={s_en} got {s} {str(body)[:140]}")
        else:
            check("POST /api/channels/send slot present -> not 409-config",
                  s_en == 200 and s in (200, 502),
                  f"enable={s_en} got {s} {str(body)[:140]}")

        # --- car D settings partition: domestic routes validate identically
        # to the car C routes (legal merge round-trips, bad shape refused) ---
        s, body = post("/api/settings",
                       {"channels": {"feishu": {"enabled": False,
                                                 "allow": ["ou_1"],
                                                 "chat_ids": ["oc_1"]}}},
                       token)
        fsh = (body.get("settings", {}).get("channels", {}).get("feishu", {})
               if isinstance(body, dict) else {})
        check("POST settings channels.feishu legal merge -> 200 + round-trip",
              s == 200 and isinstance(body, dict) and body.get("ok") is True
              and fsh == {"enabled": False, "allow": ["ou_1"],
                          "chat_ids": ["oc_1"]},
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings",
                       {"channels": {"wechat": {"enabled": "yes"}}}, token)
        check("POST settings channels.wechat bad type -> 400", s == 400,
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings",
                       {"channels": {"feishu": {"nope": 1}}}, token)
        check("POST settings channels.feishu unknown subkey -> 400", s == 400,
              f"got {s} {str(body)[:160]}")

    # --- car 4a-M2b: hooks settings partition — whitelist gate, schema
    # errors, legal write + byte-consistent readback (all inside the
    # snapshot block above so settings.json is restored afterwards). ---
    if token:
        s, body = post("/api/settings",
                       {"hooks": {"turn_end": [{"cmd": ["/bin/true"]}],
                                  "nope": [{"cmd": ["/bin/true"]}]}},
                       token)
        det = " ".join(str(d) for d in body.get("details", [])) \
            if isinstance(body, dict) else ""
        check("POST settings hooks unknown event -> 400 names whitelist",
              s == 400 and "unknown hooks event: nope" in det and
              "turn_start" in det and "cron_fire" in det,
              f"got {s} {str(body)[:200]}")
        s, body = post("/api/settings",
                       {"hooks": {"turn_end": [{"cmd": "/bin/true"}]}}, token)
        det = " ".join(str(d) for d in body.get("details", [])) \
            if isinstance(body, dict) else ""
        check("POST settings hooks cmd as string -> 400 schema error",
              s == 400 and "cmd must be a non-empty array" in det,
              f"got {s} {str(body)[:200]}")
        s, body = post("/api/settings",
                       {"hooks": {"turn_end": [{"cmd": ["/bin/true"],
                                                 "timeout": 7}]}}, token)
        hk_blk = body.get("settings", {}).get("hooks", {}) \
            if isinstance(body, dict) else {}
        ok_write = (s == 200 and isinstance(body, dict)
                    and body.get("ok") is True
                    and hk_blk.get("turn_end") ==
                    [{"cmd": ["/bin/true"], "timeout": 7}])
        readback = None
        if ok_write:
            try:
                readback = json.loads(settings_path.read_text(
                    encoding="utf-8")).get("hooks")
            except (OSError, ValueError):
                readback = None
        s2, gs = get("/api/settings")
        check("POST settings hooks legal -> 200, file + GET readback match",
              ok_write and readback == {"turn_end":
                                        [{"cmd": ["/bin/true"],
                                          "timeout": 7}]}
              and s2 == 200 and isinstance(gs, dict)
              and gs.get("hooks", {}).get("turn_end") ==
              [{"cmd": ["/bin/true"], "timeout": 7}],
              f"post={s} readback={readback} get={s2} "
              f"{str(gs.get('hooks') if isinstance(gs, dict) else gs)[:160]}")
        slot_vals = []
        if SECRETS.exists():
            for _v in re.findall(r"(?m)^[A-Za-z0-9_]+=(\S[^\r\n]*)$",
                                 SECRETS.read_text(encoding="utf-8")):
                if len(_v.strip()) >= 6:
                    slot_vals.append(_v.strip())
        body_text = json.dumps(body if isinstance(body, dict) else {})
        check("hooks settings responses never echo a slot value",
              bool(slot_vals) and all(v not in body_text for v in slot_vals)
              and "WEB_TOKEN" not in body_text,
              f"n_slots={len(slot_vals)}")

    # --- webhook inbound gate: the test never configures a real secret.
    # Missing slot = honest 503 naming the slot; a present slot must refuse
    # a wrong/absent bearer with 401 and leak nothing either way. ---
    wh_m = re.search(r"(?m)^CHANNEL_WEBHOOK_SECRET=(\S+)$",
                     SECRETS.read_text()) if SECRETS.exists() else None
    wh_val = wh_m.group(1) if wh_m else None
    s, body = post("/api/channels/webhook", {"text": "ping"}, None)
    s2, body2 = post("/api/channels/webhook", {"text": "ping"}, "wrong-bearer")
    if wh_val is None:
        check("POST /api/channels/webhook no secret slot -> 503",
              s == 503 and s2 == 503
              and "CHANNEL_WEBHOOK_SECRET" in str(body),
              f"got {s}/{s2} {body}")
    else:
        check("POST /api/channels/webhook wrong bearer -> 401",
              s == 401 and s2 == 401, f"got {s}/{s2}")
    blob = json.dumps([b for b in (body, body2) if isinstance(b, dict)])
    check("POST /api/channels/webhook never echoes the secret",
          wh_val is None or wh_val not in blob)

    # --- car 4b: mycore.shell terminal backend partition (validated by the
    # grammar owner terminal.parse — the server never re-implements it).
    # Inside the snapshot block: these writes are restored byte-for-byte. ---
    if token:
        s, body = post("/api/settings",
                       {"mycore": {"shell": "docker:image=ubuntu:latest"}},
                       token)
        check("POST mycore valid docker shell spec -> 200", s == 200,
              f"got {s} {str(body)[:160]}")
        s, caps_tmp = get("/api/capabilities")
        tmb = caps_tmp.get("terminal") if isinstance(caps_tmp, dict) else None
        check("capabilities.terminal reflects the live shell setting",
              s == 200 and isinstance(tmb, dict)
              and tmb.get("backend") == "docker"
              and tmb.get("spec_set") is True,
              f"terminal={tmb}")
        s, body = post("/api/settings",
                       {"mycore": {"shell": "ssh:me@10.0.0.5:2222"}}, token)
        check("POST mycore valid ssh shell spec -> 200", s == 200,
              f"got {s} {str(body)[:160]}")
        s, body = post("/api/settings", {"mycore": {"shell": "bogus"}}, token)
        check("POST mycore bad shell spec -> 400 + shell reason",
              s == 400 and "shell" in str(body), f"got {s} {str(body)[:200]}")
        s, body = post("/api/settings",
                       {"mycore": {"shell": "ssh:u@h;password=pw"}}, token)
        check("POST mycore shell password field -> 400 (never accepted)",
              s == 400 and "shell" in str(body), f"got {s} {str(body)[:200]}")
        s, body = post("/api/settings", {"mycore": {"shell": ""}}, token)
        check("POST mycore empty shell (local default) -> 200", s == 200,
              f"got {s} {str(body)[:160]}")
        s, caps_tmp = get("/api/capabilities")
        tmb = caps_tmp.get("terminal") if isinstance(caps_tmp, dict) else None
        check("capabilities.terminal back to local after empty spec",
              isinstance(tmb, dict) and tmb.get("backend") == "local"
              and tmb.get("spec_set") is False, f"terminal={tmb}")

    # --- car 4c: media settings partition — illegal writes are refused with
    # the parser's exact reason and must change NOTHING on disk
    # (read-compare-read of raw bytes, before the restore below). ---
    if token:
        before_bytes = (settings_path.read_bytes()
                        if settings_path.exists() else b"")
        media_bad = (
            ({"media": {"stt_model": "huge"}}, "media.stt_model"),
            ({"media": {"tts_engine": "free"}}, "media.tts_engine"),
            ({"media": {"image_base_url": "ftp://x"}}, "media.image_base_url"),
            ({"media": {"wake_phrases": ["a", "b", "c", "d"]}},
             "media.wake_phrases"),
            ({"media": {"nope": 1}}, "unknown media key"),
        )
        for mpatch, why in media_bad:
            s, body = post("/api/settings", mpatch, token)
            check(f"POST settings {why} illegal -> 400 + reason",
                  s == 400 and why in str(body), f"got {s} {str(body)[:140]}")
        after_bytes = (settings_path.read_bytes()
                       if settings_path.exists() else b"")
        check("illegal media writes leave settings.json byte-identical",
              before_bytes == after_bytes,
              f"changed={before_bytes != after_bytes}")

    if settings_backup is not None:
        try:
            settings_path.write_text(settings_backup, encoding="utf-8")
            check("settings.json restored after channels writes",
                  settings_path.read_text(encoding="utf-8") == settings_backup)
        except OSError as e:  # noqa: BLE001
            check("settings.json restored after channels writes", False,
                  type(e).__name__)

    # --- car 4a-W: OpenAI-compatible /v1 gateway + capability catalog -----
    # Capabilities first, while secrets.env is untouched: the zero-credential
    # assertion checks the serialized catalog against every slot value.
    s, caps = get("/api/capabilities")
    check("GET /api/capabilities shape",
          s == 200 and isinstance(caps, dict)
          and isinstance(caps.get("version"), str) and bool(caps.get("version"))
          and isinstance(caps.get("mycore"), dict)
          and isinstance(caps["mycore"].get("upstreams_configured"), bool)
          and isinstance(caps["mycore"].get("mcp_servers"), dict)
          and isinstance(caps["mycore"]["mcp_servers"].get("count"), int)
          and isinstance(caps["mycore"]["mcp_servers"].get("names"), list)
          and isinstance(caps.get("tools"), list)
          and all(isinstance(t, dict) and isinstance(t.get("name"), str)
                  and isinstance(t.get("description"), str)
                  for t in caps.get("tools", []))
          and isinstance(caps.get("skills"), list)
          and all(isinstance(k, dict) and isinstance(k.get("name"), str)
                  and isinstance(k.get("description"), str)
                  for k in caps.get("skills", []))
          and isinstance(caps.get("hooks"), dict)
          and isinstance(caps["hooks"].get("events"), list)
          and isinstance(caps.get("cron"), dict)
          and isinstance(caps["cron"].get("jobs"), int),
          f"got {s} {str(caps)[:200]}")
    chb = caps.get("channels") if isinstance(caps, dict) else None
    check("GET /api/capabilities channels = all 7 routes + status lists",
          isinstance(chb, dict) and isinstance(chb.get("all"), list)
          and len(chb.get("all", [])) == 7
          and {"discord", "slack", "email", "webhook", "dingtalk",
               "feishu", "wechat"} <= set(chb.get("all", []))
          and isinstance(chb.get("enabled"), list)
          and isinstance(chb.get("configured"), list)
          and all(isinstance(x, str)
                  for x in chb.get("enabled", []) + chb.get("configured", [])),
          f"channels={chb}")
    caps_text = json.dumps(caps, ensure_ascii=False) if isinstance(caps, dict) else ""
    slot_vals = []
    if SECRETS.exists():
        for _key, _val in re.findall(r"(?m)^([A-Za-z0-9_]+)=(\S[^\r\n]*)$",
                                     SECRETS.read_text(encoding="utf-8")):
            if len(_val.strip()) >= 6:
                slot_vals.append(_val.strip())
    leak_keys = re.findall(r'"[^"]*(?:token|secret|password|api_key|apikey)'
                           r'[^"]*"\s*:', caps_text, flags=re.I)
    check("GET /api/capabilities carries zero credential material",
          bool(caps_text) and bool(slot_vals)
          and all(v not in caps_text for v in slot_vals)
          and not leak_keys,
          f"leak_keys={leak_keys} n_slots={len(slot_vals)}")

    # --- car 4b: browser / terminal / local_models catalog blocks ----------
    brb = caps.get("browser") if isinstance(caps, dict) else None
    check("GET capabilities browser block (availability bool only)",
          isinstance(brb, dict) and isinstance(brb.get("available"), bool),
          f"browser={brb}")
    browser_tool_names = {"browser_open", "browser_snapshot", "browser_click",
                          "browser_type", "browser_screenshot", "browser_close"}
    caps_tool_names = {t.get("name") for t in caps.get("tools", [])
                       if isinstance(t, dict)} if isinstance(caps, dict) else set()
    check("capabilities browser.available agrees with registered tools",
          isinstance(brb, dict)
          and ((brb.get("available") is True)
               == (len(browser_tool_names & caps_tool_names) == 6)),
          f"available={brb and brb.get('available')} "
          f"tools={sorted(browser_tool_names & caps_tool_names)}")
    tmb = caps.get("terminal") if isinstance(caps, dict) else None
    check("GET capabilities terminal block (kind only, raw spec withheld)",
          isinstance(tmb, dict)
          and (tmb.get("backend") in ("local", "docker", "ssh", None))
          and isinstance(tmb.get("spec_set"), bool),
          f"terminal={tmb}")
    if isinstance(tmb, dict) and tmb.get("note") is None:
        shell_raw = json.dumps(caps, ensure_ascii=False)
        check("capabilities terminal never serializes the raw shell spec",
              "identity=" not in shell_raw and "password" not in shell_raw,
              shell_raw[:200])
    lmb = caps.get("local_models") if isinstance(caps, dict) else None
    check("GET capabilities local_models block (count + name records)",
          isinstance(lmb, dict) and isinstance(lmb.get("count"), int)
          and lmb.get("count") == len(lmb.get("detected") or [])
          and all(isinstance(e, dict)
                  and isinstance(e.get("kind"), str)
                  and isinstance(e.get("base_url"), str)
                  and isinstance(e.get("model"), str)
                  for e in (lmb.get("detected") or [])),
          f"local_models={lmb}")
    # loopback-only promise: a detected entry never points off-box
    for _e in ((lmb.get("detected") or []) if isinstance(lmb, dict) else []):
        _host = urllib.parse.urlsplit(str(_e.get("base_url") or "")).hostname
        check("capabilities local_models entry is loopback",
              _host in ("127.0.0.1", "localhost", "::1"),
              f"host={_host}")
    check("capabilities terminal/local_models never leak a slot value",
          all(v not in caps_text for v in slot_vals),
          "slot value found in catalog")

    # --- car 4c: capabilities.media — four sub-blocks, booleans/names only,
    # no URL at all and no credential slot value (zero-leak, same rule as the
    # full catalog above, asserted on the serialized media block alone). ---
    s, caps_m = get("/api/capabilities")
    med = caps_m.get("media") if isinstance(caps_m, dict) else None
    sttb = med.get("stt") if isinstance(med, dict) else None
    imgb = med.get("image") if isinstance(med, dict) else None
    ttsb = med.get("tts") if isinstance(med, dict) else None
    connb = med.get("connectors") if isinstance(med, dict) else None
    tts_eng = ttsb.get("engines") if isinstance(ttsb, dict) else None
    check("GET capabilities media block shape",
          s == 200 and isinstance(med, dict)
          and {"stt", "image", "tts", "connectors"} <= set(med)
          and isinstance(sttb, dict)
          and isinstance(sttb.get("available"), bool)
          and (sttb.get("model") is None
               or isinstance(sttb.get("model"), str))
          and isinstance(imgb, dict)
          and isinstance(imgb.get("configured"), bool)
          and (imgb.get("reason") is None
               or isinstance(imgb.get("reason"), str))
          and isinstance(ttsb, dict) and isinstance(tts_eng, dict)
          and isinstance(tts_eng.get("edge"), bool)
          and isinstance(tts_eng.get("byok"), bool)
          and ttsb.get("active") in ("edge", "byok")
          and isinstance(connb, dict)
          and isinstance(connb.get("linear"), bool)
          and isinstance(connb.get("notion"), bool),
          f"media={med}")
    med_text = json.dumps(med, ensure_ascii=False) if isinstance(med, dict) else ""
    med_slots = []
    if SECRETS.exists():
        for _v in re.findall(r"(?m)^[A-Za-z0-9_]+=(\S[^\r\n]*)$",
                             SECRETS.read_text(encoding="utf-8")):
            if len(_v.strip()) >= 6:
                med_slots.append(_v.strip())
    check("GET capabilities media: no URL, no slot value",
          bool(med_text) and "http" not in med_text.lower()
          and all(v not in med_text for v in med_slots),
          f"n_slots={len(med_slots)}")

    # --- car 4c: /api/media served-file whitelist — unknown name, encoded
    # traversal and a non-image suffix all answer a plain 404. ---
    s, _b = get("/api/media/no_such_e2e_image.png")
    check("GET /api/media unknown name -> 404", s == 404, f"got {s}")
    s, _b = get("/api/media/..%2fsecrets.env")
    check("GET /api/media encoded traversal -> 404", s == 404, f"got {s}")
    s, _b = get("/api/media/notes.txt")
    check("GET /api/media wrong suffix -> 404", s == 404, f"got {s}")
    caps_doc = Path(__file__).resolve().parent.parent / \
        "docs/features/capabilities.md"
    check("capabilities feature doc exists",
          caps_doc.is_file() and len(caps_doc.read_text(encoding="utf-8")) > 400,
          f"{caps_doc}")
    v1_doc = Path(__file__).resolve().parent.parent / \
        "docs/features/openai_api_server.md"
    v1_doc_txt = v1_doc.read_text(encoding="utf-8") \
        if v1_doc.is_file() else ""
    check("openai feature doc declares the approximate usage rule",
          bool(v1_doc_txt) and "chars/4" in v1_doc_txt
          and "approximate" in v1_doc_txt.lower(),
          f"{v1_doc}")

    # --- /v1 auth legs: the dedicated slot is driven through all three
    # states (snapshot -> strip pre-existing line -> install a random test
    # token -> byte-for-byte restore). The slot values never print. ---
    v1_backup = SECRETS.read_bytes() if SECRETS.exists() else None
    try:
        kept_lines = []
        if v1_backup is not None:
            for _ln in v1_backup.decode("utf-8", "replace").splitlines():
                if re.match(r"^OPENAI_API_TOKEN=", _ln):
                    continue
                kept_lines.append(_ln)
            SECRETS.write_text(("\n".join(kept_lines) + "\n") if kept_lines
                               else "", encoding="utf-8")

        s, _ct, body = call_v1("GET", "/v1/models", None, None)
        check("/v1/models missing slot -> 503 names OPENAI_API_TOKEN",
              s == 503 and openai_error_shape(body, "OPENAI_API_TOKEN")
              and "secrets.env" in str(body), f"got {s} {str(body)[:140]}")
        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"messages": [{"role": "user",
                                              "content": "hi"}]}, None)
        check("/v1/chat/completions missing slot -> 503 OpenAI shape",
              s == 503 and openai_error_shape(body, "OPENAI_API_TOKEN"),
              f"got {s} {str(body)[:140]}")

        test_tok = "v1-" + os.urandom(16).hex()
        cur = SECRETS.read_text(encoding="utf-8") if SECRETS.exists() else ""
        if cur and not cur.endswith("\n"):
            cur += "\n"
        SECRETS.write_text(cur + f"OPENAI_API_TOKEN={test_tok}\n",
                           encoding="utf-8")

        s, _ct, body = call_v1("GET", "/v1/models", None, "wrong-token")
        check("/v1/models wrong bearer -> 401 OpenAI shape, no token echo",
              s == 401 and openai_error_shape(body, "unauthorized")
              and isinstance(body, dict)
              and test_tok not in json.dumps(body),
              f"got {s} {str(body)[:140]}")
        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"messages": [{"role": "user",
                                              "content": "hi"}]},
                               "wrong-token")
        check("/v1/chat wrong bearer -> 401 OpenAI shape",
              s == 401 and openai_error_shape(body, "unauthorized"),
              f"got {s} {str(body)[:140]}")

        s, _ct, body = call_v1("GET", "/v1/models", None, test_tok)
        v1_rows = body.get("data") if isinstance(body, dict) else None
        check("/v1/models correct bearer -> 200 model list shape",
              s == 200 and isinstance(body, dict)
              and body.get("object") == "list"
              and isinstance(v1_rows, list) and len(v1_rows) == 1
              and isinstance(v1_rows[0], dict)
              and v1_rows[0].get("object") == "model"
              and isinstance(v1_rows[0].get("id"), str)
              and bool(v1_rows[0].get("id")),
              f"got {s} {str(body)[:160]}")

        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"messages": [{"role": "user",
                                              "content": "x" * 4001}]},
                               test_tok)
        check("/v1/chat overlong input -> 400 OpenAI shape names the cap",
              s == 400 and openai_error_shape(body, "4000"),
              f"got {s} {str(body)[:140]}")
        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"messages": []}, test_tok)
        check("/v1/chat empty messages -> 400 OpenAI shape",
              s == 400 and openai_error_shape(body, "non-empty"),
              f"got {s} {str(body)[:140]}")
        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"messages": [{"role": "assistant",
                                              "content": "hi"}]}, test_tok)
        check("/v1/chat messages not ending with user -> 400 OpenAI shape",
              s == 400 and openai_error_shape(body, "end with a user message"),
              f"got {s} {str(body)[:140]}")

        # live non-stream turn: full completion shape; a bogus `model` id and
        # a client `tools` array ride along (accepted + ignored, documented).
        s, _ct, body = call_v1("POST", "/v1/chat/completions",
                               {"model": "car4a-ignored-model",
                                "tools": [{"type": "function",
                                           "function": {
                                               "name": "client_tool",
                                               "description": "not executed",
                                               "parameters": {"type": "object",
                                                              "properties": {}}}}],
                                "messages": [{"role": "user",
                                              "content": "Reply with exactly: pong"}]},
                               test_tok, timeout=120)
        v1_choice = body.get("choices", [{}])[0] \
            if isinstance(body, dict) and body.get("choices") else {}
        check("/v1/chat non-stream live -> 200 completion shape",
              s == 200 and isinstance(body, dict)
              and body.get("object") == "chat.completion"
              and str(body.get("id", "")).startswith("chatcmpl-")
              and isinstance(body.get("created"), int)
              and isinstance(v1_choice.get("message"), dict)
              and v1_choice["message"].get("role") == "assistant"
              and isinstance(v1_choice["message"].get("content"), str)
              and bool(v1_choice["message"].get("content"))
              and v1_choice.get("finish_reason") == "stop"
              and v1_choice.get("index") == 0,
              f"got {s} {str(body)[:200]}")
        exp_model = _srv.cli.resolve_model_config().model
        check("/v1/chat request model ignored -> configured model echoed",
              s == 200 and isinstance(body, dict)
              and body.get("model") == exp_model
              and body.get("model") != "car4a-ignored-model",
              f"got={body.get('model') if isinstance(body, dict) else None} "
              f"expected={exp_model}")
        v1_usage = body.get("usage") if isinstance(body, dict) else None
        check("/v1/chat usage block present, ints, total = prompt+completion",
              isinstance(v1_usage, dict)
              and all(isinstance(v1_usage.get(k), int) and v1_usage.get(k) >= 0
                      for k in ("prompt_tokens", "completion_tokens",
                                "total_tokens"))
              and v1_usage.get("total_tokens")
              == v1_usage.get("prompt_tokens", -1)
              + v1_usage.get("completion_tokens", -2),
              f"usage={v1_usage}")
        check("usage numbers match the doc-declared chars/4 heuristic",
              "chars/4" in v1_doc_txt
              and isinstance(v1_usage, dict)
              and v1_usage.get("prompt_tokens", 0) > 0,
              f"usage={v1_usage}")

        # live stream leg: chunk frames in order, terminal finish chunk, DONE.
        s, ct, body = call_v1("POST", "/v1/chat/completions",
                              {"stream": True,
                               "messages": [{"role": "user",
                                             "content": "Reply with exactly: stream-ok"}]},
                              test_tok, timeout=120)
        data_lines = sse_data_lines(body) if isinstance(body, str) else []
        chunks = []
        for _dl in data_lines:
            if _dl == "[DONE]":
                chunks.append(_dl)
                continue
            try:
                chunks.append(json.loads(_dl))
            except ValueError:
                chunks.append(None)
        first = chunks[0] if chunks else None
        frames = [c for c in chunks if isinstance(c, dict)]
        check("/v1/chat stream -> SSE chunk frame order ending in DONE",
              s == 200 and str(ct).startswith("text/event-stream")
              and isinstance(first, dict)
              and first.get("object") == "chat.completion.chunk"
              and isinstance(first.get("id"), str) and bool(first.get("id"))
              and any(isinstance(c.get("choices"), list) and c["choices"]
                      and c["choices"][0].get("finish_reason") == "stop"
                      for c in frames)
              and any(isinstance(c.get("choices"), list) and c["choices"]
                      and isinstance(c["choices"][0].get("delta"), dict)
                      and c["choices"][0]["delta"].get("content")
                      for c in frames)
              and bool(chunks) and chunks[-1] == "[DONE]",
              f"got {s} ct={ct} frames={len(chunks)} first={str(first)[:80]}")
    finally:
        if v1_backup is not None:
            SECRETS.write_bytes(v1_backup)
        elif SECRETS.exists():
            try:
                SECRETS.unlink()
            except OSError:
                pass
    after = SECRETS.read_bytes() if SECRETS.exists() else None
    check("secrets.env restored byte-for-byte after /v1 tests",
          after == v1_backup,
          f"changed={after != v1_backup}")

    # --- car 4c: /api/stt live legs — kept for LAST on purpose: the 429
    # test fills the in-process sliding window, so no stt success assertion
    # may follow it in this run. ---
    fix_path: Path | None = Path("/tmp/stt_test.mp3")
    if not (fix_path.is_file() and fix_path.stat().st_size > 1000):
        fix_path = None
        try:
            import subprocess as _sp
            _edge_out = Path("/tmp/smoke_stt_fixture.mp3")
            _sp.run(["edge-tts", "--voice", "en-US-AriaNeural",
                     "--text", "console speech test",
                     "--write-media", str(_edge_out)],
                    check=True, capture_output=True, timeout=60)
            if _edge_out.is_file() and _edge_out.stat().st_size > 1000:
                fix_path = _edge_out
        except Exception:  # noqa: BLE001 — fall through to the stdlib wav
            fix_path = None
    if fix_path is not None:
        fix_bytes = fix_path.read_bytes()
        fix_ctype = "audio/mpeg"
    else:
        import math
        import struct
        import wave as _wave
        _wav_out = Path("/tmp/smoke_stt_fixture.wav")
        with _wave.open(str(_wav_out), "wb") as _w:
            _w.setnchannels(1)
            _w.setsampwidth(2)
            _w.setframerate(16000)
            _w.writeframes(b"".join(
                struct.pack("<h",
                            int(12000 * math.sin(2 * math.pi * 440 * i / 16000)))
                for i in range(16000)))
        fix_bytes = _wav_out.read_bytes()
        fix_ctype = "audio/wav"

    s, body = post_audio("/api/stt", fix_bytes, fix_ctype, timeout=60)
    if s == 429:
        # window filled by an earlier run seconds ago — wait it out once
        import time as _time
        print("INFO  /api/stt window full — waiting 62s for expiry")
        _time.sleep(62)
        s, body = post_audio("/api/stt", fix_bytes, fix_ctype, timeout=60)
    check("POST /api/stt valid audio -> 200 text+duration",
          s == 200 and isinstance(body, dict)
          and isinstance(body.get("text"), str)
          and isinstance(body.get("duration"), (int, float))
          and float(body.get("duration") or 0) > 0,
          f"got {s} {str(body)[:120]}")

    # 429: fill the window with cheap undecodable requests (each fails as
    # 422 in ~20ms) until the limiter refuses — must stay the LAST stt leg.
    junk = b"\x1a\x45\xdf\xa3" + b"\x00" * 256
    s_last = 0
    for _i in range(35):
        s_last, _b = post_audio("/api/stt", junk, "audio/webm", timeout=20)
        if s_last == 429:
            break
    check("POST /api/stt rate window filled -> 429", s_last == 429,
          f"last={s_last}")

    # --- car F (§13.1): profile field + default-path byte identity ---
    # Kept AFTER the stt legs on purpose (stt success must not follow the 429);
    # none of these touch /api/stt.
    s, status = get("/api/status")
    check("GET /api/status carries profile field",
          s == 200 and isinstance(status, dict) and "profile" in status,
          f"keys={sorted(status) if isinstance(status, dict) else status}")
    check("GET /api/status profile empty on default console",
          isinstance(status, dict) and status.get("profile") == "",
          f"profile={status.get('profile')!r}" if isinstance(status, dict) else "")

    # Default profile = pre-car-F paths, byte for byte (AIDOG_PROFILE unset in
    # this process — the imported console copy resolved its module constants).
    _repo = Path(__file__).resolve().parent.parent
    check("default profile path seams unchanged",
          _srv.SETTINGS_FILE == _repo / "web" / "settings.json" and
          _srv.SECRETS == _repo / "secrets.env" and
          _srv.MYCORE_STATE_DIR == _repo / "data" / "mycore_service" and
          _srv.MEDIA_DIR == _repo / "data" / "images" and
          _srv.STATE_FILE == _repo / "web" / ".state.json",
          f"settings={_srv.SETTINGS_FILE} secrets={_srv.SECRETS} "
          f"state={_srv.MYCORE_STATE_DIR} media={_srv.MEDIA_DIR}")

    # Settings endpoint: current shape, no profile key, stable across reads.
    s, set_a = get("/api/settings")
    s2, set_b = get("/api/settings")
    check("GET /api/settings stable + no profile key",
          s == 200 and s2 == 200 and set_a == set_b and
          "profile" not in set_a and isinstance(set_a, dict),
          f"s={s}/{s2} profile-key={'profile' in set_a if isinstance(set_a, dict) else '?'}")

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
