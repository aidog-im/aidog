# Feature spec — Domestic channels (task 2 / Phase D)

Status: **implemented** (2026-10-22, v0.5.26). Extends the multi-route
framework from `docs/features/channels.md` (Phase C) with the three domestic
routes — **DingTalk, Feishu, WeChat** — taking `CHANNELS` from 4 to 7
(`discord slack email webhook dingtalk feishu wechat`). Same adapter
contract as Phase C: transport-injected, `send()`/`poll()` never raise,
`configured()` names slots/settings keys only, zero credential echo.

**Honest boundary (unchanged discipline):** external provider connectivity is
**pending credentials** — what this release verified for real is listed in
§5 (all three routes against local HTTP sinks, plus the console endpoint over
the live web process). Two of the three routes are outbound-only for
structural reasons stated per route below; nothing here claims provider
connectivity that was not observed.

## 1. Routes and directions

| route | outbound | inbound | why |
|---|---|---|---|
| `dingtalk` | custom-robot webhook (URL + optional HMAC sign) | **none** (`inbound=False`) | DingTalk delivers robot messages only through the Stream protocol (WebSocket) or an HTTP callback to a **public** URL; this console is loopback-only, so neither path exists here |
| `feishu` | self-built app REST send (`im/v1/messages`, receive_id_type=chat_id) | **REST polling** (`im/v1/messages` list, single page per poll, cursor-incremental) | same app identity in and out — a second bot would split reply attribution |
| `wechat` | official-account customer-service message (`custom/send`, touser=openid) | **none** (`inbound=False`) | inbound needs a certified service account **and** a publicly reachable callback URL — both structurally absent on a loopback console |

- `Channel.inbound` is now honored by the base `poll()`: `inbound=False`
  routes return `[]` before any gate or transport call (the webhook route
  keeps `inbound=True` — its inbound arrives through the console endpoint).
- **target semantics**: dingtalk = a label only (the webhook URL decides the
  group; the target never enters the request), feishu = `chat_id`,
  wechat = `openid`.
- Feishu inbound sender allow-listing is the service layer's job — identical
  fail-closed rule as Phase C (`channels.feishu.allow`, empty = deny all).

## 2. Configuration

**`secrets.env` slots** (zero echo; `configured()` failures name the slot,
never the value):

| slot | used by |
|---|---|
| `CHANNEL_DINGTALK_WEBHOOK` | full robot webhook URL (contains `access_token`) |
| `CHANNEL_DINGTALK_SECRET` | optional robot sign key (absent = unsigned webhook) |
| `CHANNEL_FEISHU_APP_ID` / `CHANNEL_FEISHU_APP_SECRET` | feishu token + send + poll |
| `CHANNEL_WECHAT_APPID` / `CHANNEL_WECHAT_APP_SECRET` | wechat token + send |

**`web/settings.json` `channels` block additions** (validated by
`POST /api/settings` like every other partition):

```json
"dingtalk": {"enabled": false},
"feishu":   {"enabled": false, "allow": [], "chat_ids": []},
"wechat":   {"enabled": false}
```

`chat_ids` = the feishu chats polled for inbound (same fail-closed list
validation as `allow`/`channel_ids`).

## 3. Protocol contracts (all verified against multiple public
implementations before coding — response shapes are never guessed)

- **DingTalk send**: `POST` to the slot URL; when `CHANNEL_DINGTALK_SECRET`
  is set, append `&timestamp=<ms>&sign=<urlencoded>` where
  `sign = quote_plus(base64(HMAC_SHA256(key=secret, msg="<ms>\n<secret>"")))`.
  Payload `{"msgtype":"text","text":{"content":…}}` (text capped at 20000
  chars). **HTTP 200 still requires `errcode == 0`** — DingTalk signals API
  failures in the body.
- **Feishu token**: `POST /auth/v3/tenant_access_token/internal`
  `{app_id, app_secret}` → `{"code":0,"tenant_access_token":…,"expire":7200}`
  (the field is `expire`, not `expires_in`). Cached in the module-level
  `_TOKEN_CACHE` (key = app_id, refreshed `TOKEN_REFRESH_LEAD` 120 s before
  expiry).
- **Feishu send**: `POST /im/v1/messages?receive_id_type=chat_id` with
  `Authorization: Bearer <token>`, body
  `{receive_id, msg_type:"text", content:"{\"text\":…}"}`; `code != 0` fails.
- **Feishu poll**: `GET /im/v1/messages?container_id_type=chat&container_id=…[&start_time=<sec>]`
  + `sort_type=ByCreateTimeAsc&page_size=50`, single page per poll. Items
  carry `message_id / msg_type / create_time / sender.id / body.content`
  (content is a JSON string). `create_time` unit is treated defensively:
  `> 1e11` = milliseconds. First poll (fresh cursor) omits `start_time`
  (delivers the current batch, like Discord); later polls send the cursor in
  **seconds**. Cursor = `{"t": raw create_time, "ids": [ids at t]}` —
  acceptance is `raw > t` or (`raw == t` and id not yet seen), so same-t
  arrivals deliver once and boundary duplicates never repeat.
  Token-invalid codes `99991663/99991664/99991665/99991668` invalidate the
  cache and retry **once**; quota/rate codes `99991400/99991403` fail
  honestly without retry. Only the `code` is ever surfaced, never `msg`.
- **WeChat token**: `GET /cgi-bin/token?grant_type=client_credential&appid=…&secret=…`
  → `{"access_token","expires_in":7200}` (same module-level cache).
- **WeChat send**: `POST /cgi-bin/message/custom/send?access_token=…` body
  `{"touser": target, "msgtype":"text", "text":{"content":…}}`, content
  capped at **1024 bytes** (UTF-8 safe truncation, marker included in the
  budget). **`errmsg` is never surfaced anywhere** — WeChat error hints can
  embed secret fragments — only `errcode`. `45015` (outside the 48 h
  customer-session window) fails honestly; `40001/40014/42001` invalidate
  the token cache and retry **once**.

## 4. Outbound triggers (console + cron)

- **`POST /api/channels/send`** `{route, target, text}` on the loopback
  console, `Authorization: Bearer <WEB_TOKEN>` (same gate contract as the
  settings write):

  | case | status | body |
  |---|---|---|
  | missing/wrong bearer | 401 (503 if `WEB_TOKEN` slot absent) | `{"error":"unauthorized"}` |
  | unknown route / bad target / empty or >4000-char text | 400 | `{"error":"validation failed","details":[…]}` |
  | route disabled in settings | 409 | `{"ok":false,"error":"route disabled in settings"}` |
  | `configured()` false | 409 | `{"ok":false,"error":"missing <SLOT> …"}` (slot name only) |
  | adapter accepted delivery | 200 | `{"ok":true}` |
  | adapter reported failure | 502 | `{"ok":false,"error":"delivery failed"}` (detail stays server-side) |
  | channel module unavailable | 503 | `{"ok":false,"error":"channel module unavailable"}` |

- **`mycore.scheduler --origin`** now accepts `route:target` strings (e.g.
  `--origin dingtalk:ops`) in addition to integer chat ids, so a cron job
  created outside a channel turn can still deliver through a domestic route.
  Malformed origins exit rc=2 with an honest one-line message (no
  traceback). Delivery back through `route:target` reuses the Phase C chain
  unchanged (`_cron_deliver` → `_channel_send`).

## 5. Verification (what actually ran)

| leg | how | result |
|---|---|---|
| dingtalk outbound | real POST to a local sink; sink recomputed the HMAC sign from the captured ms timestamp with the known secret | sign matches, payload shape exact, `errcode!=0` → honest failure |
| feishu outbound | real token → send dialogue to a local sink (Bearer + receive_id/content asserted) | cached token reuse + token-invalid retry-once observed |
| feishu inbound | real poll against a local sink across three polls (fresh → incremental → fully deduped) | unit defense, `start_time` in seconds, boundary-id dedup, non-text skip all observed |
| wechat outbound | real token → `custom/send` dialogue to a local sink | query/body shapes, byte-safe 1024 truncation, `errmsg` never surfaced (secret planted in `errmsg` did not appear in any log), 45015 honest failure |
| console endpoint | live `POST /api/channels/send` through the running web process to the local sink | 401/400/409/502/200 contract table above, observed end-to-end |
| scheduler origin | real `mycore.scheduler` CLI subprocess | `route:target` accepted and stored as a string origin; malformed form rejected rc=2 |

E2E ran **before** release notes were finalized:
`tmp/e2e_channels_dom.py` — **44 checks, rc=0, ALL VERIFIED** (settings +
secrets snapshotted and restored byte-for-byte; tmp/ is gitignored).
External connectivity against the real DingTalk/Feishu/WeChat services is
**pending credentials** and is not claimed anywhere in the product surface.

## 6. Tests

- `mycore.tests.run::test_channels_domestic` — 57 checks (per-route send
  shapes, sign recomputation, honest missing-slot reasons, token caches with
  retry-once, cursor unit defense, byte-safe truncation, errcode-only error
  surfacing, zero-leak sweeps, scheduler origin parsing).
- `mycore.tests.run` total: **645/645** (588 from Phase C + 57; the Phase C
  check counts are unchanged).
- `tests/web_console_p3.py` — **87/87** (Phase C's 77 + 10: endpoint gates,
  new-partition validation, round-trip).
- E2E: `tmp/e2e_channels_dom.py` (44 checks, byte-for-byte restore).
