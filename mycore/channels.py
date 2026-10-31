"""mycore.channels — full-duplex messaging channels (C car, spec: c-channels).

Four routes, outbound send + inbound poll, stdlib-only (no third-party deps):

    discord   POST /api/v10/channels/{id}/messages
              poll: GET  /api/v10/channels/{id}/messages?after=<cursor> (REST
                     polling — deliberately NO WebSocket; needs the bot's
                     Message Content Intent for message text)
    slack     POST chat.postMessage
              poll: POST conversations.history?oldest=<cursor> (REST polling,
                     no Socket Mode; needs bot token scope channels:history)
    email     smtplib send (STARTTLS/SSL per settings) / imaplib UNSEEN poll
              (cursor = last accepted UID, UIDNEXT-equivalent: fresh mail UIDs
              are always greater)
    webhook   outbound POST JSON {text, route, target} to channels.webhook.url;
              inbound arrives through the console HTTP endpoint (web server),
              therefore poll() is always empty for this route.

Transport is injectable — the same callable pattern as mycore.tg — so the
offline test suite exercises every request shape with zero network and zero
credentials.

Credential rule (repo contract): slot values resolve per call (process env
first, then the own-line slot in <repo>/secrets.env), are never stored on the
instance, and never appear in errors, logs, reprs or status output. Error
text carries the method/route and exception type only (zero secret echo).

Cursor state: <state>/channels/<route>.cursor (JSON). The state root comes
from settings["state_dir"], else env AIDOG_MC_STATE, else
<repo>/data/mycore_service — the same convention as mycore.service.state_dir().

Inbound allowlisting is the SERVICE layer's job (fail-closed there); the
adapters only report what arrived. First poll on a fresh cursor delivers the
current recent batch (no replay guard); every later poll is strictly
incremental (cursor delta).

English comments per repo i18n policy.
"""
from __future__ import annotations

import email
import hmac
import imaplib
import json
import os
import re
import smtplib
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

CHANNELS = ("discord", "slack", "email", "webhook")

DISCORD_API = "https://discord.com/api/v10"
SLACK_API = "https://slack.com/api"
DISCORD_LIMIT = 50            # messages per poll page (API max 100)
SLACK_LIMIT = 100             # messages per conversations.history call
DISCORD_CONTENT_CAP = 2000    # Discord message text limit
REQUEST_TIMEOUT = 30.0        # seconds per HTTP call
STATE_ENV = "AIDOG_MC_STATE"  # mirrors mycore.service.STATE_ENV
EMAIL_SUBJECT = "AiDog"

# secrets.env slots (names may be reported; values never).
SLOT_DISCORD_TOKEN = "CHANNEL_DISCORD_TOKEN"
SLOT_SLACK_TOKEN = "CHANNEL_SLACK_TOKEN"
SLOT_SMTP_USER = "CHANNEL_SMTP_USER"
SLOT_SMTP_PASS = "CHANNEL_SMTP_PASS"
SLOT_IMAP_USER = "CHANNEL_IMAP_USER"
SLOT_IMAP_PASS = "CHANNEL_IMAP_PASS"
SLOT_WEBHOOK_SECRET = "CHANNEL_WEBHOOK_SECRET"

__all__ = ["CHANNELS", "Inbound", "ChannelError", "Channel", "Transport",
           "load_channels", "status", "check_webhook_auth"]


class ChannelError(RuntimeError):
    """Honest transport/protocol failure. Carries method/route + exception
    type only — never a URL header, credential or payload."""


# transport: (url, payload_bytes_or_None, headers, timeout) -> response text
#   payload None = GET, bytes = POST (JSON body — every route here POSTs JSON)
Transport = Callable[[str, bytes | None, dict, float], str]


def _urllib_transport(url: str, payload: bytes | None, headers: dict,
                      timeout: float) -> str:
    req = urllib.request.Request(url, data=payload,
                                 method="POST" if payload is not None else "GET")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Status/reason only: e.url may embed a receiver path (webhook url)
        # and request headers must never surface.
        raise ChannelError(f"HTTP {e.code} {e.reason}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ChannelError(f"transport failure: {type(e).__name__}") from None


@dataclass
class Inbound:
    """One inbound message, normalized across routes. `key` is the session
    key the service layer uses to route replies (f"{route}:{sender}")."""
    route: str
    sender: str
    target: str
    text: str

    @property
    def key(self) -> str:
        return f"{self.route}:{self.sender}"


def _slot(repo: Path, name: str) -> str:
    """Resolve one credential slot: process env first, then the own-line slot
    in <repo>/secrets.env. The value is for immediate use only — never log it,
    never embed it in an error, repr or status output."""
    val = os.environ.get(name, "").strip()
    if val:
        return val
    try:
        text = (Path(repo) / "secrets.env").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"(?m)^{re.escape(name)}=(\S[^\r\n]*)$", text)
    return match.group(1).strip() if match else ""


def _channels_block(settings) -> dict:
    """The routes sub-dict of web/settings.json. Tolerates being handed the
    full settings document, the bare `channels` block, or nothing."""
    if isinstance(settings, dict):
        block = settings.get("channels")
        if isinstance(block, dict):
            return block
        if any(key in settings for key in CHANNELS):
            return settings
    return {}


def state_root(repo, settings=None) -> Path:
    """Cursor/state root: settings["state_dir"] > env AIDOG_MC_STATE >
    <repo>/data/mycore_service (mirrors mycore.service.state_dir())."""
    if isinstance(settings, dict):
        raw = settings.get("state_dir")
        if isinstance(raw, str) and raw.strip():
            return Path(raw.strip())
    env = os.environ.get(STATE_ENV, "").strip()
    if env:
        return Path(env)
    return Path(repo) / "data" / "mycore_service"


def _json(text: str, method: str):
    try:
        return json.loads(text)
    except ValueError:
        raise ChannelError(f"{method}: response is not JSON") from None


def _id_list(value) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _port(value, default: int) -> int:
    try:
        port = int(value)
        return port if 0 < port < 65536 else default
    except (TypeError, ValueError):
        return default


def _ts_key(ts: str):
    """Slack timestamp -> sortable (sec, frac) tuple; (-1, -1) = unparsable."""
    head, _, frac = str(ts).partition(".")
    try:
        return int(head), int(frac) if frac else 0
    except ValueError:
        return -1, -1


def check_webhook_auth(authorization, secret: str) -> bool:
    """Timing-safe Bearer check for the console webhook endpoint
    (Authorization: Bearer <CHANNEL_WEBHOOK_SECRET>). Fail-closed: empty
    secret, missing header, wrong scheme or wrong value all return False.
    The comparison runs on UTF-8 bytes so non-ASCII secrets cannot raise."""
    if not secret or not isinstance(authorization, str):
        return False
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return False
    return hmac.compare_digest(parts[1].strip().encode("utf-8"),
                               secret.encode("utf-8"))


class Channel:
    """Base adapter: enabled/configured gates, send wrapper (bool + honest
    log, never raises), poll wrapper (never raises, returns []), JSON cursor
    persistence. Subclasses implement configured()/_send/_poll."""

    name = ""
    inbound = True        # route accepts inbound messages (poll or endpoint)

    def __init__(self, repo, cfg=None, state=None, transport=None,
                 timeout: float = REQUEST_TIMEOUT) -> None:
        self.repo = Path(repo)
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.state = Path(state) if state is not None \
            else Path(repo) / "data" / "mycore_service"
        self.transport = transport or _urllib_transport
        self.timeout = timeout

    # ------------------------------------------------------------ gates ----
    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled"))

    def configured(self) -> tuple[bool, str]:
        """(ok, honest reason). Reasons name slots/settings keys only —
        never values."""
        raise NotImplementedError

    def _secret(self, slot: str) -> str:
        return _slot(self.repo, slot)

    def __repr__(self) -> str:      # defensive: no credential ever stored
        ok, _ = self.configured()
        return (f"<{type(self).__name__} name={self.name} "
                f"enabled={self.enabled} configured={ok}>")

    # ----------------------------------------------------------- logging ----
    def _log(self, event: str, detail: str = "") -> None:
        line = f"CHANNEL_{event} route={self.name}"
        if detail:
            line += " " + detail
        print(line, file=sys.stderr)

    # ---------------------------------------------------------- transport ---
    def _request(self, method: str, url: str, payload: bytes | None = None,
                 headers: dict | None = None) -> str:
        try:
            return self.transport(url, payload, dict(headers or {}), self.timeout)
        except ChannelError:
            raise                       # already sanitized
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            raise ChannelError(
                f"{method} failed: {type(e).__name__}") from None

    # ------------------------------------------------------------- cursor ---
    def _cursor_path(self) -> Path:
        return self.state / "channels" / f"{self.name}.cursor"

    def _load_cursor(self, default):
        try:
            raw = self._cursor_path().read_text(encoding="utf-8").strip()
        except OSError:
            return default
        if not raw:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def _save_cursor(self, value) -> None:
        try:
            path = self._cursor_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value), encoding="utf-8")
        except OSError as e:
            # Path/type only — an OSError message can embed file contents.
            self._log("CURSOR_FAIL", f"type={type(e).__name__}")

    # ------------------------------------------------------------ surface ---
    def send(self, target: str, text: str) -> bool:
        """Outbound. Returns True on accepted delivery, False on an honest
        failure (already logged, sanitized). Never raises, never echoes a
        credential."""
        try:
            if not self.enabled:
                raise ChannelError("route disabled in settings")
            ok, reason = self.configured()
            if not ok:
                raise ChannelError(f"not configured: {reason}")
            if not isinstance(text, str) or not text.strip():
                raise ChannelError("text is required")
            tgt = target.strip() if isinstance(target, str) else ""
            if not tgt:
                raise ChannelError("target is required")
            self._send(tgt, text)
            return True
        except ChannelError as e:
            self._log("SEND_FAIL", str(e))
            return False
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            self._log("SEND_FAIL", f"unexpected: {type(e).__name__}")
            return False

    def poll(self) -> list[Inbound]:
        """Single incremental inbound fetch. Never raises: disabled or
        unconfigured routes stay silent; transport/protocol failures log one
        sanitized line and return []."""
        if not self.enabled:
            return []
        ok, _ = self.configured()
        if not ok:
            return []
        try:
            return self._poll()
        except ChannelError as e:
            self._log("POLL_FAIL", str(e))
            return []
        except Exception as e:          # noqa: BLE001 — sanitize unknowns too
            self._log("POLL_FAIL", f"unexpected: {type(e).__name__}")
            return []

    def _send(self, target: str, text: str) -> None:
        raise ChannelError("send not implemented")

    def _poll(self) -> list[Inbound]:
        raise ChannelError("poll not implemented")


class DiscordChannel(Channel):
    name = "discord"

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_DISCORD_TOKEN):
            return False, f"missing {SLOT_DISCORD_TOKEN} slot"
        return True, ""

    def _send(self, target: str, text: str) -> None:
        token = self._secret(SLOT_DISCORD_TOKEN)
        if len(text) > DISCORD_CONTENT_CAP:
            mark = " …[truncated]"
            text = text[:DISCORD_CONTENT_CAP - len(mark)] + mark
        url = (f"{DISCORD_API}/channels/"
               f"{urllib.parse.quote(target, safe='')}/messages")
        payload = json.dumps({"content": text}).encode("utf-8")
        self._request("discord.send", url, payload,
                      {"Authorization": f"Bot {token}",
                       "Content-Type": "application/json"})

    def _poll(self) -> list[Inbound]:
        token = self._secret(SLOT_DISCORD_TOKEN)
        channels = _id_list(self.cfg.get("channel_ids"))
        if not channels:
            return []
        cursor = self._load_cursor({})
        if not isinstance(cursor, dict):
            cursor = {}
        out: list[tuple[tuple, Inbound]] = []
        for cid in channels:
            after = str(cursor.get(cid, "") or "")
            if not after.isdigit():
                after = ""
            url = f"{DISCORD_API}/channels/{cid}/messages?limit={DISCORD_LIMIT}"
            if after:
                url += f"&after={urllib.parse.quote(after)}"
            doc = _json(self._request("discord.poll", url, None,
                                      {"Authorization": f"Bot {token}"}),
                        "discord.poll")
            if not isinstance(doc, list):
                raise ChannelError("discord.poll: response is not a message list")
            best = int(after) if after.isdigit() else 0
            for msg in doc:
                if not isinstance(msg, dict):
                    continue
                mid = str(msg.get("id") or "")
                author = msg.get("author") if isinstance(msg.get("author"), dict) else {}
                content = str(msg.get("content") or "")
                sender = str(author.get("id") or "")
                if not mid.isdigit() or not sender or not content:
                    continue          # no text (intent off) or unattributable
                if after and int(mid) <= int(after):
                    continue          # already delivered (client-side dedup)
                out.append((int(mid), Inbound(route=self.name, sender=sender,
                                              target=cid, text=content)))
                best = max(best, int(mid))
            if best and str(best) != after:
                cursor[cid] = str(best)
        if cursor != self._load_cursor({}):
            self._save_cursor(cursor)
        out.sort(key=lambda pair: pair[0])
        return [inbound for _, inbound in out]


class SlackChannel(Channel):
    name = "slack"

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_SLACK_TOKEN):
            return False, f"missing {SLOT_SLACK_TOKEN} slot"
        return True, ""

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._secret(SLOT_SLACK_TOKEN)}",
                "Content-Type": "application/json"}

    def _send(self, target: str, text: str) -> None:
        payload = json.dumps({"channel": target, "text": text}).encode("utf-8")
        doc = _json(self._request("slack.send", f"{SLACK_API}/chat.postMessage",
                                  payload, self._auth_headers()),
                    "slack.send")
        if not isinstance(doc, dict) or doc.get("ok") is not True:
            err = doc.get("error") if isinstance(doc, dict) else "bad response"
            raise ChannelError(f"slack.send: api error: {str(err)[:80]}")

    def _poll(self) -> list[Inbound]:
        channels = _id_list(self.cfg.get("channel_ids"))
        if not channels:
            return []
        cursor = self._load_cursor({})
        if not isinstance(cursor, dict):
            cursor = {}
        out: list[tuple[tuple, Inbound]] = []
        for cid in channels:
            oldest = str(cursor.get(cid, "") or "")
            after_key = _ts_key(oldest) if oldest else None
            if after_key == (-1, -1):   # corrupted cursor -> treat as fresh
                oldest, after_key = "", None
            body: dict = {"channel": cid, "limit": SLACK_LIMIT}
            if oldest:
                body["oldest"] = oldest
            doc = _json(self._request("slack.poll", f"{SLACK_API}/conversations.history",
                                      json.dumps(body).encode("utf-8"),
                                      self._auth_headers()),
                        "slack.poll")
            if not isinstance(doc, dict) or doc.get("ok") is not True:
                err = doc.get("error") if isinstance(doc, dict) else "bad response"
                raise ChannelError(f"slack.poll: api error: {str(err)[:80]}")
            messages = doc.get("messages")
            if not isinstance(messages, list):
                raise ChannelError("slack.poll: response has no messages list")
            best = after_key if after_key is not None else (-1, -1)
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                ts = str(msg.get("ts") or "")
                sender = str(msg.get("user") or "")
                text = str(msg.get("text") or "")
                key = _ts_key(ts)
                if key == (-1, -1) or not sender or not text:
                    continue          # system/bot events without a user
                if after_key is not None and key <= after_key:
                    continue          # already delivered (client-side dedup)
                out.append((key, Inbound(route=self.name, sender=sender,
                                         target=cid, text=text)))
                best = max(best, key)
            if best != (-1, -1) and best != after_key:
                cursor[cid] = f"{best[0]}.{best[1]:06d}"
        if cursor != self._load_cursor({}):
            self._save_cursor(cursor)
        out.sort(key=lambda pair: pair[0])
        return [inbound for _, inbound in out]


class EmailChannel(Channel):
    name = "email"

    def __init__(self, *args, smtp_factory=None, imap_factory=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._smtp_factory = smtp_factory or _default_smtp
        self._imap_factory = imap_factory or _default_imap

    def configured(self) -> tuple[bool, str]:
        for slot in (SLOT_SMTP_USER, SLOT_SMTP_PASS,
                     SLOT_IMAP_USER, SLOT_IMAP_PASS):
            if not self._secret(slot):
                return False, f"missing {slot} slot"
        if not str(self.cfg.get("smtp_host") or "").strip():
            return False, "missing channels.email.smtp_host setting"
        if not str(self.cfg.get("imap_host") or "").strip():
            return False, "missing channels.email.imap_host setting"
        if not str(self.cfg.get("from") or "").strip():
            return False, "missing channels.email.from setting"
        return True, ""

    def _send(self, target: str, text: str) -> None:
        host = str(self.cfg.get("smtp_host") or "").strip()
        port = _port(self.cfg.get("smtp_port"), 587)
        ssl_on = bool(self.cfg.get("smtp_ssl"))
        user = self._secret(SLOT_SMTP_USER)
        password = self._secret(SLOT_SMTP_PASS)
        from_addr = str(self.cfg.get("from") or "").strip()
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = target
        msg["Subject"] = EMAIL_SUBJECT
        msg.set_content(text)
        srv = self._smtp_factory(host, port, ssl_on, self.timeout)
        try:
            if not ssl_on:
                srv.starttls()
            srv.login(user, password)
            srv.sendmail(from_addr, [target], msg.as_string())
        finally:
            try:
                srv.quit()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass

    def _poll(self) -> list[Inbound]:
        host = str(self.cfg.get("imap_host") or "").strip()
        port = _port(self.cfg.get("imap_port"), 993)
        user = self._secret(SLOT_IMAP_USER)
        password = self._secret(SLOT_IMAP_PASS)
        try:
            cursor = int(self._load_cursor(0) or 0)
        except (TypeError, ValueError):
            cursor = 0
        conn = self._imap_factory(host, port, self.timeout)
        out: list[tuple[int, Inbound]] = []
        try:
            conn.login(user, password)
            typ, _ = conn.select("INBOX")
            if str(typ).upper() != "OK":
                raise ChannelError("imap.poll: select failed")
            typ, data = conn.uid("search", None, "UNSEEN")
            if str(typ).upper() != "OK":
                raise ChannelError("imap.poll: search failed")
            raw = (data or [b""])[0]
            if isinstance(raw, (bytes, bytearray)):
                raw = raw.decode("ascii", "replace")
            uids = sorted(int(tok) for tok in str(raw).split() if tok.isdigit())
            fresh = [uid for uid in uids if uid >= cursor]
            for uid in fresh:
                typ, fetched = conn.uid("fetch", str(uid), "(RFC822)")
                if str(typ).upper() != "OK":
                    continue
                raw_mail = None
                for item in (fetched or []):
                    if isinstance(item, tuple) and len(item) == 2 \
                            and isinstance(item[1], (bytes, bytearray)):
                        raw_mail = bytes(item[1])
                        break
                if raw_mail is None:
                    continue          # nothing fetchable — do not retry forever
                inbound = _parse_email(raw_mail)
                if inbound is not None:
                    out.append((uid, inbound))
            # Advance to UIDNEXT (>= every uid seen now): any mail assigned
            # later is >= this value, so it is delivered exactly once.
            next_uid = 0
            try:
                resp = conn.response("UIDNEXT")
                if resp and resp[1]:
                    token = resp[1][0]
                    if isinstance(token, (bytes, bytearray)):
                        token = token.decode("ascii", "replace")
                    next_uid = int(str(token))
            except (TypeError, ValueError, IndexError):
                next_uid = 0
            new_cursor = max(next_uid, (max(fresh) + 1) if fresh else 0, cursor)
            if new_cursor != cursor:
                self._save_cursor(new_cursor)
        finally:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 — teardown is best-effort
                pass
        out.sort(key=lambda pair: pair[0])
        return [inbound for _, inbound in out]


class WebhookChannel(Channel):
    name = "webhook"
    # Inbound arrives at the console HTTP endpoint (web server), not here —
    # poll() stays empty by design; the flag above still advertises support.

    def configured(self) -> tuple[bool, str]:
        if not str(self.cfg.get("url") or "").strip():
            return False, "missing channels.webhook.url setting"
        if not self._secret(SLOT_WEBHOOK_SECRET):
            return False, f"missing {SLOT_WEBHOOK_SECRET} slot"
        return True, ""

    def _send(self, target: str, text: str) -> None:
        url = str(self.cfg.get("url") or "").strip()
        payload = json.dumps({"text": text, "route": self.name,
                              "target": target}).encode("utf-8")
        self._request("webhook.send", url, payload,
                      {"Content-Type": "application/json"})

    def _poll(self) -> list[Inbound]:
        return []            # delivered by the console endpoint (web server)


# ------------------------------------------------------------ email utils ---
def _default_smtp(host: str, port: int, ssl_on: bool, timeout: float):
    cls = smtplib.SMTP_SSL if ssl_on else smtplib.SMTP
    return cls(host, port, timeout=timeout)


def _default_imap(host: str, port: int, timeout: float):
    return imaplib.IMAP4_SSL(host, port, timeout=timeout)


def _decode_header(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 — malformed encoded-word, keep raw
        return str(value)


def _part_text(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        body = part.get_payload()
        return body if isinstance(body, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, "replace")
    except LookupError:
        return payload.decode("utf-8", "replace")


def _email_body(msg) -> str:
    plain = html = ""
    parts = msg.walk() if msg.is_multipart() else [msg]
    for part in parts:
        if getattr(part, "is_multipart", lambda: False)():
            continue
        ctype = part.get_content_type() or ""
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp:
            continue
        if ctype == "text/plain" and not plain:
            plain = _part_text(part)
        elif ctype == "text/html" and not html:
            html = _part_text(part)
    if plain.strip():
        return plain.strip()
    if html.strip():
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()
    return ""


def _parse_email(raw: bytes) -> Inbound | None:
    """Raw RFC822 -> Inbound (sender = bare From address, text = subject +
    body). Returns None for mail with no usable sender or text."""
    try:
        msg = email.message_from_bytes(raw)
    except Exception:  # noqa: BLE001 — unparsable mail is skipped, not fatal
        return None
    from_raw = _decode_header(msg.get("From", ""))
    _, sender = parseaddr(from_raw)
    sender = (sender or from_raw).strip()
    if not sender:
        return None
    subject = _decode_header(msg.get("Subject", ""))
    body = _email_body(msg)
    text = "\n".join(part for part in (subject, body) if part)
    if not text:
        return None
    return Inbound(route="email", sender=sender, target=sender, text=text)


# -------------------------------------------------------------- factory -----
def load_channels(repo, settings, transport=None) -> dict[str, Channel]:
    """Build all four route adapters. `settings` is the web/settings.json
    document (or the bare channels block). `transport` injects the HTTP
    callable for offline tests; email leaves use stdlib — inject per-instance
    factories when testing them directly."""
    block = _channels_block(settings)
    state = state_root(repo, settings)
    def build(cls):
        return cls(repo=repo, cfg=block.get(cls.name) or {}, state=state,
                   transport=transport)
    return {cls.name: build(cls) for cls in
            (DiscordChannel, SlackChannel, EmailChannel, WebhookChannel)}


def status(settings, repo) -> list[dict]:
    """One row per route for GET /api/channels:
    {name, enabled, configured, note, inbound}.
    note = skip reason: "disabled" | "missing <SLOT> slot" | "" (ready).
    Zero secret echo by construction (reasons name slots/settings keys only).
    """
    chans = load_channels(repo, settings, transport=None)
    rows: list[dict] = []
    for name in CHANNELS:
        chan = chans[name]
        try:
            ok, reason = chan.configured()
        except Exception as e:        # noqa: BLE001 — status must not raise
            ok, reason = False, f"config check failed: {type(e).__name__}"
        if not chan.enabled:
            note = "disabled"
        elif not ok:
            note = reason
        else:
            note = ""
        rows.append({"name": name,
                     "enabled": bool(chan.enabled),
                     "configured": bool(ok),
                     "note": note,
                     "inbound": bool(chan.inbound)})
    return rows
