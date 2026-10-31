# Feature spec — Multi-upstream model failover (`mycore/modeltier`)

Car 1-D4 (v0.5.22, v1) · car 4a-M1 (v0.5.27, v2). Closes the post-swap
backlog item `mycore/modeltier`: the product must ship endpoint failover
**built in**, because end users connect their own model API keys and run no
local proxy (distribution requirement, `docs/model_access_tier.md` — Tier C,
five strategies).

## What v1 ships (kept verbatim in v2)

1. **Ordered chain**: primary `mycore.base_url` first, then each configured
   upstream — tried only after a failover-class failure.
2. **Exactly-once discipline — REVISED in v2**: v1 attempted each *endpoint*
   exactly once per call. v2 revises the unit to **(endpoint, key slot)**:
   every pool key is tried at most once per call, and only *auth* failures
   rotate keys inside the endpoint (see below). Capacity/transport failures
   still fail the whole endpoint at once — another key rarely fixes a
   429/5xx on the same endpoint (the root cause of the historical
   Retry-After-less 503 loop stays fixed).
3. **Two-way error routing** (the spec's 4-way routing, trimmed honestly):
   - **fatal** — HTTP 400/405/413/415/422: the identical payload would be
     rejected by every endpoint, so failing over would only multiply a
     client-side bug → raise at once;
   - **failover-class** — transport failures, 401/403/404/408/429/5xx,
     malformed/JSON-less responses → next candidate.
4. **No replay after the first streamed byte**: once any delta reached the
   caller, a mid-stream failure raises instead of re-running the model and
   duplicating output.
5. **Credential pool = slot names**: each upstream names environment
   variable(s) (`key_env`) for its key(s); values are read from the
   environment per attempt and never stored, logged, or echoed (zero-echo
   rule holds through the failover error paths — `ModelError` carries status
   codes, exception class names and endpoint URLs only, never slot names
   either).

A single-endpoint config (the default) behaves byte-identically to pre-D4,
including error messages.

## What v2 adds (car 4a-M1)

1. **Credential pool (multi-slot `key_env`)** — `url|K1,K2,K3|model`: the
   slot field may list several env-var names. On **401/403** the call
   rotates to the next untried slot of the *same* endpoint (once per slot);
   pool exhausted = that endpoint failed → failover class → next endpoint.
   Non-auth failures do **not** rotate (rule 2 above).
2. **Per-key health + capacity backoff** — an **in-process** map (never
   persisted — honest scope) counts consecutive failover-class failures per
   `(endpoint, key slot)` identity (an unnamed key cools per endpoint). The
   2nd consecutive failure starts a cooldown: **30s → 60s → 120s → 240s,
   capped at 300s**. Cooling units are **skipped** when candidates are
   built — but when the *whole* chain cools, the **least cold** unit is
   still attempted (a call never degrades into a silent zero-attempt stop).
   One success clears failures, cooldown and escalation level. Fatal
   (payload-class) errors are never counted — they say nothing about
   endpoint health.
3. **Prompt-cache signals** (pass-through only, no directives sent):
   - **deterministic system prefix**: `assembly.build_system_stack` +
     `stack_digest` are asserted byte-identical across two builds of the
     same inputs (the premise of upstream prompt caching), in the suite;
   - **usage passthrough**: when a response carries
     `usage.prompt_tokens_details.cached_tokens` or
     `usage.cache_read_input_tokens`, the count is copied into the extended
     field `usage["cached_tokens"]` (streamed final-chunk `usage` included).
     No provider count → nothing is invented (no usage block → no usage key).

### v2 visibility of cooldown state

- `HealthRegistry.note()` / `modeltier.health_note()` — the cooling list as
  `<base_url>|<slot name> <remaining>s` entries (slot NAMES only, zero key
  material).
- `ModelConfig.describe()` appends the cooling state **scoped to that
  config's units**; it appears in the CLI `[mycore] ...` stderr line and in
  `ChatClient.__repr__`.
- The web console status card reports the *configured* chain
  (`fallback_enabled` from `mycore.upstreams` in settings, via
  `/api/status` → `_mycore_config_bits`) plus settings-validation errors;
  it does **not** yet render process cooldown (the web layer does not call
  `describe()` — surfacing it there would be a web-layer addition).

## Configuration

Precedence: env `MYCORE_UPSTREAMS` > CONSOLE settings `mycore.upstreams` >
off (empty = off). One line, comma-separated entries, at most 4, ≤1024
chars; **a new entry starts only where a comma is followed by an
`http(s)://` scheme**, so commas inside a pool belong to the entry:

```
url[|key_env[|model]] , url[|key_env[|model]]
key_env = SLOT  |  SLOT,SLOT,SLOT        (max 8 slots per endpoint)
```

- `key_env` — env-var name(s) holding that endpoint's key(s); empty =
  inherit the primary slot(s) (`MODEL_API_KEY`). Slot grammar:
  `[A-Za-z_][A-Za-z0-9_]{0,63}`; a URL may not contain a comma (entry-data
  rule keeps v1's rejection of `http://a,b` byte-compatible).
- `model` — model id for that upstream; empty = inherit the primary model.
- A per-call `model` option overrides every chain model (explicit beats
  configured).

The settings validator runs the same parser (`mycore.modeltier.parse_upstreams`)
and reports its exact reason; an invalid spec at turn time turns the chain off
and surfaces in `ModelConfig.describe()` as `upstreams-invalid: <reason>` —
never a silent off, never a crash. The console status card shows
Fallback chain = enabled when `mycore.upstreams` is non-empty.

## Deliberately NOT in v2 (stated up front)

- **cross-process health persistence** — cooldown state lives in this
  process only (a restart starts fresh; stated, not hidden);
- **request queues / dual-tier timeouts** (30s interactive / 300s capacity)
  — this product has no queue; the fail-fast chain is the design;
- **`cache_control` injection** — we never send prompt-cache directives;
  we only pass through cache hits the provider reports;
- protocol adaptation across heterogeneous provider dialects (still needs a
  real multi-provider fleet to tune).

## Tests / gates

`python3 -m mycore.tests.run` — `test_modeltier()` now runs **44 checks
(18 v1 + 26 v2)**; suite total **981/981** (car F; car-4c was 959/959; baseline was 645).

- v1 (unchanged): spec grammar, fatal-vs-failover classification, chain
  default resolution, env > settings precedence with honest invalid-spec
  reporting, failover success with per-upstream model + key slot, payload
  error stopping at one attempt, exhausted-chain error with failover context
  and no key material, single-endpoint message unchanged, per-upstream
  auth-header policy, stream failover before the first byte, no replay after.
- v2 (new): multi-slot parsing (valid / invalid slot names / slot cap /
  entry cap), units expansion, 401 → rotation → second key succeeds,
  rotation reads values from env per attempt (describe shows names only),
  pool exhausted → failover onward, every pool key exactly once per call,
  capacity error does NOT rotate, auth-exhausted error without slot names or
  key material, 2 consecutive failures cool the key and the cooled one is
  skipped, honest cooldown note, escalation ladder 30/60/120/240 capped
  300 (fake clock), one success clears everything, whole chain cooling →
  least cold still attempted (and still raises honestly if it fails),
  cooling surfaced in `describe()`/`health_note()` scoped per config with no
  key material, `cached_tokens` passthrough (both provider shapes),
  nothing invented when absent, streamed final-chunk usage surfaced,
  byte-identical system prefix + stable stack digest across two builds.

## Related

- Module: `mycore/modeltier.py` (parsing + classification + health policy
  state) · attempt loop: `mycore/model.ChatClient.chat/_candidates/_next_unit`
- Cache signals: `mycore/model.py::surface_cached_tokens` · prefix premise:
  `mycore/assembly.py::build_system_stack/stack_digest`
- Config: `mycore/cli.py::resolve_model_config`, `web/server.py`
  (`MYCORE_KEYS`, `_mycore_config_bits`), `web/settings.example.json`
- Design/history: `docs/model_access_tier.md`
