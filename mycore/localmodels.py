"""localmodels — zero-key onboarding: detect local model servers (car 4b).

Pure stdlib, no proxy, never raises. Probes well-known local OpenAI-compatible
model servers so the setup wizard can configure base_url/model with NO API key
(local endpoints are keyless by design — the MODEL_API_KEY slot stays empty).

Default probes (each with a hard 0.5s timeout):
  Ollama       GET http://127.0.0.1:11434/api/tags   own shape:  models[].name
               -> configured base_url http://127.0.0.1:11434/v1
  llama-server GET http://127.0.0.1:8080/v1/models    OpenAI shape: data[].id
  LM Studio    GET http://127.0.0.1:1234/v1/models    OpenAI shape: data[].id

Env override AIDOG_LOCAL_MODEL_PROBES: comma-separated base URLs (e.g.
"http://127.0.0.1:18100/v1,http://127.0.0.1:18101/v1"). When present ONLY
those are probed, uniformly with the OpenAI GET <url>/models shape, and each
detected entry carries kind "local".

probe() returns [{"kind": ..., "base_url": ..., "model": ...}, ...]; a refused
connection, timeout, HTTP error, bad JSON, or missing/empty model all count as
"not detected" for that endpoint. describe(found) renders one display line per
entry (kind + base_url + model only — never any credential).
"""
from __future__ import annotations

import json
import os
import urllib.request

PROBE_TIMEOUT = 0.5  # seconds per endpoint — detection must never stall the wizard

# (kind, probe URL, response shape, base_url to configure on adoption)
DEFAULT_PROBES: tuple[tuple[str, str, str, str], ...] = (
    ("ollama", "http://127.0.0.1:11434/api/tags", "ollama",
     "http://127.0.0.1:11434/v1"),
    ("llamacpp", "http://127.0.0.1:8080/v1/models", "openai",
     "http://127.0.0.1:8080/v1"),
    ("lmstudio", "http://127.0.0.1:1234/v1/models", "openai",
     "http://127.0.0.1:1234/v1"),
)


def _fetch(url: str) -> dict | None:
    """GET url -> parsed JSON object, or None on ANY failure. Proxy explicitly
    disabled (host may export http_proxy; loopback detection must stay direct)."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with opener.open(req, timeout=PROBE_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8", "replace"))
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _model_of(payload: "dict | None", shape: str) -> str:
    """First model id from a payload; "" when absent/empty/malformed."""
    if not isinstance(payload, dict):
        return ""
    bucket = payload.get("models" if shape == "ollama" else "data")
    if isinstance(bucket, list) and bucket and isinstance(bucket[0], dict):
        name = bucket[0].get("name" if shape == "ollama" else "id")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return ""


def probe() -> list[dict]:
    """Detect local model servers. Returns [] when none answered. NEVER raises."""
    found: list[dict] = []
    try:
        override = os.environ.get("AIDOG_LOCAL_MODEL_PROBES")
    except Exception:
        override = None
    try:
        if override is not None:
            for raw in override.split(","):
                base = raw.strip().rstrip("/")
                if not base:
                    continue
                probe_url = base if base.endswith("/models") else base + "/models"
                model = _model_of(_fetch(probe_url), "openai")
                if model:
                    found.append({"kind": "local", "base_url": base, "model": model})
        else:
            for kind, probe_url, shape, base_url in DEFAULT_PROBES:
                model = _model_of(_fetch(probe_url), shape)
                if model:
                    found.append({"kind": kind, "base_url": base_url,
                                  "model": model})
    except Exception:
        return found  # defence in depth — probe() never raises
    return found


def describe(found: "list[dict]") -> list[str]:
    """One English display line per detected server: kind + base_url + model.
    Contains no credential material by construction (those three fields only)."""
    lines: list[str] = []
    for entry in found or []:
        if not isinstance(entry, dict):
            continue
        lines.append(f"{entry.get('kind') or 'local'} "
                     f"{entry.get('base_url') or ''} "
                     f"(model: {entry.get('model') or ''})")
    return lines
