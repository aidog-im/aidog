# Community posting (in-app) — 0.5.54

Reading the product repository's GitHub Discussions has shipped since 0.5.53
(anonymous REST, `GET /api/discussions`, 600s cache). This car adds the WRITE
side: the owner and the agent can start a discussion or reply to one **without
leaving the console**, and every credential slot can be checked in place.

## The one credential

`AIDOG_COMMUNITY_PAT` — a dedicated slot in `secrets.env`, editable from
Settings → Credentials like every other slot.

*Why a separate slot?* `AIDOG_GH_PAT` exists only so `aidog update` can read
the release repository with a higher anonymous rate limit; a public repo reads
fine without it, so ordinary users never fill it, and whoever does holds a
broadly-scoped token the console should not borrow for posting. The community
slot is scoped to Discussions, optional (reads stay tokenless), and its absence
is a designed state: every write answers `reason: "no_token"` and the UI shows
which slot to set instead of a dead form.

Verified by `POST /api/slots/verify` (`level: "live"`): the token resolves to a
GitHub user **and** can read this repository's discussion categories — exactly
what posting needs.

## Faces

| Route | Gate | Contract |
|---|---|---|
| `GET /api/discussions` | none (loopback) | unchanged — the public list, anonymous |
| `GET /api/discussions/categories` | none (needs the slot upstream) | `{available, categories[{id,name,slug}], checked_at[, reason]}`; GraphQL is the only source of category node ids |
| `GET /api/discussions/replies?number=N` | none (loopback) | `{available, number, replies[{id,author,body,created_at,url}], checked_at[, reason]}` — anonymous REST, 60s per-thread cache, `?fresh=1` busts it, a non-`https://github.com/` href scrubs to `""` |
| `POST /api/discussions/create` | Bearer `WEB_TOKEN` | `{title, body, category}` -> `{ok, number, url}` |
| `POST /api/discussions/comment` | Bearer `WEB_TOKEN` | `{number, body}` -> `{ok, number, comment_id}` |
| `POST /api/slots/verify` | Bearer `WEB_TOKEN` | `{name}` or `{all:true}` -> `{results[{name, group, set, level, ok, reason, detail}], checked_at}` |

Validation answers 400 **before** any outbound call; upstream failures land on
one ladder — 404 `not_found` · 409 `no_token`/`no_scope` · 429
`busy`/`rate_limited` (with `retry_s`) · 502 `network`/`upstream_error` — every
one a category WORD plus an upstream-safe detail. Never a token, never a
traceback, never a URL with a query.

## One limiter, two writers

`mycore/community.py` is the single channel behind BOTH the console routes and
the agent's `community_post` tool, so the two cannot race past each other:

* **interval** — one write per 30s, persisted in the state dir so it spans the
  console process and the chat service; a refusal says how many seconds to
  wait;
* **caps** — title 120, body 2000 chars, checked before the network;
* **failure vocabulary** — `REASONS` in the module, mapped by the UI through
  `comm.reason.*`.

Owner decision (2026-10-27): the agent posts **directly** — no prepare/approve
gate for community posts, unlike `exchange_*` orders. The interval and the caps
are the bound; the console still needs the access token for its own posts.

## The tool

`community_post` (always registered, `KNOWN_TOOL_NAMES` #26):
`action: post | reply`, required `body`, `title`/`category` for a post,
`number` for a reply. No slot -> an honest `ERROR:` line naming nothing secret,
exactly like `generate_image`.

## Slot verification (Settings → Credentials)

Every row gets a **VERIFY** button, the card gets **VERIFY ALL**. Two levels,
both honest about what they proved:

* `format` — the stored value's shape against a conservative rule (8 slots
  carry a specific rule, the rest the generic single-line one); the detail
  states the *expectation*, never the value;
* `live` — a real 5s probe for the four slots a provider can actually prove:
  `AIDOG_COMMUNITY_PAT` (user + Discussions scope), `AIDOG_GH_PAT` (`/user`),
  `MYCORE_TG_TOKEN` (`getMe`), `MODEL_API_KEY` (`GET {base_url}/models` — the
  URL itself never echoed, it may carry embedded credentials).

Unset slots answer `ok: null, reason: "not_set"` — absence is information, not
a failure. Live probes run in parallel, so `VERIFY ALL` costs one timeout.

## Tests

* core `mycore/tests/run.py`: `test_community` (validation before transport,
  category mapping + cache, the create/comment mutations, the shared 30s
  window, status classification, `verify_token`, zero credential echo) and
  `test_community_tool` (registration, schema, honest ERROR without the slot).
* console smoke `tests/web_console_p3.py`: gate + validation legs against the
  live console (no outbound), success paths in-process with an injected
  transport, a temp secrets/state dir and a local `getMe` double.
* probe `tmp/parity_delta4_verify.js` extends to the composer, thread
  expansion and the verify controls.
