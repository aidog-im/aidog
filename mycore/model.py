"""mycore.model — OpenAI-compatible chat client, stdlib only.

The loop depends on exactly this surface:

    chat(messages, tools=None, **opts) -> dict   # chat.completions response shape

Provider details (base URL, model name, key) live in a ModelConfig whose key is
read from the environment at call time and NEVER echoed: not in logs, not in
reprs, not in exception messages, not in argv (the transport is urllib inside
this process — nothing secret ever reaches `ps`).

Failover (car 1-D4 -> 4a-M1, mycore.modeltier v2): when the config carries
upstreams the attempt runs primary-first through the FLATTENED unit list —
one (endpoint, key slot) pair at a time, credential pools rotating on auth
errors (401/403), payload-class errors failing immediately, cooling units
skipped (least-cold fallback when the whole chain cools), and a failure
after the first streamed delta NEVER replayed (no duplicate output).

`transport` is injectable so tests run fully offline with a scripted fake.
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from . import meter
from .modeltier import (HealthRegistry, SHARED_HEALTH, Upstream,  # noqa: F401
                        RateRegistry, SHARED_RATE, RotationState,
                        SHARED_ROTATION, health_note, is_auth_class,
                        is_failover_class)

DEFAULT_TIMEOUT = 120.0


@dataclass
class ModelConfig:
    """Connection facts for one OpenAI-compatible endpoint.

    `api_key_env` names an environment variable — the key itself is resolved
    per call and deliberately has no field that stores its value, so it cannot
    leak through repr()/logging of this object.

    Defaults point at the local test gateway group E (owner 2026-10-18:
    "test the embodied system with local-3460 mimo-v2.6-flash"); any endpoint
    overrides via MYCORE_BASE_URL / MYCORE_MODEL or a custom ModelConfig.
    """
    base_url: str = "http://127.0.0.1:3460/v1"
    model: str = "mimo-v2.6-flash"
    api_key_env: str = "MODEL_API_KEY"
    temperature: float = 0.2
    max_tokens: int = 4096
    timeout: float = DEFAULT_TIMEOUT
    extra_headers: dict = field(default_factory=dict)
    # Failover chain AFTER the primary endpoint (car 1-D4): entries hold env
    # slot NAMES for keys, never values. Empty = single endpoint (today's
    # default topology, zero behavior change).
    upstreams: tuple[Upstream, ...] = ()
    upstreams_note: str = ""          # parse problem, surfaced via describe()
    # X15 (Wave 6 delta2): opt-in per-model request params merged into the
    # chat/completions payload ONLY when non-empty (settings
    # mycore.reasoning_effort resolves it). Empty dict = the payload stays
    # byte-identical to the pre-X15 wire — defaults never change silently.
    extra_body: dict = field(default_factory=dict)
    # v0.5.55 gateway car: candidate-order rotation (""/"ordered" = the
    # historical fixed order, "round_robin" = start offset walks the chain)
    # and GLOBAL rate caps (0 = off) enforced per (endpoint, key slot) by an
    # in-process sliding window. All three default to today's behavior.
    rotation: str = ""
    max_rpm: int = 0
    max_tpm: int = 0

    def chain(self) -> tuple[tuple[str, str, str], ...]:
        """Ordered attempts: primary first, then each upstream. Every entry is
        (base_url, key_env, model) with defaults already resolved; key_env is
        the RAW slot field (possibly a comma-separated pool) and key VALUES
        are read from the environment per attempt inside ChatClient."""
        out = [(self.base_url, self.api_key_env, self.model)]
        for u in self.upstreams:
            out.append((u.base_url, u.key_env or self.api_key_env,
                        u.model or self.model))
        return tuple(out)

    def units(self) -> tuple[tuple[str, str, str], ...]:
        """Flattened v2 attempt units: one (base_url, key_slot, model) per
        credential-pool slot, pools expanded in configured order. The attempt
        loop runs over these; health/cooldown identity = endpoint + slot."""
        out: list[tuple[str, str, str]] = []
        for base_url, key_env, model in self.chain():
            slots = tuple(s for s in (key_env or "").split(",") if s) or ("",)
            for slot in slots:
                out.append((base_url, slot, model))
        return tuple(out)

    def describe(self) -> str:
        """Safe-to-log summary: presence of a key, never its value. v2 appends
        the process-shared cooldown state scoped to THIS config's units —
        URLs + slot names only, so the zero-echo rule holds."""
        has_key = any(os.environ.get(s) for s in
                      (self.api_key_env or "").split(",") if s)
        out = (f"{self.base_url} model={self.model} "
               f"key[{self.api_key_env}]={'set' if has_key else 'unset'} "
               f"upstreams={len(self.upstreams)}")
        if self.rotation == "round_robin":
            out += " rotation=round_robin"
        if self.max_rpm > 0:
            out += f" max_rpm={self.max_rpm}"
        if self.max_tpm > 0:
            out += f" max_tpm={self.max_tpm}"
        if self.upstreams_note:
            out += f" upstreams-invalid: {self.upstreams_note}"
        cool = health_note([HealthRegistry.identity(u[0], u[1])
                            for u in self.units()])
        if cool:
            out += f" {cool}"
        return out


class ModelError(RuntimeError):
    """Honest transport/protocol failure. Message carries context, never secrets."""


def _is_loopback(url: str) -> bool:
    try:
        import urllib.parse as _up
        parts = _up.urlparse(url)
    except ValueError:
        return False
    return parts.hostname in ("127.0.0.1", "localhost", "::1")


def _error_class(message: str) -> str:
    """Short, secret-free failure class for the meter row: the HTTP status
    when present, else the exception-class token before the colon ("transport
    failure" / "response is not JSON"). Never the full message (it may name
    endpoints beyond the meter's need) and never a body."""
    import re as _re
    m = _re.search(r"HTTP (\d{3})", message or "")
    if m:
        return f"HTTP {m.group(1)}"
    head = (message or "error").split(":", 1)[0].strip()
    return (head or "error")[:80]


def _slot_key(name: str) -> str:
    """Own-line secrets.env slot fallback for the API key (activation car
    §1.8): the console writes `secrets.env` while this client used to read the
    process ENV only, so a console-set key never reached the model. The env
    still wins at the call site (an operator override is never ignored);
    this reader returns "" on any failure (fail-closed) and the value it
    returns is used for one request header — never logged, never repr'd."""
    if not name:
        return ""
    try:
        from . import envslots   # local import: keeps the module graph acyclic
        return envslots.slot_value(name)
    except Exception:  # noqa: BLE001 — a missing reader degrades to "no key"
        return ""


# transport signature: (url, headers, payload_bytes, timeout) -> response text
Transport = Callable[[str, dict, bytes, float], str]

# streaming transport: same facts + a per-line callback; returns the FULL body
# text (SSE frames when the endpoint honored `stream`, otherwise the plain
# JSON document) so the caller can degrade honestly.
StreamTransport = Callable[[str, dict, bytes, float, Callable[[str], None]], str]


def _urllib_transport(url: str, headers: dict, payload: bytes, timeout: float) -> str:
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # Body may echo request contents; keep only status + reason (no body dump).
        raise ModelError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelError(f"transport failure: {type(e).__name__}") from None


def _urllib_stream_transport(url: str, headers: dict, payload: bytes, timeout: float,
                             on_line: Callable[[str], None]) -> str:
    """Line-by-line POST read: every line is handed to `on_line` as it arrives
    (live deltas) and the accumulated body is returned for whole-body
    fallback when the endpoint ignores `stream`.

    Error handling mirrors _urllib_transport: status + reason only, the
    response body (which may echo the request) is never dumped.
    """
    req = urllib.request.Request(url, data=payload, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    buf: list[str] = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:                      # yields per line as it lands
                line = raw.decode("utf-8", "replace")
                buf.append(line)
                on_line(line)
    except urllib.error.HTTPError as e:
        raise ModelError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ModelError(f"transport failure: {type(e).__name__}") from None
    return "".join(buf)


class _SseAssembler:
    """Assembles an SSE chat stream into the SAME completion dict shape the
    non-streaming path returns, so loop.py needs no provider knowledge.

    - `delta.content` pieces are concatenated (final text) and each piece is
      forwarded to `on_delta` as it lands (live preview only — the caller
      replaces the preview with the authoritative `done` text at the end);
    - `delta.tool_calls` fragments are merged per index (id / name /
      arguments) into `choices[0].message.tool_calls`;
    - other SSE fields (event:, id:, retry:, comments) are ignored; a
      non-JSON `data:` line is counted, never raised (honest empty result
      if nothing usable arrived);
    - a `usage` block (some providers attach it to the final chunk) is kept
      and passed through — including prompt-cache hit counts (v2 signal).
    """

    def __init__(self, on_delta: Callable[[str], None]) -> None:
        self.on_delta = on_delta
        self.parts: list[str] = []
        self.tools: dict[int, dict] = {}    # index -> {id, name, args parts}
        self.finish: str | None = None
        self.meta: dict = {}
        self.usage: dict | None = None
        self.chunks = 0
        self.done = False
        self.bad = 0
        self._pending: list[str] = []

    # ---------------------------------------------------------- feeding ---
    def feed_line(self, line: str) -> None:
        """One physical line of the response body (SSE framing rules)."""
        line = line.rstrip("\n").rstrip("\r")
        if not line:
            if self._pending:
                payload = "\n".join(self._pending)
                self._pending = []
                self._event(payload)
            return
        if line.startswith(":"):            # comment / keepalive
            return
        if line.startswith("data:"):
            self._pending.append(line[5:].lstrip(" "))
            return
        # event:/id:/retry: — not needed for chat streams, ignored by spec order

    def _event(self, payload: str) -> None:
        payload = payload.strip()
        if not payload:
            return
        if payload == "[DONE]":
            self.done = True
            return
        try:
            chunk = json.loads(payload)
        except ValueError:
            self.bad += 1
            return
        if not isinstance(chunk, dict):
            self.bad += 1
            return
        self._chunk(chunk)

    def _chunk(self, chunk: dict) -> None:
        self.chunks += 1
        usage = chunk.get("usage")
        if isinstance(usage, dict) and not self.usage:
            self.usage = usage            # e.g. final-chunk usage (v2 signal)
        for key in ("id", "model", "created"):
            if key in chunk and key not in self.meta:
                self.meta[key] = chunk[key]
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        ch = choices[0] if isinstance(choices[0], dict) else {}
        if ch.get("finish_reason"):
            self.finish = ch["finish_reason"]
        delta = ch.get("delta") if isinstance(ch.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            self.parts.append(content)
            self.on_delta(content)
        calls = delta.get("tool_calls")
        if not isinstance(calls, list):
            return
        for tc in calls:
            if not isinstance(tc, dict):
                continue
            idx = tc.get("index") if isinstance(tc.get("index"), int) else 0
            slot = self.tools.setdefault(idx, {"id": "", "name": "", "args": []})
            if not slot["id"] and isinstance(tc.get("id"), str) and tc["id"]:
                slot["id"] = tc["id"]
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
            if not slot["name"] and isinstance(fn.get("name"), str) and fn["name"]:
                slot["name"] = fn["name"]
            arg = fn.get("arguments")
            if isinstance(arg, str) and arg:
                slot["args"].append(arg)

    # ---------------------------------------------------------- result ----
    def doc(self) -> dict:
        """The assembled `chat.completions`-shaped dict (empty stream -> None
        handled by the caller, which then falls back to raw-body parsing)."""
        msg: dict = {"role": "assistant", "content": "".join(self.parts)}
        calls = []
        for idx in sorted(self.tools):
            slot = self.tools[idx]
            calls.append({"id": slot["id"], "type": "function",
                          "function": {"name": slot["name"],
                                       "arguments": "".join(slot["args"])}})
        if calls:
            msg["tool_calls"] = calls
        finish = self.finish or ("tool_calls" if calls else "stop")
        doc = {
            "id": self.meta.get("id", ""),
            "object": "chat.completion",
            "created": self.meta.get("created", 0),
            "model": self.meta.get("model", ""),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        }
        if self.usage is not None:
            doc["usage"] = self.usage
        return surface_cached_tokens(doc)


class ChatClient:
    def __init__(self, cfg: ModelConfig | None = None,
                 transport: Transport | None = None,
                 stream_transport: StreamTransport | None = None,
                 health: HealthRegistry | None = None,
                 rate: RateRegistry | None = None,
                 rotation: RotationState | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.transport = transport or _urllib_transport
        self.stream_transport = stream_transport or _urllib_stream_transport
        # Health/cooldown state defaults to the process-shared registry so a
        # per-turn client still honors cooldowns across turns; tests inject
        # an isolated HealthRegistry (usually with a fake clock).
        self.health = health if health is not None else SHARED_HEALTH
        # v0.5.55: rate windows + rotation cursor follow the same pattern —
        # shared by default (one chat service = one process), injectable for
        # isolated tests.
        self.rate = rate if rate is not None else SHARED_RATE
        self.rotation = rotation if rotation is not None else SHARED_ROTATION

    def _candidates(self) -> tuple[tuple[str, str, str], ...]:
        """v2 attempt units in chain order: cooling units skipped (least-cold
        fallback when the WHOLE chain cools — a call must never degrade into
        a silent zero-attempt stop). v0.5.55 adds, IN ORDER:
          1. rate gating — units AT an active global cap (max_rpm/max_tpm)
             are skipped the same way; when every remaining unit is limited,
             the LEAST limited one is still returned (same never-silent
             rule as the cooldown path);
          2. rotation — with rotation=round_robin the returned tuple starts
             at a walking offset (ordered/"" keeps the historical
             primary-first order byte-for-byte)."""
        units = self.cfg.units()
        if not units:
            return ()

        def ident(u: tuple[str, str, str]) -> str:
            return HealthRegistry.identity(u[0], u[1])

        cooling = [u for u in units if self.health.cooling(ident(u))]
        if cooling:
            if len(cooling) == len(units):
                units = (min(units, key=lambda u: self.health.remaining(ident(u))),)
            else:
                units = tuple(u for u in units if not self.health.cooling(ident(u)))
        if self.cfg.max_rpm > 0 or self.cfg.max_tpm > 0:
            open_units = tuple(
                u for u in units
                if not self.rate.limited(ident(u), self.cfg.max_rpm,
                                         self.cfg.max_tpm))
            if open_units:
                units = open_units
            elif len(units) > 1:
                units = (min(units, key=lambda u: self.rate.pressure(
                    ident(u), self.cfg.max_rpm, self.cfg.max_tpm)),)
            # len == 1: the single unit stays (attempting it is strictly
            # better than failing the call outright — least-cold precedent).
        if self.cfg.rotation == "round_robin" and len(units) > 1:
            off = self.rotation.next(len(units))
            units = units[off:] + units[:off]
        return units

    @staticmethod
    def _next_unit(units: tuple[tuple[str, str, str], ...], i: int,
                   message: str, failover: bool) -> int | None:
        """Index of the next unit to try, or None to raise here.

        fatal (payload-class) -> never fails over. auth (401/403) -> the very
        next unit: pool rotation when it belongs to the SAME endpoint, plain
        failover when the pool is exhausted. any other failover-class error ->
        the whole endpoint fails at once (v1 same-endpoint fail-fast kept for
        capacity/transport errors — its untried pool keys are NOT rotated)."""
        if not failover:
            return None
        j = i + 1
        if j >= len(units):
            return None
        if is_auth_class(message):
            return j                    # rotate in-pool or cross endpoints
        while j < len(units) and units[j][0] == units[i][0]:
            j += 1
        return j if j < len(units) else None

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             **opts) -> dict:
        """One completion. Returns the parsed `chat.completions` JSON object.

        Failover (v2): candidates run primary-first over flattened
        (endpoint, key slot) units — each unit at most once per call, auth
        errors rotating the credential pool inside the endpoint, cooling
        units skipped (least-cold fallback when everything cools), payload-
        class errors (HTTP 400/405/413/415/422) raising at once, and a
        failure after the first streamed delta raised without replay (no
        duplicated output). A single-endpoint config behaves exactly as
        before. Health successes/failures feed the injected (default:
        process-shared) HealthRegistry.

        `opts.on_delta`, when callable, requests streaming: the payload gains
        `stream: true`, each content piece is passed to it as it arrives, and
        the return value is assembled into the same dict shape as the
        non-streaming path (loop-transparent). An endpoint that ignores
        `stream` (plain JSON body) degrades to one `on_delta` call with the
        full text — never an error, never a fake.

        Raises ModelError with safe context only (status codes / exception
        class names / endpoint URLs); neither API keys nor key slot names
        enter the exception chain.
        """
        on_delta = opts.get("on_delta")
        units = self._candidates()
        emitted = [False]

        def _delta(piece: str) -> None:
            emitted[0] = True
            on_delta(piece)

        delta_fn = _delta if callable(on_delta) else None
        failed_from: str | None = None
        last: ModelError | None = None
        i = 0
        while i < len(units):
            base_url, key_slot, model = units[i]
            ident = HealthRegistry.identity(base_url, key_slot)
            if self.cfg.max_rpm > 0 or self.cfg.max_tpm > 0:
                self.rate.record_request(ident)   # attempt starts -> window
            started = time.monotonic()
            try:
                doc = self._attempt(base_url, key_slot, model, messages,
                                    tools, opts, delta_fn)
                self.health.record_success(ident)
                self._record_usage(base_url, opts.get("model") or model,
                                   doc, started, ident)
                return doc
            except ModelError as e:
                last = e
                meter.record(base_url, opts.get("model") or model, ok=False,
                             latency_ms=int((time.monotonic() - started) * 1000),
                             error_class=_error_class(str(e)))
                failover = is_failover_class(str(e))
                if failover:
                    self.health.record_failure(ident)
                if emitted[0]:
                    raise              # strategy 5: no replay mid-stream
                nxt = self._next_unit(units, i, str(e), failover)
                if nxt is None:
                    if failed_from is not None and failed_from != base_url:
                        raise ModelError(
                            f"{e} [failover: {failed_from} -> {base_url}]") from None
                    raise
                if units[nxt][0] != base_url:
                    failed_from = base_url   # crossing to a different endpoint
                i = nxt
        raise last if last is not None else ModelError("no upstream to attempt")

    def _record_usage(self, base_url: str, model: str, doc: dict,
                      started: float, ident: str) -> None:
        """v0.5.55 meter + rate accounting after a SUCCESSFUL attempt.
        Token counts come only from the provider's usage block (absent =
        None = honest no-usage row, never a fabricated 0); rate windows get
        the sum only when at least one count was reported. Meter failures
        are swallowed inside meter.record — accounting can never break a
        completed call."""
        usage = doc.get("usage") if isinstance(doc, dict) else None
        in_tok = out_tok = cached = None
        if isinstance(usage, dict):
            if type(usage.get("prompt_tokens")) is int:
                in_tok = usage["prompt_tokens"]
            if type(usage.get("completion_tokens")) is int:
                out_tok = usage["completion_tokens"]
            if type(usage.get("cached_tokens")) is int:
                cached = usage["cached_tokens"]
        meter.record(base_url, model, ok=True,
                     latency_ms=int((time.monotonic() - started) * 1000),
                     in_tok=in_tok, out_tok=out_tok, cached=cached)
        if self.cfg.max_tpm > 0 and (in_tok or out_tok):
            self.rate.record_tokens(ident, (in_tok or 0) + (out_tok or 0))

    def _headers(self, base_url: str, key_env: str) -> dict:
        """Per-attempt headers. The key VALUE is resolved as env FIRST, then
        the secrets.env slot of the same name (the store the console writes) —
        the value is never logged, echoed or embedded in an error."""
        cfg = self.cfg
        headers = {"Content-Type": "application/json"}
        # A comma-separated pool name is expanded so each slot is looked up on
        # its own (units() already splits; this stays correct if it does not).
        key = ""
        for name in (n for n in (key_env or cfg.api_key_env or "").split(",") if n):
            key = os.environ.get(name, "") or _slot_key(name)
            if key:
                break
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif _is_loopback(base_url):
            # Local gateway groups require the header's PRESENCE but no real
            # secret (observed 2026-10-18 on the local OpenAI-compatible gate).
            # Placeholder value only; never sent to non-loopback endpoints.
            headers["Authorization"] = "Bearer local"
        headers.update(cfg.extra_headers)
        return headers

    def _attempt(self, base_url: str, key_env: str, model: str,
                 messages: list[dict], tools: list[dict] | None, opts: dict,
                 on_delta: Callable[[str], None] | None) -> dict:
        """Exactly one request against one upstream. Raises ModelError; the
        caller (chat) owns failover policy. A per-call `opts['model']` wins
        over every chain model (explicit beats configured)."""
        cfg = self.cfg
        payload: dict = {
            "model": opts.get("model") or model,
            "messages": messages,
            "temperature": opts.get("temperature", cfg.temperature),
            "max_tokens": opts.get("max_tokens", cfg.max_tokens),
        }
        # X15: opt-in request params (reasoning effort etc.). setdefault
        # semantics: an explicit opt/CLI value always wins, and with an empty
        # extra_body this loop never runs — the wire is unchanged.
        if isinstance(cfg.extra_body, dict):
            for key, val in cfg.extra_body.items():
                payload.setdefault(key, val)
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = opts.get("tool_choice", "auto")
        if callable(on_delta):
            payload["stream"] = True
        headers = self._headers(base_url, key_env)
        body = json.dumps(payload).encode("utf-8")
        url = f"{base_url.rstrip('/')}/chat/completions"
        timeout = opts.get("timeout", cfg.timeout)
        if not callable(on_delta):
            return self._finish(self.transport(url, headers, body, timeout))

        asm = _SseAssembler(on_delta)
        raw = self.stream_transport(url, headers, body, timeout, asm.feed_line)
        if asm.chunks or asm.done:
            return asm.doc()
        # Endpoint did not stream (ignored `stream`, sent one JSON document):
        # return it as-is and report the whole text as a single delta.
        doc = self._finish(raw)
        text = first_choice_text(doc)
        if text:
            on_delta(text)          # whole text as one delta (honest fallback)
        return doc

    def _finish(self, text: str) -> dict:
        try:
            doc = json.loads(text)
        except ValueError:
            raise ModelError("response is not JSON") from None
        if not isinstance(doc, dict) or "choices" not in doc:
            raise ModelError("response missing 'choices'")
        return surface_cached_tokens(doc)

    def __repr__(self) -> str:  # defensive: no accidental key surfacing
        return f"ChatClient({self.cfg.describe()})"


def surface_cached_tokens(doc: dict) -> dict:
    """Prompt-cache signal pass-through (v2): when the PROVIDER reports cache
    hits we copy them into the extended field `usage["cached_tokens"]` —
    `prompt_tokens_details.cached_tokens` shape, or the
    `usage["cache_read_input_tokens"]` shape. Nothing is invented: no usage
    block, no cached count. We never SEND cache directives ourselves
    (`cache_control` injection is out of v2 scope — see modeltier docstring).
    Mutates and returns `doc` (parse-side normalization only)."""
    usage = doc.get("usage")
    if not isinstance(usage, dict):
        return doc
    if type(usage.get("cached_tokens")) is int:
        return doc
    val = None
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and type(details.get("cached_tokens")) is int:
        val = details["cached_tokens"]
    elif type(usage.get("cache_read_input_tokens")) is int:
        val = usage["cache_read_input_tokens"]
    if val is not None and val >= 0:
        usage["cached_tokens"] = val
    return doc


def first_choice_text(doc: dict) -> str | None:
    """Final assistant text of a completion (None when the model only called tools)."""
    try:
        msg = doc["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None
    content = msg.get("content")
    return content if isinstance(content, str) and content else None


def tool_calls(doc: dict) -> list[dict]:
    """Normalized tool calls: [{id, name, arguments(dict)}]; malformed args -> {}."""
    try:
        calls = doc["choices"][0]["message"].get("tool_calls") or []
    except (KeyError, IndexError, TypeError):
        return []
    out: list[dict] = []
    for c in calls:
        if not isinstance(c, dict):
            continue
        fn = c.get("function") or {}
        raw = fn.get("arguments", "{}")
        if isinstance(raw, str):
            try:
                args = json.loads(raw) if raw.strip() else {}
            except ValueError:
                args = {}
        elif isinstance(raw, dict):
            args = raw
        else:
            args = {}
        out.append({"id": c.get("id") or "", "name": fn.get("name") or "",
                    "arguments": args if isinstance(args, dict) else {}})
    return out
