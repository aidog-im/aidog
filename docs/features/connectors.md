# Connectors (read-only Linear + Notion search)

Status: shipped in car 4c (`mycore/connectors.py`; tool wiring in
`mycore/tools.py`). Companion pages: [capabilities.md](capabilities.md)
(the `media.connectors` block).

Read-only BYOK retrieval: one `connector_search` tool answers from whichever
connector has a credential slot. **No slot → the tool does not register** and
the registry carries an honest `connector_note` instead (same fail-soft
pattern as `registry.browser_note`) — a capability we don't have is never
advertised as a tool that will fail.

## 1. Protocol contracts (verified against both official SDK sources)

Protocol facts were taken from the vendors' **own SDK sources**, not from
memory or blog posts:

- **Linear** — official TypeScript SDK source
  (`_generated_sdk/_documents.ts`):
  - `POST https://api.linear.app/graphql`;
  - a **personal API key rides `Authorization: <key>` RAW — no `Bearer`
    prefix** (the `Bearer` form is for OAuth tokens; sending it with a
    personal key is the classic integration bug we avoided);
  - retrieval query `issueSearch(query, first)` →
    `{data: {issueSearch: {nodes: [{identifier, title, url}]}}}`;
    only always-present fields are projected;
  - https enforced by the service (local http only for the test seam).
- **Notion** — official JS SDK source:
  - `POST https://api.notion.com/v1/search`;
  - headers `Authorization: Bearer <key>` **+ `Notion-Version: 2025-09-03`**
    (the SDK's current default, adopted verbatim);
  - body `{query, page_size}` → `{results, has_more, next_cursor}`;
  - a result title = concatenation of every `properties` entry with
    `type: "title"`, falling back to `object` + `url`.

Endpoint override seams for the offline E2E mock: env `AIDOG_LINEAR_URL` /
`AIDOG_NOTION_URL`. The transport is injectable (callable
`(url, headers, payload_bytes, timeout) -> response text`), so the core suite
runs with **zero network and zero real keys**.

## 2. Registration and failure semantics

| Situation | Behavior |
|---|---|
| ≥1 of `LINEAR_API_KEY` / `NOTION_API_KEY` present (env or `secrets.env`) | `connector_search` registered; `registry.connector_note = None` |
| neither slot present | tool **not registered**; `connector_note = "connectors not configured (LINEAR_API_KEY / NOTION_API_KEY)"` |
| probe raises (broken module etc.) | tool not registered; note carries `unavailable: <Type>: <msg>` — the probe never breaks the registry |
| calling the unregistered name | honest `ERROR: unknown tool 'connector_search'` |
| slot appears/disappears | registration re-evaluates on every `build_registry()` — no restart ceremony beyond the process that builds it |

`GET /api/capabilities` → `media.connectors: {linear: bool, notion: bool}`
— **slot PRESENCE only**; the value never leaves `connectors.status()`.

## 3. Tool contract

```
connector_search {query, source?=auto, limit?=5}
```

- `source`: `auto | linear | notion`. `auto` queries **every configured
  connector** and labels sections `[linear]` / `[notion]`; an explicit
  source fails honestly when its own slot is missing (naming the slot).
- `limit`: integer 1..20 (default 5); `query` must be non-empty; default
  timeout 20 s per request.
- Result = compact text lines (`identifier · title · url` for Linear,
  `title · url` for Notion) — enough for the model to cite, not a data dump.
- **Empty result is an honest empty line** (`[linear] no results for '…'`),
  never fabricated content.
- Upstream/provider errors → `ConnectorError` with a bounded message
  (provider body truncated to 160 chars); credentials never appear in any
  error, log, repr or return value.

## 4. Honest boundaries

- **Read-only, search only**: `issueSearch` and Notion `/v1/search` — no
  writes, no mutations, no comment posting, no state changes. This is the
  deliberate starting scope; a write surface would need its own approval
  gate (three-class request rule) and does not exist yet.
- **Not the full vendor APIs**: no issue creation, no database queries, no
  board updates, no OAuth flow — personal-key search retrieval and nothing
  more. "Connectors" here means exactly this tool.
- **No key = pending**: without slots the tool is absent (§2) and the
  capability block reads `false` — external reachability is BYOK and is
  claimed nowhere in the product surface.
- `auto` mode's coverage is bounded by `limit` per source — a huge workspace
  returns the first N hits per connector, not exhaustive results.

## 5. Verification (what actually exercises this)

- **Core suite** (`mycore/tests/run.py::test_connectors`): `status()`
  presence-only semantics (slot values never surface), header contracts via
  injected transports (**Linear raw `Authorization`, Notion `Bearer` +
  `Notion-Version: 2025-09-03`**), response-shape parsing to compact lines,
  validation (blank query, bad source, limit bounds), bounded upstream
  errors (one loopback 500 exercises real HTTP truncation), and both
  endpoint-override seams. `test_media_tools` covers registration gating
  both ways (no slot → note, slot → registered + note cleared) and dispatch
  rendering.
- **Web smoke** (`tests/web_console_p3.py`): the `media.connectors`
  booleans inside the zero-credential capability assertion.
- **E2E** (`tmp/e2e_car4c.py`): local mock Linear GraphQL + Notion search
  endpoints over real sockets → `search()` returns both titles, with the
  captured headers asserting the raw-key vs Bearer distinction; temporary
  `secrets.env` slots make `media.connectors` read `true` (values never
  serialized) and are restored **byte-for-byte**, followed by a green smoke
  re-run. Real-provider connectivity remains **pending credentials**.
- **Console coverage (frontend car, 0.5.39)**: `LINEAR_API_KEY` and
  `NOTION_API_KEY` now have `connectors`-group rows in the Settings
  credential editor (set/clear behind the Bearer gate; echo = name +
  length). Evidence: `tmp/e2e_frontend_coverage.py` `L1` (roundtrip +
  zero-echo sweep) and `tmp/e2e_frontend_ui.js` `U2` (set/clear + DOM value
  scan) — see [frontend_coverage.md](frontend_coverage.md).
