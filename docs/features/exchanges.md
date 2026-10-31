# Exchanges (CEX orders behind a human approval gate + chain market data, Web3 phase 2 W1 → W2)

Status: read-only layer shipped in the W1 car; order placement with console
approval shipped in the **W2 car** (`mycore/exchanges.py`; tool wiring in
`mycore/tools.py::build_registry`; console surface in `web/server.py`).
Companion pages: [capabilities.md](capabilities.md) (the `exchange` and
`web3` blocks), [tool_toggles.md](tool_toggles.md); skill packs
`overlay/skills/okx-read-only/`, `overlay/skills/kraken-read-only/` and
`overlay/skills/binance-read-only/`.

Web3 pilot: **9 venues in one catalog** (`okx`, `kraken`, `jupiter`,
`velora`, `hyperliquid`, `dydx`, `gmx`, `rpc`, `binance`) behind six
`exchange_*` tools: `exchange_markets / exchange_portfolio / exchange_orders
/ exchange_prepare_order / exchange_execute_order / exchange_plans`. Public
market data needs **no credential at all**; account reads and every W2 order
tool are slot-gated with the same capability-honest pattern as
[connectors.md](connectors.md) — **no slot → the tool does not register** and
the registry carries an honest `exchange_note` naming exactly which slots are
missing. A capability we don't have is never advertised as a tool that will
fail.

Three hard contracts this module enforces **in code, not in configuration**
(owner decrees, 2026-10-24 / 2026-10-25):

1. **ORDERS ONLY THROUGH A HUMAN APPROVAL GATE.** Public reads are keyless;
   account reads are slot-gated. Order placement exists **only** as
   `exchange_prepare_order` (a dry run that stores a plan) → the **owner**
   approves or rejects that plan in the console (Bearer `WEB_TOKEN` write
   gate) → `exchange_execute_order(plan_id)`. `execute_order` takes a plan id
   and NOTHING else — venue, side, symbol, type, qty and price are read back
   from the stored plan — and it **fails closed** on a chain of checks that
   no settings value, env var or model instruction can relax: default
   thresholds are `0` so every plan waits for a human, **market orders never
   auto-approve at any threshold**, and an auto-approval is re-checked
   against the day's ledger at execution time. **Withdrawals, on-chain
   transfers and transaction signing do not exist in `mycore/exchanges.py` at
   all** — no function, no endpoint, no settings key can move funds off a
   venue — and the core suite asserts that absence against this very source
   file (§7, §9).
2. **ZERO CREDENTIAL ECHO.** Slot values are read only to sign a request and
   are never returned, logged, formatted or embedded in an error. Errors name
   the SLOT (`OKX_API_KEY`), never the value. The plan store and every
   capability/plan payload are asserted credential-free by the smoke.
3. **SLOT GATED.** Public market data needs no slot; account reads and the
   W2 order tools require the venue's full slot set, and a missing slot
   raises an honest error naming exactly what to set.

## 1. Venue catalog

| Venue | Kind | Auth | Slots (`secrets.env`) | Default symbol |
|---|---|---|---|---|
| `okx` | cex | key | `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_API_PASSPHRASE` | `BTC-USDT` |
| `kraken` | cex | key | `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` | `XBT/USD` |
| `jupiter` | chain | public | **none (built in)** | `SOL` |
| `velora` | chain | public | **none (built in)** | `USDC/WETH` |
| `hyperliquid` | chain | public | **none (built in)** | `BTC` |
| `dydx` | chain | public | **none (built in)** | `BTC-USD` |
| `gmx` | chain | public | **none (built in)** | `BTC` |
| `rpc` | chain | public | **none (built in)** | `base` (`base \| ethereum \| solana`) |
| `binance` | cex | key | `BINANCE_API_KEY` / `BINANCE_API_SECRET` | `BTCUSDT` |

Binance joined in W2 as the third CEX (owner decree 2026-10-25). It is our
own **thin adapter over the official, documented REST surface** — see §2 for
why there is no official Binance MCP and why no community MCP will ever be
used instead.

The six chain venues are keyless and non-custodial market sources: there is
no credential to gate, so they are built in with **NO slot**. The catalog row
(`key`, `label`, `kind`, `auth`, `configured`, `read_only`, `orderable`,
`symbol`, `summary`, plus an honest `reason` for a key venue without a full
slot set) is what the capability directory serializes — names, kinds,
booleans and sentences only, never a slot value and never a URL.

**`read_only` follows orderability** (W2 semantics): a row claims
`read_only: true` only when this product really cannot write there, so the
six chain rows are `read_only: true / orderable: false` while the three CEX
rows are `read_only: false / orderable: true` — an orderable venue carrying
`read_only: true` would be a false promise. The three orderable rows append
the gate to their summary: `order placement only via prepare -> console
approval -> execute`.

## 2. Protocol contracts (verified live 2026-10-24)

Protocol facts were taken from the vendors' **own endpoints and official
documentation**, exercised against production that day — not from memory.

**Public endpoints (keyless):**

- **OKX** — `GET /api/v5/market/ticker?instId=…`.
- **Kraken** — `GET /0/public/Ticker?pair=…`; BTC is spelled `XBT`,
  separators are dropped (`XBTUSD`), and `BTC` maps to `XBT` automatically.
- **Binance** — `GET /api/v3/ticker/24hr?symbol=BTCUSDT` (and the depth feed
  below); symbol form is the compact ticker with **no separator** (`BTCUSDT`).
- **Jupiter** — `GET /price/v3?ids=<mint>` on `lite-api.jup.ag`; built-in
  **verified** mint map (`SOL`/`USDC`/`USDT`/`WETH` — only mints that answered
  a real price are listed) or a raw Solana mint address.
- **Velora** — `GET /v2/quote?chainId=1&partner=…&srcToken=…` with **exactly
  1 unit** of the source token in; the USD read comes from
  `route.origin.input` / `output.amountUSD`.
- **Hyperliquid** — `POST /info {"type":"allMids"}`; observed to answer a
  **flat** `coin -> mid` map (the wrapped shape is accepted too).
- **dYdX** — `GET /v4/perpetualMarkets?market=BTC-USD` on
  `indexer.dydx.trade`.
- **GMX** — `GET /v1/pairs` on `arbitrum.gmxapi.io`.
- **Public JSON-RPC chain heads** — `base` = `mainnet.base.org`
  (`eth_blockNumber`), `ethereum` = `ethereum-rpc.publicnode.com`
  (`eth_blockNumber`), `solana` = `api.mainnet-beta.solana.com` (`getSlot`).
  Honest selection note: **the Cloudflare Ethereum gateway was dropped**
  because it refused `eth_blockNumber` when tested that day — every entry in
  this list answered a real request, it is not a remembered-by-heart list.

**Private read endpoints (slot-gated):**

- **OKX** — `GET /api/v5/account/balance` + `GET
  /api/v5/trade/orders-pending?instType=SPOT`, signed
  `Base64(HMAC-SHA256(secret, timestamp + method + requestPath + body))`
  with headers `OK-ACCESS-KEY` / `OK-ACCESS-SIGN` / `OK-ACCESS-TIMESTAMP`
  (ISO 8601 UTC milliseconds) / `OK-ACCESS-PASSPHRASE`.
- **Kraken** — `POST /0/private/Balance` + `POST /0/private/OpenOrders` with
  headers `API-Key` / `API-Sign` =
  `Base64(HMAC-SHA512(path + SHA256(nonce + postdata),
  base64_decode(secret)))` and a **strictly increasing millisecond nonce**.
  The Kraken implementation is verified against Kraken's **official
  published example vector** (nonce `1616492376594`, path
  `/0/private/AddOrder` →
  `4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==`);
  that published documentation example is why the signature code is
  trustworthy **without a live key**.
- **Binance** — `GET /api/v3/account` + `GET /api/v3/openOrders`, signed:
  header `X-MBX-APIKEY: <key>`, all parameters (including `timestamp` and
  `recvWindow`) travel in the **query string**, and
  `signature = HMAC_SHA256(secret, queryString)` appended **hex-encoded**
  (`&signature=…`). Hitting an endpoint like `/api/v3/order` without that
  parameter answered `HTTP 400 {"code":-1102,"msg":"Mandatory parameter
  'signature' …"}` when tested — the signing requirement is observed, not
  assumed.

**W2 order sends (slot-gated, only from `execute_order`):**

| Venue | Endpoint | Wire format |
|---|---|---|
| OKX | `POST /api/v5/trade/order` | JSON body `{"instId","tdMode":"cash","side","ordType","sz"[,"px"]}` with `Content-Type: application/json`; the **body bytes participate in the v5 pre-hash signature** (sign exactly what is sent) |
| Kraken | `POST /0/private/AddOrder` | form body `nonce + pair + type + ordertype + volume [+ price]`, same `API-Sign` construction as W1 |
| Binance | `POST /api/v3/order` | parameters in the **query string** (the POST carries no body), `signature = HMAC_SHA256(secret, queryString)` in **hex**, `X-MBX-APIKEY` header; limit orders add `timeInForce=GTC` |

**W2 order-book feeds (the dry run's VWAP source):**

- **OKX** — `GET /api/v5/market/books?instId=…&sz=50`.
- **Kraken** — `GET /0/public/Depth?pair=…&count=100`.
- **Binance** — `GET /api/v3/depth?symbol=…&limit=100`.

**W2 fee quotes (signed, per-venue):**

- **OKX** — `GET /api/v5/account/trade-fee?instType=SPOT&instId=…`.
- **Kraken** — `POST /0/private/TradeVolume` (fee tier as a percentage →
  fraction).
- **Binance** — `GET /api/v3/account/commission?symbol=…`.

**Binance has no official MCP / agent API (verified 2026-10-24).** The
official GitHub org search (`org:binance+mcp`) returned **0 MCP repos**;
what exists is `binance/binance-skills-hub` (official Skills) plus the
documented REST surface. So the adapter in `mycore/exchanges.py` is **our own
thin client over documented REST** — no third-party server ever sees the key
— and **community MCP servers stay permanently disabled** (§8).

**Test seams:** env `AIDOG_EX_<VENUE>_URL` (and `AIDOG_EX_RPC`) override base
URLs; the HTTP transport is injectable (callable
`(url, method, headers, payload_bytes, timeout) -> response text`), so the
core suite runs with **zero network and zero real keys**.

## 3. Registration and failure semantics

| Situation | Behavior |
|---|---|
| always | `exchange_markets` registered — public keyless data for all 9 venues |
| ≥1 CEX venue's **full** slot set present | `exchange_portfolio` + `exchange_orders` + the three W2 tools (`exchange_prepare_order` / `exchange_execute_order` / `exchange_plans`) registered; `registry.exchange_note = None` |
| no CEX slot set at all | account and order tools **not registered**; `registry.exchange_note = "exchange account tools not configured: okx not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env); kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET in secrets.env); binance not configured (set BINANCE_API_KEY / BINANCE_API_SECRET in secrets.env)"` |
| probe raises (broken module etc.) | `exchange_markets` still registers (fail-soft literal venue enum); note carries `exchange unavailable: <Type>: <msg>` — the probe never breaks the registry |
| calling a withheld/unknown name | ordinary `ERROR: unknown tool '<name>'` line |
| slot appears/disappears | registration re-evaluates on every `build_registry()` — no restart ceremony beyond the process that builds it |
| partial config (e.g. only OKX set) | all six register; a `kraken` account or order call then fails honestly at call time with `kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET in secrets.env)` — slots are read at call time |
| `mycore.tools_off` / `MYCORE_TOOLS_OFF` names one of them | the tool is unregistered like any other (see [tool_toggles.md](tool_toggles.md)) — the switch can only remove, never enable |

`GET /api/capabilities` → `exchange: {read_only: false, orderable: [3
keys], order_gate, withdrawals: false, onchain_transfers: false, venues: [9
rows], tools: [...], note: <registry.exchange_note or null>}` and a
top-level `web3: {…}` block (§5) — slot PRESENCE booleans and counters only;
values never leave `exchanges.status()`/`catalog()`/`plan_summary()`.

## 4. Tool contract

```
exchange_markets        {venue, symbol?}                                  # always registered
exchange_portfolio      {venue}                                            # slot-gated, cex only
exchange_orders         {venue}                                            # slot-gated, cex only
exchange_prepare_order  {venue, side, symbol?, order_type, qty, price?}    # slot-gated — DRY RUN
exchange_execute_order  {plan_id}                                          # slot-gated — fail-closed
exchange_plans          {action: list|cancel, plan_id?}                    # slot-gated — no approve here
```

- `venue` is an enum taken from the live catalog; `symbol` defaults to the
  venue's default pair and must match `^[A-Za-z0-9._:/-]{1,64}$` (a symbol is
  interpolated into a request path, so the alphabet is closed on purpose).
- Result = compact text lines (one per ticker/balance/order row), capped at
  **40 lines** — enough for the model to cite, not a data dump
  (`exchange_plans list` has the same 40-line cap).
- **Empty result is an honest empty line** (`no open spot orders`,
  `no non-zero balances`, `no plans — prepare one with
  exchange_prepare_order`), never fabricated data. Chain venues have no
  account surface: `exchange_portfolio`/`exchange_orders` on one returns an
  honest error saying so, never fabricated balances.
- `exchange_prepare_order` **sends nothing** — it validates
  (`side ∈ buy|sell`, `order_type ∈ limit|market`, `qty > 0`, limit requires
  `price > 0`, market refuses a price), refuses chain venues and slotless
  venues before any network I/O, walks the live book, and stores a plan
  (§6). Its return text carries the `plan_id` and the exact next step.
- `exchange_execute_order` takes **only** `plan_id`; a refusal (unknown id,
  expired, pending, voided auto-approval) happens before any venue call and
  renders as the ordinary `ERROR: …` line.
- `exchange_plans` can **list** and **cancel** — `approve` and `reject`
  deliberately do not exist in any tool: only the owner decides, in the
  console, behind the `WEB_TOKEN` gate.
- Upstream/provider errors raise `ExchangeError` with a **bounded** message
  (provider body truncated to 160 chars) and render as the ordinary
  `ERROR: …` line; credentials never appear in any error, log, repr or
  return value.

## 5. Console surface

- **`GET /api/capabilities` → `exchange` block**:
  `{read_only: false, orderable: ["okx","kraken","binance"], order_gate:
  "prepare -> console approval -> execute", withdrawals: false,
  onchain_transfers: false, venues: [9 rows], tools: [live exchange_*
  names], note}`. The block-level `read_only: false` states the REAL surface
  (three CEX venues can carry an order — only through the gate above), while
  `withdrawals` / `onchain_transfers` stay hard `false`. A broken probe
  degrades to the same keys with empty `venues` and
  `note: "exchange probe failed"` — never a 500. The smoke asserts the
  serialized block against every `secrets.env` slot value and rejects
  credential-bearing key names in it.
- **`GET /api/capabilities` → top-level `web3` block**: `{auto_usd_per_tx,
  auto_usd_daily, pending_plans, approved_plans, spent_today_usd,
  ledger_day}` — the two fail-closed caps (USD numbers in `0..1e6`), two
  plan counters (ints ≥ 0), today's spent USD and the ledger's Beijing date
  (`"YYYY-MM-DD"` or `""`). Probe failure → the same six keys zeroed plus
  `note: "web3 probe failed"`. Numbers and one date, no credential, no venue
  data.
- **`GET /api/exchange/quote?venue=&symbol=`** — public market probe:
  `200 {venue, symbol, lines}` on success, `400 {error}` with an honest
  message for an unknown venue/bad symbol/dead upstream (names slots, never
  values), `429 {"error": "rate limited"}` on its **own** sliding window of
  **10 requests per minute** (never the voice/STT budget).
- **`GET /api/exchange/plans`** — the stored plans, newest first: `200
  {plans: [...]}` as a plain read (no token, same contract as every GET —
  the payload carries plan parameters and counters only, never a
  credential); an unavailable plan store answers `503 {error}`, never a 500.
- **`POST /api/exchange/plans/<id>`** — **THE approval surface**, body
  `{action: "approve" | "reject" | "cancel"}`. It demands
  `Authorization: Bearer <WEB_TOKEN>` **first**: no/incorrect token → `401
  {"error": "unauthorized"}`, `WEB_TOKEN` slot not configured → `503`
  naming the slot, and the token value never appears in a response, a log
  line or an exception. Then: invalid action → `400` with
  `details: ["action must be one of approve|reject|cancel"]`; unknown plan
  id → `404` carrying the module's honest `unknown plan_id …` text; any
  other impossible decision (already approved, terminal status) → `400`
  verbatim; unexpected store failure → `502`. Success → `200 {plan: {...}}`.
  The model has no `WEB_TOKEN`, therefore no approve path — that is the
  whole point of the gate.
- **Public market data only over HTTP.** Account reads (`portfolio` /
  `orders`) are **model tools only, never an HTTP surface** — the probe
  reuses the read-only `markets()` path and nothing else.

## 6. The W2 order path (prepare → approve → execute)

### 6.1 The plan object

`prepare_order` stores one JSON plan in `<state_dir>/web3_orders.json`
(module lock + atomic write, tmp + `os.replace`; a corrupt/missing file
reads as an EMPTY store — fail-closed, never a crash):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `pl_` + 12 hex chars, generated with `secrets.token_hex` |
| `venue`, `side`, `symbol`, `order_type` | string | copied at prepare time; `side ∈ buy\|sell`, `order_type ∈ limit\|market` |
| `qty`, `price` | number \| null | base-asset quantity; `price` is set for limit, `null` for market |
| `notional_usd` | number | **limit** = `price × qty`; **market** = `vwap × qty` (§6.4) |
| `vwap`, `slippage_bps` | number \| null | live-book walk results; `null` when nothing was marketable (§6.4) |
| `depth_ok` | bool | whether the book covered the full `qty` (§6.4) |
| `fee_rate`, `fee_usd` | number \| null | venue's own quoted fee; `null` when unknown (§6.5) |
| `fee_note` | string | honest one-liner: the quoted rate, or why the fee is unknown |
| `status` | string | `pending \| approved \| rejected \| executed \| expired \| cancelled` |
| `approval` | string | `none \| manual \| auto-threshold` |
| `reason` | string | the exact verdict (which cap failed, who decided, why it expired) |
| `created_at`, `expires_at`, `decided_at`, `executed_at` | number \| null | **epoch seconds** (float); `decided_at`/`executed_at` are `null` until they happen |
| `decided_by` | string \| null | `"console"`, `"model"` (cancel), `"auto-threshold"` |
| `order_id` | string \| null | the venue's own order id, set only by a successful send |
| `attempts` | int | send attempts; a failed send increments it and keeps the plan retryable |

A plan lives **`PLAN_TTL_SEC = 900` seconds (15 minutes)**; expiry makes it
unexecutable — prepare a new one. The store keeps at most **50 plans**
(`PLAN_MAX`): inserting at the cap first evicts the oldest *terminal* plans,
and with 50 live plans a new prepare fails honestly (`plan store is full …`).

### 6.2 Threshold semantics

`thresholds(settings)` reads **`web3.auto_usd_per_tx`** (per-transaction cap)
and **`web3.auto_usd_daily`** (daily cap), both in **USD**:

- **`0` = off.** The default for both is `0` — nothing auto-approves, every
  plan waits for the owner.
- **Fail-closed parsing:** a missing `web3` block, a non-numeric value, a
  boolean, a negative number or anything above the cap **collapses to
  `0.0`** — an unusable value never becomes a permissive one.
- **Cap:** values above **1,000,000 USD** are treated as invalid (→ 0). The
  console's `POST /api/settings` validator mirrors this: the `web3` block
  must set **both** keys together (all-or-nothing), numbers only (bools
  refused), each in `0..1000000`, otherwise the write is a `400` and nothing
  is stored.

Auto-approval verdict — **every** condition must hold, in this order, and the
stored `reason` names exactly which one failed:

1. `order_type == "market"` → **never** auto-approved (hard rule, whatever
   the caps say);
2. `per_tx > 0` (else `auto approvals disabled (web3.auto_usd_per_tx = 0)`);
3. `notional_usd ≤ per_tx`;
4. `daily > 0` (else `daily cap disabled (web3.auto_usd_daily = 0)`);
5. `spent_today + notional_usd ≤ daily`.

All hold → `status=approved`, `approval=auto-threshold`; otherwise
`status=pending`, `approval=none`.

### 6.3 The approval flow

- **Who can approve: only the owner, through the console's `WEB_TOKEN`
  gate** (`POST /api/exchange/plans/<id>`, §5). There is **no tool wrapper
  for approve/reject** — the model cannot approve its own plan; the only
  model-side decision is `exchange_plans action=cancel` (decided_by
  `"model"`), which can only *stop* a plan.
- **`execute_order(plan_id)` fails closed**, in order: plan exists → not
  expired (lazy TTL sweep + an explicit check) → `status == approved` (a
  pending plan answers `awaiting human approval; the OWNER approves this
  plan in the console…`) → if the plan was **auto**-approved, the day's
  ledger and caps are **re-checked now** — if they no longer allow it, the
  plan drops back to `pending` with `auto approval void at execution: …` and
  nothing is sent → only then is the order sent, **while the store lock is
  held**, so one plan can never be sent twice.
- **A failed send never touches the ledger**: `attempts += 1`, the status
  stays `approved`, and the plan remains retryable (or rejectable in the
  console). The venue's rejection surfaces verbatim (bounded).
- Manual (`manual`) approvals are **not** re-checked against thresholds at
  execution — the owner already decided — but their notional still counts
  toward the day's ledger (§6.6).
- Second execute of an executed plan → `plan is executed; only an approved
  plan executes`; rejected/cancelled/expired plans likewise refuse.

### 6.4 VWAP, slippage and depth (the dry run's book math)

`prepare_order` fetches the venue's live book (§2) and **walks one side** of
it for `qty`:

- **`vwap`** — volume-weighted average price of that walk. For a **limit**
  order the walk is filtered to marketable levels (asks ≤ price for a buy,
  bids ≥ price for a sell), so a resting limit honestly reports **no fill**
  (`vwap: null`, `slippage_bps: null`, `depth_ok: false`) instead of a
  fabricated price.
- **`slippage_bps`** — `(vwap − best) / best × 10000` for a buy,
  `(best − vwap) / best × 10000` for a sell: **positive means the walk was
  worse than the top of book**, negative means better; `null` when nothing
  filled.
- **`depth_ok`** — `true` only when the walk filled the full `qty`.
  `depth_ok: false` **for a resting limit just means "no crossing level" —
  it is an honest state, not a failure**; for a market order, a book with no
  marketable depth at all is refused before a plan is stored
  (`no marketable depth … at the top of book`).
- Malformed levels are skipped, never turned into prices; an empty book is
  an honest error, never an invented quote.
- **Notional** (the number the thresholds compare): market → `vwap × qty`;
  limit → `price × qty`. Fees are deliberately **not** folded into it
  (§6.5).

### 6.5 Fee rules

- A fee is quoted **only from the venue's own signed endpoints** (§2) and
  only when the venue's slots exist: OKX trade-fee, Kraken TradeVolume,
  Binance commission.
- **No slot, or a failed quote → `fee_rate: null`, `fee_usd: null` and a
  `fee_note` that says `fee unknown (set <SLOT> in secrets.env to quote
  <venue> fees)` / `fee unknown (<bounded error>)`.** The fee is **never
  guessed**, never defaulted to zero, and **never silently folded into a
  threshold it did not contribute to** — the auto-approval verdict works on
  `notional_usd` alone.
- When quoted, `fee_usd = fee_rate × notional_usd` and the plan's return
  text shows both the note and the number. A response carrying no numeric
  rate is honest too: `fee unknown (… response carried no numeric rate)`.

### 6.6 Ledger, TTL and store rules

- **Every executed notional is counted** — manual approvals included (a
  spent day is a smaller tomorrow): `ledger["YYYY-MM-DD"] =
  {spent_usd, count}`.
- **The ledger day is the Beijing date** — the same day boundary the cron
  scheduler uses, so "today" means the same thing everywhere in the product.
- **TTL 900 s** per plan; `plans_list()`/`plan_summary()` sweep expiry
  lazily so counters never lie about live plans.
- **50-plan cap**, oldest terminal plans evicted first; store writes are
  serialised by a module lock and land atomically (a reader never observes a
  half-written file).
- `plan_summary()` feeds the console/`web3` block:
  `{pending_plans, approved_plans, spent_today_usd, ledger_day,
  plans_total}` — counters and a date only.

### 6.7 Failure modes (honest refusals)

| Where | Symptom | Meaning / fix |
|---|---|---|
| prepare | `jupiter cannot carry an order (chain venues are public market sources…)` | only the three CEX are orderable |
| prepare | `okx not configured (set OKX_API_KEY / … in secrets.env)` | slots missing — the refusal happens **before any network I/O** |
| prepare | `side must be one of buy\|sell` / `order_type must be one of limit\|market` / `qty must be a positive number` / `limit orders require a positive price` / `market orders take no price` | validation precedes everything, including the book fetch |
| prepare | `okx: no marketable depth for … at the top of book` | the market order cannot fill — nothing was stored |
| prepare | `plan store is full (50 live plans)…` | approve/execute/reject existing plans first |
| execute | `unknown plan_id '…' (list plans with exchange_plans)` | typo or already-evicted plan |
| execute | `plan expired (TTL 900s elapsed); prepare a new plan` | plans are short-lived by design |
| execute | `awaiting human approval; the OWNER approves this plan in the console…` | fail-closed gate doing its job — no venue call was made |
| execute | `auto approval no longer valid at execution (caps or day's ledger moved): …` | the day filled up between prepare and execute; the plan is pending again |
| execute | `okx order rejected: <venue message>` | venue refused; `attempts` incremented, plan still approved/retryable, ledger untouched |
| console | `401 {"error":"unauthorized"}` | missing/wrong `Bearer WEB_TOKEN` — the model never has it |
| console | `503` naming `WEB_TOKEN` | the slot itself is unset in `secrets.env` |
| console | `404 unknown plan_id …` / `400` | wrong id / bad action or a decision that is no longer possible |
| reads | `HTTP 401/403` from a venue | key rejected or missing the permission the call needs (§7) |
| reads | `fee unknown (…)` | no slot or fee endpoint failed — honest, never a guess |
| probe | `429 {"error":"rate limited"}` | the quote endpoint's own 10-per-minute window |

## 7. Honest boundaries

- **Not a trading bot, not custody.** No wallet, no private key, no
  transaction signing anywhere: the chain venues are public price/head reads
  and the CEX venues honour the key permission the operator configured.
  **Withdrawals and on-chain transfers are not absent by configuration —
  they are absent by source**: no function, endpoint, tool name or settings
  key exists, and `test_exchanges_w2` asserts that against
  `mycore/exchanges.py` itself (banned function names, banned endpoint
  paths, banned tool names, and the exact `execute_order` signature).
- **Key permissions to prescribe — two profiles, W1 vs W2:**
  - **Reads only (W1 surface: portfolio / orders / fee quotes):**
    - **OKX: `Read`** — never `Withdraw`. OKX allows at most **20 IPs per
      key**, and **expires unbound trade/withdraw keys after 14 idle days**
      (a further reason the key stays IP-bound).
    - **Kraken: `Funds permissions - Query`** +
      **`Orders and trades - Query open orders & trades`** only.
    - **Binance: `USER_DATA`** granted, **`TRADE` NOT granted** (official
      docs: "By default, an API key cannot `TRADE`" — the split is the
      official least-privilege posture; the official Skills Hub README
      advises scoping keys to what the agent needs and allow-listing its
      egress IP).
  - **Order path (W2: prepare → execute):** the key must additionally carry
    the venue's **trade permission** (OKX `Trade`; Binance `TRADE` enabled
    in API Management; Kraken the order-create permission under `Orders and
    trades` — **the exact Kraken label was NOT re-verified in the 2026-10-24
    pass and must be read off the key-creation screen**). Without it,
    `execute` is refused by the venue. **Withdraw permission stays off at
    any threshold, forever.**
  - **`portfolio` / `orders` / fee quotes keep working on a Read/Query-only
    key** — that is the recommended posture until you deliberately enable
    the order path. The key lives in this machine's `secrets.env` (mode
    0600), is read only to sign a request, and **never leaves this machine
    and is never echoed** — report slot names and lengths only.
- **Thresholds are opt-in and fail-closed.** Per-transaction and daily USD
  caps default to **0 = full manual approval** (unreadable/unparsable
  config counts as 0); no threshold, however high, ever enables a withdrawal
  or an on-chain transfer — those do not exist (§6.2).
- **Disclosure (applies to every exchange surface — doc, skills, console):**
  1. The product is **permanently free**, but exchange trading fees and
     on-chain gas are **NOT zero** — they are settled by the user with the
     exchange/chain.
  2. **This is not investment advice.**
  3. **Trading risk is borne by the user.**
  4. **Credentials stay in this machine's `secrets.env`** and are never
     echoed into chats, logs, documents or screenshots.

## 8. Hardened risks (research §5 → code-level mitigations)

1. **Prompt-injection-driven automated transfers.** Public agent frameworks
   have shipped without approval gates, spending caps or address
   allowlists, with public theft cases in 2026. Our mitigation is structural
   and now **exercised**: prepare/execute is a code boundary, not a setting
   (§6) — execute takes only a plan id, defaults to fail-closed full manual
   approval that no configuration can silently relax, market orders never
   auto-approve, auto-approvals re-check the day's ledger at execution, and
   the fund-moving surface (withdraw/transfer/deposit) provably does not
   exist in the module.
2. **Credential hand-off to third parties.** Community MCP servers that hold
   a user's API key violate exchange key terms and our zero-echo discipline:
   **community MCP is permanently banned** — and for Binance this is easy:
   there is no official MCP to be tempted by (§2), so the thin REST adapter
   is the only path. **Remote MCP is only tolerated for official, read-only
   surfaces and only when the user opts in explicitly.** Credentials live in
   this machine's `secrets.env` and are used for local signing only, never
   handed over.
3. **Geo / KYC / terms still unverified.** Venue terms on bot trading and
   regional availability differ and were not fully re-read. Concretely:
   **the HTX, Crypto.com and Bitget authentication sections, the Gate REST
   security surface, and the Binance API Management FAQ plus
   `PROD-TERMS-OF-USE.md` are explicitly UNVERIFIED** from the 2026-10-24
   research pass and **must be re-verified before any later car** — none of
   the first group is in this catalog, and none may be added on the strength
   of remembered docs.

## 9. Verification (what actually exercises this)

- **Test seams** (§2): `AIDOG_EX_<VENUE>_URL` / `AIDOG_EX_RPC` overrides plus
  the injectable transport — the core suite signs, parses and gates with
  **zero network and zero real keys**; the Kraken signature is pinned to the
  official example vector rather than a live call.
- **`mycore/tests/run.py::test_exchanges`** — the W1 read-only face:
  catalog shape, keyless markets, slot gating, zero-credential output.
- **`mycore/tests/run.py::test_exchanges_w2`** — the behavior contract for
  this section: thresholds fail closed on every unusable shape; plan
  lifecycle (pending → approved → executed, plus reject/cancel/expiry and
  second-execute refusal); market orders never auto-approve; default caps
  block everything and `prepare` says WHY a human is needed; VWAP/slippage
  math checked against a hand-computed book (including the resting-limit
  no-fill case); all three venue order signatures recomputed from the wire
  bytes; the ledger rolls over on the Beijing date; terminal plans are
  pruned before the 50-cap; and the **absence** of any withdrawal/transfer
  capability is asserted against the source file itself (§7).
- **Web smoke** (`tests/web_console_p3.py`): the `exchange` block shape (9
  venue rows, 6 chain `read_only: true` / 3 CEX `orderable: true`, block
  `read_only: false`, `order_gate`, `withdrawals`/`onchain_transfers`
  `false`), the `web3` block's six keys with their types and ranges, the
  zero-credential assertion on both serialized blocks (no slot value, no
  credential-bearing key name), `GET /api/exchange/quote` answering an
  unknown venue with an honest `400`, `GET /api/exchange/plans` as a plain
  credential-free read, and `POST /api/exchange/plans/<id>` refusing with
  `401` (no bearer) / `400` (bad action) / `404` (unknown id) without ever
  echoing the token — every POST in the smoke targets a nonexistent plan, so
  the suite decides nothing and never writes the plan store.
- **Real-provider connectivity is BYOK**: with slots filled, the same tools
  hit production endpoints; without them the account/order tools are absent
  (§3) and nothing in the product surface claims otherwise.
