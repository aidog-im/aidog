"""mycore.bridge — the user-owned bridge (car 6, line 5, owner ruling D9).

The problem line 5 actually asks: some URLs this agent needs are simply not
reachable from this machine. The answer is a reverse proxy running on a
sub-domain the USER creates on their own Cloudflare account. The product ships
four things and nothing else: the guided Worker template, the configuration
fields, an enable switch (the kill switch) and one audit line per proxied
request. The product is never the relay operator — no default middle-man
server ships with it, and the "we operate the bridge" alternative stays
deferred unless the owner asks for it.

Guardrails (design doc section 5.2), each mapped to code here:

  * explicit target allow-list  -> `allow` + host_matches(); a host that is
    not listed is never routed (fail-closed: no open proxy). The Worker
    template carries its OWN copy of the list and refuses on its own, so even
    a leaked worker URL cannot be used as an open proxy.
  * no credential forwarding     -> http_fetch never attaches a credential to
    a bridge request, and the template forwards only an explicit request
    header allow-list (deny by default) and drops any incoming
    Authorization / Cookie unless the host is listed in `allow_auth`.
  * rate + size caps             -> Worker-side (the RATE_* / MAX_* constants
    in the template) and product-side (`max_bytes` bounds one proxied read).
  * visible audit trail          -> audit_add() appends one JSON line per
    proxied request to <state>/bridge_audit.jsonl. Only the host, the path
    and the byte count are recorded — a query string may carry a secret, so
    it is never written.
  * one-click kill switch        -> `enabled`; false makes route() return
    None immediately, which the console can flip without touching anything
    else.

Configuration lives in web/settings.json under the top-level `bridge` block.
validate() is the single grammar (the console write path calls it, exactly
like mycore.hooks), and config() is the fail-soft read used at request time.

Test seams mirror mycore.decision: set_settings_for_tests pins the document,
set_state_for_tests pins the audit file, and the transport is injectable with
mycore.model's callable style (url, headers, payload_bytes, timeout) ->
response text, so the offline suite exercises every path with zero network.

English-only source (repo i18n policy).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from lib import profile_paths as pp

DEFAULT_SETTINGS: dict = {
    "enabled": False,       # the kill switch: false routes nothing, ever
    "worker_url": "",       # origin only, e.g. https://name.workers.dev
    "allow": [],            # target hosts the bridge may reach (fail-closed)
    "allow_auth": [],       # hosts a credential MAY be forwarded to (rare)
    "max_bytes": 1048576,   # one proxied response read ceiling
}
KEYS = tuple(DEFAULT_SETTINGS)
MAX_ALLOW = 32
MAX_URL = 2048
MAX_BYTES_MIN, MAX_BYTES_MAX = 4096, 8 * 1024 * 1024
PATH_AUDIT_MAX = 200        # recorded path cap (query strings are dropped)
ERR_SNIPPET = 160           # upstream error text cap (honest, bounded)

AUDIT_NAME = "bridge_audit.jsonl"
AUDIT_MAX_BYTES = 256 * 1024
AUDIT_KEEP = 400            # lines kept when the file rolls over
AUDIT_TAIL = 20             # rows returned by audit_tail()

# A host pattern: a dotted hostname, optionally labelled `*.` (sub-domains
# only — the apex needs its own entry, so a wildcard never silently widens).
HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
HOST_RE = re.compile(rf"^(?:\*\.)?{HOST_LABEL}(?:\.{HOST_LABEL})*$")
# The worker URL is an ORIGIN: no path, no query, no fragment. A path prefix
# would make the target prefix ambiguous, so it is refused at the door.
WORKER_URL_RE = re.compile(rf"^https?://{HOST_LABEL}(?:\.{HOST_LABEL})*"
                           r"(?::\d{1,5})?$", re.IGNORECASE)

_settings_override: dict | None = None
_state_override: Path | None = None
_transport_override = None
_lock = threading.Lock()


class BridgeError(RuntimeError):
    """A proxied request failed. `reason` is a WORD (never a sentence, never
    a URL, never a credential): not_routed | unreachable | timeout |
    http_error | bad_response."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# ------------------------------------------------------------------ config ---

def set_settings_for_tests(doc: dict | None) -> None:
    """Test seam: pin the document config() reads (None restores the
    production read). Production callers never pass it."""
    global _settings_override
    _settings_override = doc


def set_state_for_tests(path: Path | None) -> None:
    """Test seam: pin the audit file location (None restores <state>/)."""
    global _state_override
    _state_override = path


def set_transport_for_tests(fn) -> None:
    """Test seam: pin the transport proxy_get() uses (None restores the real
    one). Production callers never pass it."""
    global _transport_override
    _transport_override = fn


def _settings() -> dict:
    """Read web/settings.json (profile-aware) -> dict; missing/corrupt = {}."""
    if _settings_override is not None:
        return _settings_override
    try:
        doc = json.loads(pp.settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return doc if isinstance(doc, dict) else {}


def validate(doc) -> tuple[dict, list[str]]:
    """Grammar for the settings `bridge` block -> (clean, errors). Fail-closed:
    an unknown key, a bad host or an out-of-range number is reported and the
    offending VALUE is never echoed (host patterns are echoed only as far as
    their index — they are not secrets, but a refusal must stay a word)."""
    if not isinstance(doc, dict):
        return {}, ["bridge must be an object"]
    errors: list[str] = []
    clean: dict = {}
    for key in doc:
        if key not in KEYS:
            errors.append(f"unknown bridge key: {key}")
    if "enabled" in doc:
        if isinstance(doc["enabled"], bool):
            clean["enabled"] = doc["enabled"]
        else:
            errors.append("bridge.enabled must be a boolean")
    if "worker_url" in doc:
        val = doc["worker_url"]
        if not isinstance(val, str):
            errors.append("bridge.worker_url must be a string")
        else:
            url = val.strip().rstrip("/")
            if url and not WORKER_URL_RE.match(url):
                errors.append("bridge.worker_url must be an http(s) origin with "
                              "no path (https://name.workers.dev)")
            else:
                clean["worker_url"] = url
    for key in ("allow", "allow_auth"):
        if key not in doc:
            continue
        val = doc[key]
        if not isinstance(val, list):
            errors.append(f"bridge.{key} must be a list of host names")
            continue
        if len(val) > MAX_ALLOW:
            errors.append(f"bridge.{key} must hold at most {MAX_ALLOW} hosts")
            continue
        out, bad = [], 0
        for item in val:
            host = str(item).strip().lower()
            if not host:
                continue
            core = host[2:] if host.startswith("*.") else host
            if len(host) > 253 or not HOST_RE.match(host):
                bad += 1
                continue
            if host not in out:
                out.append(host)
        if bad:
            errors.append(f"bridge.{key}: {bad} entry(ies) are not valid host "
                          "names (a wildcard must look like *.example.com)")
            continue
        clean[key] = out
    if "max_bytes" in doc:
        val = doc["max_bytes"]
        if isinstance(val, bool) or not isinstance(val, int) \
                or not MAX_BYTES_MIN <= val <= MAX_BYTES_MAX:
            errors.append(f"bridge.max_bytes must be an integer in "
                          f"{MAX_BYTES_MIN}..{MAX_BYTES_MAX}")
        else:
            clean["max_bytes"] = val
    return clean, errors


def config(settings: dict | None = None) -> dict:
    """Resolved bridge configuration, fail-soft: anything unusable collapses
    to the safe default (off / no worker / empty allow-list), never an
    exception. An EMPTY allow-list routes nothing — that is the fail-closed
    default, not a bug."""
    cfg = dict(DEFAULT_SETTINGS)
    if settings is None:
        settings = _settings()
    block = settings.get("bridge") if isinstance(settings, dict) else None
    if not isinstance(block, dict):
        return cfg
    clean, _ = validate(block)
    for key in KEYS:
        if key in clean:
            cfg[key] = clean[key]
    return cfg


# ----------------------------------------------------------------- routing ---

def host_matches(host: str, patterns) -> bool:
    """Exact host, or `*.example.com` for a SUB-domain of example.com (the
    apex is never implied — it needs its own entry). Case-insensitive, a
    trailing dot is ignored, an empty list never matches."""
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return False
    for pat in patterns or ():
        p = str(pat).strip().lower().rstrip(".")
        if not p:
            continue
        if p.startswith("*."):
            base = p[2:]
            if base and h.endswith("." + base):
                return True
        elif h == p:
            return True
    return False


def route(url: str, settings: dict | None = None) -> str | None:
    """The bridge URL for `url`, or None when the bridge must NOT be used
    (disabled, unconfigured, off the allow-list, malformed). None means the
    caller fetches directly — never that the request is dropped."""
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None
    if len(url) > MAX_URL:
        return None
    url = url.split("#", 1)[0]      # a fragment never reaches a server
    cfg = config(settings)
    if not cfg["enabled"] or not cfg["worker_url"]:
        return None
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return None
    if not host_matches(host, cfg["allow"]):
        return None
    return cfg["worker_url"] + "/" + url


def target_parts(url: str) -> tuple[str, str]:
    """(host, path) for the audit line. The query string is dropped on
    purpose — it frequently carries a token."""
    try:
        sp = urlsplit(url)
        host = (sp.hostname or "").lower()
        path = sp.path or "/"
    except ValueError:
        return "", ""
    if len(path) > PATH_AUDIT_MAX:
        path = path[:PATH_AUDIT_MAX] + "..."
    return host, path


# ------------------------------------------------------------------- audit ---

def audit_file() -> Path:
    if _state_override is not None:
        return _state_override
    return pp.state_dir() / AUDIT_NAME


def audit_add(host: str, path: str, ok: bool, reason: str = "",
              nbytes: int = 0) -> dict:
    """One append-only line per proxied request. An audit write failure never
    breaks the fetch that triggered it — it costs one sanitized stderr line
    and nothing else (no credential can be in it: the row is built here from
    fixed fields)."""
    row = {"ts": round(time.time(), 3), "host": str(host)[:253],
           "path": str(path)[:PATH_AUDIT_MAX + 3], "bytes": int(nbytes),
           "ok": bool(ok), "reason": str(reason)[:32]}
    with _lock:
        try:
            path_obj = audit_file()
            path_obj.parent.mkdir(parents=True, exist_ok=True)
            with path_obj.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=True) + "\n")
            if path_obj.stat().st_size > AUDIT_MAX_BYTES:
                lines = path_obj.read_text(encoding="utf-8",
                                           errors="replace").splitlines()
                kept = "\n".join(lines[-AUDIT_KEEP:]) + "\n"
                tmp = path_obj.with_suffix(".tmp")
                tmp.write_text(kept, encoding="utf-8")
                os.replace(tmp, path_obj)
        except OSError as e:  # noqa: BLE001 — type name only
            import sys
            sys.stderr.write(f"bridge audit write failed err={type(e).__name__}\n")
    return row


def audit_tail(limit: int = AUDIT_TAIL) -> list[dict]:
    """Newest-last rows for the console. A corrupt line is skipped, never
    repaired and never echoed."""
    try:
        text = audit_file().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    rows: list[dict] = []
    for line in text.splitlines()[-max(1, min(int(limit or AUDIT_TAIL), AUDIT_KEEP)):]:
        try:
            doc = json.loads(line)
        except ValueError:
            continue
        if isinstance(doc, dict) and "host" in doc:
            rows.append(doc)
    return rows


# ------------------------------------------------------------------ fetch ----

def _urllib_transport(url: str, headers: dict, payload: bytes, timeout: float,
                      limit: int) -> str:
    req = urllib.request.Request(url, data=payload, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(limit).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:ERR_SNIPPET]
        except OSError:
            pass
        raise BridgeError("http_error",
                          f"HTTP {e.code} {e.reason}: {snippet}") from None
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", None)
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise BridgeError("timeout", "request timed out") from None
        raise BridgeError("unreachable",
                          f"transport failure: {type(reason).__name__}") from None
    except (TimeoutError, OSError) as e:
        raise BridgeError("unreachable",
                          f"transport failure: {type(e).__name__}") from None


def proxy_get(url: str, *, timeout: float = 20, transport=None,
              settings: dict | None = None) -> str:
    """GET `url` through the user's Worker. Refuses (BridgeError) when the
    route is not open — a refusal here is the fail-closed guardrail talking.

    No credential is ever attached: http_fetch has none to send, and the
    request header set is deliberately minimal. Exactly one audit line is
    written per attempt, success or failure."""
    cfg = config(settings)
    target = route(url, settings)
    if not target:
        raise BridgeError("not_routed", "target is not on the bridge allow-list")
    limit = cfg["max_bytes"]
    if transport is None:
        transport = _transport_override
    if transport is None:
        transport = lambda u, h, p, t: _urllib_transport(u, h, p, t, limit)  # noqa: E731
    headers = {"User-Agent": "aidog-bridge/1 (fetch)", "Accept": "*/*"}
    host, path = target_parts(url)
    try:
        body = transport(target, headers, b"", timeout)
    except BridgeError as e:
        audit_add(host, path, False, e.reason, 0)
        raise
    if not isinstance(body, str):
        audit_add(host, path, False, "bad_response", 0)
        raise BridgeError("bad_response", "bridge returned a non-text body")
    raw = body.encode("utf-8", "replace")
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
        body = raw.decode("utf-8", "replace") + "\n... [truncated at bridge max_bytes]"
    audit_add(host, path, True, "", len(raw))
    return body
