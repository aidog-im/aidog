---
name: okx-read-only
description: Use when reading OKX balances, orders or market data, creating a least-privilege read-only OKX API key, or diagnosing OKX auth (passphrase, timestamp skew) — never for trading.
---

# OKX Read-Only Access

## Scope (W1, read-only)

This skill covers **reading** OKX through this product's `exchange_*` tools,
implemented in `mycore/exchanges.py`:

- `exchange_markets {venue: "okx", symbol?}` — public ticker data, **no
  credential needed**, defaults to `BTC-USDT`.
- `exchange_portfolio {venue: "okx"}` — account balances; requires the full
  slot set `OKX_API_KEY` / `OKX_API_SECRET` / `OKX_API_PASSPHRASE`.
- `exchange_orders {venue: "okx"}` — open spot orders (listing only).
- Console probe: `GET /api/exchange/quote?venue=okx&symbol=BTC-USDT` —
  public market data over HTTP only, 10 requests/minute (429 beyond).

**No order placement exists in W1.** The prepare/execute split is a boundary
drawn in the module's code — no config value, env var or model instruction
can make these tools write. There is no cancel, no transfer, no withdrawal,
no signing anywhere on this path.

## Create a least-privilege key

1. In OKX (web → account → API keys, `okx.com/account/my-api`), create an
   API key; set a strong passphrase now —
   it becomes the `OKX_API_PASSPHRASE` slot.
2. Grant **`Read` permission ONLY**. Never `Trade`, never `Withdraw`. Read
   covers balances and order queries, which is everything W1 calls.
3. Bind the key to the egress IP(s): OKX allows **at most 20 IPs per key**,
   and **expires unbound trade/withdraw keys after 14 idle days** — a
   further reason to keep the key `Read`-only and IP-bound.
4. Store the three values in this machine's `secrets.env`, one per own line
   (`OKX_API_KEY=…`), file mode 0600. The values are read only to sign a
   request and are **never echoed** into chats, logs, documents or
   screenshots — report slot names and lengths only.
5. With no slots set, `exchange_portfolio` / `exchange_orders` simply do not
   register; `exchange_markets` keeps working keyless. Configuration is
   re-read at registry build — no ceremony beyond restarting the process
   that builds it.

## Calling the tools

- Symbol grammar is closed: `^[A-Za-z0-9._:/-]{1,64}$` (a symbol is
  interpolated into a request path). Omit `symbol` for the venue default.
- Results are compact lines capped at 40 rows — cite them, don't dump them.
- Empty means empty: `no open spot orders` / `no non-zero balances` are
  honest lines, never a reason to invent data.
- Signing (done locally, never sent anywhere but OKX):
  `Base64(HMAC-SHA256(secret, timestamp + method + requestPath + body))`
  with headers `OK-ACCESS-KEY`, `OK-ACCESS-SIGN`, `OK-ACCESS-TIMESTAMP`
  (ISO 8601 UTC milliseconds), `OK-ACCESS-PASSPHRASE`.

## Approval semantics for anything beyond reading

Reading needs no approval. **Anything beyond reading is never automatic**:
order placement does not exist in W1, and when prepare/execute lands in W2 it
will sit behind the human approval gate with fail-closed thresholds
(unconfigured = full manual approval). **Withdrawals and on-chain transfers
are never automatic at any threshold.** If asked to trade, cancel, transfer
or withdraw: decline the write and route it to the human gate — do not look
for a setting that permits it; there is none.

## Common failure modes

| Symptom | Meaning / fix |
|---|---|
| `exchange_portfolio`/`exchange_orders` absent; `registry.exchange_note` starts with `exchange account tools not configured:` and contains `okx not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)` (it names every unconfigured CEX venue) | slots missing — set all three own-line in `secrets.env`; names only, never paste values anywhere else |
| call-time error `okx not configured (set OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE in secrets.env)` | registry was built when a slot existed but it's gone now — re-check the file, rebuild/restart |
| `HTTP 401` / `HTTP 403` | key rejected: wrong/extra-missing slot value, key revoked, or the key lacks `Read` / isn't IP-bound — verify permissions and IP binding, not the tool |
| error code `50102` or timestamp complaints | **OKX timestamp skew > 30 s** between this machine and OKX — fix system clock/NTP, not the signature |
| provider error text (bounded to 160 chars) | upstream's own message, truncated honestly — read it as-is; the credential never appears in it |
| HTTP 429 from the console probe | the quote endpoint's own 10-per-minute window — back off, it never shares the voice budget |

## Required disclosures

Whenever presenting OKX-derived data or capabilities, keep these four true:

1. The product is **permanently free**, but exchange trading fees and
   on-chain gas are **NOT zero** — the user settles them with the exchange.
2. **This is not investment advice.**
3. **Trading risk is borne by the user.**
4. **Credentials stay in this machine's `secrets.env`** and are never
   echoed — slot names and counts only, in every output.
