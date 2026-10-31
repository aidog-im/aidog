# Model gateway management (structured endpoints, rotation, rate caps, self-metering) — 0.5.55

The 0.5.54 console could only express the failover chain as ONE text line
(`mycore.upstreams`) and had a single primary `base_url`/`model`; usage
numbers came from an external metrics source most end users do not run. This
car (owner directive 2026-10-27 #4) turns the Model card into a real
management panel — structured endpoints, per-key credential pools, candidate
rotation, sliding-window rate caps and a SELF-METERED usage/cost block — with
**no new process**: everything rides the existing failover kernel
(`mycore.modeltier` v2) and the console the product already ships.

Design source of truth: the owner's three decisions (2026-10-28) —
management panel (not a local proxy process), one slot per key, full feature
set in one car; product-native metering as the data source; dual-track
compatibility with the one-line spec.

## 1. Structured chain — `settings.mycore.endpoints`

```jsonc
"endpoints": [
  { "url": "https://api.example/v1",     // required, http(s)+host
    "model": "some-model",               // optional, inherits primary
    "keys": ["MODEL_UP_K1", "MODEL_UP_K2"] }  // optional slot names (<= 8)
]
```

* Grammar lives in `mycore.modeltier.parse_endpoints()` and answers with the
  same chat-safe `entry N` reasons as the one-line parser (never a URL, slot
  value or model id from the input).
* **Key allowlist is deliberately narrower than the one-line spec**:
  `MODEL_API_KEY | OPENAI_API_TOKEN | MODEL_UP_K[1-8]` only
  (`KEY_SLOT_ALLOW_RE`). The one-line/env path keeps its operator-level
  grammar (any env-var name) — a settings write can never aim a sensitive
  non-model slot (channel/bot/GH tokens) at an operator-chosen URL. This
  defense-in-depth boundary is stated, not accidental.
* Keys reuse `Upstream.key_env` verbatim (`",".join(keys)`), so chain /
  units / failover / health behave byte-identically downstream.

**Dual-track precedence** (`mycore.cli.resolve_model_config`):
`env MYCORE_UPSTREAMS` > `settings.mycore.endpoints` > `settings.mycore.upstreams`.
One PATCH carrying BOTH tracks non-empty is refused 400 (the console clears
the other side on save); a hand-edited document holding both loads fine and
`load_settings()` keeps both so the render matches cli's endpoints-win.

## 2. Rotation and rate caps

| Key | Values | Default |
|---|---|---|
| `mycore.rotation` | `""` / `ordered` / `round_robin` | `""` = today's primary-first order |
| `mycore.max_rpm` | int 0..1000 (0 = off) | 0 |
| `mycore.max_tpm` | int 0..10_000_000 (0 = off) | 0 |

* **RotationState** (`modeltier`, process-shared, thread-safe): with
  `round_robin`, `ChatClient._candidates()` starts each call at the next
  offset of the surviving unit list — the chain walks instead of hammering
  the primary. `ordered`/`""` skips the rotation entirely (wire unchanged).
* **RateRegistry** (`modeltier`, mirrors `HealthRegistry`'s injectable
  clock): a 60s sliding window per **(endpoint, key slot)** for requests
  (counted at attempt start) and tokens (provider-reported, counted at
  success — never estimated). Caps are global knobs, windows are per unit,
  so one hot key does not starve another.
* Candidate filter order: cooling skip (v2) → rate skip → rotation. When
  EVERY remaining unit is at a cap, the **least-limited** one is still
  attempted — the same never-silent-stop rule as the cooldown path.
* Honest scope: in-process only (no cross-process persistence), same as
  cooldown state.

## 3. Self-metered usage & cost — `mycore/meter.py`

Every chat attempt appends one JSONL row to
`<state>/model_usage/<UTC-day>.jsonl`: `{ts, day, ep, model, ok[, ms, in,
out, cached, err]}` — endpoint URL, model id and counts only; **no key
material can enter a row** (the source data never reaches the module).

* **Inert by default**: nothing is written until `activate()` runs from the
  three process entries (`mycore.service`, `mycore.cli`, `web.server`), so
  the offline suites have zero file side effects; `record()` on an inactive
  meter is a cheap no-op and every write is wrapped (a full disk drops a
  row, never breaks a call).
* Bounded: daily files, 90-day retention pruned on activate, 2048-char row
  cap, torn lines skipped on read.
* `meter.read(7)` aggregates requests/ok/fail/tokens per endpoint, model and
  day; rows that reported no usage are counted in `no_usage` — **never
  guessed as zero**.
* `meter.estimate_cost(usage, pricing)` prices CNY per 1M tokens from
  `usage.model_pricing` (`{model_id | "default": {in, out}}`, exact match
  then default). A model with no price reports `cost: null` and is listed
  `unpriced` — the panel shows "no price configured" instead of inventing 0.
  Display currency conversion reuses the `/api/usage` FX machinery.

| Route | Gate | Contract |
|---|---|---|
| `GET /api/usage/self` | none (loopback) | `{available, window_days, requests, ok, fail, in/out/cached_tokens, no_usage, by_endpoint[], by_model[], by_day{}, pricing{configured,total_cny,per_model,unpriced}, display}`; inactive meter → `{available:false, reason}` |

The legacy `/api/usage` (external metrics + external pricing JSON) is
untouched — two sources, each honest about its own availability.

## 4. Faces

| Route | Gate | Contract |
|---|---|---|
| `GET /api/mycore/endpoints` | none (loopback) | `{source: env\|endpoints\|upstreams\|none\|invalid, endpoints:[{url, model, primary, keys:[{slot, set}]}], rotation, max_rpm, max_tpm, note, key_presence:{10 slots: bool}}` — slot NAMES + presence booleans only, values never |
| `POST /api/mycore/upstreams/test` | Bearer | now accepts `{url, slot}` — per-key probe of a model slot (`slot` alone → 400; non-model slot → 400; unset slot → 400). Capability is no more than save-then-test; the value resolves for ONE header in this frame (X5 rule unchanged) |
| `POST /api/secrets` | Bearer | additionally accepts `MODEL_UP_K[1-8]` (group `model`, value rule `^\S{8,}$`). **`GET /api/secrets` catalog unchanged (38 rows)** — the pool slots are managed from the Model panel, whose presence comes from `key_presence` |
| `POST /api/settings` | Bearer | `mycore.endpoints/rotation/max_rpm/max_tpm` + `usage.model_pricing` validated (`_validate_model_pricing`); bool rejected for the caps (`True` is not a cap) |
| `POST /api/models/discover` | Bearer | **D1 (round D)** `{base_url, api_key?}` → ONE read-only `GET {base}/models` → `{ok:true, base_url, count, total, truncated, auth, models:[{id}]}` \| `{ok:false, reason}`. `reason` is only an HTTP status, an exception class or a fixed guard word (`body-too-large` / `not-compatible` / `bad-url`); `base_url` is echoed WITHOUT userinfo/query/fragment |

### Settings → Model card

* **Endpoint manager**: one read-only PRIMARY row (its url/model live in the
  Kernel endpoint/model fields) + editable upstream rows — url, model, key
  chips (presence dot, click removes, `+ key slot…` select adds), per-row
  TEST (uses the row's first SET slot) and remove. Source chip:
  `ENV OVERRIDES` (read-only rows) / `CONFIG INVALID` (red + note) /
  `STRUCTURED` / `ONE-LINE`.
* **KEY SLOTS** toggles the 10-slot pool: presence dots + a password input
  per slot writing through `POST /api/secrets` — the input is cleared the
  moment SET is pressed and the value never enters any other node (the UI
  probe greps the whole DOM for it).
* Rotation select + two cap inputs join the save payload only when they
  really changed (untouched form never rewrites the document).
* The old one-line field survives as a collapsed **Advanced** disclosure
  (same `#mycUpstreams` id — probes and old docs keep working).
* **Overview** gains a `SELF-METERED (7 DAYS)` block: requests / ok / fail /
  in / out / cost chips + honest notes (unpriced models, no-usage count,
  truncation). Inactive meter = the whole block hidden, never fake zeros.
* **DISCOVER MODELS (D1)** sits under the whitelist field: an optional key
  (probe-only, never stored) + `DISCOVER` → checkboxes + select-all → `ADD`,
  which appends into the existing `#mycModels` field and rides the normal
  dirty → `#saveBtn` save (no write route of its own). The field's chips
  mirror it both ways.

## 5. Gates (0.5.55)

`python3 -m mycore.tests.run` **2045/2045** (the `gw:` lane: grammar,
rotation, rate windows, meter rows/aggregation/cost, config precedence,
ChatClient gating) · `tests/web_console_p3.py` **432/432** (validation
matrix, mutex, secrets group, both new routes) · `tmp/e2e_gateway_ui.js`
**25/25** (manager round trips, knob persistence, pool write + DOM grep,
remove, self block, zh repaint, zero pageerror).

## 6. Model one-click discovery (D1, round D car 1)

`GET {base_url}/v1/models` is the OpenAI-compatible standard and **reads a
model directory — it never spends a token and never reaches a completion**
(design `ux_round2_design.md` §5.4).

* **Endpoint**: `POST /api/models/discover {base_url, api_key?}` behind the
  console Bearer. Guards: http(s) + host only · one fetch, ≤10s
  (`AIDOG_DISCOVER_TIMEOUT` is a clamped test seam) · response ≤1MB ·
  ≤200 models offered · log line carries counts only.
* **api_key, three rules** (in the code comments and the panel hint):
  inline key → that host's `Authorization` header only; empty key +
  `base_url == settings mycore.base_url` → the saved `MODEL_API_KEY` slot;
  empty key + any other endpoint → NO `Authorization` (an honest 401 then
  says a key is needed). The value is never echoed, never logged, never in
  an exception — and a reflecting endpoint cannot spell it back either (an id
  containing it is dropped like a malformed one).
* **Honest answers**: `401 → reason "HTTP 401"`, non-JSON → the exception
  class, non-list payload → `not-compatible`, >1MB → `body-too-large`;
  `count`/`total`/`truncated` make both the 200 cap and any filtered id
  visible instead of silent. A bare host falls back to `{base}/v1/models`.
* **Client**: `#discPanel` (key + DISCOVER + results + ADD) under the
  whitelist field. `#mycModels` stays the text field activation `U6` fills;
  ADD edits it and dispatches one `input` event, so the dirty flag, the
  sticky save bar and `saveSettings()` remain the single writer. The 32-entry
  ceiling is spoken (`only {n} added …`), never silent.

Gates: `python3 tmp/e2e_d1_discover.py` **37/37** (validation matrix, the
three key rules against a live mock, honest reasons, 1MB/timeout budgets,
cap + filters, `/v1` fallback, URL credential, zero-echo sweep + log sweep,
read-only proof, production byte identity) · `tmp/d1_ui_probe.js` **28/28**
(no-token honesty, key absent from DOM/storage, discovery dirties nothing,
ADD/dedup/chips/bar, checkbox skip, 32 cap, zh/en repaint, reload restores).

## Deliberately not in this car

* per-endpoint rate limits (global knobs only — the reference config is
  global too; the schema seam is ready if the owner asks),
* cross-process persistence of rotation/rate windows (in-process, stated),
* any local proxy process (owner decision: panel, not gateway daemon),
* touching the one-line spec's permissive `key_env` grammar (operator-level
  trust, unchanged).
