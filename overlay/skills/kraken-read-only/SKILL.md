---
name: kraken-read-only
description: Use when reading Kraken balances, orders or market data, creating a least-privilege Query-only Kraken API key, or diagnosing Kraken auth (nonce, API-Sign) — never for trading.
---

# Kraken Read-Only Access

## Scope (W1, read-only)

This skill covers **reading** Kraken through this product's `exchange_*`
tools, implemented in `mycore/exchanges.py`:

- `exchange_markets {venue: "kraken", symbol?}` — public ticker data, **no
  credential needed**, defaults to `XBT/USD`.
- `exchange_portfolio {venue: "kraken"}` — balances via
  `POST /0/private/Balance`; requires `KRAKEN_API_KEY` /
  `KRAKEN_API_SECRET`.
- `exchange_orders {venue: "kraken"}` — open orders via
  `POST /0/private/OpenOrders` (listing only).
- Console probe: `GET /api/exchange/quote?venue=kraken&symbol=XBT/USD` —
  public market data over HTTP only, 10 requests/minute (429 beyond).

**No order placement exists in W1.** The prepare/execute split is a boundary
drawn in the module's code — no config value, env var or model instruction
can make these tools write. There is no cancel, no transfer, no withdrawal,
no signing of a transaction anywhere on this path.

## Create a least-privilege key

1. In Kraken: **Settings → API → Create API key**
   (`kraken.com/u/security/api`).
2. Grant **only**: `Funds permissions - Query` **and** `Orders and trades -
   Query open orders & trades`. Nothing else — no trading permission, no
   funding/withdrawal permission, no closing permissions.
3. Store the two values in this machine's `secrets.env`, one per own line
   (`KRAKEN_API_KEY=…`), file mode 0600. The secret is **base64-decoded
   locally for signing only** and is never echoed into chats, logs,
   documents or screenshots — report slot names and lengths only.
4. With no slots set, `exchange_portfolio` / `exchange_orders` simply do not
   register; `exchange_markets` keeps working keyless. Configuration is
   re-read at registry build.

## Calling the tools

- **Pair spelling:** Kraken spells BTC as `XBT` and drops separators —
  `BTC/USD` is normalized to `XBTUSD` automatically; the default symbol is
  `XBT/USD`. Symbol grammar is closed: `^[A-Za-z0-9._:/-]{1,64}$`.
- Results are compact lines capped at 40 rows; `no open orders` /
  `no non-zero balances` are honest empty lines, never a reason to invent.
- Signing (done locally): `API-Sign =
  Base64(HMAC-SHA512(path + SHA256(nonce + postdata),
  base64_decode(secret)))` with headers `API-Key` and `API-Sign`, and a
  **strictly increasing millisecond nonce**. This exact construction is
  verified against Kraken's official published example vector (nonce
  `1616492376594`, path `/0/private/AddOrder`), which is why it is
  trustworthy without using your key to test it.

## Approval semantics for anything beyond reading

Reading needs no approval. **Anything beyond reading is never automatic**:
order placement does not exist in W1, and when prepare/execute lands in W2 it
will sit behind the human approval gate with fail-closed thresholds
(unconfigured = full manual approval). **Withdrawals and on-chain transfers
are never automatic at any threshold.** If asked to trade, cancel, transfer
or withdraw: decline the write and route it to the human gate — no setting
permits it.

## Common failure modes

| Symptom | Meaning / fix |
|---|---|
| `exchange_portfolio`/`exchange_orders` absent; `registry.exchange_note` starts with `exchange account tools not configured:` and contains `kraken not configured (set KRAKEN_API_KEY / KRAKEN_API_SECRET in secrets.env)` (it names every unconfigured CEX venue) | slots missing — set both own-line in `secrets.env`; names only, never paste values anywhere else |
| `HTTP 401` / `HTTP 403` | key rejected: wrong/missing slot value, key revoked, or the key lacks the Query permissions — verify permissions, not the tool |
| nonce / `EAPI:Invalid nonce` style errors | **nonce regression** — the millisecond nonce must strictly increase per key; concurrent or clock-backward use violates it. Fix the system clock (NTP) and retry; the module already guarantees strict monotonicity within one process |
| signature errors despite a valid key | `API-Sign` binds the exact path + body — a proxy rewriting the path or body breaks it; check for an intermediary, not the secret |
| provider error text (bounded to 160 chars) | upstream's own message, truncated honestly — the credential never appears in it |
| `kraken: no ticker for …` | unknown pair — use Kraken's compact spelling (`XBTUSD`), separators are dropped |
| HTTP 429 from the console probe | the quote endpoint's own 10-per-minute window — back off, it never shares the voice budget |

## Required disclosures

Whenever presenting Kraken-derived data or capabilities, keep these four
true:

1. The product is **permanently free**, but exchange trading fees and
   on-chain gas are **NOT zero** — the user settles them with the exchange.
2. **This is not investment advice.**
3. **Trading risk is borne by the user.**
4. **Credentials stay in this machine's `secrets.env`** and are never
   echoed — slot names and counts only, in every output.
