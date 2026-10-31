# Event Hooks

Status: shipped in car 4a (`mycore/hooks.py`, M2b; wired into
`mycore/service.py` and validated on settings writes in `web/server.py`).

Run a local command on agent lifecycle events — notifications, metrics
pings, audit trails — without forking the agent and without giving a hook
any way to block or break a conversation.

## Event whitelist (six, nothing else)

| Event | Fires when |
|---|---|
| `turn_start` | A chat turn begins (before the model call). |
| `turn_end` | A chat turn finishes (reply ready or turn stopped). |
| `tool_call` | A tool is dispatched inside a turn (tool name only). |
| `compact` | Context compaction folded a conversation. |
| `cron_fire` | A scheduled job is delivered. |
| `error` | The turn pipeline hit an error path. |

Any other key is refused **at settings-write time** (HTTP 400 naming the
whitelist) — unknown events never persist and never fire. `checkpoint` is
deliberately absent: snapshots happen inside the tool layer, and a
restore-only hook would be misleading.

## Settings schema (`web/settings.json` top-level `hooks`)

```json
{
  "hooks": {
    "turn_end": [
      {"cmd": ["scripts/notify.sh", "arg"], "timeout": 5}
    ]
  }
}
```

- `cmd` — **required**, a non-empty array of single-line strings (≤ 32
  args, each ≤ 4096 chars). `argv[0]` must exist at write time: a
  path-shaped name (contains `/`, or starts with `.`/`~`) resolves against
  the repo root, a bare name resolves through `PATH`. Missing → 400.
- `timeout` — optional integer in **1..30** seconds (default 5).
- Unknown keys inside a hook object → 400. Max 16 hooks per event.
- Validation is all-or-nothing per patch: any error refuses the whole
  write with the failing paths in `details`.

## Execution semantics

- **Background thread, never inline**: one daemon thread per `fire`; the
  event's hooks run sequentially in registration order on that thread. A
  hook can never block or fail a turn — `fire` returns the thread (or
  `None` when nothing is configured) and never raises.
- **Timeout kills the child**: each hook runs under `subprocess.run` with
  its timeout; expiry kills the process and logs one failure line.
- **Failure isolation**: a non-zero exit, missing binary, timeout, or OS
  error becomes exactly one `HOOK_FAIL` log line (event + idx + status +
  argv[0] basename + duration). The next hook of the same event still runs.
- **No shell, ever**: `cmd` is exec'd directly as argv. Shell
  metacharacters in arguments are literal text — `; rm -rf` is just a
  string, never syntax.
- **Minimal environment**: the child inherits only `PATH` and `HOME`, plus
  `AIDOG_HOOK_EVENT` (event name), optional `AIDOG_HOOK_CHAT`
  (pre-redacted `id:<last4>` — full chat ids never cross), and
  `AIDOG_HOOK_TOOL` (tool name only, never arguments). API keys, console
  tokens, and channel credentials do not cross into a hook process.
- **Zero echo**: hook stdout/stderr is captured and discarded. It never
  enters a chat reply, a log line, or any API response — a hook that prints
  a secret leaks nowhere on our side.

## Console write entry

`POST /api/settings` with the `hooks` partition (authenticated, same
allow-list as every other partition — `hooks` is in `ALLOWED_TOP`).
Validation delegates to `hooks.validate_hooks`, the single source of truth
for both write-time checks and load-time cleanup (`load_hooks` fails soft:
an unreadable/corrupt settings file just means "no hooks configured").
`GET /api/capabilities` reports the configured event names under
`hooks.events`.

## Testing evidence

- `mycore/tests/run.py::test_hooks` — **20 offline checks** (hermetic temp
  repos, never production `data/`): schema/whitelist/bounds (unknown event,
  cmd shape, timeout range, missing argv[0], unknown keys), real background
  execution (sequential order, live-thread return without blocking), env
  whitelist (hook vars + PATH present, parent secret never crosses),
  failure isolation (`HOOK_FAIL exit:3`, no raise), timeout kill
  (`timeout:1s`), no-shell literalness (metacharacters stay data), and
  zero-echo (a fake secret printed by the hook never reaches any log line).
- `tests/web_console_p3.py` — **4 smoke checks**: unknown event → 400
  naming the whitelist, `cmd` as string → 400 schema error, legal write →
  200 with byte-consistent file + `GET /api/settings` readback (settings
  restored afterwards), and responses never echo a slot value.

Suite totals after car 4b: core **840/840**, smoke **123/123**.
