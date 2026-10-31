#!/usr/bin/env python3
"""P3 web console smoke test — endpoints, auth gate, validation, idempotency.

Runs against a live console (default http://127.0.0.1:8790). The access token
is read in-process from secrets.env and never printed or written anywhere.
Exit code 0 = all assertions passed.
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
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

    print(f"\n{len(PASS)} pass · {len(FAIL)} fail")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
