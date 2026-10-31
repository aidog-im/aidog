"""mycore.tg — Telegram adapter (P4-1c, design §14.3).

Surface (stdlib urllib, injectable transport for offline tests):

    TgClient(...).get_me()              # read-only identity probe
    TgClient(...).send_message(chat, text)
    TgClient(...).send_chat_action(chat)  # typing heartbeat (stateless)
    TgClient(...).get_file(file_id)      # getFile -> {file_path, file_size, ...}
    TgClient(...).download_file(path)    # binary file download (bytes seam)
    TgClient(...).send_photo(chat, ...)  # multipart/form-data photo upload
    TgClient(...).run_poll_loop(handler)  # getUpdates long-poll loop

CONFLICT GUARD (owner system: one bot token = one getUpdates consumer):
  sendMessage/getMe are stateless — safe alongside any running gateway.
  The POLL LOOP refuses to use the live gateway's bot token: it requires a
  dedicated `MYCORE_TG_TOKEN` (the second bot). Starting it against the live
  bot would silently steal updates from the transition stack's polling
  session (known operational hazard). This is a hard refusal, not a warning.

Credential rule (README contract 2): the token is resolved at call time and
never appears in logs, reprs, exception messages, or argv. Telegram URLs embed
the token by design — exception handling therefore reports ONLY the method and
the exception type, never the URL (from None breaks the chain too).
English comments per repo i18n policy.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

DEFAULT_API = "https://api.telegram.org"
LIVE_BOT_ENV = "TELEGRAM_BOT_TOKEN"      # transition gateway's own bot (send-only safe)
POLL_BOT_ENV = "MYCORE_TG_TOKEN"         # dedicated second bot for polling
MAX_FILE_BYTES = 5_000_000               # TG download/upload cap (car 4c voice)


class TgError(RuntimeError):
    """Honest transport/protocol failure. Carries method + exception type only."""


# transport: (url, payload_bytes_or_None, timeout) -> response text
#   payload None = GET, bytes = POST (form-encoded or JSON depending on caller)
Transport = Callable[[str, bytes | None, float], str]


def _urllib_transport(url: str, payload: bytes | None, timeout: float) -> str:
    req = urllib.request.Request(url, data=payload, method="POST" if payload else "GET")
    if payload is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        # Only status/reason: e.url would embed the token — never touch it.
        raise TgError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TgError(f"transport failure: {type(e).__name__}") from None


# BINARY transport: (url, timeout) -> raw bytes. The text seam above decodes
# utf-8 and therefore cannot carry audio/photo payloads (car 4c).
BinTransport = Callable[[str, float], bytes]


def _urllib_bin_transport(url: str, timeout: float) -> bytes:
    """Default binary GET. Reads at most MAX_FILE_BYTES + 1 so an oversized
    response can never balloon memory — download_file turns the +1 into an
    honest 'file too large' TgError."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(MAX_FILE_BYTES + 1)
    except urllib.error.HTTPError as e:
        # Only status/reason: the URL embeds the token — never touch it.
        raise TgError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TgError(f"transport failure: {type(e).__name__}") from None


# MULTIPART transport: (url, body_bytes, content_type_with_boundary,
# timeout) -> response text. Needed because _call's transport only speaks
# application/x-www-form-urlencoded and cannot upload a file (car 4c).
MPTransport = Callable[[str, bytes, str, float], str]


def _urllib_mp_transport(url: str, payload: bytes, content_type: str,
                         timeout: float) -> str:
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        raise TgError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TgError(f"transport failure: {type(e).__name__}") from None


def _multipart_body(boundary: str, fields: list[tuple[str, str]],
                    file_field: str, filename: str, blob: bytes) -> bytes:
    """RFC 7578 form body: plain fields first, then the file part. Names are
    code-controlled literals (never user input), so no quoting is needed."""
    out = bytearray()
    for name, value in fields:
        out += f"--{boundary}\r\n".encode("ascii")
        out += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii")
        out += value.encode("utf-8")
        out += b"\r\n"
    out += f"--{boundary}\r\n".encode("ascii")
    out += (f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{filename}"\r\n').encode("ascii")
    out += b"Content-Type: application/octet-stream\r\n\r\n"
    out += blob
    out += b"\r\n"
    out += f"--{boundary}--\r\n".encode("ascii")
    return bytes(out)


class TgClient:
    def __init__(self, token: str | None = None, token_env: str = LIVE_BOT_ENV,
                 api_base: str = DEFAULT_API,
                 transport: Transport | None = None, timeout: float = 60.0,
                 *, bin_transport: BinTransport | None = None,
                 mp_transport: MPTransport | None = None) -> None:
        # Transport read timeout must EXCEED the long-poll window (getUpdates
        # caps at 50s; service polls at 30s) — equal values deadlock every poll
        # (server holds, client gives up: observed POLL_ERR TimeoutError storm,
        # 2026-01-19). 60s leaves margin over the cap.
        self._token = token if token is not None else os.environ.get(token_env, "")
        self._token_env = token_env
        self.api_base = api_base.rstrip("/")
        self.transport = transport or _urllib_transport
        self.bin_transport = bin_transport or _urllib_bin_transport
        self.mp_transport = mp_transport or _urllib_mp_transport
        self.timeout = timeout

    # ------------------------------------------------------------- guards ---
    @property
    def has_token(self) -> bool:
        return bool(self._token)

    @staticmethod
    def polling_token() -> str:
        """Token for the poll loop. HARD REFUSES two cases:
        1. MYCORE_TG_TOKEN unset (obviously no dedicated bot);
        2. its value EQUALS the live gateway's TELEGRAM_BOT_TOKEN — same token
           anywhere = one getUpdates consumer; two pollers on one token steal
           each other's updates (transition-stack hazard; owner's `2.txt` was
           verified identical to the live token on 2026-10-18)."""
        tok = os.environ.get(POLL_BOT_ENV, "")
        if not tok:
            raise TgError(
                f"poll loop requires a dedicated second bot: set {POLL_BOT_ENV} "
                f"(refusing {LIVE_BOT_ENV} — it belongs to the live gateway)")
        live = os.environ.get(LIVE_BOT_ENV, "")
        if live and tok == live:
            raise TgError(
                f"{POLL_BOT_ENV} equals {LIVE_BOT_ENV} — this is the LIVE gateway's "
                "token, not a second bot; polling it would steal the gateway's "
                "updates (get a separate bot via BotFather)")
        return tok

    def _url(self, method: str) -> str:
        return f"{self.api_base}/bot{self._token}/{method}"

    def _require(self) -> None:
        if not self._token:
            raise TgError(f"no token: set {self._token_env}")

    def __repr__(self) -> str:      # defensive: token never surfaces
        return (f"TgClient(api={self.api_base}, "
                f"token[{'set' if self._token else 'unset'}], "
                f"poll_env={POLL_BOT_ENV})")

    # ------------------------------------------------------------ calls -----
    def _call(self, method: str, payload: dict | None = None) -> dict:
        self._require()
        data = None
        if payload is not None:
            import urllib.parse
            data = urllib.parse.urlencode(payload).encode("utf-8")
        try:
            # Method name only in error paths: _url() embeds the token.
            text = self.transport(self._url(method), data, self.timeout)
        except TgError:
            raise                       # already sanitized (no URL inside)
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            raise TgError(f"{method} failed: {type(e).__name__}") from None
        return self._parse(method, text)

    @staticmethod
    def _parse(method: str, text: str) -> dict:
        """Response JSON -> result envelope check, shared by urlencoded,
        multipart and any future seam. Error text carries the METHOD only —
        a response body could theoretically echo the URL, never trust it."""
        try:
            doc = json.loads(text)
        except ValueError:
            raise TgError(f"{method}: response is not JSON") from None
        if not isinstance(doc, dict) or doc.get("ok") is not True:
            desc = doc.get("description") if isinstance(doc, dict) else None
            # Telegram error descriptions are safe text (no token), truncate hard.
            raise TgError(f"{method}: api error: {str(desc or 'unknown')[:120]}")
        return doc

    def get_me(self) -> dict:
        """Read-only identity probe (safe alongside a live poller)."""
        return self._call("getMe")

    def send_message(self, chat_id: str | int, text: str,
                     thread_id: int | None = None) -> dict:
        """Stateless send — safe alongside a live poller. Text capped at 4096
        (Telegram's limit) with an honest truncation marker.
        `thread_id` (forum topic) rides as `message_thread_id` when given —
        None omits the field entirely, so non-topic chats are byte-identical
        to the pre-topic payload."""
        if not chat_id:
            raise TgError("chat_id is required")
        if not isinstance(text, str) or not text.strip():
            raise TgError("text is required")
        if len(text) > 4096:
            text = text[:4090] + " …[truncated]"
        payload: dict = {"chat_id": chat_id, "text": text}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        return self._call("sendMessage", payload)

    def send_chat_action(self, chat_id: str | int, action: str = "typing") -> dict:
        """Stateless 'typing…' indicator (safe alongside a live poller).

        Best-effort by contract: the caller treats any failure as silent
        stop — a lost heartbeat must never break or delay a reply.
        """
        if not chat_id:
            raise TgError("chat_id is required")
        if not isinstance(action, str) or not action.strip():
            raise TgError("action is required")
        return self._call("sendChatAction", {"chat_id": chat_id,
                                              "action": action.strip()})

    # -------------------------------------------------- media (car 4c) ---
    def get_file(self, file_id: str) -> dict:
        """getFile -> the `result` dict ({file_id, file_unique_id, file_path,
        file_size, ...}). file_path is required for download_file()."""
        if not isinstance(file_id, str) or not file_id:
            raise TgError("file_id is required")
        doc = self._call("getFile", {"file_id": file_id})
        result = doc.get("result")
        if not isinstance(result, dict):
            raise TgError("getFile: result is not an object")
        return result

    def download_file(self, file_path: str) -> bytes:
        """Binary GET of {api}/file/bot{token}/{file_path} through the bytes
        seam (the text seam decodes utf-8 and cannot carry audio). Bounded at
        MAX_FILE_BYTES. Error paths report method + exception type only —
        the URL embeds the token and must never surface."""
        self._require()
        if not isinstance(file_path, str) or not file_path:
            raise TgError("file_path is required")
        if ".." in file_path:
            raise TgError("download_file: bad file_path")
        try:
            data = self.bin_transport(
                f"{self.api_base}/file/bot{self._token}/{file_path}",
                self.timeout)
        except TgError:
            raise                       # already sanitized (no URL inside)
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            raise TgError(f"download_file failed: {type(e).__name__}") from None
        if not isinstance(data, (bytes, bytearray)):
            raise TgError("download_file: response is not bytes")
        if len(data) > MAX_FILE_BYTES:
            raise TgError("file too large")
        return bytes(data)

    def send_photo(self, chat_id: str | int, photo: bytes | Path,
                   caption: str = "", thread_id: int | None = None) -> dict:
        """multipart/form-data sendPhoto (a file upload — the urlencoded _call
        seam cannot carry bytes). caption hard-cut at 1000 chars (Telegram's
        own cap is 1024; service passes prompt[:1000]). thread_id rides as
        message_thread_id when given — None omits the field entirely."""
        self._require()
        if not chat_id:
            raise TgError("chat_id is required")
        if isinstance(photo, (str, Path)):
            try:
                photo = Path(photo).read_bytes()
            except OSError as e:
                raise TgError(f"photo read failed: {type(e).__name__}") from None
        if not isinstance(photo, (bytes, bytearray)) or not photo:
            raise TgError("photo must be non-empty bytes or a Path")
        photo = bytes(photo)
        if len(photo) > MAX_FILE_BYTES:
            raise TgError("file too large")
        if not isinstance(caption, str):
            caption = ""
        caption = caption[:1000]
        fields: list[tuple[str, str]] = [("chat_id", str(chat_id))]
        if thread_id is not None:
            fields.append(("message_thread_id", str(thread_id)))
        if caption:
            fields.append(("caption", caption))
        boundary = "mycore" + uuid.uuid4().hex
        body = _multipart_body(boundary, fields, "photo", "photo.jpg", photo)
        content_type = f"multipart/form-data; boundary={boundary}"
        try:
            # Method name only in error paths: _url() embeds the token.
            text = self.mp_transport(self._url("sendPhoto"), body,
                                     content_type, self.timeout)
        except TgError:
            raise                       # already sanitized (no URL inside)
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            raise TgError(f"sendPhoto failed: {type(e).__name__}") from None
        return self._parse("sendPhoto", text)

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        """Long-poll getUpdates. Caller must have obtained the token through
        polling_token() — this method itself uses whatever token this client
        was constructed with (the factory below enforces the guard)."""
        payload = {"timeout": str(max(0, min(timeout, 50)))}
        if offset is not None:
            payload["offset"] = str(offset)
        doc = self._call("getUpdates", payload)
        # Telegram's response field is `result` (a `updates` key never exists —
        # the wrong key parsed every real response as empty: E2E-caught 2026-01-19,
        # test fixtures had mirrored the same wrong key = tautological blind spot).
        updates = doc.get("result")
        return updates if isinstance(updates, list) else []

    def run_poll_loop(self, handler: Callable[[dict], None],
                      on_error: Callable[[TgError], None] | None = None,
                      max_errors: int = 20, sleep: Callable[[float], None] = time.sleep,
                      stop_after: int | None = None) -> int:
        """Long-poll loop with offset bookkeeping + backoff.

        HARD GUARD: constructs its own client from polling_token(), so the
        live gateway's token can never enter here. Returns processed count
        (mainly for tests); stop_after bounds the loop in tests/dry runs.
        """
        client = TgClient(token=self.polling_token(), token_env=POLL_BOT_ENV,
                          api_base=self.api_base, transport=self.transport,
                          timeout=self.timeout,
                          bin_transport=self.bin_transport,
                          mp_transport=self.mp_transport)
        offset: int | None = None
        processed = 0
        errors = 0
        while True:
            if stop_after is not None and processed >= stop_after:
                return processed
            try:
                for upd in client.get_updates(offset=offset, timeout=30):
                    if isinstance(upd, dict):
                        offset = upd.get("update_id", offset)
                        if offset is not None:
                            offset += 1
                        handler(upd)
                        processed += 1
                        if stop_after is not None and processed >= stop_after:
                            return processed
                errors = 0                      # successful poll resets backoff
            except TgError as e:
                errors += 1
                if on_error:
                    on_error(e)
                if errors >= max_errors:
                    raise
                sleep(min(30.0, 1.5 ** min(errors, 10)))   # bounded backoff
