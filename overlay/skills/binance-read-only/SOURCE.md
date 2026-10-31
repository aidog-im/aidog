# SOURCE — binance-read-only

Provenance record for `SKILL.md` in this directory (traceability, not a
product brand surface).

- **Authorship:** this is **our own skill page**, written for I'm AiDog from
  the vendor's official documentation. No third-party skill text was copied
  or adapted; there is no upstream skill file this derives from.
- **Why there is no upstream Binance skill to derive from:** the official
  GitHub org search (`https://api.github.com/search/repositories?q=org:binance+mcp`)
  returned **total 0** on 2026-10-24 — Binance ships no official MCP/agent
  API. What exists officially is `github.com/binance/binance-skills-hub`
  (Skills + CLI) and the documented REST surface, so the product's adapter in
  `mycore/exchanges.py` is our own thin client and this skill documents it.
  Community MCP servers (`TermiX-official/binance-mcp`, `AnalyticAce/binance-mcp-server`,
  `snjyor/binance-mcp`, `nirholas/Binance-MCP`, all ≤97★ and unofficial) are
  deliberately NOT used and stay permanently disabled — they would have to
  hold the user's key.
- **Official documentation relied on (all retrieved 2026-10-24):**
  - Binance Spot API docs (official org repo `binance/binance-spot-api-docs`)
    — the security model quoted in `SKILL.md`: signature types
    `NONE` / `TRADE` / `USER_DATA` / `USER_STREAM`, the verbatim rule
    "By default, an API key cannot `TRADE`" (so reads need only `USER_DATA`),
    the official advice to split keys and allow-list the agent's IP, and the
    signing scheme `signature = HMAC_SHA256(secret, queryString)` in hex with
    the `X-MBX-APIKEY` header and `timestamp`/`recvWindow` parameters.
  - Endpoints used by `exchange_markets` / `exchange_portfolio` /
    `exchange_orders` (`GET /api/v3/ticker/24hr`, `GET /api/v3/account`,
    `GET /api/v3/openOrders`), by the dry run (`GET /api/v3/depth?limit=100`),
    by the fee quote (`GET /api/v3/account/commission?symbol=`) and by the
    W2 order send (`POST /api/v3/order`) — all from that same official REST
    reference; `binance/binance-spot-api-docs` also documents the commission
    FAQ mapping to `/api/v3/account/commission`.
  - Official Skills Hub `github.com/binance/binance-skills-hub` (1082★,
    last commit 2026-10-23) — README guidance quoted in `SKILL.md`: scope the
    key to what the agent needs + IP allow-list the agent's host; credentials
    live in env / `.env` / `secrets.env`, never in chat.
  - The `data-api.binance.vision` keyless public-market domain exists per the
    official docs, but this product's adapter uses the main
    `api.binance.com` public endpoints — the ones actually exercised below.
- **Verification:** the public endpoints in `SKILL.md` (`/api/v3/ticker/24hr`
  and `/api/v3/depth`) were exercised live on 2026-10-24 (see
  `docs/features/exchanges.md` §2); the unsigned signed-endpoint probe
  returned the honest `HTTP 400 {"code":-1102,"msg":"Mandatory parameter
  'signature' …"}` shape, confirming the signing requirement from the wire
  rather than from memory; the HMAC-hex signer is additionally recomputed
  from the captured request bytes by the core suite
  (`mycore/tests/run.py::test_exchanges_w2`) with zero network and zero real
  keys.

## Notes

1. Frontmatter follows this repository's skill convention (`name` + a
   single-line `description` under 200 chars, the router trigger parsed by
   `lib/skill_route.py`).
2. The four required disclosures (free product ≠ zero fees, not investment
   advice, trading risk borne by user, credentials stay in this machine's
   `secrets.env`) are I'm AiDog policy (owner decree, 2026-10-24), not
   vendor text.
3. The W2 approval-gate language (prepare → console approval → execute,
   fail-closed thresholds, no tool can approve, no withdrawals/transfers)
   is this product's own contract, taken from `docs/features/exchanges.md`
   §6 — Binance's official docs describe `TRADE` permission but know nothing
   about our gate; the gate is ours.
4. Regional/terms caveats from the 2026-10-24 research pass still stand:
   Binance's API Management FAQ and `PROD-TERMS-OF-USE.md` were **not**
   fully retrievable (anti-bot) and must be re-verified before any later car
   extends this surface.
