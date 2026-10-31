# Capabilities Catalog

Status: shipped in car 4a (`web/server.py`), extended in car 4b with the
`browser` / `terminal` / `local_models` blocks, in car 4c with the
`media` block, in the W1 Web3 car with the `exchange` block and in the W2
order car with the top-level `web3` block (plus the `exchange` block's real,
non-read-only surface). Companion pages:
[openai_api_server.md](openai_api_server.md),
[browser_automation.md](browser_automation.md),
[terminal_backends.md](terminal_backends.md),
[zero_key_onboarding.md](zero_key_onboarding.md),
[voice_session.md](voice_session.md),
[image_generation.md](image_generation.md),
[tts_engines.md](tts_engines.md),
[connectors.md](connectors.md), [exchanges.md](exchanges.md).

## Endpoint

`GET /api/capabilities` — **read-only, no auth** (same contract as
`GET /api/status`: loopback status surfaces stay open, writes stay gated).

```json
{
  "version": "<VERSION file>",
  "mycore": {
    "model": "<configured model id>",
    "upstreams_configured": true,
    "mcp_servers": {"count": 2, "names": ["demo", "files"]}
  },
  "tools":   [{"name": "read_file", "description": "…"}],
  "skills":  [{"name": "…", "description": "…"}],
  "channels": {
    "all": ["discord","slack","email","webhook","dingtalk","feishu","wechat"],
    "enabled":   ["webhook"],
    "configured": []
  },
  "hooks": {"events": [], "count": 0},
  "cron":  {"jobs": 0},
  "browser":  {"available": true},
  "terminal": {"backend": "local", "spec_set": false},
  "local_models": {"count": 0, "detected": []},
  "media": {
    "stt":  {"available": true, "model": "base"},
    "image": {"configured": false, "reason": "no image endpoint (set media.image_base_url in settings)"},
    "tts":  {"engines": {"edge": true, "byok": false}, "active": "edge"},
    "connectors": {"linear": false, "notion": false}
  },
  "exchange": {
    "read_only": false,
    "orderable": ["okx", "kraken", "binance"],
    "order_gate": "prepare -> console approval -> execute",
    "withdrawals": false,
    "onchain_transfers": false,
    "venues": [{"key": "okx", "label": "OKX", "kind": "cex", "auth": "key",
                "configured": false, "read_only": false, "orderable": true,
                "symbol": "BTC-USDT",
                "summary": "Spot REST v5, signed with a Read-only API key — order placement only via prepare -> console approval -> execute",
                "reason": "not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)"},
               "… 8 more venue rows (kraken, jupiter, velora, hyperliquid, dydx, gmx, rpc, binance)"],
    "tools": ["exchange_markets"],
    "note": "exchange account tools not configured: …"
  },
  "web3": {
    "auto_usd_per_tx": 0.0,
    "auto_usd_daily": 0.0,
    "pending_plans": 0,
    "approved_plans": 0,
    "spent_today_usd": 0.0,
    "ledger_day": ""
  }
}
```

Field sources:

| Field | Source | Notes |
|---|---|---|
| `version` | `VERSION` file | single source of truth |
| `mycore.model` / `upstreams_configured` | `cli.resolve_model_config()` | boolean only — the upstream spec (urls, env names) never serializes |
| `mycore.mcp_servers` | `mcp.parse_config(mcp.raw_config(...))` | **names + count only**; an invalid config reports `note`, never the raw text |
| `tools` | `build_registry().schema()` | names + descriptions of the agent's live tool set — follows `mycore.tools_off` (disabled tools are absent, see [tool_toggles.md](tool_toggles.md)) |
| `skills` | skill frontmatter discovery | read, never executed — same source as the console gallery |
| `channels` | `collect_channels()` + `channel_routes()` | `all` lists all 9 routes; `enabled`/`configured` are name lists (`configured()` consults slot **presence** only) |
| `hooks` | settings `hooks` block | absent block (before the event-hooks car) → empty list — honest, not fabricated |
| `cron` | scheduler state file | absent/corrupt registry → `0` |
| `browser` | `mycore.browser.available()` | **availability bool only** — a discovery probe that never launches anything; `true` iff the six `browser_*` entries appear in `tools` (probe failure → `available:false` + `note`) — see [browser_automation.md](browser_automation.md) |
| `terminal` | `terminal.parse(settings mycore.shell)` | parsed backend **kind** (`local`/`docker`/`ssh`) + `spec_set` bool; the raw spec (user, host, identity path) **never serializes**; invalid line → `backend:null` + `note` — see [terminal_backends.md](terminal_backends.md) |
| `local_models` | `mycore.localmodels.probe()` | `count` + `detected[]` of `{kind, base_url, model}` — loopback endpoints, `userinfo` stripped before serializing; probe failure → `count:0` + `note` (never fabricated) — see [zero_key_onboarding.md](zero_key_onboarding.md) |
| `media` | `media_capability(settings)` | four sub-blocks, booleans and engine/model **names** only — see the next section; the serialized block is asserted to contain no URL and no slot value — see [voice_session.md](voice_session.md), [image_generation.md](image_generation.md), [tts_engines.md](tts_engines.md), [connectors.md](connectors.md) |
| `exchange` | `mycore.exchanges.catalog()` + `build_registry().exchange_note` | the Web3 venue block (W1+W2): **9 catalog rows** (names, kinds, presence booleans, `read_only` iff the row is a chain source, `orderable` iff it is one of the three CEX, honest per-venue `reason`), the block-level order surface (`read_only: false`, `orderable`, `order_gate`, `withdrawals: false`, `onchain_transfers: false`), the live `exchange_*` tool names and the registry's honest `note` when account/order tools are withheld — **booleans, names and sentences only**, never a slot value and never a URL — see [exchanges.md](exchanges.md) |
| `web3` | `mycore.exchanges.thresholds(settings)` + `plan_summary()` | the W2 order-approval block: the two fail-closed auto-approval caps in USD plus plan counters and the Beijing ledger date — pure numbers and one date string, no credential, no venue data — see [exchanges.md](exchanges.md) §6 |

## The `media` block (car 4c — four sub-blocks)

| Sub-block | Shape | Meaning |
|---|---|---|
| `stt` | `{available: bool, model?: "tiny"\|"base"\|"small"}` | local speech-to-text presence (import + weight-cache probe, never loads a model) + the configured console model; `model` present iff available |
| `image` | `{configured: bool, reason?: string}` | BYOK image generation readiness; `reason` appears only when unconfigured, is **URL-stripped** (any scheme://… is replaced by the word `url`) and bounded (160 chars) |
| `tts` | `{engines: {edge: bool, byok: bool}, active: "edge"\|"byok"}` | engine *presence* on this host (edge = CLI installed; byok = base URL resolvable **and** credential policy passes — loopback may run keyless, remote needs a slot) + the settings-selected active engine |
| `connectors` | `{linear: bool, notion: bool}` | credential-slot **presence** per connector — values never leave `connectors.status()` |

**Zero-key serialization guarantee.** The block obeys the same promise as the
rest of the catalog, and the smoke asserts it on the serialized `media` text
alone: no `secrets.env` value, no URL of any scheme, no key, no path —
booleans, engine names and model-size names only. Probe failures degrade to
`available:false` / `configured:false` + a bounded honest `reason`, or to the
block defaulting to `false` when the optional module is absent — never a
fabricated `true`.

## Zero-credential promise

The response contains **names, counts, and booleans only**:

- no `secrets.env` value ever appears (the web smoke asserts the serialized
  response against every slot value in the file);
- no credential-bearing URL, no raw MCP config, no upstream spec;
- `configured` is presence-of-a-slot, never the slot's content.

The three car-4b blocks obey the same promise: `browser` is a boolean,
`terminal` is a parsed kind plus a boolean (the raw `mycore.shell` line never
serializes), and `local_models` carries display-only local records
(`kind`/`base_url`/`model`) from keyless loopback endpoints — no slot value,
no credential, no URL userinfo crosses into any of them. The car-4c `media`
block is held to it on its own serialized text (§ *The `media` block*): the
smoke asserts **no URL and no slot value** there independently of the full-
catalog assertion.

## The `exchange` block (W1 + W2 — Web3 venues and the real order surface)

| Field | Shape | Meaning |
|---|---|---|
| `read_only` | always `false` (W2) | the block states the REAL surface: three CEX venues can carry an order — only through `prepare -> console approval -> execute`. W1's `read_only: true` was true then and is deliberately flipped now; claiming read-only over an orderable venue would be a false promise |
| `orderable` | `["okx", "kraken", "binance"]` | exactly the venues whose rows can carry an order (mirrors `exchanges.ORDERABLE_VENUES`); the six chain sources are never orderable |
| `order_gate` | `"prepare -> console approval -> execute"` | the one sentence describing HOW an order happens — dry run, then the owner's console decision behind the `WEB_TOKEN` gate, then `execute(plan_id)`; no other path exists |
| `withdrawals` | always `false` | no withdrawal capability exists in the module — not gated, absent (asserted by the core suite) |
| `onchain_transfers` | always `false` | same absence for on-chain transfers / tx signing |
| `venues` | 9 rows `{key, label, kind, auth, configured, read_only, orderable, symbol, summary[, reason]}` | one row per catalog venue (OKX, Kraken, Jupiter, Velora, Hyperliquid, dYdX, GMX, public RPC, Binance); `configured` is slot **presence** (keyless chain venues are always `true`); `read_only` is `true` **iff** the row is a chain source and `orderable` **iff** it is one of the three CEX (they are exact complements); `reason` appears only for a key venue without a full slot set and **names the missing slots** (e.g. `not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)`); orderable rows append the gate to `summary` |
| `tools` | string array | the `exchange_*` names present in the **live** registry — `exchange_markets` always; `exchange_portfolio` / `exchange_orders` / `exchange_prepare_order` / `exchange_execute_order` / `exchange_plans` only when at least one CEX slot set exists |
| `note` | string \| null | the registry's honest `exchange_note` when the account/order tools are withheld (naming exactly which slots are missing), else `null` |

Fail-soft like every other probe: a broken exchange module degrades to the
same keys with empty `venues`/`tools` plus `note: "exchange probe failed"`
and one stderr line, never a 500. The smoke asserts the serialized block
against every `secrets.env` slot value and rejects credential-bearing key
names in it. The console's public market probe is a **separate** endpoint —
`GET /api/exchange/quote` (200 lines / 400 honest error / 429 at 10 requests
per minute); account reads never surface over HTTP — see
[exchanges.md](exchanges.md) §5.

## The `web3` block (W2 — order-approval caps and plan counters)

| Field | Type | Meaning |
|---|---|---|
| `auto_usd_per_tx` | number (`0..1e6`) | per-transaction auto-approval cap in USD from settings `web3.auto_usd_per_tx`; **fail-closed**: missing block / non-numeric / boolean / out-of-range collapses to `0.0`, and `0` means nothing auto-approves |
| `auto_usd_daily` | number (`0..1e6`) | daily cap in USD from settings `web3.auto_usd_daily`, same fail-closed parsing; both caps default to `0` — every plan waits for the owner |
| `pending_plans` | int ≥ 0 | stored plans awaiting a decision (TTL sweep applied before counting) |
| `approved_plans` | int ≥ 0 | plans ready to execute (owner-approved or auto-approved and still valid) |
| `spent_today_usd` | number ≥ 0 | today's executed notional from the Beijing-date ledger — manual approvals count too |
| `ledger_day` | `"YYYY-MM-DD"` or `""` | the ledger's Beijing calendar date (same day boundary as the cron scheduler); empty when there is no ledger entry yet |

Fail-soft like every other probe: an exchange-module failure degrades to
the **same six keys** with zeroed values plus `note: "web3 probe failed"`
and one stderr line — never a 500, never a fabricated number. The smoke
asserts the six keys' types and ranges and re-checks the serialized block
against every `secrets.env` slot value (numbers and one date only — no
capability here can leak a credential). See [exchanges.md](exchanges.md)
§6 for the threshold semantics and the approval flow these numbers feed.

## Console display status

**Rendered since the frontend coverage car (0.5.39).** The catalog is still
an unauthenticated loopback endpoint first — the console simply stopped
throwing most of it away: the Overview view now renders an `#capGrid`
key/value table (tools + `exchange_*` names, skills, enabled/configured
channels, configured hook events, cron job count, browser availability,
terminal backend, detected local models, MCP server names, exchange venue
count, the two web3 caps and the env-override names) plus a `#cronList`
backed by the new `GET /api/cron`. The Settings view consumes the two
catalog additions directly: `mycore.known_tools` feeds the `#toolsOffList`
checkbox grid and `env_overrides` feeds `#envOverrideChip` (the honest
"this env var beats your console value" note). Every row is names and
counts only — the same zero-credential promise the endpoint already had.
Evidence: `tmp/e2e_frontend_coverage.py` legs `L1`/`L3`/`L5` and
`tmp/e2e_frontend_ui.js` leg `U5`; the index for this coverage is
[frontend_coverage.md](frontend_coverage.md).

## Testing evidence

`tests/web_console_p3.py`: shape check (the catalog blocks with their inner
types), the channels route list (all 9 routes), the car-4b block checks (`browser.available` agreed
against the live registered tool list, `terminal` kind-only with the raw spec
withheld, `local_models.count == len(detected)` with a loopback host per
entry), the car-4c `media` block check (all four sub-blocks with their inner
types, `active ∈ {edge, byok}`), the W1/W2 `exchange` block check (9 venue
rows with `read_only` iff chain and `orderable` iff one of the three CEX,
block `read_only: false` + `order_gate` + `withdrawals`/`onchain_transfers`
`false`, serialized block free of slot values and credential-bearing key
names, plus
`GET /api/exchange/quote` answering an unknown venue with an honest 400),
the W2 `web3` block check (six keys, numeric caps in `0..1e6`, non-negative
plan counters, `ledger_day` empty-or-`YYYY-MM-DD`, serialized block free of
credential material), the `GET /api/exchange/plans` plain-read check (list
shape + no credential material) and the `POST /api/exchange/plans/<id>`
refusal legs (401 without bearer / 400 bad action / 404 unknown id, token
never echoed), and
the zero-credential assertion (response
text contains no slot value and no secret-like keys) — applied to the
serialized `media` block on its own as well: no URL, no slot value.
- **Console coverage car (0.5.39)**: `env_overrides` (names only — a sweep
  asserts the exported value never serializes) and `mycore.known_tools`
  (25 names) added to this payload; `GET /api/cron` added alongside it.
  Evidence: `tmp/e2e_frontend_coverage.py` (`L1` `L3` `L5`) and
  `tmp/e2e_frontend_ui.js` (`U5`) — see [frontend_coverage.md](frontend_coverage.md).
