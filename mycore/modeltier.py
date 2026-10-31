"""mycore.modeltier — multi-upstream failover policy (Tier C v1, car 1-D4).

Closes the post-swap backlog item `mycore/modeltier`: the product must ship
gateway-grade endpoint failover BUILT IN (owner 2026-10-17 distribution
requirement — end users connect their own model API keys and run no local
proxy). Spec source: docs/model_access_tier.md (Tier C, five strategies).

What v1 ships (and what it deliberately does NOT — honest scope line):
  ① ordered failover chain: primary `mycore.base_url` first, then every
     `mycore.upstreams` entry on a failover-class error   [strategy ④+chain]
  ② same-URL fail-fast: an upstream is attempted exactly once per call —
     never retried inside the same turn                    [strategy ④]
  ③ 4-way routing trimmed to a 2-way split: payload/protocol errors
     (HTTP 400/405/413/415/422) fail immediately because every upstream
     would reject the same request; everything else (transport, 401/403/
     404/408/429/5xx, malformed response) is failover-class.
     NOT in v1: queue dual-tier timeouts (30s/300s), capacity backoff,
     per-key health scoring — those need a real multi-provider fleet to
     tune; the local gateway still provides the key pool behind the
     primary endpoint (Tier A remains the default topology).
  ④ NO replay after the first streamed byte: once any delta reached the
     caller, a mid-stream failure raises — never re-runs the model and
     duplicates output                                      [strategy ⑤]

Config (precedence env MYCORE_UPSTREAMS > CONSOLE settings mycore.upstreams >
off), one line, comma-separated entries:

    url[|key_env[|model]] , url[|key_env[|model]] , ...

`key_env` names an ENVIRONMENT VARIABLE holding the key for that upstream —
the credential pool stores slot names, never values (zero-echo rule; the key
is read from the environment per attempt inside ChatClient). `model` defaults
to the primary model when omitted.

This module owns config parsing + error classification only; the attempt
loop lives in mycore.model.ChatClient (single process, stdlib only).
English-only source (repo i18n policy).
"""
from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

# Upstream spec limits (config knobs, generous but bounded).
MAX_UPSTREAMS = 4
MAX_SPEC_CHARS = 1024

# Payload/protocol errors: the SAME request would fail everywhere, so
# failing over would only multiply a client-side bug (strategy ③, trimmed).
FATAL_HTTP = frozenset({400, 405, 413, 415, 422})

_KEY_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}$")
_HTTP_ERR_RE = re.compile(r"HTTP (\d{3})")


@dataclass(frozen=True)
class Upstream:
    """One failover endpoint. `key_env` is an env-var NAME (never a value);
    empty = inherit the primary config's key slot; `model` empty = inherit."""
    base_url: str
    key_env: str = ""
    model: str = ""


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
    entries = [e.strip() for e in spec.split(",")]
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
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            raise ValueError(f"entry {i}: bad url") from None
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"entry {i}: url must be http(s) with a host")
        key_env = parts[1] if len(parts) > 1 else ""
        if key_env and not _KEY_ENV_RE.match(key_env):
            raise ValueError(f"entry {i}: key_env must be an env var name "
                             "(letters, digits, underscore)")
        model = parts[2] if len(parts) > 2 else ""
        if model and not _MODEL_RE.match(model):
            raise ValueError(f"entry {i}: bad model id")
        out.append(Upstream(base_url=url.rstrip("/"), key_env=key_env,
                            model=model))
    return tuple(out)


def is_failover_class(message: str) -> bool:
    """True when a ModelError text is worth the NEXT upstream (same-URL
    fail-fast: never this one again). Payload/protocol errors are fatal —
    every upstream would reject the identical request."""
    m = _HTTP_ERR_RE.search(message or "")
    if m and int(m.group(1)) in FATAL_HTTP:
        return False
    return True
