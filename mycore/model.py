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


class ChatClient:
    def __init__(self, cfg: ModelConfig | None = None,
                 transport: Transport | None = None) -> None:
        self.cfg = cfg or ModelConfig()
        self.transport = transport or _urllib_transport

    def chat(self, messages: list[dict], tools: list[dict] | None = None,
             **opts) -> dict:
        """One completion. Returns the parsed `chat.completions` JSON object.

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
        text = self.transport(f"{cfg.base_url.rstrip('/')}/chat/completions",
                              headers, body, opts.get("timeout", cfg.timeout))
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
