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
