---
name: binance-read-only
description: Use when reading Binance balances, orders or market data, creating a least-privilege USER_DATA API key, or diagnosing Binance auth — orders only via the console approval gate.
---

# Binance Read-Only Access (W2 order gate included)

## Scope

This skill covers **reading** Binance through this product's `exchange_*`
tools, implemented in our own thin adapter over the official REST surface in
`mycore/exchanges.py`:

- `exchange_markets {venue: "binance", symbol?}` — public ticker data, **no
  credential needed**, defaults to `BTCUSDT`.
- `exchange_portfolio {venue: "binance"}` — account balances via
  `GET /api/v3/account`; requires the slot set `BINANCE_API_KEY` /
  `BINANCE_API_SECRET`.
- `exchange_orders {venue: "binance"}` — open spot orders via
  `GET /api/v3/openOrders` (listing only).
- Console probe: `GET /api/exchange/quote?venue=binance&symbol=BTCUSDT` —
  public market data over HTTP only, 10 requests/minute (429 beyond).
- **W2 order path**: `exchange_prepare_order` (dry run — validates, walks
  the live book for a VWAP/slippage/depth verdict, quotes the fee when the
  slots exist, stores a PLAN and sends **nothing**) → the **owner** approves
  or rejects it in the console behind the Bearer `WEB_TOKEN` gate →
  `exchange_execute_order {plan_id}` (fail-closed; takes only the plan id).
  `exchange_plans` lists or cancels plans — it has **no approve/reject**,
  because approval lives only in the console.

**No official Binance MCP exists.** The official GitHub org search
(`org:binance+mcp`) returned 0 MCP repos (verified 2026-10-24); Binance ships
official Skills and the documented REST API, nothing else. Community MCP
servers are therefore **never used** — they would have to hold the key, which
our zero-credential-echo discipline forbids. This adapter signs locally and
no third-party server ever sees the key.

**Out of scope permanently:** withdrawals, on-chain transfers and transaction
signing do not exist anywhere on this path — no function, no endpoint, no
settings key, and the core suite asserts that absence against the module.

## Create a least-privilege key

1. In Binance (web → account → API Management), create an API key with the
   **HMAC** signing scheme — that is the scheme this product's adapter signs
   with (`HMAC_SHA256(secret, queryString)` in hex). Official docs now
   *recommend* Ed25519 and mark HMAC keys as deprecated; our thin adapter
   does not implement Ed25519, so pick HMAC and store the secret now — it
   becomes the `BINANCE_API_SECRET` slot (the key id becomes
   `BINANCE_API_KEY`).
2. For **reads only** (balances, open orders, commission): grant **`USER_DATA`
   only** — official docs state "By default, an API key cannot `TRADE`", which
   is exactly the least-privilege posture. **Never enable Withdrawals.**
3. For the **W2 order path**, `TRADE` must additionally be enabled — without
   it, `execute` is refused by the venue (honest error; the plan stays
   retryable). Turn it on deliberately, not by default.
4. Allow-list the egress IP of this machine when the API Management UI
   offers it (official Skills Hub guidance: scope the key to what the agent
   needs + IP allow-list).
5. Store the two values in this machine's `secrets.env`, one per own line
   (`BINANCE_API_KEY=…`), file mode 0600. The values are read only to sign a
   request and are **never echoed** into chats, logs, documents or
   screenshots — report slot names and lengths only.
6. With no slots set, `exchange_portfolio` / `exchange_orders` / the W2 order
   tools simply do not register; `exchange_markets` keeps working keyless.
   Configuration is re-read at registry build — no ceremony beyond restarting
   the process that builds it.

## Calling the tools

- **Symbol form is the compact ticker with no separator**: `BTCUSDT`,
  `ETHUSDT`. Omit `symbol` for the venue default (`BTCUSDT`). Symbol grammar
  is closed: `^[A-Za-z0-9._:/-]{1,64}$` (a symbol is interpolated into a
  request path).
- Results are compact lines capped at 40 rows — cite them, don't dump them.
  `exchange_portfolio` states honestly that this endpoint carries no USD
  valuation — quote prices from `exchange_markets` instead of inventing them.
- Empty means empty: `no open orders` / `no non-zero balances` are honest
  lines, never a reason to invent data.
- Signing (done locally, never sent anywhere but Binance): header
  `X-MBX-APIKEY: <key>`; **all parameters travel in the query string**
  (including `timestamp` and `recvWindow=5000`), and
  `signature = HMAC_SHA256(secret, queryString)` is appended **hex-encoded**
  (`&signature=…`). The order call `POST /api/v3/order` carries parameters in
  the query too — the POST body stays empty.
- Fee quotes come only from Binance's own signed
  `GET /api/v3/account/commission?symbol=…`; with no slot or a failed quote
  the plan says `fee unknown (set BINANCE_API_KEY / BINANCE_API_SECRET in
  secrets.env …)` — the fee is never guessed and never folded into a
  threshold it did not contribute to.

## Approval semantics for anything beyond reading

Reading needs no approval. **Order placement always goes
prepare → owner's console approval → execute(plan_id)**: default thresholds
(`web3.auto_usd_per_tx` / `web3.auto_usd_daily`) are `0`, so every plan waits
for a human; market orders never auto-approve at any threshold; auto-approved
plans are re-checked against the day's ledger at execution. **Withdrawals and
on-chain transfers are never automatic at any threshold — they do not exist
here at all.** If asked to withdraw or transfer: decline and say there is no
such capability; if asked to trade: prepare the plan if asked to, then route
approval to the human in the console — do not look for a setting that lets
the model approve; there is none (no tool wraps approve/reject, and the model
has no `WEB_TOKEN`).

## Common failure modes

| Symptom | Meaning / fix |
|---|---|
| `exchange_portfolio`/`exchange_orders`/W2 order tools absent; `registry.exchange_note` contains `binance not configured (set BINANCE_API_KEY / BINANCE_API_SECRET in secrets.env)` | slots missing — set both own-line in `secrets.env`; names only, never paste values anywhere else |
| call-time error `binance not configured (set BINANCE_API_KEY / BINANCE_API_SECRET in secrets.env)` | registry was built when a slot existed but it's gone now — re-check the file, rebuild/restart |
| `binance: Mandatory parameter 'signature' …` (HTTP 400, code `-1102`) | the query string was not signed — a proxy or wrapper dropped the signature; check the local signer, not the key |
| `HTTP 401` / `HTTP 403` / signature validation error | key rejected: wrong/revoked slot value, clock skew beyond `recvWindow`, or missing permission — verify time sync (`timestamp`), `X-MBX-APIKEY`, and the `USER_DATA`/`TRADE` grant, not the tool |
| venue rejects at `execute` despite an approved plan | the key lacks `TRADE` (or the venue refused the order) — the plan keeps `attempts += 1` and stays retryable; the ledger was untouched |
| `fee unknown (…)` in a prepared plan | no slot or the commission endpoint failed — honest state, the fee is never guessed; set both slots to quote it |
| provider error text (bounded to 160 chars) | upstream's own message, truncated honestly — read it as-is; the credential never appears in it |
| `401 unauthorized` from `POST /api/exchange/plans/<id>` | missing/wrong `Bearer WEB_TOKEN` — only the owner holds it; the model has no approve path by design |
| HTTP 429 from the console probe | the quote endpoint's own 10-per-minute window — back off, it never shares the voice budget |

## Required disclosures

Whenever presenting Binance-derived data or capabilities, keep these four
true:

1. The product is **permanently free**, but exchange trading fees and
   on-chain gas are **NOT zero** — the user settles them with the exchange.
2. **This is not investment advice.**
3. **Trading risk is borne by the user.**
4. **Credentials stay in this machine's `secrets.env`** and are never
   echoed — slot names and counts only, in every output.
