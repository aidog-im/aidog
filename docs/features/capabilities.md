# Capabilities Catalog

Status: shipped in car 4a (`web/server.py`), extended in car 4b with the
`browser` / `terminal` / `local_models` blocks, in car 4c with the
`media` block and in the W1 Web3 car with the `exchange` block. Companion pages:
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
    "read_only": true,
    "venues": [{"key": "okx", "label": "OKX", "kind": "cex", "auth": "key",
                "configured": false, "read_only": true, "symbol": "BTC-USDT",
                "summary": "Spot REST v5, signed with a Read-only API key",
                "reason": "not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)"},
               "… 7 more venue rows (kraken, jupiter, velora, hyperliquid, dydx, gmx, rpc)"],
    "tools": ["exchange_markets"],
    "note": "exchange account tools not configured: …"
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
| `exchange` | `mycore.exchanges.catalog()` + `build_registry().exchange_note` | the W1 read-only venue block: 8 catalog rows (names, kinds, presence booleans, honest per-venue `reason`), the live `exchange_*` tool names and the registry's honest `note` when account tools are withheld — **booleans, names and sentences only**, never a slot value and never a URL — see [exchanges.md](exchanges.md) |

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

## The `exchange` block (W1 — read-only Web3 venues)

| Field | Shape | Meaning |
|---|---|---|
| `read_only` | always `true` | the whole block describes read-only surfaces; W1 ships no order placement (hard code boundary, not a setting) |
| `venues` | 8 rows `{key, label, kind, auth, configured, read_only, symbol, summary[, reason]}` | one row per catalog venue (OKX, Kraken, Jupiter, Velora, Hyperliquid, dYdX, GMX, public RPC); `configured` is slot **presence** (keyless chain venues are always `true`); `reason` appears only for a key venue without a full slot set and **names the missing slots** (e.g. `not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)`) |
| `tools` | string array | the `exchange_*` names present in the **live** registry — `exchange_markets` always; `exchange_portfolio` / `exchange_orders` only when at least one CEX slot set exists |
| `note` | string \| null | the registry's honest `exchange_note` when the account tools are withheld (naming exactly which slots are missing), else `null` |

Fail-soft like every other probe: a broken exchange module degrades to an
empty `venues`/`tools` plus `note: "exchange probe failed"` and one stderr
line, never a 500. The smoke asserts the serialized block against every
`secrets.env` slot value and rejects credential-bearing key names in it.
The console's public market probe is a **separate** endpoint —
`GET /api/exchange/quote` (200 lines / 400 honest error / 429 at 10 requests
per minute); account reads never surface over HTTP — see
[exchanges.md](exchanges.md) §5.

## Console display status

**Wave 1 = API only.** The catalog ships as an endpoint + this document; no
console DOM was touched in this car (the console app shell was owned by a
parallel workstream in the same wave, and the `getElementById` contract audit
cost more than a decorative read-only panel was worth). Planned display: a
read-only "Capabilities" line/panel fed from `/api/capabilities` (model,
tool count, skill count, enabled channels, cron jobs) — deferred to the
follow-up that owns `web/static/app.js`.

## Testing evidence

`tests/web_console_p3.py`: shape check (the catalog blocks with their inner
types), the 7-route list, the car-4b block checks (`browser.available` agreed
against the live registered tool list, `terminal` kind-only with the raw spec
withheld, `local_models.count == len(detected)` with a loopback host per
entry), the car-4c `media` block check (all four sub-blocks with their inner
types, `active ∈ {edge, byok}`), the W1 `exchange` block check (8 read-only
venue rows with `read_only: true` and the `tools` list, serialized block free
of slot values and credential-bearing key names, plus
`GET /api/exchange/quote` answering an unknown venue with an honest 400), and
the zero-credential assertion (response
text contains no slot value and no secret-like keys) — applied to the
serialized `media` block on its own as well: no URL, no slot value.
