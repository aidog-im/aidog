# Per-Tool Kill Switch (`mycore.tools_off`)

Status: shipped in **v0.5.32** (`mycore/tools.py::build_registry` +
`resolve_disabled_tools`, settings validator in `web/server.py`).

One bounded list decides **which tools the model can see**. A disabled tool
is not partially restricted — it is *unregistered*: absent from the OpenAI
`tools` schema, and a dispatch against it returns the ordinary
`ERROR: unknown tool '<name>'` line the model already understands for any
unknown name. Nothing else about the registry changes (probes, MCP sessions,
notes, handler closures are all built exactly as before).

## What it does

| Surface | Behavior |
|---|---|
| Model schema | `build_registry(disabled=...)` drops the named tools from `schema()` |
| Dispatch | disabled name → normal `unknown tool` error (never a crash, never a different message) |
| Capability catalog | `/api/capabilities` `tools` block **follows the live registry** — it is built from `build_registry(...)`, never a hand-maintained list, so it cannot drift |
| Console / CLI / ACP / service | every production registry builder passes `disabled=resolve_disabled_tools(...)` |
| Empty config | `disabled` None/empty → the produced registry is **byte-identical** to the pre-switch one (same names, same schema JSON) |

## Config precedence

```
env MYCORE_TOOLS_OFF (comma-separated, whitespace tolerated)
    >  settings key  mycore.tools_off  (JSON list of strings)
    >  empty set (everything enabled)
```

- The env var **overrides** settings whenever it is present and non-empty —
  a deployment can pin the kill set without touching `web/settings.json`.
- When `settings` is `None`, the resolver loads the standard document via
  `lib.profile_paths.settings_path()` — the same loader/path convention
  `mycore.service._settings_doc` uses (per-profile aware, car F §13.1).
- Invalid settings shape (`tools_off` not a list, non-string entries) is
  ignored by the resolver (fail-safe: registry construction never raises);
  such a value is rejected earlier at write time, so it should not exist.

## Fail-closed validation (`POST /api/settings`)

`mycore.tools_off` is an allow-listed key (`MYCORE_KEYS`) with a dedicated
branch. All of these are refused with a `400` and an exact
`mycore.tools_off ...` reason, and **nothing is written**:

| Rule | Error |
|---|---|
| must be a list of **1..32** strings | `mycore.tools_off must be a list of 1..32 tool names` |
| each entry `^[a-z][a-z0-9_]{0,39}$` | `mycore.tools_off entries must match [a-z][a-z0-9_]{0,39} (got '...')` |
| no duplicates | `mycore.tools_off duplicate entry: <name>` |
| each in `KNOWN_TOOL_NAMES` | `mycore.tools_off unknown tool: <name>` |
| catalog not importable | `mycore.tools_off unavailable` (import failure → refuse, never guess) |

Only the offending **tool name** is ever echoed — never a value, never
anything secret (the field holds names only, so there is no secret to leak).
The catalog lives in `mycore/tools.py::KNOWN_TOOL_NAMES` (**25 names** = 11
core + `generate_image` + `connector_search` + the six `browser_*` + the six
`exchange_*`);
capability-honest registration (browser binary / connector slots / CEX
credential slots — see [exchanges.md](exchanges.md)) still
governs whether a *known* tool actually appears — the switch can only remove.

The three W2 order tools joined the catalog with the order car:
`exchange_prepare_order` (dry run — stores a plan, sends nothing),
`exchange_execute_order` (takes only `plan_id`, fail-closed), and
`exchange_plans` (`list`/`cancel` — no approve, approval is console-only).
They are gated exactly like the W1 account reads: registered only when
**at least one CEX credential slot set exists**, otherwise withheld with the
registry's honest `exchange_note` naming the missing slots. Knowing a name
is not having the capability — and like every other name, they can be
*removed* with `MYCORE_TOOLS_OFF=exchange_execute_order` (env) or
`"mycore": {"tools_off": ["exchange_execute_order"]}` (settings); the kill
switch can never *enable* a slot-gated tool.

An **unknown name from the env var** is not fatal: it is ignored with one
honest, non-secret stderr note per name —
`tools_off unknown: <name>` — and the remaining known names still apply.

## Security note

- No secrets: the key stores tool names only; validation echoes just the
  offending name; resolver stderr notes carry a name, never a value.
- Bounded: 1..32 entries × ≤40 chars each (≤ ~1.3 KB), regex-checked — the
  list is never unbounded input, never a path, never a command.
- Fail-closed: an unavailable catalog refuses the write rather than
  admitting an unchecked name; an invalid stored document degrades to
  "resolver ignores it" instead of crashing registry construction.

## Test evidence

**Core suite** (`mycore.tests.run::test_tools_toggle`,
12 checks, wired into the suite by car G):

- `tools_off: KNOWN_TOOL_NAMES covers core + conditional`
- `tools_off: settings dict -> set` / `tools_off: disabled tool absent from schema`
- `tools_off: dispatch says unknown tool`
- `tools_off: env overrides settings`
- `tools_off: empty/invalid config resolves empty …` (×4)
- `tools_off: disabled=None parity` / `tools_off: disabled=frozenset() parity (byte-identical names)`
- `tools_off: unknown env name ignored, known kept`

**Web console smoke** (`tests/web_console_p3.py`, 4 new legs → total **154/154**):

- `POST mycore valid tools_off -> 200 + round-trips`
- `POST mycore unknown tools_off name -> 400 + name`
- `POST mycore tools_off wrong type -> 400`
- `POST mycore tools_off duplicate entry -> 400`

Shipped in v0.5.32.

**Console coverage (frontend car, 0.5.39)** — the switch now has a face:
Settings renders `#toolsOffList`, a checkbox grid built from
`capabilities.mycore.known_tools` (checked = off), and `MYCORE_TOOLS_OFF`
still wins over it when exported (the catalog then also reports the name in
`env_overrides`). Evidence: `tmp/e2e_frontend_coverage.py` `L3` (write →
`capabilities.tools` shrinks) and `tmp/e2e_frontend_ui.js` `U1` (save round
trip) — see [frontend_coverage.md](frontend_coverage.md).
