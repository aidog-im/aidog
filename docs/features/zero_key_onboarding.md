# Zero-Key Onboarding (Local Model Detection)

Status: shipped in car 4b (`mycore/localmodels.py`; wizard insertion in
`deploy/aidog_setup.py`, catalog block in `web/server.py` — see
[capabilities.md](capabilities.md)).

The setup wizard probes well-known **local, keyless** OpenAI-compatible model
servers on loopback so a first run can configure `mycore.base_url` +
`mycore.model` **with no API key at all** — the `MODEL_API_KEY` slot stays
empty, by construction.

## Probe table

| Server | Probe URL | Response shape | Adopted `base_url` |
|---|---|---|---|
| Ollama | `http://127.0.0.1:11434/api/tags` | own: `models[].name` | `http://127.0.0.1:11434/v1` |
| llama-server (llama.cpp) | `http://127.0.0.1:8080/v1/models` | OpenAI: `data[].id` | `http://127.0.0.1:8080/v1` |
| LM Studio | `http://127.0.0.1:1234/v1/models` | OpenAI: `data[].id` | `http://127.0.0.1:1234/v1` |

Per-endpoint rules:

- **0.5 s hard timeout per endpoint** — detection can never stall the wizard.
- **Proxies explicitly disabled** (`ProxyHandler({})`) — loopback detection
  stays direct even when the host exports `http_proxy`.
- **`probe()` never raises**: a refused connection, timeout, HTTP error, bad
  JSON, non-object payload, or missing/blank model id all count as "not
  detected" for that endpoint. `[]` means "nothing answered", not an error.

## `AIDOG_LOCAL_MODEL_PROBES` override

Comma-separated base URLs (e.g.
`http://127.0.0.1:18100/v1,http://127.0.0.1:18101/v1`). When present,
**only** those endpoints are probed — the three defaults are skipped — and
each is probed uniformly in the OpenAI shape (`GET <url>/models`, with an
already-`/models` URL passed through verbatim). Whitespace and empty items
are tolerated; every hit carries `kind: "local"`.

## Wizard insertion (interactive only)

Insertion point: **after `web/settings.json` is created from the template,
before the manual `base_url` prompt.**

| Step | Behavior |
|---|---|
| detect | `detect_local_model()` runs `probe()` — only when the module is importable, and never on `--check` / `--yes` (non-interactive paths skip detection entirely) |
| miss | one honest line: `no local model server detected (tried ollama :11434, llama-server :8080, lm-studio :1234)` — the tried-list names the override URLs when the env var is set. **Printed only when the probe module really imported** — the wizard never claims probes that did not run |
| hit | numbered list, one line per server (`kind base_url (model: …)`) + `use which? [enter = 1, 2..N, n = manual setup]` |
| adopt | **Enter = 1**; a valid pick writes `mycore.base_url` + `mycore.model` (other `mycore` keys preserved from the template), prints `using local model server: <url> (model: <m>) — no API key needed`, **skips the manual base_url/model prompts**, and `MODEL_API_KEY` remains empty |
| decline | `n` / invalid answer → falls through to the unchanged legacy manual prompt flow |

## Capabilities block

`GET /api/capabilities` → `local_models`:

```json
"local_models": {"count": 1,
  "detected": [{"kind": "ollama",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "…"}]}
```

- Records carry **`kind` / `base_url` / `model` only** — three display fields,
  no credential material exists in the payload by construction.
- Default probes are **loopback-only** (`127.0.0.1`); if an operator ever put
  userinfo into an override URL, it is **stripped before serializing**.
- A probe failure inside the endpoint reports `{"count": 0, "detected": [],
  "note": "probe failed"}` — honest degradation, never a fabricated list.
- The endpoint's probes are the same 0.5 s/loopback ones, so the catalog read
  never adds meaningful latency.

## Security and honest boundaries

- **Zero-key by design**: local endpoints are keyless; no slot is read,
  written, or echoed — the wizard's confirmation line explicitly says "no API
  key needed", and `MODEL_API_KEY` is untouched (stays empty).
- **Never raises, never stalls**: bounded timeouts and blanket `except` in
  `probe()`; the wizard additionally guards the import.
- **No false claims**: the miss line is emitted only when detection actually
  ran; `--check`/`--yes` are byte-unchanged legacy paths (read-only /
  non-interactive).
- `describe()` renders `kind + base_url + model` and nothing else — no
  hostname credentials, no payload dumps.

## Testing evidence

- `mycore/tests/run.py::test_localmodels` — **13 offline checks** with an
  injected `_fetch` (**zero sockets**): both payload shapes, malformed
  payloads, the override list (comma/whitespace tolerance, `/models`
  normalization, `kind: local`), per-kind default probes with base_url
  mapping, blank model = not detected, never-raises legs, and `describe()`
  field discipline.
- `tests/setup_wizard_b3.py` — **19 checks** across two wizard legs (suite
  total **99/99**): (A) fake loopback server via `AIDOG_LOCAL_MODEL_PROBES` →
  detected line, numbered entry, Enter = 1 adoption written to settings,
  other keys preserved, `MODEL_API_KEY` still empty, confirmation printed,
  manual prompts absent, fast run, zero-echo; (B) all probes closed → one
  miss line naming the three defaults, legacy prompt sequence unchanged.
- `tests/web_console_p3.py` — the `local_models` catalog block (`count` ==
  `len(detected)`, record field types, loopback host per entry, no slot value
  anywhere in the catalog).
- Because every probe is loopback + 0.5 s, this whole surface is testable on a
  host **without Ollama installed** — the suites fake the endpoint instead of
  requiring the server; the live-wire leg lives in the repo-local E2E script
  `tmp/e2e_car4b.py` (tmp harness, outside the gate suites).

Suite totals after car 4b: core **840/840**, smoke **123/123**, wizard
**99/99**.
