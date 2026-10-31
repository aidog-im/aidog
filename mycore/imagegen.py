"""mycore.imagegen — BYOK image generation against an OpenAI-compatible
`/v1/images/generations` endpoint (car 4c media face).

Config precedence (owner contract): env AIDOG_IMAGE_URL > settings
media.image_base_url > settings mycore.base_url. Credential slot:
IMAGE_API_KEY falling back to MODEL_API_KEY, read with the same own-line
reader as mycore.channels._slot (process env first, then <repo>/secrets.env)
— the value never reaches a log, an error message, a repr or a return value.

generate() prefers response_format="b64_json" and tolerates a `url` reply
(downloaded with a plain GET). Every saved file lands in
data/images/<sha256>.png and is appended as (path, prompt) to a module-level
outbox guarded by a lock; the service layer drains it to send photos to TG.
Failures raise ImageError with a short English message (upstream error text
truncated to 160 chars). English comments per repo i18n policy.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIZE_RE = re.compile(r"^\d{2,5}x\d{2,5}$")
ERR_SNIPPET = 160          # upstream error text cap (honest, bounded)

_outbox: list[tuple[Path, str]] = []
_outbox_lock = threading.Lock()


class ImageError(RuntimeError):
    """Honest image-generation failure; never contains a credential."""


# ------------------------------------------------------------- config -------

def _settings() -> dict:
    """The raw settings document; {} on any read/parse failure."""
    try:
        doc = json.loads((REPO / "web" / "settings.json").read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _slot(name: str) -> str:
    """Own-line slot read (channels._slot style): process env first, then
    <repo>/secrets.env. Value for immediate use only — never surfaced."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        text = (REPO / "secrets.env").read_text(encoding="utf-8")
    except OSError:
        return ""
    m = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return m.group(1).strip() if m else ""


def _base_url() -> str:
    env = os.environ.get("AIDOG_IMAGE_URL", "").strip()
    if env:
        return env.rstrip("/")
    doc = _settings()
    media = doc.get("media")
    if isinstance(media, dict) and isinstance(media.get("image_base_url"), str) \
            and media["image_base_url"].strip():
        return media["image_base_url"].strip().rstrip("/")
    mc = doc.get("mycore")
    if isinstance(mc, dict) and isinstance(mc.get("base_url"), str) and mc["base_url"].strip():
        return mc["base_url"].strip().rstrip("/")
    return ""


def _key() -> str:
    return _slot("IMAGE_API_KEY") or _slot("MODEL_API_KEY")


def _model() -> str:
    """media.image_model from settings — empty means omit the field (the
    gateway infers); slot NAMES only in any failure path."""
    media = _settings().get("media")
    if isinstance(media, dict) and isinstance(media.get("image_model"), str):
        return media["image_model"].strip()
    return ""


def _is_loopback(url: str) -> bool:
    try:
        import urllib.parse as up
        host = up.urlparse(url).hostname
    except ValueError:
        return False
    return host in ("127.0.0.1", "localhost", "::1")


def configured() -> tuple[bool, str]:
    """(ok, reason) — reason is "" when ok. Slot NAMES only, never values."""
    base = _base_url()
    if not base:
        return False, "no image endpoint (set media.image_base_url in settings)"
    if not _key() and not _is_loopback(base):
        return False, "IMAGE_API_KEY / MODEL_API_KEY slot not set"
    return True, ""


# -------------------------------------------------------------- transport ---

def _post(url: str, payload: dict, headers: dict, timeout: float) -> str:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        snippet = ""
        try:
            snippet = e.read().decode("utf-8", "replace").strip()[:ERR_SNIPPET]
        except OSError:
            pass
        raise ImageError(f"image API HTTP {e.code}: {snippet or e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ImageError(f"image API transport failure: {type(e).__name__}") from None


def _get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise ImageError(f"image download HTTP {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ImageError(f"image download failure: {type(e).__name__}") from None


# ---------------------------------------------------------------- outbox ----

def drain_outbox() -> list[tuple[Path, str]]:
    """Atomically take everything queued since the last drain."""
    with _outbox_lock:
        items = list(_outbox)
        _outbox.clear()
    return items


# -------------------------------------------------------------- generate ----

def _headers(base: str) -> dict:
    key = _key()
    if key:
        auth = f"Bearer {key}"
    elif _is_loopback(base):
        auth = "Bearer local"      # local gateway/mock convention (model.py)
    else:
        raise ImageError("IMAGE_API_KEY / MODEL_API_KEY slot not set")
    return {"Content-Type": "application/json", "Authorization": auth}


def generate(prompt: str, *, size: str = "1024x1024", n: int = 1,
             timeout: float = 120) -> list[Path]:
    """Generate `n` image(s) for `prompt`; returns the saved file paths and
    queues (path, prompt) for the TG outbox. Raises ImageError when not
    configured or the upstream fails."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ImageError("prompt must be a non-empty string")
    if not isinstance(size, str) or not SIZE_RE.match(size.strip()):
        raise ImageError(f"invalid size '{size}' (expected WxH, e.g. 1024x1024)")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 10:
        raise ImageError("n must be an integer 1..10")
    ok, reason = configured()
    if not ok:
        raise ImageError(f"image generation not configured: {reason}")

    base = _base_url()
    payload = {"prompt": prompt.strip(), "size": size, "n": n,
               "response_format": "b64_json"}
    model = _model()
    if model:
        payload["model"] = model   # media.image_model (empty = omit; some
        #                           gateways infer, official OpenAI requires it)
    raw = _post(f"{base}/images/generations", payload, _headers(base), timeout)
    try:
        doc = json.loads(raw)
    except ValueError:
        raise ImageError("image API returned invalid JSON") from None
    if not isinstance(doc, dict):
        raise ImageError("image API returned unexpected JSON")
    data = doc.get("data")
    if not isinstance(data, list) or not data:
        err = doc.get("error")
        msg = ""
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            msg = err["message"].strip()[:ERR_SNIPPET]
        raise ImageError(f"image API returned no data{': ' + msg if msg else ''}")

    out_dir = REPO / "data" / "images"
    paths: list[Path] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        b64 = item.get("b64_json")
        if isinstance(b64, str) and b64:
            try:
                blob = base64.b64decode(b64, validate=False)
            except (ValueError, TypeError):
                raise ImageError("image API returned invalid base64") from None
        elif isinstance(item.get("url"), str) and item["url"]:
            blob = _get(item["url"], timeout)
        else:
            raise ImageError("image API item has neither b64_json nor url")
        if not blob:
            raise ImageError("image API returned empty image data")
        path = out_dir / f"{hashlib.sha256(blob).hexdigest()}.png"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        except OSError as e:
            raise ImageError(f"cannot save image: {type(e).__name__}") from None
        paths.append(path)
        with _outbox_lock:
            _outbox.append((path, prompt.strip()))
    if not paths:
        raise ImageError("image API returned no usable images")
    return paths
