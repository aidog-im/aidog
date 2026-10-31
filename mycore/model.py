"""mycore.model — OpenAI-compatible chat client, stdlib only.

The loop depends on exactly this surface:

    chat(messages, tools=None, **opts) -> dict   # chat.completions response shape

Provider details (base URL, model name, key) live in a ModelConfig whose key is
read from the environment at call time and NEVER echoed: not in logs, not in
reprs, not in exception messages, not in argv (the transport is urllib inside
this process — nothing secret ever reaches `ps`).

`transport` is injectable so tests run fully offline with a scripted fake.
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

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

    def describe(self) -> str:
        """Safe-to-log summary: presence of a key, never its value."""
        has_key = bool(os.environ.get(self.api_key_env))
        return f"{self.base_url} model={self.model} key[{self.api_key_env}]={'set' if has_key else 'unset'}"


class ModelError(RuntimeError):
    """Honest transport/protocol failure. Message carries context, never secrets."""


def _is_loopback(url: str) -> bool:
    try:
        import urllib.parse as _up
        parts = _up.urlparse(url)
    except ValueError:
        return False
    return parts.hostname in ("127.0.0.1", "localhost", "::1")


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
      if nothing usable arrived).
    """

    def __init__(self, on_delta: Callable[[str], None]) -> None:
        self.on_delta = on_delta
        self.parts: list[str] = []
        self.tools: dict[int, dict] = {}    # index -> {id, name, args parts}
        self.finish: str | None = None
        self.meta: dict = {}
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
        return {
            "id": self.meta.get("id", ""),
            "object": "chat.completion",
            "created": self.meta.get("created", 0),
            "model": self.meta.get("model", ""),
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
        }


class ChatClient:
    def __init__(self, cfg: ModelConfig | None = None,
                 transport: Transport | None = None,
                 stream_transport: StreamTransport | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.transport = transport or _urllib_transport
        self.stream_transport = stream_transport or _urllib_stream_transport

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             **opts) -> dict:
        """One completion. Returns the parsed `chat.completions` JSON object.

        `opts.on_delta`, when callable, requests streaming: the payload gains
        `stream: true`, each content piece is passed to it as it arrives, and
        the return value is assembled into the same dict shape as the
        non-streaming path (loop-transparent). An endpoint that ignores
        `stream` (plain JSON body) degrades to one `on_delta` call with the
        full text — never an error, never a fake.

        Raises ModelError with safe context only (status codes / exception
        class names); the API key never enters the exception chain.
        """
        cfg = self.cfg
        payload: dict = {
            "model": opts.get("model", cfg.model),
            "messages": messages,
            "temperature": opts.get("temperature", cfg.temperature),
            "max_tokens": opts.get("max_tokens", cfg.max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = opts.get("tool_choice", "auto")
        on_delta = opts.get("on_delta")
        if callable(on_delta):
            payload["stream"] = True
        key = os.environ.get(cfg.api_key_env, "")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        elif _is_loopback(cfg.base_url):
            # Local gateway groups require the header's PRESENCE but no real
            # secret (observed 2026-10-18 on the local OpenAI-compatible gate).
            # Placeholder value only; never sent to non-loopback endpoints.
            headers["Authorization"] = "Bearer local"
        headers.update(cfg.extra_headers)
        body = json.dumps(payload).encode("utf-8")
        url = f"{cfg.base_url.rstrip('/')}/chat/completions"
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
        return doc

    def __repr__(self) -> str:  # defensive: no accidental key surfacing
        return f"ChatClient({self.cfg.describe()})"


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
