# Feature spec — Multi-upstream model failover (`mycore/modeltier`)

Car 1-D4 (v0.5.22). Closes the post-swap backlog item `mycore/modeltier`:
the product must ship endpoint failover **built in**, because end users
connect their own model API keys and run no local proxy (distribution
requirement, `docs/model_access_tier.md` — Tier C, five strategies).

## What v1 ships

1. **Ordered chain**: primary `mycore.base_url` first, then each configured
   upstream — tried only after a failover-class failure.
2. **Same-URL fail-fast**: an upstream is attempted exactly once per call,
   never retried inside the same turn (the root cause of the historical
   Retry-After-less 503 loop).
3. **Two-way error routing** (the spec's 4-way routing, trimmed honestly):
   - **fatal** — HTTP 400/405/413/415/422: the identical payload would be
     rejected by every upstream, so failing over would only multiply a
     client-side bug → raise at once;
   - **failover-class** — transport failures, 401/403/404/408/429/5xx,
     malformed/JSON-less responses → next upstream.
4. **No replay after the first streamed byte**: once any delta reached the
   caller, a mid-stream failure raises instead of re-running the model and
   duplicating output.
5. **Credential pool = slot names**: each upstream names an environment
   variable (`key_env`) for its key; values are read from the environment per
   attempt and never stored, logged, or echoed (zero-echo rule holds through
   the failover error paths).

A single-endpoint config (the default) behaves byte-identically to pre-D4,
including error messages.

## Configuration

Precedence: env `MYCORE_UPSTREAMS` > CONSOLE settings `mycore.upstreams` >
off (empty = off). One line, comma-separated entries, at most 4, ≤1024 chars:

```
url[|key_env[|model]] , url[|key_env[|model]]
```

- `key_env` — env-var name holding that upstream's key; empty = inherit the
  primary slot (`MODEL_API_KEY`).
- `model` — model id for that upstream; empty = inherit the primary model.
- A per-call `model` option overrides every chain model (explicit beats
  configured).

The settings validator runs the same parser (`mycore.modeltier.parse_upstreams`)
and reports its exact reason; an invalid spec at turn time turns the chain off
and surfaces in `ModelConfig.describe()` as `upstreams-invalid: <reason>` —
never a silent off, never a crash. The console status card shows
Fallback chain = enabled when `mycore.upstreams` is non-empty.

## Deliberately NOT in v1 (spec residual, stated up front)

- queue dual-tier timeouts (30s interactive / 300s capacity),
- 60s capacity backoff without failure-marking,
- per-key health scoring / circuit breaker across attempts,
- protocol adaptation across heterogeneous provider dialects.

Those need a real multi-provider fleet to tune. On a single primary endpoint
the local gateway's key pool (Tier A topology) still sits in front, so v1's
chain is the *portability* layer, not a replacement for key-pool ops.

## Tests / gates

`python3 -m mycore.tests.run` — `test_modeltier()`: spec grammar (valid/
invalid/count cap), fatal-vs-failover classification, chain default
resolution, env > settings precedence with honest invalid-spec reporting,
failover success with per-upstream model + its own key slot, payload error
stopping at one attempt, exhausted-chain error with failover context and no
key material, single-endpoint message unchanged, per-upstream auth-header
policy (loopback placeholder vs keyless non-loopback), stream failover before
the first byte, and no replay after it.

## Related

- Module: `mycore/modeltier.py` (parsing + classification) · attempt loop:
  `mycore/model.ChatClient.chat/_attempt`
- Config: `mycore/cli.py::resolve_model_config`, `web/server.py`
  (`MYCORE_KEYS`, `_mycore_config_bits`), `web/settings.example.json`
- Design/history: `docs/model_access_tier.md`
