"""mycore.leash — restraint controls (car 7, D11): cross-process STOP/pause
state plus the global spend guardrails gate. (The Restraint trait.)

Two surfaces, one module:

1. CONTROL STATE — `<state>/leash.json` (profile seam `pp.state_dir`, so the
   console process and the mycore service read the SAME file):

       {"paused": bool, "reason": str, "since": float,
        "stop_seq": int, "stop_ts": float}

   * `paused` — the pause switch (owner ruling 2026-10-30: new turns are
     refused with an honest line until a HUMAN resumes; fail-closed, no
     process killed, nothing turned readonly). A MISSING file means
     "running" (fresh install); an UNREADABLE file fails CLOSED (reads as
     paused) and `resume()` rewrites the file, so one human click always
     recovers.
   * `stop_seq` — a monotonically increasing STOP counter. A turn captures
     the baseline at entry; the guards in `loop.run_turn(should_stop=...)`
     abort when the counter moves past it — a STOP only kills turns that
     were already running, never a later one.
   Reads are TTL-cached (~0.25s) because the streaming delta guard calls
   them at token frequency; writes invalidate the cache in-process.

2. GUARDRAILS GATE — settings block `guardrails = {usd_day, token_day}`
   (both 0 = off). Checked at every NEW-turn entry point (console chat, TG
   pipeline, cron prompt turn, channel webhook, v1 gateway) and around the
   service loop's automatic legs; `gate()` answers (ok, code, ctx) with
   code in {"", "paused", "budget_usd", "budget_tokens"}.
   Data source: `mycore.meter` (today = UTC day) + `usage.model_pricing`
   (CNY per 1M). USD conversion resolves, in order: `settings.fx_rate` when
   the display currency is USD (manual, offline-safe) -> a shared
   `<state>/fx_usd.json` cache (6h positive / 5min negative TTL, at most one
   network fetch per window) -> honest `unknown` (does NOT block and never
   renders as OK — the token guard still applies). `usd_day = 0` never
   touches the network.

Honesty notes:
  * meter inactive (nothing measured today) -> budget states are "unknown",
    never "ok" and never blocking — an instrumentation gap must not read as
    a pass, and must not brick the product either.
  * a turn aborted mid-stream leaves that partial call un-metered (the
    provider billed it, our row is dropped with the abort) — an accepted,
    documented undercount on manual STOP only.
  * budget day = UTC day (meter file names), reset 00:00 UTC.

Owner rulings (binding): STOP = stop the current turn + a resumable
"pause automatic actions" switch (fail-closed resume); budget = USD-primary
+ a loose token guard, over-limit refuses NEW turns with an honest message.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
from lib import profile_paths as pp  # noqa: E402  (profile path seam)

# ----------------------------------------------------------------- state ---

STATE_FILE_NAME = "leash.json"
FX_FILE_NAME = "fx_usd.json"

READ_TTL = 0.25            # seconds — delta-guard cache window
FX_TTL = 6 * 3600          # positive FX cache (mirrors display_fx)
FX_NEG_TTL = 300           # negative FX cache (a dead endpoint stays cheap)
FX_TIMEOUT = 4             # seconds; one fetch per window, never per turn

_cache_lock = threading.Lock()
_cache: dict = {"ts": 0.0, "doc": None}
_fx_lock = threading.Lock()


def state_path() -> Path:
    return pp.state_dir(REPO) / STATE_FILE_NAME


def _default() -> dict:
    return {"paused": False, "reason": "", "since": 0.0,
            "stop_seq": 0, "stop_ts": 0.0}


def _normalize(doc: object) -> dict:
    """Any dict -> the full shape with coerced types (partial writes tolerated)."""
    out = _default()
    if isinstance(doc, dict):
        out["paused"] = bool(doc.get("paused", False))
        reason = doc.get("reason")
        out["reason"] = str(reason)[:200] if reason else ""
        try:
            out["since"] = float(doc.get("since") or 0.0)
        except (TypeError, ValueError):
            pass
        seq = doc.get("stop_seq")
        out["stop_seq"] = seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else 0
        try:
            out["stop_ts"] = float(doc.get("stop_ts") or 0.0)
        except (TypeError, ValueError):
            pass
    return out


def _read_uncached() -> dict:
    """Missing file = running (fresh install). Unreadable/corrupt = PAUSED
    (fail-closed) with an honest reason — `resume()` rewrites the file, so a
    human always recovers with one click."""
    path = state_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _default()
    except OSError:
        doc = _default()
        doc.update(paused=True, reason="control state unreadable (fail-closed)")
        return doc
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        doc = _default()
        doc.update(paused=True, reason="control state corrupt (fail-closed)")
        return doc
    return _normalize(parsed)


def read_state(ttl: float = READ_TTL) -> dict:
    """Fresh-enough control state (TTL cache for the streaming guard)."""
    now = time.monotonic()
    with _cache_lock:
        if _cache["doc"] is not None and now - _cache["ts"] < ttl:
            return dict(_cache["doc"])
    doc = _read_uncached()
    with _cache_lock:
        _cache["ts"] = now
        _cache["doc"] = doc
    return dict(doc)


def _write(doc: dict) -> dict:
    """Atomic persist (tmp + os.replace) + in-process cache refresh.
    A write failure propagates as OSError — the caller answers honestly."""
    doc = _normalize(doc)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    with _cache_lock:
        _cache["ts"] = time.monotonic()
        _cache["doc"] = doc
    return dict(doc)


def set_paused(paused: bool, reason: str = "") -> dict:
    doc = read_state()
    doc["paused"] = bool(paused)
    doc["reason"] = (str(reason)[:200] if paused and reason else "")
    doc["since"] = time.time()
    return _write(doc)


def pause(reason: str = "manual") -> dict:
    return set_paused(True, reason)


def resume() -> dict:
    """Human action only (fail-closed): this is the one click that clears a
    pause AND rewrites a corrupt state file back to a valid one."""
    return set_paused(False, "")


def stop_baseline() -> int:
    """Capture BEFORE a turn starts: only a later request_stop() aborts it."""
    return int(read_state().get("stop_seq") or 0)


def request_stop() -> dict:
    """STOP = move the counter. Returns the fresh state."""
    doc = read_state()
    doc["stop_seq"] = int(doc.get("stop_seq") or 0) + 1
    doc["stop_ts"] = time.time()
    return _write(doc)


def stop_requested(baseline: int, ttl: float = READ_TTL) -> bool:
    """True when a STOP landed after `baseline` was captured."""
    try:
        return int(read_state(ttl=ttl).get("stop_seq") or 0) > int(baseline)
    except (TypeError, ValueError):
        return False


# ------------------------------------------------------------ guardrails ---

DEFAULT_GUARDRAILS = {"usd_day": 0.0, "token_day": 0}
USDay_MAX = 100000.0
TOKEN_DAY_MAX = 10_000_000_000


def validate_guardrails(doc: object) -> tuple[dict, list[str]]:
    """Grammar for the settings `guardrails` block (single source of truth
    for the console write path and load_settings). 0 = off (opt-in guard);
    bool is refused (it is not a number); values are bounded; the offending
    VALUE is never echoed."""
    if not isinstance(doc, dict):
        return {}, ["guardrails must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for key in doc:
        if key not in DEFAULT_GUARDRAILS:
            errors.append(f"unknown guardrails key: {key}")
    if "usd_day" in doc:
        val = doc["usd_day"]
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            errors.append("guardrails.usd_day must be a number (0 = off)")
        elif not 0 <= float(val) <= USDay_MAX:
            errors.append(f"guardrails.usd_day must be in 0..{USDay_MAX:g} (0 = off)")
        else:
            clean["usd_day"] = float(val)
    if "token_day" in doc:
        val = doc["token_day"]
        if isinstance(val, bool) or not isinstance(val, int):
            errors.append("guardrails.token_day must be an integer (0 = off)")
        elif not 0 <= val <= TOKEN_DAY_MAX:
            errors.append(f"guardrails.token_day must be in 0..{TOKEN_DAY_MAX} (0 = off)")
        else:
            clean["token_day"] = int(val)
    return clean, errors


def _guardrails(settings: dict | None) -> dict:
    """Validated view of the block; anything unusable collapses to 0 = off
    (a broken guard must never brick turns — it just stops guarding, and
    GET /api/control shows the zeros)."""
    raw = (settings or {}).get("guardrails")
    clean, _ = validate_guardrails(raw if raw is not None else DEFAULT_GUARDRAILS)
    out = dict(DEFAULT_GUARDRAILS)
    out.update(clean)
    return out


# --------------------------------------------------------------- fx (USD) --

def _fx_path() -> Path:
    return pp.state_dir(REPO) / FX_FILE_NAME


def _fx_cached() -> tuple[float | None, str]:
    try:
        doc = json.loads(_fx_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "none"
    age = time.time() - float(doc.get("ts") or 0)
    if doc.get("error"):
        return None, "cooling" if age < FX_NEG_TTL else "none"
    rate = doc.get("rate")
    if isinstance(rate, (int, float)) and not isinstance(rate, bool) \
            and rate > 0 and age < FX_TTL:
        return float(rate), "cache"
    return None, "none"


def _fx_store(payload: dict) -> None:
    try:
        path = _fx_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass                     # cache is an optimization, never fatal


def usd_rate(settings: dict | None) -> tuple[float | None, str]:
    """CNY per 1 USD for budget conversion. Sources (offline-first):
    manual `settings.fx_rate` when the display currency is USD -> shared
    6h cache (one fetch per window across both processes) -> None."""
    settings = settings or {}
    manual = settings.get("fx_rate")
    cur = settings.get("currency")
    if cur == "USD" and isinstance(manual, (int, float)) \
            and not isinstance(manual, bool) and float(manual) > 0:
        return float(manual), "manual"
    rate, src = _fx_cached()
    if rate is not None:
        return rate, src
    if src == "cooling":
        return None, "cooling"
    with _fx_lock:
        rate, src = _fx_cached()          # another thread may have filled it
        if rate is not None or src == "cooling":
            return rate, src
        try:
            with urllib.request.urlopen(
                    "https://open.er-api.com/v6/latest/USD",
                    timeout=FX_TIMEOUT) as resp:
                doc = json.loads(resp.read().decode("utf-8", "replace"))
            val = float((doc.get("rates") or {}).get("CNY") or 0)
            if doc.get("result") == "success" and val > 0:
                _fx_store({"rate": round(val, 6), "ts": time.time()})
                return round(val, 6), "auto"
        except Exception:  # noqa: BLE001 — honest degradation, never fatal
            pass
        _fx_store({"error": True, "ts": time.time()})
    return None, "unavailable"


# --------------------------------------------------------------- budget ----

def budget_status(settings: dict | None, now: float | None = None) -> dict:
    """Today's (UTC) spend vs the guardrails -> the read model the console
    card and GET /api/control render. States: off | ok | over | unknown."""
    from . import meter                          # local: inert unless activated

    guard = _guardrails(settings)
    settings = settings or {}
    out: dict = {
        "limits": {"usd_day": guard["usd_day"],
                   "token_day": guard["token_day"]},
        "usd": {"limit": guard["usd_day"], "used": None, "state": "off",
                "rate": None, "rate_source": None},
        "tokens": {"limit": guard["token_day"], "used": None, "state": "off"},
        "day": "utc",
    }
    if guard["usd_day"] <= 0 and guard["token_day"] <= 0:
        return out                       # both off: no meter read, no network

    usage = meter.read(days=1)
    if not usage.get("available"):
        out["usd"]["state"] = "unknown"
        out["tokens"]["state"] = "unknown"
        out["reason"] = str(usage.get("reason") or "meter inactive")
        return out

    tokens = int(usage.get("in_tokens") or 0) + int(usage.get("out_tokens") or 0)
    out["tokens"]["used"] = tokens
    if guard["token_day"] > 0:
        out["tokens"]["state"] = "over" if tokens > guard["token_day"] else "ok"

    if guard["usd_day"] > 0:
        pricing = settings.get("usage", {}).get("model_pricing") \
            if isinstance(settings.get("usage"), dict) else None
        pricing = pricing if isinstance(pricing, dict) else {}
        cost = meter.estimate_cost(usage, pricing)
        cny = cost.get("total_cny")
        if cny is None:
            out["usd"]["state"] = "unknown"
            out["reason"] = "no price configured for today's models"
        else:
            rate, source = usd_rate(settings)
            out["usd"]["rate"] = rate
            out["usd"]["rate_source"] = source
            if rate is None:
                out["usd"]["state"] = "unknown"
                out["reason"] = f"usd rate unavailable ({source})"
            else:
                used = round(float(cny) / rate, 6)
                out["usd"]["used"] = used
                out["usd"]["state"] = "over" if used > guard["usd_day"] else "ok"
    return out


def gate(settings: dict | None = None, *, turns: bool = True,
         ttl: float = READ_TTL) -> tuple[bool, str, dict]:
    """The new-turn gate: (ok, code, ctx). code "" = allowed, else one of
    "paused" | "budget_usd" | "budget_tokens"; ctx carries the honest numbers
    for both the console detail line and the localized service reply.
    `turns=False` checks the PAUSE switch only — for the pure automatic legs
    (sniff sweep, evolution reflect) that spend no model tokens, so a budget
    hold never silences the senses while paused-state remains the owner's
    explicit kill switch."""
    state = read_state(ttl=ttl)
    if state.get("paused"):
        return False, "paused", {"reason": state.get("reason") or "",
                                 "since": state.get("since") or 0.0}
    if not turns:
        return True, "", {}
    status = budget_status(settings)
    limits = status["limits"]
    if limits["usd_day"] > 0:
        usd = status["usd"]
        if usd["state"] == "over":
            return False, "budget_usd", {
                "used": usd.get("used"), "limit": usd.get("limit"),
                "day": "utc"}
        if usd["state"] == "unknown":
            # unknown never blocks (documented) and never renders as OK —
            # the token guard below still applies.
            pass
    if limits["token_day"] > 0:
        tok = status["tokens"]
        if tok["state"] == "over":
            return False, "budget_tokens", {
                "used": tok.get("used"), "limit": tok.get("limit"),
                "day": "utc"}
    return True, "", {}


def block_detail(code: str, ctx: dict) -> str:
    """English detail line for HTTP responses (server-side messages stay
    English per the i18n policy; the TG leg localizes the same code)."""
    if code == "paused":
        reason = str(ctx.get("reason") or "").strip()
        base = "paused — resume from the console to accept new turns"
        return f"{base} ({reason})" if reason else base
    if code == "budget_usd":
        return (f"daily budget reached: "
                f"${float(ctx.get('used') or 0):.2f} / "
                f"${float(ctx.get('limit') or 0):.2f} — new turns are "
                f"refused until 00:00 UTC")
    if code == "budget_tokens":
        return (f"token guard reached: {int(ctx.get('used') or 0):,} / "
                f"{int(ctx.get('limit') or 0):,} today (UTC) — new turns "
                f"are refused until 00:00 UTC")
    return code
