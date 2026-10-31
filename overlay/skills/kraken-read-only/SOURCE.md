# SOURCE — kraken-read-only

Provenance record for `SKILL.md` in this directory (traceability, not a
product brand surface).

- **Authorship:** this is **our own skill page**, written for I'm AiDog from
  the vendor's official documentation. No third-party skill text was copied
  or adapted; there is no upstream skill file this derives from.
- **Official documentation relied on (all retrieved 2026-10-24):**
  - Spot REST Authentication guide:
    `https://docs.kraken.com/exchange/guides/rest/authentication` — the
    `API-Sign` formula (`HMAC-SHA512 of (URI path + SHA256(nonce + POST
    data)) and base64 decoded secret API key`, path starting at `/0/private`),
    the strictly-increasing nonce requirement, `EAPI:Invalid nonce`, and the
    **official published example vector** the skill cites (nonce
    `1616492376594`, path `/0/private/AddOrder` →
    `4/dpxb3iT4tp/ZCVEwSnEsLxx0bqyhLpdfOpc6fn7OR8+UClSV5n9E6aSS8MPtnRfp32bAb0nmbRn6H8ndwLUQ==`).
  - API key permissions guide:
    `https://docs.kraken.com/exchange/guides/rest/api-keys` — the
    least-privilege permission names quoted verbatim in `SKILL.md`
    (`Funds permissions - Query`, `Orders and trades - Query open orders &
    trades`), the `Query Funds` → `Balance` mapping, and the advice that
    over-granting permissions is the risk to avoid.
  - Spot REST API reference: `https://docs.kraken.com/api/` (OpenAPI-derived;
    also `https://docs.kraken.com/api-reference/`) — the endpoints this skill
    names: `GET /0/public/Ticker`, `POST /0/private/Balance`,
    `POST /0/private/OpenOrders`.
  - Documentation index: `https://docs.kraken.com/llms.txt` — confirms
    Spot REST auth is HMAC-SHA512 via `API-Key` + `API-Sign` headers.
- **Verification:** the signature construction in `SKILL.md` matches the
  official example vector above **byte-for-byte**, which is why it is
  trustworthy without exercising a live key; the public ticker endpoint was
  exercised live on 2026-10-24 (see `docs/features/exchanges.md` §2).

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
