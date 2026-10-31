"""mycore.modeltier — multi-upstream failover policy (Tier C, v2 / car 4a-M1).

Closes the post-swap backlog item `mycore/modeltier`: the product must ship
gateway-grade endpoint failover BUILT IN (owner distribution requirement —
end users connect their own model API keys and run no local proxy).
Spec source: docs/model_access_tier.md (Tier C, five strategies).

What v2 ships (v1 scope kept verbatim, v2 adds the former "NOT in v1" list):
  (1) ordered failover chain: primary `mycore.base_url` first, then every
      `mycore.upstreams` entry on a failover-class error   [strategy 4+chain]
  (2) attempt discipline REVISED in v2: v1 attempted each ENDPOINT exactly
      once per call; the unit is now (endpoint, key slot) — each pool key is
      tried at most once per call, and a credential pool rotates on auth
      errors (below). Capacity/transport failures still fail the WHOLE
      endpoint at once (no key rotation for them — another key rarely fixes
      a 429/5xx on the same endpoint).
  (3) credential pool: an upstream's `key_env` may list several comma-
      separated env SLOT NAMES (`K1,K2,K3`); on 401/403 the call rotates to
      the next untried slot of the SAME endpoint, once per slot; pool
      exhausted = that endpoint failed -> failover class (next endpoint).
  (4) 2-way error routing: payload/protocol errors (HTTP 400/405/413/415/422)
      fail immediately because every endpoint would reject the same request;
      everything else (transport, 401/403/404/408/429/5xx, malformed
      response) is failover-class.
  (5) NO replay after the first streamed byte   [strategy 5]
  (6) per-key health + capacity backoff (v2): an IN-PROCESS map (never
      persisted — honest scope) counts consecutive failover-class failures
      per (endpoint, key slot); the 2nd consecutive failure starts a
      cooldown (30s -> 60s -> 120s -> ... capped at 300s). Cooling units are
      SKIPPED when picking candidates — but when the whole chain is cooling
      the LEAST cold unit is still attempted (never a silent total stop).
      One success clears failures, cooldown and escalation level.
      Surfaced via `HealthRegistry.note()` / `health_note()` and the cooling
      suffix in `ModelConfig.describe()` (URLs + slot NAMES only, zero key
      material — zero-echo rule holds).

Deliberately NOT in v2 (stated up front, do not build on assumptions):
  - cross-process health persistence (cooldown lives in this process only),
  - request queues / dual-tier timeouts (this product has no queue — the
    fail-fast chain is the design),
  - prompt-cache `cache_control` injection (we never send cache directives;
    we only pass provider-reported cache hits through — see model.py).

Config (precedence env MYCORE_UPSTREAMS > CONSOLE settings mycore.upstreams >
off), one line, comma-separated entries; a NEW entry starts only where a
comma is followed by an `http(s)://` scheme, so commas inside a pool are
entry data:

    url[|key_env[|model]] , url[|key_env[|model]] , ...

`key_env` names ENVIRONMENT VARIABLE(S) holding the key(s) for that upstream
— slot NAMES only, never values (zero-echo rule; values are read from the
environment per attempt inside ChatClient). `model` defaults to the primary
model when omitted.

Division of labor (stable across v1/v2): this module owns config parsing,
error classification and health/backoff POLICY STATE; the attempt loop lives
in mycore.model.ChatClient (single process, stdlib only).
English-only source (repo i18n policy).
"""
from __future__ import annotations

import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable

# Upstream spec limits (config knobs, generous but bounded).
MAX_UPSTREAMS = 4
MAX_SPEC_CHARS = 1024
MAX_KEY_SLOTS = 8              # credential-pool slots per endpoint (v2)

# Structured chain (v0.5.55 gateway car): settings `mycore.endpoints` entries.
# Unlike the one-line spec (operator-level trust, any env-var name), the
# console-managed form only references the CONSOLE model-key slots — a
# defense-in-depth boundary so a settings write can never aim a sensitive
# non-model slot (channel/bot/GH tokens) at an operator-chosen URL.
KEY_SLOT_ALLOW_RE = re.compile(r"^(?:MODEL_API_KEY|OPENAI_API_TOKEN|MODEL_UP_K[1-8])$")
# Global rate knobs (0 = off), sliding window per (endpoint, key slot).
MAX_RPM = 1000                 # requests per minute cap ceiling
MAX_TPM = 10_000_000           # tokens per minute cap ceiling
RATE_WINDOW_S = 60.0           # sliding window length
RATE_MAX_EVENTS = 4096         # per-ident event cap inside one window


# Payload/protocol errors: the SAME request would fail everywhere, so
# failing over would only multiply a client-side bug (routing 2-way, trimmed).
FATAL_HTTP = frozenset({400, 405, 413, 415, 422})
# Auth errors rotate the credential pool INSIDE the endpoint (v2): the key is
# wrong/revoked/quota-dead — another configured slot may work.
AUTH_HTTP = frozenset({401, 403})

# v2 health/backoff: the 2nd consecutive failover-class failure cools the
# (endpoint, key slot) pair — exponential from COOLDOWN_BASE_S, capped.
COOLDOWN_AFTER_FAILS = 2
COOLDOWN_BASE_S = 30.0
COOLDOWN_MAX_S = 300.0

_KEY_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
_HTTP_ERR_RE = re.compile(r"HTTP (\d{3})")
# A new entry starts only where a comma is followed by an http(s) scheme —
# commas inside a pool (`url|K1,K2|model`) are entry data, while every valid
# legacy entry (scheme-led) still splits exactly as before.
_ENTRY_SPLIT_RE = re.compile(r",\s*(?=(?:https?)://)", re.IGNORECASE)


@dataclass(frozen=True)
class Upstream:
    """One failover endpoint. `key_env` is an env-var NAME (never a value),
    optionally a comma-separated pool of slot names; empty = inherit the
    primary config's key slot(s); `model` empty = inherit."""
    base_url: str
    key_env: str = ""
    model: str = ""

    def key_slots(self) -> tuple[str, ...]:
        """Expanded credential pool as slot NAMES. () = inherit primary."""
        if not self.key_env:
            return ()
        return tuple(p for p in self.key_env.split(",") if p)


def parse_upstreams(spec: str) -> tuple[Upstream, ...]:
    """Parse the configured chain. Raises ValueError with a chat-safe reason
    (no secrets can be present — spec carries URLs, env names, model ids)."""
    if not isinstance(spec, str):
        raise ValueError("upstreams must be a string")
    spec = spec.strip()
    if len(spec) > MAX_SPEC_CHARS:
        raise ValueError(f"spec longer than {MAX_SPEC_CHARS} chars")
    if not spec:
        return ()
    entries = [e.strip() for e in _ENTRY_SPLIT_RE.split(spec)]
    if len(entries) > MAX_UPSTREAMS:
        raise ValueError(f"at most {MAX_UPSTREAMS} upstreams")
    out: list[Upstream] = []
    for i, entry in enumerate(entries, 1):
        if not entry:
            raise ValueError(f"entry {i} is empty")
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) > 3:
            raise ValueError(f"entry {i} has too many fields (url|key_env|model)")
        url = parts[0]
        if "," in url:
            # entry splitting never cuts inside a URL — a comma here means a
            # malformed entry (kept byte-compatible with the v1 parser)
            raise ValueError(f"entry {i}: bad url")
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            raise ValueError(f"entry {i}: bad url") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"entry {i}: url must be http(s) with a host")
        key_env = parts[1] if len(parts) > 1 else ""
        if key_env:
            slots = tuple(s for s in key_env.split(","))
            if len(slots) > MAX_KEY_SLOTS:
                raise ValueError(f"entry {i}: at most {MAX_KEY_SLOTS} key slots")
            for slot in slots:
                if not _KEY_ENV_RE.match(slot):
                    raise ValueError(f"entry {i}: key_env must be env var names "
                                     "(letters, digits, underscore)")
        model = parts[2] if len(parts) > 2 else ""
        if model and not _MODEL_RE.match(model):
            raise ValueError(f"entry {i}: bad model id")
        out.append(Upstream(base_url=url.rstrip("/"), key_env=key_env,
                            model=model))
    return tuple(out)


def parse_endpoints(raw: object) -> tuple[Upstream, ...]:
    """Parse settings `mycore.endpoints` (structured chain, v0.5.55 gateway
    car) into the SAME Upstream tuple parse_upstreams() builds — everything
    downstream (chain/units/failover/health) is unchanged.

    Grammar: a LIST of at most MAX_UPSTREAMS objects:
      {"url": <http(s) URL, required>, "model": <model id, optional>,
       "keys": [<slot name>, ...] (<= MAX_KEY_SLOTS, optional)}

    Keys reference console model-key SLOTS ONLY (KEY_SLOT_ALLOW_RE) — stricter
    than the one-line spec on purpose (see the constant's comment). Empty keys
    = inherit the primary key slot(s), same semantics as an empty key_env.
    Raises ValueError with chat-safe reasons: entry numbers and field names
    only, never a URL, slot value or model id from the input.
    """
    if not isinstance(raw, list):
        raise ValueError("endpoints must be a list")
    if len(raw) > MAX_UPSTREAMS:
        raise ValueError(f"at most {MAX_UPSTREAMS} endpoints")
    out: list[Upstream] = []
    for i, entry in enumerate(raw, 1):
        if not isinstance(entry, dict):
            raise ValueError(f"entry {i} must be an object")
        unknown = [k for k in entry if k not in ("url", "model", "keys")]
        if unknown:
            raise ValueError(f"entry {i}: unknown field")
        url = entry.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(f"entry {i}: url is required")
        url = url.strip()
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            raise ValueError(f"entry {i}: bad url") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"entry {i}: url must be http(s) with a host")
        model = entry.get("model", "")
        if model is None:
            model = ""
        if model and (not isinstance(model, str) or not _MODEL_RE.match(model)):
            raise ValueError(f"entry {i}: bad model id")
        keys = entry.get("keys", [])
        if keys is None:
            keys = []
        if not isinstance(keys, list):
            raise ValueError(f"entry {i}: keys must be a list")
        if len(keys) > MAX_KEY_SLOTS:
            raise ValueError(f"entry {i}: at most {MAX_KEY_SLOTS} key slots")
        seen: set[str] = set()
        for slot in keys:
            if not isinstance(slot, str) or not KEY_SLOT_ALLOW_RE.match(slot):
                raise ValueError(f"entry {i}: key slot not allowed")
            if slot in seen:
                raise ValueError(f"entry {i}: duplicate key slot")
            seen.add(slot)
        # every slot passed the isinstance+regex gate above, so keys is all-str
        out.append(Upstream(base_url=url.rstrip("/"),
                            key_env=",".join(keys), model=model))
    return tuple(out)


def is_failover_class(message: str) -> bool:
    """True when a ModelError text is worth the NEXT candidate (endpoint
    fail-fast: never this endpoint's remaining keys again). Payload/protocol
    errors are fatal — every endpoint would reject the identical request."""
    m = _HTTP_ERR_RE.search(message or "")
    if m and int(m.group(1)) in FATAL_HTTP:
        return False
    return True


def is_auth_class(message: str) -> bool:
    """True for 401/403 — the ONLY class that rotates the credential pool
    inside the same endpoint (v2 revision of the v1 exactly-once rule)."""
    m = _HTTP_ERR_RE.search(message or "")
    return bool(m and int(m.group(1)) in AUTH_HTTP)


class HealthRegistry:
    """In-process per-(endpoint, key slot) health with capacity backoff (v2).

    Deliberately NOT persisted across processes (stated scope): state lives
    in this map only and dies with the process. Keys are identity strings of
    the form `<base_url>|<slot name>` — slot NAMES, never values; `note()`
    output is safe to log (zero-echo).

    `now` is injectable so tests drive the cooldown clock deterministically.
    """
    __slots__ = ("_now", "_state")

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._state: dict[str, dict] = {}

    @staticmethod
    def identity(base_url: str, key_slot: str) -> str:
        """Stable identity: an unnamed key cools per-endpoint (slot empty)."""
        return f"{base_url}|{key_slot}"

    def _rec(self, ident: str) -> dict:
        rec = self._state.get(ident)
        if rec is None:
            rec = {"fails": 0, "episodes": 0, "until": 0.0}
            self._state[ident] = rec
        return rec

    def record_failure(self, ident: str) -> None:
        """One consecutive failover-class failure. The 2nd consecutive one
        starts a cooldown: 30s, doubling per episode, capped at 300s.
        Fatal (payload-class) errors are never recorded — they say nothing
        about endpoint health. Failures arriving DURING a cooldown count
        toward the next escalation but never stack on the running one."""
        rec = self._rec(ident)
        now = self._now()
        rec["fails"] += 1
        if rec["until"] > now:
            return                      # already cooling — no stacked episode
        if rec["fails"] >= COOLDOWN_AFTER_FAILS:
            delay = min(COOLDOWN_BASE_S * (2 ** rec["episodes"]),
                        COOLDOWN_MAX_S)
            rec["until"] = now + delay
            rec["episodes"] += 1
            rec["fails"] = 0

    def record_success(self, ident: str) -> None:
        """One success clears failures, cooldown and escalation level."""
        rec = self._rec(ident)
        rec["fails"] = 0
        rec["episodes"] = 0
        rec["until"] = 0.0

    def remaining(self, ident: str) -> float:
        """Seconds left in cooldown (0.0 = not cooling)."""
        rec = self._state.get(ident)
        if not rec:
            return 0.0
        return max(0.0, rec["until"] - self._now())

    def cooling(self, ident: str) -> bool:
        return self.remaining(ident) > 0.0

    def observed(self, ident: str) -> bool:
        """True when this unit has EVER been noted (a record exists).
        Distinguishes 'never seen' (honest null upstream) from 'fine right
        now' — remaining()/cooling() alone cannot (both read 0.0/False)."""
        return ident in self._state

    def note(self, idents: tuple[str, ...] | list[str] | None = None) -> str:
        """Safe-to-log cooling summary: endpoints + slot names, never values.
        `idents` filters to one config's units (describe()); None = all."""
        if idents is None:
            selected = sorted(self._state)
        else:
            selected = [i for i in idents if i in self._state]
        parts = [f"{ident} {self.remaining(ident):.0f}s" for ident in selected
                 if self.remaining(ident) > 0.0]
        return "cooling: " + "; ".join(parts) if parts else ""

    def reset(self) -> None:
        self._state.clear()


# Process-wide registry (surfaces in describe()/health_note()). Tests that
# need isolation inject their own HealthRegistry into ChatClient.
SHARED_HEALTH = HealthRegistry()


def health_note(idents: tuple[str, ...] | list[str] | None = None) -> str:
    """Cooling state of the process-shared registry (empty = nothing cooling)."""
    return SHARED_HEALTH.note(idents)


def reset_health() -> None:
    """Clear the process-shared registry (test isolation / full reset)."""
    SHARED_HEALTH.reset()


class RotationState:
    """Round-robin cursor over the candidate unit list (v0.5.55 gateway car).

    In-process only (same honest scope as HealthRegistry — no cross-process
    persistence). `next(span)` returns the starting offset for ONE call and
    advances the cursor, so consecutive calls walk the chain instead of always
    hammering the primary. Thread-safe: the chat service rotates from multiple
    worker threads. Tests inject their own instance (or seed `at`).
    """
    __slots__ = ("_lock", "_at")

    def __init__(self, at: int = 0) -> None:
        import threading
        self._lock = threading.Lock()
        self._at = int(at) % 1_000_003

    def next(self, span: int) -> int:
        """Offset in [0, span) for this call; advances the shared cursor."""
        if span <= 1:
            return 0
        with self._lock:
            off = self._at % span
            self._at = (self._at + 1) % 1_000_003
            return off

    def reset(self, at: int = 0) -> None:
        with self._lock:
            self._at = int(at) % 1_000_003


SHARED_ROTATION = RotationState()


class RateRegistry:
    """In-process sliding-window rate limiter per (endpoint, key slot)
    (v0.5.55 gateway car; mirrors HealthRegistry's injection style).

    Two independent windows per ident, both 60s sliding:
      * requests:  record_request(ident) once per ATTEMPT start;
      * tokens:    record_tokens(ident, n) with the provider-reported usage
                   of a successful call (honest: never estimated).
    Thresholds (max_rpm / max_tpm, 0 = off) are passed in per check from
    ModelConfig — the registry stores state only, no config.
    `limited()` gates candidates; when EVERY unit is limited the caller must
    fall back to the LEAST limited one (never a silent total stop — same rule
    as the cooldown path). Events are pruned on access and capped per ident
    (RATE_MAX_EVENTS) so a hot window cannot grow unboundedly.
    """
    __slots__ = ("_now", "_state")

    def __init__(self, now: Callable[[], float] | None = None) -> None:
        self._now = now or time.monotonic
        self._state: dict[str, dict] = {}

    def _rec(self, ident: str) -> dict:
        rec = self._state.get(ident)
        if rec is None:
            rec = {"reqs": [], "toks": []}
            self._state[ident] = rec
        return rec

    def _prune(self, rec: dict) -> None:
        # events are appended in monotonic order: prune when the OLDEST one
        # has left the window (checking the head, not the tail — the tail is
        # the newest event and stays fresh while old heads linger).
        cut = self._now() - RATE_WINDOW_S
        if rec["reqs"] and rec["reqs"][0] < cut:
            rec["reqs"] = [t for t in rec["reqs"] if t >= cut]
        if rec["toks"] and rec["toks"][0][0] < cut:
            rec["toks"] = [e for e in rec["toks"] if e[0] >= cut]

    def record_request(self, ident: str) -> None:
        """One attempt against this unit starts now (counts toward max_rpm)."""
        rec = self._rec(ident)
        self._prune(rec)
        rec["reqs"].append(self._now())
        if len(rec["reqs"]) > RATE_MAX_EVENTS:
            del rec["reqs"][: len(rec["reqs"]) - RATE_MAX_EVENTS]

    def record_tokens(self, ident: str, n: int) -> None:
        """Provider-reported tokens of a finished call (toward max_tpm)."""
        if n <= 0:
            return
        rec = self._rec(ident)
        self._prune(rec)
        rec["toks"].append((self._now(), int(n)))
        if len(rec["toks"]) > RATE_MAX_EVENTS:
            del rec["toks"][: len(rec["toks"]) - RATE_MAX_EVENTS]

    def counts(self, ident: str) -> tuple[int, int]:
        """(requests, tokens) inside the current window."""
        rec = self._state.get(ident)
        if rec is None:
            return (0, 0)
        self._prune(rec)
        return (len(rec["reqs"]), sum(n for _, n in rec["toks"]))

    def limited(self, ident: str, max_rpm: int, max_tpm: int) -> bool:
        """True when this unit is AT/over an active cap for the window."""
        reqs, toks = self.counts(ident)
        if max_rpm > 0 and reqs >= max_rpm:
            return True
        return bool(max_tpm > 0 and toks >= max_tpm)

    def pressure(self, ident: str, max_rpm: int, max_tpm: int) -> float:
        """0.0..1+ over-limit ratio for the least-limited fallback pick."""
        reqs, toks = self.counts(ident)
        p = 0.0
        if max_rpm > 0:
            p = max(p, reqs / max_rpm)
        if max_tpm > 0:
            p = max(p, toks / max_tpm)
        return p

    def reset(self) -> None:
        self._state.clear()


SHARED_RATE = RateRegistry()


def reset_rate() -> None:
    """Clear the process-shared rate windows (test isolation / full reset)."""
    SHARED_RATE.reset()
