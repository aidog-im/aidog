# Feature spec — Multi-route channels (task 2 / Phase C)

Status: **implemented** (2026-10-22, v0.5.25; domestic routes added in
v0.5.26 — see `channels_domestic.md`). Adds a second and further
messaging family beside the live Telegram route: a route framework with four
adapters — **Discord, Slack, Email, Webhook** — each with an outbound send
path and an inbound path, wired into the mycore service loop and the web
console.

**Honest boundary:** Discord and Slack inbound use REST polling (no
WebSocket client exists in this stdlib-only repo), email inbound polls IMAP,
and the webhook inbound is an HTTP endpoint on the console. External
*end-to-end* verification against the real Discord/Slack/email providers is
**pending credentials** — what has been verified for real is listed in
§5 (webhook round trip incl. a live model turn, and a full SMTP dialogue
against a local sink). Nothing here claims provider connectivity that was
not observed.

## 1. Route framework (`mycore/channels.py`)

```python
CHANNELS = ("discord", "slack", "email", "webhook")

class Inbound:            # route, sender, target, text; .key = "route:sender"
class ChannelError(RuntimeError)   # honest, sanitized, zero credential
class Channel:
    name: str
    inbound: bool
    def configured(self) -> tuple[bool, str]        # (ok, honest reason)
    def send(self, target: str, text: str) -> bool  # never raises
    def poll(self) -> list[Inbound]                 # never raises, [] = nothing

def load_channels(repo, settings, transport=None) -> dict[str, Channel]
def status(settings, repo) -> list[dict]            # {name, enabled, configured, note, inbound}
def check_webhook_auth(authorization, secret) -> bool  # fail-closed, timing-safe
```

- **Transport injection** mirrors `mycore/tg.py`: every network call goes
  through a transport callable, so the whole suite runs offline with fakes.
- **Credential rule** (README contract 2): secrets resolve at call time from
  `secrets.env` slots and never appear in logs, reprs, exceptions, argv, or
  API responses. `configured()` failures name the *slot*, never the value.
- **Cursor state**: `<state>/channels/<route>.cursor` (state dir follows the
  service convention: `AIDOG_MC_STATE` > `data/mycore_service`). Polls are
  incremental — Discord `after=<id>`, Slack `oldest=<ts>`, email IMAP UIDNEXT.
- **Fail-soft by design**: disabled or unconfigured routes stay silent; a
  transport failure logs one sanitized line and returns `[]`/`False`. A route
  can never crash the service loop.

## 2. Configuration

**`secrets.env` slots** (zero echo, gitignored):

| slot | used by |
|---|---|
| `CHANNEL_DISCORD_TOKEN` | Discord bot token (out + in) |
| `CHANNEL_SLACK_TOKEN` | Slack bot token (out + in) |
| `CHANNEL_SMTP_USER` / `CHANNEL_SMTP_PASS` | email outbound login |
| `CHANNEL_IMAP_USER` / `CHANNEL_IMAP_PASS` | email inbound login |
| `CHANNEL_WEBHOOK_SECRET` | webhook route: outbound readiness + inbound Bearer gate |

**`web/settings.json` `channels` block** (non-secret, validated by
`POST /api/settings`, all-or-nothing like every other partition):

```json
"channels": {
  "discord": {"enabled": false, "allow": [], "channel_ids": []},
  "slack":   {"enabled": false, "allow": [], "channel_ids": []},
  "email":   {"enabled": false, "allow": [], "to": "", "from": "",
              "smtp_host": "", "smtp_port": 587, "smtp_ssl": false,
              "imap_host": "", "imap_port": 993},
  "webhook": {"enabled": false, "url": ""}
}
```

- `allow` = inbound sender whitelist (platform user id / email address).
  **Empty = all inbound denied** — the same fail-closed rule as the TG
  `AIDOG_TG_ALLOWED_USERS` gate.
- Route ready ⇔ `enabled` **and** `configured()` (settings + all needed slots).

## 3. Inbound path (service integration)

`mycore/service.py` gained a poll leg next to the evolution and cron legs,
throttled by `CHANNEL_POLL_SEC = 30`:

```
run() loop → _maybe_poll_channels()
  → load_channels(settings) → for each route: poll()
  → allowlist gate (channels.<route>.allow contains sender?) 
      no  → throttled CHANNEL_DENY log, no reply, ever
      yes → "/" → _command   |   else → _pipeline   → route.send(target, reply)
```

- The session key is the string `route:sender` (e.g. `discord:123456`);
  session files, model overrides and `/reset` archives work unchanged on
  string keys. The TG typing heartbeat only fires for integer (TG) chats.
- **Cron origin extended**: `scheduler` origin is now `int | str | None`,
  string form `route:target`. A `/cron` job created from a channel chat
  delivers back through the same route (`delivered=channel:<route>`); a dead
  route falls back to the original `origin > MYCORE_WD_CHAT > log` chain.
- dry-run semantics hold: a channel send under `--dry-run` logs and previews,
  never transmits.

## 4. Webhook inbound (console endpoint)

`POST /api/channels/webhook` on the loopback console (`127.0.0.1:8790`):

1. `Authorization: Bearer <CHANNEL_WEBHOOK_SECRET>` — slot absent → **503**
   naming the slot; wrong/absent bearer → **401**; comparison is
   `hmac.compare_digest`. The value never appears in any response.
2. Body `{text, sender?}` (size caps mirror the chat route).
3. One agent turn runs **in the web process** over an isolated session
   `webhook:<sender|default>` (never the console `web` session), no SSE —
   the reply returns as `{"ok": true, "reply": "..."}`.

Outbound webhook sends `POST {text, route, target}` to `channels.webhook.url`
— so a webhook route is bidirectional with any HTTP receiver.

## 5. Verification (what actually ran)

| leg | how | result |
|---|---|---|
| webhook outbound | real POST to a local HTTP receiver | payload captured, E2E 8/8 |
| webhook inbound | real Bearer auth + **live model turn** on the console | `reply` returned |
| webhook authz | wrong bearer → 401, no secret echo; slot absent → 503 | verified |
| email outbound | full SMTP dialogue (EHLO/STARTTLS/AUTH/MAIL/RCPT/DATA) against a local sink | message captured |
| discord/slack | offline suite with fake transports (request shapes, cursors, zero-leak) | mycore suite green |
| inbound allowlist | offline service tests: allow→reply, empty→deny, `/` command, str session | mycore suite green |

External provider connectivity (real Discord/Slack/IMAP) is **pending
credentials** and is not claimed anywhere in the product surface.

## 6. API surface

- `GET /api/channels` → `{"channels": [{name, enabled, configured, note,
  inbound}, …]}` — readiness only, never secrets.
- `POST /api/channels/webhook` → §4.
- `POST /api/settings` accepts the `channels` block with strict validation
  (unknown keys, wrong types, bad ports and non-http(s) webhook URLs are
  rejected all-or-nothing).

## 7. Tests

(Phase C counts at v0.5.25; the D car added `test_channels_domestic`, the
G car (v0.5.32) added `test_channels_wa_signal` — current run.py total
**1020/1020**, console smoke **154/154**.)

- `mycore.tests.run::test_channels` — 50 checks (four-route send shapes,
  Authorization header vs. zero-leak assertions, cursor incrementality,
  honest missing-slot reasons, webhook auth, status structure).
- `mycore.tests.run::test_channels_service` — 27 checks (poll leg, allowlist
  fail-closed, command/pipeline dispatch, string-key sessions, cron origin
  round trip, dry-run, loop survival on poll errors).
- `mycore.tests.run::test_channels_wa_signal` — 17 checks (§9 request
  shapes, gates, inbound mapping, fail-soft, the 9-route CHANNELS contract,
  zero credential echo).
- `tests/web_console_p3.py` — 13 channel-side checks (status endpoint,
  settings partition validation, webhook gates, settings restore, plus the
  §9 whatsapp/signal validation legs).
- E2E script: `tmp/e2e_channels.py` (8 checks, restores settings/secrets
  byte-for-byte; tmp/ is gitignored).

## 8. Telegram forum-topic isolation (car 4a-M3)

A Telegram **forum topic** message carries `message_thread_id`; `handle_update`
reads it (int only — a string/bool field is treated as absent):

- **Pipeline sessions are topic-scoped**: a non-command message in topic `T`
  of chat `C` runs over the session file `C:T.jsonl` (`session_key` on
  `_pipeline`; `C.jsonl` stays the chat-level file). Two topics of the same
  chat never share context — deliberate isolation, **cross-topic context
  transfer is v1 out of scope** (documented boundary: copy what you need
  into the new topic yourself).
- **Commands stay chat-level**: `/cron`, `/checkpoint`, `/model`, … key on
  the plain chat id even when invoked inside a topic — their state (jobs,
  snapshots, model overrides) is chat-wide by design.
- **Replies thread back**: `_send(cid, text, thread_id=T)` adds
  `message_thread_id` to `sendMessage` (commands included — the answer lands
  where the question was asked); `None` omits the field entirely, so plain
  chats are byte-identical to the pre-topic payload.
- Everything chat-wide is unchanged: typing heartbeat, cron origin, hooks
  and log lines keep the numeric chat id.

**Verification**: offline only — `mycore/tests/run::test_tg_topics` drives a
fake transport (`mk_update(..., thread_id=)` + recorded
`message_thread_id`): threaded reply payload, `C:T` session isolation across
two topics, commands not opening a topic session, non-int thread field
ignored, plain-chat zero regression. **No live TG E2E was re-run for this
feature** (offline suite is the evidence, per the car-4a scope decision).

## 9. WhatsApp + Signal routes (channel growth 7 -> 9)

Two routes appended to `CHANNELS` (append order = `status()` row order):
`("discord", "slack", "email", "webhook", "dingtalk", "feishu", "wechat",
"whatsapp", "signal")`. Same framework contract as §1 — injectable
transport, `configured()` naming keys/slots only, fail-soft poll, zero
credential echo.

### 9.1 WhatsApp (`whatsapp`) — Meta WhatsApp Cloud API, outbound only

- **Slot** (zero echo): `CHANNEL_WHATSAPP_TOKEN` (Meta permanent/system user
  token) — required.
- **Settings** (`POST /api/settings` schema: `enabled`, `phone_number_id`):
  `phone_number_id` = non-secret single-line id, **max 64 chars**; empty or
  missing token ⇒ `configured()` is `False` with an honest reason naming the
  slot/setting key, never a value.
- **Outbound shape**: `POST https://graph.facebook.com/v20.0/{phone_number_id}/messages`
  with `Authorization: Bearer <token>` and JSON
  `{"messaging_product": "whatsapp", "to": <target>, "type": "text",
  "text": {"preview_url": false, "body": <text>}}`; text capped at **4096
  chars** (`…[truncated]` marker); a response without a `messages` entry or
  carrying an `error` object is an honest `ChannelError` (code + 80-char
  platform message, dingtalk policy).
- **Inbound honest boundary**: **structural**, not pending — the Cloud API
  exposes no polling endpoint, so `inbound = False`, `poll()` is silent, and
  a ready status row carries the note
  `inbound needs a public Meta callback (structural)` (receiving needs a Meta
  webhook callback on a public HTTPS URL).
- **Test legs**: `tests/web_console_p3.py` — whatsapp legal merge -> 200,
  unknown `whatsapp` subkey -> 400, `phone_number_id` over 64 -> 400; core
  suite: URL shape, Bearer-in-header-only, body shape, 4096 cap,
  missing-slot gate with zero transport calls, `inbound=False` silent poll,
  status note + zero echo.

### 9.2 Signal (`signal`) — local signal-cli REST daemon, bidirectional

- **Slot** (zero echo): `CHANNEL_SIGNAL_TOKEN` — **optional**; sent as
  `Authorization: Bearer` only when set (the vanilla daemon has no auth; a
  reverse proxy may require one). Absent ⇒ no `Authorization` header at all.
- **Settings** (`POST /api/settings` schema: `enabled`, `url`, `allow`):
  `url` = explicit `http(s)://` base of the local daemon (**empty = not
  configured** — no default is guessed, a wrong guess would POST to an
  unintended host); `allow` = fail-closed inbound sender allow-list, same
  rule as §2 (empty/absent = every inbound denied).
- **Outbound shape**: `POST {url}/v2/send` with JSON
  `{"message": <text>, "numbers": [<target>]}`; text capped at 4096 chars
  (consistent with WhatsApp).
- **Inbound honest boundary**: **best-effort local daemon polling** —
  `GET {url}/v1/receive` maps daemon envelopes to
  `Inbound(route="signal", sender=<source>, target=<source>, text=…)`; when
  the daemon is absent or refuses the connection the failure degrades to one
  sanitized `CHANNEL_POLL_FAIL` line and an empty batch (fail-soft, the
  feishu convention), and a ready status row carries the note
  `inbound best-effort local daemon polling (no daemon -> empty)`. No cursor
  file is written: a successful receive delivers the daemon's current batch
  once.
- **Test legs**: `tests/web_console_p3.py` — signal bad URL scheme -> 400
  (route-generic `channels.signal.url must be an http(s) URL` message);
  core suite: send URL/body, auth header only when the slot is set, empty-`url`
  gate, poll envelope -> `Inbound` mapping, daemon-absent fail-soft, status
  note + zero echo.
