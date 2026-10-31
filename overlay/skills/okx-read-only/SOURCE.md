# SOURCE — okx-read-only

Provenance record for `SKILL.md` in this directory (traceability, not a
product brand surface).

- **Authorship:** this is **our own skill page**, written for I'm AiDog from
  the vendor's official documentation. No third-party skill text was copied
  or adapted; there is no upstream skill file this derives from.
- **Official documentation relied on (all retrieved 2026-10-24):**
  - OKX API v5 reference (REST): `https://www.okx.com/docs-v5/en/` —
    authentication headers (`OK-ACCESS-KEY` / `OK-ACCESS-SIGN` /
    `OK-ACCESS-TIMESTAMP` / `OK-ACCESS-PASSPHRASE`), the
    `Base64(HMAC-SHA256(secret, timestamp + method + requestPath + body))`
    signing rule, ISO 8601 UTC-millisecond timestamps with the >30 s skew
    rejection (error `50102`), the `Read` / `Trade` / `Withdraw` permission
    tiers, the ≤20 IPs per key limit and the 14-idle-day expiry of unbound
    trade/withdraw keys, and the endpoints used by `exchange_markets` /
    `exchange_portfolio` / `exchange_orders`
    (`/api/v5/market/ticker`, `/api/v5/account/balance`,
    `/api/v5/trade/orders-pending?instType=SPOT`).
  - OKX agent documentation: `https://app.okx.com/docs-v5/agent_en/` —
    official read-only posture (market module keyless, other modules need
    `Read`, writes need `Trade`), which corroborates the least-privilege
    prescription above.
- **Verification:** the public endpoints in `SKILL.md` were exercised live
  on 2026-10-24 (see `docs/features/exchanges.md` §2); the private
  endpoints and every header above were verified against the official
  signing rule and permission docs cited here, not against a stored key.

## Notes

1. Frontmatter follows this repository's skill convention (`name` + a
   single-line `description` under 200 chars, the router trigger parsed by
   `lib/skill_route.py`).
2. The four required disclosures (free product ≠ zero fees, not investment
   advice, trading risk borne by user, credentials stay in this machine's
   `secrets.env`) are I'm AiDog policy (owner decree, 2026-10-24), not
   vendor text.
3. W1 has no order placement; the prepare/execute code boundary and the
   approval-gate language are this product's own contract, taken from
   `docs/features/exchanges.md`.
