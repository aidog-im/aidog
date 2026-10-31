# Exchanges (read-only CEX + chain market data, Web3 phase 2 W1)

Status: shipped in the W1 car (`mycore/exchanges.py`; tool wiring in
`mycore/tools.py::build_registry`; console surface in `web/server.py`).
Companion pages: [capabilities.md](capabilities.md) (the `exchange` block),
[tool_toggles.md](tool_toggles.md); skill packs
`overlay/skills/okx-read-only/` and `overlay/skills/kraken-read-only/`.

Read-only Web3 pilot: **8 venues in one catalog** behind three `exchange_*`
tools. Public market data needs **no credential at all**; account reads are
slot-gated with the same capability-honest pattern as
[connectors.md](connectors.md) — **no slot → the tool does not register** and
the registry carries an honest `exchange_note` naming exactly which slots are
missing. A capability we don't have is never advertised as a tool that will
fail.

Three hard contracts this module enforces **in code, not in configuration**
(owner decrees, 2026-10-24):

1. **READ-ONLY.** Every request fetches data. There is no order placement, no
   transfer, no withdrawal, no transaction signing anywhere in
   `mycore/exchanges.py` — no settings value, env var or model instruction can
   turn this file into a writer (§6).
2. **ZERO CREDENTIAL ECHO.** Slot values are read only to sign a request and
   are never returned, logged, formatted or embedded in an error. Errors name
   the SLOT (`OKX_API_KEY`), never the value.
3. **SLOT GATED.** Public market data needs no slot; account reads require the
   venue's full slot set, and a missing slot raises an honest error naming
   exactly what to set.

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

The six chain venues are keyless and non-custodial market sources: there is
no credential to gate, so they are built in with **NO slot**. The catalog row
(`key`, `label`, `kind`, `auth`, `configured`, `read_only`, `symbol`,
`summary`, plus an honest `reason` for a key venue without a full slot set) is
what the capability directory serializes — names, kinds, booleans and
sentences only, never a slot value and never a URL.

## 2. Protocol contracts (verified live 2026-10-24)

Protocol facts were taken from the vendors' **own endpoints and official
documentation**, exercised against production that day — not from memory.

**Public endpoints (keyless):**

- **OKX** — `GET /api/v5/market/ticker?instId=…`.
- **Kraken** — `GET /0/public/Ticker?pair=…`; BTC is spelled `XBT`,
  separators are dropped (`XBTUSD`), and `BTC` maps to `XBT` automatically.
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

**Private endpoints (slot-gated):**

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

**Test seams:** env `AIDOG_EX_<VENUE>_URL` (and `AIDOG_EX_RPC`) override base
URLs; the HTTP transport is injectable (callable
`(url, method, headers, payload_bytes, timeout) -> response text`), so the
core suite runs with **zero network and zero real keys**.

## 3. Registration and failure semantics

| Situation | Behavior |
|---|---|
| always | `exchange_markets` registered — public keyless data for all 8 venues |
| ≥1 CEX venue's **full** slot set present | `exchange_portfolio` + `exchange_orders` registered; `registry.exchange_note = None` |
| no CEX slot set at all | account tools **not registered**; `registry.exchange_note = "exchange account tools not configured: okx not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env); kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET in secrets.env)"` |
| probe raises (broken module etc.) | `exchange_markets` still registers (fail-soft literal venue enum); note carries `exchange unavailable: <Type>: <msg>` — the probe never breaks the registry |
| calling a withheld/unknown name | ordinary `ERROR: unknown tool '<name>'` line |
| slot appears/disappears | registration re-evaluates on every `build_registry()` — no restart ceremony beyond the process that builds it |
| partial config (e.g. only OKX set) | account tools register; a `kraken` account call then fails honestly at call time with `kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET in secrets.env)` — slots are read at call time |

`GET /api/capabilities` → `exchange: {read_only: true, venues: [8 rows],
tools: [...], note: <registry.exchange_note or null>}` — slot PRESENCE booleans
only; values never leave `exchanges.status()`/`catalog()`.

## 4. Tool contract

```
exchange_markets  {venue, symbol?}                     # always registered
exchange_portfolio {venue}                             # slot-gated, okx|kraken
exchange_orders   {venue}                              # slot-gated, okx|kraken
```

- `venue` is an enum taken from the live catalog; `symbol` defaults to the
  venue's default pair and must match `^[A-Za-z0-9._:/-]{1,64}$` (a symbol is
  interpolated into a request path, so the alphabet is closed on purpose).
- Result = compact text lines (one per ticker/balance/order row), capped at
  **40 lines** — enough for the model to cite, not a data dump.
- **Empty result is an honest empty line** (`no open spot orders`,
  `no non-zero balances`), never fabricated data. Chain venues have no
  account surface: `exchange_portfolio`/`exchange_orders` on one returns an
  honest error saying so, never fabricated balances.
- Upstream/provider errors raise `ExchangeError` with a **bounded** message
  (provider body truncated to 160 chars) and render as the ordinary
  `ERROR: …` line; credentials never appear in any error, log, repr or
  return value.

## 5. Console surface

- **`GET /api/capabilities` → `exchange` block**: `{read_only, venues[],
  tools[], note}` exactly as §3 — the same zero-credential promise as the
  rest of the catalog (the smoke asserts no slot value and no
  credential-bearing key name in the serialized block).
- **`GET /api/exchange/quote?venue=&symbol=`** — public market probe:
  `200 {venue, symbol, lines}` on success, `400 {error}` with an honest
  message for an unknown venue/bad symbol/dead upstream (names slots, never
  values), `429 {"error": "rate limited"}` on its **own** sliding window of
  **10 requests per minute** (never the voice/STT budget).
- **Public market data only over HTTP.** Account reads (`portfolio` /
  `orders`) are **model tools only, never an HTTP surface** — the probe
  reuses the read-only `markets()` path and nothing else.

## 6. Honest boundaries (W1)

- **W1 ships NO order placement.** The prepare/execute separation of phase 2
  is a boundary drawn **in CODE**: no config value, env var or model
  instruction can enable writing (§ *Three hard contracts*). Order
  preparation (`exchange_prepare_order`, dry-run) and the human approval
  gate land in **W2**.
- **Thresholds are W2, fail-closed.** Per-transaction and daily USD caps
  default to **0 = full manual approval** (unreadable/unparsable config
  counts as 0); withdrawals and on-chain transfers are **never automatic at
  any threshold**.
- **Not a trading bot, not custody.** No wallet, no private key, no signing:
  the chain venues are public price/head reads and the CEX venues honour the
  read-only key permission the operator configured.
- **Key permissions to prescribe** (create the key this way, nothing more):
  - **OKX: `Read` only** — never `Trade`, never `Withdraw`. OKX allows at
    most **20 IPs per key**, and **expires unbound trade/withdraw keys after
    14 idle days** (a further reason the key stays `Read`-only and
    IP-bound).
  - **Kraken: `Funds permissions - Query`** and **`Orders and trades - Query
    open orders & trades`** only.
- **Disclosure (applies to every exchange surface — doc, skills, console):**
  1. The product is **permanently free**, but exchange trading fees and
     on-chain gas are **NOT zero** — they are settled by the user with the
     exchange/chain.
  2. **This is not investment advice.**
  3. **Trading risk is borne by the user.**
  4. **Credentials stay in this machine's `secrets.env`** and are never
     echoed into chats, logs, documents or screenshots.

## 7. Hardened risks (research §5 → code-level mitigations)

1. **Prompt-injection-driven automated transfers.** Public agent frameworks
   have shipped without approval gates, spending caps or address
   allowlists, with public theft cases in 2026. Our mitigation is structural:
   **prepare/execute is a code boundary, not a setting** (§6) — W1 contains
   no writer at all, and W2's execute path defaults to fail-closed full
   manual approval that no configuration can silently relax.
2. **Credential hand-off to third parties.** Community MCP servers that hold
   a user's API key violate exchange key terms and our zero-echo discipline:
   **community MCP is permanently banned**; **remote MCP is only tolerated
   for official, read-only surfaces and only when the user opts in
   explicitly**. Credentials live in this machine's `secrets.env` and are
   used for local signing only, never handed over.
3. **Geo / KYC / terms still unverified.** Venue terms on bot trading and
   regional availability differ and were not fully re-read. Concretely:
   **the HTX, Crypto.com and Bitget authentication sections and the Gate
   REST security surface are explicitly UNVERIFIED** from the 2026-10-24
   research pass and **must be re-verified before any later car** — none of
   them is in this W1 catalog, and none may be added on the strength of
   remembered docs.

## 8. Verification (what actually exercises this)

- **Test seams** (§2): `AIDOG_EX_<VENUE>_URL` / `AIDOG_EX_RPC` overrides plus
  the injectable transport — the core suite signs, parses and gates with
  **zero network and zero real keys**; the Kraken signature is pinned to the
  official example vector rather than a live call.
- **Web smoke** (`tests/web_console_p3.py`): the `exchange` block shape (8
  read-only venue rows with `key/label/kind/auth/configured/read_only`,
  `read_only: true`, `tools` list), the zero-credential assertion on the
  serialized block (no slot value, no credential-bearing key name), and
  `GET /api/exchange/quote` answering an unknown venue with an honest `400`.
- **Real-provider connectivity is BYOK**: with slots filled, the same tools
  hit production endpoints; without them the account tools are absent (§3)
  and nothing in the product surface claims otherwise.
