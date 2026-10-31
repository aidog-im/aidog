"""mycore.channels — full-duplex messaging channels (C car spec: c-channels;
D car spec: imaidog-d-channels adds the three domestic routes).

Nine routes (CHANNELS, append order = status() row order), outbound send +
inbound poll, stdlib-only (no third-party deps):

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
    dingtalk  POST the full custom-robot URL from CHANNEL_DINGTALK_WEBHOOK
              (optional HMAC-SHA256 sign via CHANNEL_DINGTALK_SECRET); the
              URL picks the group, `target` is a label. Outbound only
              (inbound=False): inbound needs an enterprise app + stream.
    feishu    POST im/v1/messages with a tenant_access_token (self-built app,
              same identity in and out); poll: single-page im/v1/messages
              history per channels.feishu.chat_ids entry (cursor = raw
              create_time + boundary message ids; ms/s unit defensive).
    wechat    POST cgi-bin/message/custom/send with a cached access_token
              (module-level _TOKEN_CACHE; errmsg is NEVER surfaced — WeChat
              hints can echo appsecret fragments). Outbound only
              (inbound=False): inbound needs a verified service account
              plus a public callback endpoint.
    whatsapp  POST graph.facebook.com/v20.0/{phone_number_id}/messages with a
              Bearer token from the CHANNEL_WHATSAPP_TOKEN slot. Outbound only
              (inbound=False): inbound is STRUCTURAL — the Meta Cloud API has
              no polling endpoint, receiving needs a public Meta webhook
              callback (status() states it in the ready-row note).
    signal    POST {channels.signal.url}/v2/send (local signal-cli REST
              daemon; optional CHANNEL_SIGNAL_TOKEN as Authorization when
              set); poll GET {url}/v1/receive maps daemon envelopes to
              Inbound — best-effort local polling: no daemon -> one
              sanitized POLL_FAIL line + empty batch (fail-soft).

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

import base64
import email
import hashlib
import hmac
import imaplib
import json
import os
import re
import smtplib
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr
from pathlib import Path
from typing import Callable

from lib import profile_paths as pp  # noqa: E402  (car F §13.1: profile seams)

CHANNELS = ("discord", "slack", "email", "webhook",
            "dingtalk", "feishu", "wechat", "whatsapp", "signal")

DISCORD_API = "https://discord.com/api/v10"
SLACK_API = "https://slack.com/api"
DISCORD_LIMIT = 50            # messages per poll page (API max 100)
SLACK_LIMIT = 100             # messages per conversations.history call
DISCORD_CONTENT_CAP = 2000    # Discord message text limit
REQUEST_TIMEOUT = 30.0        # seconds per HTTP call
STATE_ENV = "AIDOG_MC_STATE"  # mirrors mycore.service.STATE_ENV
EMAIL_SUBJECT = "AiDog"
DINGTALK_TEXT_CAP = 20000      # DingTalk text message cap (chars)
WECHAT_TEXT_CAP = 1024         # WeChat customer-service cap (BYTES, utf-8 safe)
WHATSAPP_TEXT_CAP = 4096       # WhatsApp text body cap (characters)
SIGNAL_TEXT_CAP = 4096         # Signal text cap (kept consistent, characters)
WHATSAPP_API = "https://graph.facebook.com/v20.0"   # Meta Cloud API base
FEISHU_API = os.environ.get("AIDOG_FEISHU_API") or "https://open.feishu.cn/open-apis"
WECHAT_API = os.environ.get("AIDOG_WECHAT_API") or "https://api.weixin.qq.com"
# Base URLs are env-overridable as an E2E test seam (the offline suite and
# production never set AIDOG_FEISHU_API / AIDOG_WECHAT_API; tmp/e2e_* points
# them at a local sink to verify the real wire shapes without credentials).
TOKEN_REFRESH_LEAD = 120       # refresh a cached token this many secs early
FEISHU_MS_ABOVE = 10 ** 11     # create_time above this is ms, not seconds
FEISHU_TOKEN_INVALID = (99991663, 99991664, 99991665, 99991668)
FEISHU_QUOTA_CODES = (99991400, 99991403)
WECHAT_TOKEN_CODES = (40001, 40014, 42001)
WECHAT_WINDOW_CODE = 45015     # outside the 48h customer-session window

# secrets.env slots (names may be reported; values never).
SLOT_DISCORD_TOKEN = "CHANNEL_DISCORD_TOKEN"
SLOT_SLACK_TOKEN = "CHANNEL_SLACK_TOKEN"
SLOT_SMTP_USER = "CHANNEL_SMTP_USER"
SLOT_SMTP_PASS = "CHANNEL_SMTP_PASS"
SLOT_IMAP_USER = "CHANNEL_IMAP_USER"
SLOT_IMAP_PASS = "CHANNEL_IMAP_PASS"
SLOT_WEBHOOK_SECRET = "CHANNEL_WEBHOOK_SECRET"
SLOT_DINGTALK_WEBHOOK = "CHANNEL_DINGTALK_WEBHOOK"
SLOT_DINGTALK_SECRET = "CHANNEL_DINGTALK_SECRET"
SLOT_FEISHU_APP_ID = "CHANNEL_FEISHU_APP_ID"
SLOT_FEISHU_APP_SECRET = "CHANNEL_FEISHU_APP_SECRET"
SLOT_WECHAT_APPID = "CHANNEL_WECHAT_APPID"
SLOT_WECHAT_APP_SECRET = "CHANNEL_WECHAT_APP_SECRET"
SLOT_WHATSAPP_TOKEN = "CHANNEL_WHATSAPP_TOKEN"
SLOT_SIGNAL_TOKEN = "CHANNEL_SIGNAL_TOKEN"

# Process-wide token cache (feishu tenant token / wechat access_token).
# Adapter instances may be rebuilt per poll, so only a MODULE-level dict
# keeps the cache alive across instances. Key = app_id/appid, value =
# (token, expiry epoch). Credentials never reach disk or logs.
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}

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
        text = pp.secrets_path(Path(repo)).read_text(encoding="utf-8")
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
    <profile>/state > <repo>/data/mycore_service (car F: the tail two come
    from the shared profile seam, mirrored by mycore.service.state_dir())."""
    if isinstance(settings, dict):
        raw = settings.get("state_dir")
        if isinstance(raw, str) and raw.strip():
            return Path(raw.strip())
    return pp.state_dir(Path(repo))


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


def _int_or_none(value) -> int | None:
    """String/int timestamp -> int, None when unparsable (never raises)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _truncate_bytes(text: str, cap: int) -> str:
    """UTF-8-safe truncation to `cap` BYTES including the style marker —
    never splits a multi-byte character (WeChat 1024-byte text limit)."""
    mark = " …[truncated]"
    raw = text.encode("utf-8")
    if len(raw) <= cap:
        return text
    keep = cap - len(mark.encode("utf-8"))
    if keep <= 0:
        return mark.encode("utf-8")[:cap].decode("utf-8", "ignore")
    return raw[:keep].decode("utf-8", "ignore") + mark


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
    # Ready-row honesty: non-empty = appended as the status() note when the
    # route is enabled AND configured (structural/best-effort inbound limits).
    inbound_note = ""

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
        """Single incremental inbound fetch. Never raises: outbound-only
        routes (inbound=False) return [] before any gate or transport call;
        disabled or unconfigured routes stay silent; transport/protocol
        failures log one sanitized line and return []."""
        if not self.inbound:
            return []
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


# ---------------------------------------------------- domestic routes (D) ---
class _FeishuTokenRejected(Exception):
    """Internal signal (never surfaces raw): the API refused the cached
    tenant token with a token-invalid code. Carries the numeric code only;
    the retry boundary converts it to an honest ChannelError."""

    def __init__(self, code) -> None:
        super().__init__(str(code))
        self.code = code


def _feishu_text(item: dict) -> str:
    """Extract the text of a feishu message: body.content for msg_type=text
    is itself a JSON string {"text": ...}; other message types carry no
    text for this route ("")."""
    if str(item.get("msg_type") or "") != "text":
        return ""
    body = item.get("body") if isinstance(item.get("body"), dict) else {}
    content = body.get("content")
    if not isinstance(content, str) or not content:
        return ""
    try:
        doc = json.loads(content)
    except ValueError:
        return ""
    text = doc.get("text") if isinstance(doc, dict) else None
    return str(text) if text else ""


class DingTalkChannel(Channel):
    """DingTalk custom robot (group webhook): POST to the full URL stored in
    the slot (the URL picks the group — `target` is a label only), optional
    HMAC-SHA256 sign. Outbound only: an inbound leg needs an enterprise app
    plus a WebSocket stream or a public callback endpoint. An HTTP 200 body
    is still checked for errcode (non-zero = honest failure)."""

    name = "dingtalk"
    inbound = False

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_DINGTALK_WEBHOOK):
            return False, f"missing {SLOT_DINGTALK_WEBHOOK} slot"
        return True, ""

    def _send(self, target: str, text: str) -> None:
        # `target` is a label only — the webhook URL decides the group.
        url = self._secret(SLOT_DINGTALK_WEBHOOK)
        if len(text) > DINGTALK_TEXT_CAP:
            mark = " …[truncated]"
            text = text[:DINGTALK_TEXT_CAP - len(mark)] + mark
        secret = self._secret(SLOT_DINGTALK_SECRET)
        if secret:                       # optional robot sign (spec: D car)
            ts = str(round(time.time() * 1000))     # millisecond timestamp
            digest = hmac.new(secret.encode("utf-8"),
                              f"{ts}\n{secret}".encode("utf-8"),
                              hashlib.sha256).digest()
            sign = urllib.parse.quote_plus(
                base64.b64encode(digest).decode("ascii"))
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}timestamp={ts}&sign={sign}"
        payload = json.dumps({"msgtype": "text",
                              "text": {"content": text}}).encode("utf-8")
        doc = _json(self._request("dingtalk.send", url, payload,
                                  {"Content-Type": "application/json"}),
                    "dingtalk.send")
        if not isinstance(doc, dict):
            raise ChannelError("dingtalk.send: response is not a JSON object")
        code = doc.get("errcode", 0)
        if code:
            # errmsg may surface (platform text, no credential echo) — 80 cap
            errmsg = str(doc.get("errmsg") or "")[:80]
            raise ChannelError(f"dingtalk.send: errcode={code} {errmsg}")

    def _poll(self) -> list[Inbound]:
        return []            # inbound needs an enterprise app (inbound=False)


class FeishuChannel(Channel):
    """Feishu (Lark) self-built app route: tenant_access_token auth, im/v1
    text messages out, single-page chat-history polling in (same identity
    in and out — a second bot would split reply attribution). target =
    chat_id; allow-listing of sender open_ids is the service layer's job."""

    name = "feishu"

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_FEISHU_APP_ID):
            return False, f"missing {SLOT_FEISHU_APP_ID} slot"
        if not self._secret(SLOT_FEISHU_APP_SECRET):
            return False, f"missing {SLOT_FEISHU_APP_SECRET} slot"
        return True, ""

    def _tenant_token(self) -> str:
        """Cached tenant_access_token (module-level _TOKEN_CACHE, refreshed
        TOKEN_REFRESH_LEAD seconds before expiry)."""
        app_id = self._secret(SLOT_FEISHU_APP_ID)
        app_secret = self._secret(SLOT_FEISHU_APP_SECRET)
        now = time.time()
        hit = _TOKEN_CACHE.get(app_id)
        if isinstance(hit, tuple) and len(hit) == 2:
            try:
                if float(hit[1]) - TOKEN_REFRESH_LEAD > now:
                    return str(hit[0])
            except (TypeError, ValueError):
                pass                         # corrupt cache entry -> refetch
        doc = _json(self._request(
            "feishu.token",
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json.dumps({"app_id": app_id,
                        "app_secret": app_secret}).encode("utf-8"),
            {"Content-Type": "application/json"}), "feishu.token")
        if not isinstance(doc, dict):
            raise ChannelError("feishu.token: response is not a JSON object")
        code = doc.get("code")
        token = str(doc.get("tenant_access_token") or "")   # field: expire
        if code != 0 or not token:
            raise ChannelError(f"feishu.token: code={str(code)[:40]}")
        try:
            expires = int(doc.get("expire") or 7200)
        except (TypeError, ValueError):
            expires = 7200
        _TOKEN_CACHE[app_id] = (token, now + expires)
        return token

    def _with_token(self, method: str, op):
        """Run op(token); on a token-invalid code drop the cache, refetch and
        retry EXACTLY once, then fail honestly (no loops)."""
        for attempt in (0, 1):
            token = self._tenant_token()
            try:
                return op(token)
            except _FeishuTokenRejected as e:
                _TOKEN_CACHE.pop(self._secret(SLOT_FEISHU_APP_ID), None)
                if attempt:
                    raise ChannelError(
                        f"{method}: tenant token rejected "
                        f"(code={e.code})") from None
        raise ChannelError(f"{method}: tenant token retry exhausted")

    @staticmethod
    def _check_code(method: str, doc) -> dict:
        """Protocol gate for send/poll bodies: non-zero code -> honest
        ChannelError, token-invalid codes -> the retryable internal signal."""
        if not isinstance(doc, dict):
            raise ChannelError(f"{method}: response is not a JSON object")
        code = doc.get("code")
        if code in FEISHU_TOKEN_INVALID:
            raise _FeishuTokenRejected(code)
        if code in FEISHU_QUOTA_CODES:
            raise ChannelError(
                f"{method}: code={code} rate limited or quota exceeded")
        if code != 0:
            raise ChannelError(f"{method}: code={str(code)[:40]}")
        return doc

    def _send(self, target: str, text: str) -> None:
        payload = json.dumps({"receive_id": target, "msg_type": "text",
                              "content": json.dumps({"text": text})}) \
            .encode("utf-8")
        url = f"{FEISHU_API}/im/v1/messages?receive_id_type=chat_id"

        def op(token: str):
            doc = _json(self._request(
                "feishu.send", url, payload,
                {"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"}), "feishu.send")
            return self._check_code("feishu.send", doc)

        self._with_token("feishu.send", op)

    def _poll(self) -> list[Inbound]:
        chat_ids = _id_list(self.cfg.get("chat_ids"))
        if not chat_ids:
            return []
        cursor = self._load_cursor({})
        if not isinstance(cursor, dict):
            cursor = {}
        out: list[tuple[tuple, Inbound]] = []
        for cid in chat_ids:
            state = cursor.get(cid) if isinstance(cursor.get(cid), dict) else {}
            t_val = _int_or_none(state.get("t"))
            ids = {str(x) for x in state.get("ids", [])} \
                if isinstance(state.get("ids"), list) else set()
            # start_time is SECONDS (defensive ms->s conversion of the raw
            # cursor value); fresh polls omit it (current batch, discord-like)
            url = (f"{FEISHU_API}/im/v1/messages?container_id_type=chat"
                   f"&container_id={urllib.parse.quote(cid, safe='')}")
            if t_val is not None:
                url += f"&start_time={t_val // 1000 if t_val > FEISHU_MS_ABOVE else t_val}"
            url += "&sort_type=ByCreateTimeAsc&page_size=50"

            def op(token: str, url: str = url):
                doc = _json(self._request("feishu.poll", url, None,
                                          {"Authorization": f"Bearer {token}"}),
                            "feishu.poll")
                return self._check_code("feishu.poll", doc)

            doc = self._with_token("feishu.poll", op)
            data = doc.get("data")
            items = data.get("items") if isinstance(data, dict) else None
            if items is None:
                items = []
            if not isinstance(items, list):
                raise ChannelError("feishu.poll: items is not a list")
            rows: list[tuple[int, str, str, str]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("message_id") or "")
                raw = _int_or_none(item.get("create_time"))
                if raw is None or not mid:
                    continue        # unparsable id/timestamp is never delivered
                sender_doc = item.get("sender") \
                    if isinstance(item.get("sender"), dict) else {}
                sender = str(sender_doc.get("id") or "")
                rows.append((raw, mid, sender, _feishu_text(item)))
            if not rows:
                continue
            max_raw = max(r[0] for r in rows)
            if t_val is not None and max_raw < t_val:
                max_raw = t_val                 # the cursor never regresses
            at_boundary = {m for r, m, _, _ in rows if r == max_raw}
            if t_val is None or max_raw > t_val:
                new_ids = at_boundary
            else:
                new_ids = ids | at_boundary     # same-ms arrivals extend ids
            if max_raw != t_val or new_ids != ids:
                cursor[cid] = {"t": max_raw, "ids": sorted(new_ids)}
            for raw, mid, sender, text in rows:
                # acceptance: raw > t, or raw == t with an id not yet seen
                if t_val is not None and not (
                        raw > t_val or (raw == t_val and mid not in ids)):
                    continue                    # already delivered / older
                if not sender or not text:
                    continue                    # no sender or not a text message
                out.append(((raw, mid),
                            Inbound(route=self.name, sender=sender,
                                    target=cid, text=text)))
        if cursor != self._load_cursor({}):
            self._save_cursor(cursor)
        out.sort(key=lambda pair: pair[0])
        return [inbound for _, inbound in out]


class WeChatChannel(Channel):
    """WeChat Official Account customer-service route: access_token auth
    (module-level _TOKEN_CACHE, keyed by appid) + text messages to an
    openid. Outbound only: inbound needs a verified service account and a
    public callback endpoint (structurally blocked for now). errmsg is NEVER
    surfaced — WeChat error hints can echo appsecret fragments, so only the
    numeric errcode reaches logs and errors."""

    name = "wechat"
    inbound = False

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_WECHAT_APPID):
            return False, f"missing {SLOT_WECHAT_APPID} slot"
        if not self._secret(SLOT_WECHAT_APP_SECRET):
            return False, f"missing {SLOT_WECHAT_APP_SECRET} slot"
        return True, ""

    def _access_token(self) -> str:
        """Cached access_token (module-level _TOKEN_CACHE keyed by appid,
        refreshed TOKEN_REFRESH_LEAD seconds before expires_in)."""
        appid = self._secret(SLOT_WECHAT_APPID)
        secret = self._secret(SLOT_WECHAT_APP_SECRET)
        now = time.time()
        hit = _TOKEN_CACHE.get(appid)
        if isinstance(hit, tuple) and len(hit) == 2:
            try:
                if float(hit[1]) - TOKEN_REFRESH_LEAD > now:
                    return str(hit[0])
            except (TypeError, ValueError):
                pass                         # corrupt cache entry -> refetch
        url = (f"{WECHAT_API}/cgi-bin/token?grant_type=client_credential"
               f"&appid={urllib.parse.quote(appid, safe='')}"
               f"&secret={urllib.parse.quote(secret, safe='')}")
        doc = _json(self._request("wechat.token", url, None), "wechat.token")
        if not isinstance(doc, dict):
            raise ChannelError("wechat.token: response is not a JSON object")
        code = doc.get("errcode", 0)
        token = str(doc.get("access_token") or "")
        if code:
            # errmsg dropped on purpose — WeChat hints can echo the appsecret
            raise ChannelError(f"wechat.token: errcode={str(code)[:40]}")
        if not token:
            raise ChannelError("wechat.token: response has no access_token")
        try:
            expires = int(doc.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expires = 7200
        _TOKEN_CACHE[appid] = (token, now + expires)
        return token

    def _send(self, target: str, text: str) -> None:
        appid = self._secret(SLOT_WECHAT_APPID)
        body = _truncate_bytes(text, WECHAT_TEXT_CAP)   # 1024 BYTES, utf-8 safe
        payload = json.dumps({"touser": target, "msgtype": "text",
                              "text": {"content": body}}).encode("utf-8")
        for attempt in (0, 1):
            token = self._access_token()
            url = (f"{WECHAT_API}/cgi-bin/message/custom/send"
                   f"?access_token={urllib.parse.quote(token, safe='')}")
            doc = _json(self._request("wechat.send", url, payload,
                                      {"Content-Type": "application/json"}),
                        "wechat.send")
            if not isinstance(doc, dict):
                raise ChannelError("wechat.send: response is not a JSON object")
            code = doc.get("errcode", 0)
            if not code:
                return
            if code in WECHAT_TOKEN_CODES and attempt == 0:
                _TOKEN_CACHE.pop(appid, None)   # stale token -> refetch ONCE
                continue
            if code == WECHAT_WINDOW_CODE:
                raise ChannelError(
                    "wechat.send: errcode=45015 outside the 48h "
                    "customer-service session window")
            # errmsg never surfaces — WeChat hints can echo appsecret pieces
            raise ChannelError(f"wechat.send: errcode={str(code)[:40]}")

    def _poll(self) -> list[Inbound]:
        return []            # inbound needs a verified service account


class WhatsAppChannel(Channel):
    """Meta WhatsApp Cloud API route: Bearer token from the
    CHANNEL_WHATSAPP_TOKEN slot + the non-secret `phone_number_id` setting,
    text messages POSTed to graph.facebook.com/v20.0/{id}/messages.

    Honest boundary — outbound only: inbound is STRUCTURALLY absent, not
    merely unimplemented. The Cloud API exposes no polling endpoint;
    receiving messages requires a Meta webhook callback on a PUBLIC https
    URL, so `inbound = False`, poll() is silent by design, and a ready
    status row carries `inbound_note` stating exactly that."""

    name = "whatsapp"
    inbound = False
    inbound_note = "inbound needs a public Meta callback (structural)"

    def configured(self) -> tuple[bool, str]:
        if not self._secret(SLOT_WHATSAPP_TOKEN):
            return False, f"missing {SLOT_WHATSAPP_TOKEN} slot"
        if not str(self.cfg.get("phone_number_id") or "").strip():
            return False, "missing channels.whatsapp.phone_number_id setting"
        return True, ""

    def _send(self, target: str, text: str) -> None:
        token = self._secret(SLOT_WHATSAPP_TOKEN)
        pnid = str(self.cfg.get("phone_number_id") or "").strip()
        if len(text) > WHATSAPP_TEXT_CAP:
            mark = " …[truncated]"
            text = text[:WHATSAPP_TEXT_CAP - len(mark)] + mark
        payload = json.dumps({"messaging_product": "whatsapp", "to": target,
                              "type": "text",
                              "text": {"preview_url": False,
                                       "body": text}}).encode("utf-8")
        url = f"{WHATSAPP_API}/{urllib.parse.quote(pnid, safe='')}/messages"
        doc = _json(self._request("whatsapp.send", url, payload,
                                  {"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"}),
                    "whatsapp.send")
        if not isinstance(doc, dict):
            raise ChannelError("whatsapp.send: response is not a JSON object")
        err = doc.get("error")
        if err:
            # Meta error objects carry code + message (platform text, no
            # credential echo) — 80-char cap, same policy as dingtalk.
            code = err.get("code") if isinstance(err, dict) else ""
            msg = str((err.get("message") if isinstance(err, dict) else err)
                      or "")[:80]
            raise ChannelError(f"whatsapp.send: error code={code} {msg}")
        if "messages" not in doc:
            raise ChannelError("whatsapp.send: response has no messages entry")

    def _poll(self) -> list[Inbound]:
        return []            # inbound = a public Meta callback (structural)


class SignalChannel(Channel):
    """Signal route via a LOCAL signal-cli REST daemon: explicit non-secret
    `url` setting (http(s); empty = not configured — no default is guessed,
    a wrong guess would POST to an unintended host) + an OPTIONAL
    CHANNEL_SIGNAL_TOKEN slot sent as `Authorization: Bearer` only when set
    (the vanilla daemon has no auth; a reverse proxy may require one).

    Honest boundary — inbound is BEST-EFFORT LOCAL POLLING: GET {url}/v1/receive
    maps daemon envelopes to Inbound; when the daemon is absent or refuses
    the connection the failure degrades to one sanitized POLL_FAIL line and
    an empty batch (fail-soft, the feishu polling convention), which a ready
    status row states in `inbound_note`. No cursor is persisted: a successful
    receive delivers the daemon's current batch once."""

    name = "signal"
    inbound = True
    inbound_note = "inbound best-effort local daemon polling (no daemon -> empty)"

    def configured(self) -> tuple[bool, str]:
        if not str(self.cfg.get("url") or "").strip():
            return False, "missing channels.signal.url setting"
        return True, ""

    def _base_url(self) -> str:
        return str(self.cfg.get("url") or "").strip().rstrip("/")

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        token = self._secret(SLOT_SIGNAL_TOKEN)
        if token:                       # optional auth — header only when set
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _send(self, target: str, text: str) -> None:
        if len(text) > SIGNAL_TEXT_CAP:
            mark = " …[truncated]"
            text = text[:SIGNAL_TEXT_CAP - len(mark)] + mark
        payload = json.dumps({"message": text, "numbers": [target]}) \
            .encode("utf-8")
        doc = _json(self._request("signal.send", f"{self._base_url()}/v2/send",
                                  payload, self._headers()), "signal.send")
        if not isinstance(doc, (dict, list)):
            raise ChannelError("signal.send: response is not a JSON object")

    def _poll(self) -> list[Inbound]:
        doc = _json(self._request("signal.poll",
                                  f"{self._base_url()}/v1/receive", None,
                                  self._headers()), "signal.poll")
        if isinstance(doc, dict):       # newer daemon builds wrap envelopes
            doc = doc.get("envelopes")
        if doc is None:
            return []                   # nothing pending (honest empty batch)
        if not isinstance(doc, list):
            raise ChannelError("signal.poll: response is not a message list")
        out: list[Inbound] = []
        for env in doc:
            if not isinstance(env, dict):
                continue
            data = env.get("dataMessage") \
                if isinstance(env.get("dataMessage"), dict) else {}
            sender = str(env.get("source") or env.get("sourceName") or "")
            text = str(env.get("message") or data.get("message") or "")
            if not sender or not text:
                continue                 # unattributable or non-text envelope
            # target = the sender's number: replies go back to the same device
            out.append(Inbound(route=self.name, sender=sender, target=sender,
                               text=text))
        return out


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
    """Build every route adapter in CHANNELS. `settings` is the
    web/settings.json document (or the bare channels block). `transport`
    injects the HTTP callable for offline tests; email leaves use stdlib —
    inject per-instance factories when testing them directly."""
    block = _channels_block(settings)
    state = state_root(repo, settings)
    def build(cls):
        return cls(repo=repo, cfg=block.get(cls.name) or {}, state=state,
                   transport=transport)
    return {cls.name: build(cls) for cls in
            (DiscordChannel, SlackChannel, EmailChannel, WebhookChannel,
             DingTalkChannel, FeishuChannel, WeChatChannel,
             WhatsAppChannel, SignalChannel)}


def status(settings, repo) -> list[dict]:
    """One row per route for GET /api/channels:
    {name, enabled, configured, note, inbound}.
    note = skip reason: "disabled" | "missing <SLOT> slot" | "" (ready) |
    the route's inbound_note when ready and it carries an honest inbound
    boundary (structural/best-effort — never a credential).
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
            note = chan.inbound_note
        rows.append({"name": name,
                     "enabled": bool(chan.enabled),
                     "configured": bool(ok),
                     "note": note,
                     "inbound": bool(chan.inbound)})
    return rows
