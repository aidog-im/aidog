# MCP client (`mycore/mcp.py`) — car 2-D5 (v0.5.23)

Connect external MCP (Model Context Protocol) servers and expose their tools
to the model — stdlib only, zero third-party dependencies, default **off**.

## Configuration (empty = off, zero behavior change)

Precedence: env `MYCORE_MCP` (raw JSON) > settings `web/settings.json`
`mycore.mcp` > off. The JSON is an array of server objects (≤4 servers,
≤4000 chars total):

```json
[
  {"name": "demo",
   "transport": "stdio",
   "command": "python3 -u server.py",
   "cwd": "/optional/workdir",
   "env":    {"API_KEY": "$MY_SLOT"},
   "tools":  ["search_*"],
   "deny":   ["*secret*"]},
  {"name": "web",
   "transport": "http",
   "url": "http://127.0.0.1:3000/mcp",
   "headers": {"Authorization": "$MCP_TOKEN"}}
]
```

- `name`: `[A-Za-z0-9][A-Za-z0-9_.-]{0,31}`, unique.
- `tools` = allow patterns (fnmatch, default = all), `deny` = applied after.
- **Secrets are env slot NAMES only** (`$SLOT` resolved at connect time —
  process environment first, then the profile `secrets.env` own-line slot,
  added in car 8 so an operator who only ever wrote the console never has to
  export the value by hand; a slot missing in BOTH fails that server with an
  honest note naming the SLOT — never a literal secret in settings, never
  echoed). This is the same credential-pool discipline as `modeltier`.
- Settings validation: `mycore.mcp` is a single-line string (≤4000 chars);
  `web/server.py` reports `mycore.mcp: <exact parser reason>` on a bad value.

## Transports

- **stdio**: child process, newline-delimited JSON-RPC 2.0; a daemon reader
  thread routes responses to per-id queues (concurrent calls from delegate
  threads stay matched), stderr drained into a bounded ring (a chatty server
  can never deadlock the pipe), dead-server reason includes exit code + stderr
  tail.
- **HTTP** (Streamable HTTP single-shot): one POST per message, response read
  as `application/json` **or** `text/event-stream` (all `data:` JSON objects
  parsed in order); `Mcp-Session-Id` response header captured and replayed on
  later requests; HTTP ≥400 and JSON-RPC `error` objects surface as honest
  `ERROR:` lines.

## Progress (car 4a-M3)

When the caller passes an `on_progress` callback to `call_tool`/`request`, the
request stamps `_meta.progressToken` (= our JSON-RPC id) and the session
dispatches incoming `notifications/progress` messages whose token matches:
`{progressToken, message (≤500 chars), progress (number|null)}`. The stdio
reader thread routes id-less notifications to this path; the HTTP transport
parses interleaved notifications from the same single-shot SSE body before
the response arrives. Delivery chain: session → tool handler (fixed shape,
`{server, tool, message ≤200, progress}`) → `registry.bind_progress(fn)` —
same binding style as `bind_turn` — → web console `progress` SSE frame
(`_run_chat_turn`), rendered as a transient status line in the chat stream
(2s fade, cleared on `done`).

Honest boundaries:

- no callback bound → no `_meta` stamp at all (request byte-identical to the
  pre-progress shape), unknown/stale tokens drop silently, a raising sink is
  swallowed — progress can never fail a tool call;
- **Telegram does not receive progress** (single-send reply model — there is
  no channel for incremental status); it stays a web-console-only event;
- unrelated notifications (`notifications/message`, …) are ignored.

## Lifecycle (fail-soft)

`build_registry()` connects each configured server (`initialize` handshake →
`notifications/initialized` → `tools/list`, ≤5 pages), applies the filters,
and registers survivors as `mcp_<server>_<tool>` (name-sanitized, ≤64 chars,
collisions skipped). Outcomes land on `registry.mcp_notes` and the service
log (`MCP` event):

- success: `mcp 'demo': connected, 2 tool(s)` (+ filtered count)
- failure: `mcp 'dead': SKIPPED — <honest reason>`
- bad config: `mcp config invalid: <exact reason>`

A broken config or unreachable server **never crashes startup** and never
removes the 11 core tools.

## Trust model (documented, not hidden)

An MCP tool runs with whatever rights its own server has — our allowed-roots
path policy does **not** apply to remote tools. The allow/deny filters bound
which tools the model can *see*; they are not a sandbox. Calls time out after
30s; tool results flow through the registry's 24000-char output cap.

## E2E (real, before release notes)

A stdio MCP server exposing `add_numbers(a, b)` was connected through
`MYCORE_MCP`; a real model turn called `mcp_e2e_add_numbers(17, 25)` and
reported **42**.

Progress evidence is unit-level (`mycore/tests/run.py`): a fake stdio server
emits `notifications/progress` (+ a stale token and a foreign notification)
and the bound registry sink must receive exactly the live-token frames; the
HTTP interleaved-SSE case is asserted with an injected `post` (no sockets).

## Console coverage (frontend car, 0.5.39)

Settings now has the `#mycMcp` textarea (JSON array, ≤4000 chars, hint:
reference secrets as `$SLOT` names — never values), joined into the payload
only when edited; the Overview `#capGrid` renders the configured server
names. Evidence: `tmp/e2e_frontend_coverage.py` `L3` (write →
`capabilities.mycore.mcp_servers.names`) and `tmp/e2e_frontend_ui.js` `U5`
(system card) — see [frontend_coverage.md](frontend_coverage.md).

## CKX backup server (`ckx`, car 8 / CKX-P2)

The console's **AGENT BACKUP** section can register the vendored
`deploy/ckx/ckx-mcp.mjs` MCP server with one button (`POST /api/backup/agent
{action:"mcp_on"}` / `mcp_off`). The write path touches ONLY the `ckx` entry
of `mycore.mcp` — every other server the operator configured survives
byte-identical, unknown hand-written settings keys survive too, and the value
goes through the same `validate_settings_patch` + merge-onto-raw-store write
as the settings face.

```jsonc
{"name": "ckx", "transport": "stdio",
 "command": "<node> <repo>/deploy/ckx/ckx-mcp.mjs",
 "env":    {"AIDOG_CKX_PASSWORD": "$AIDOG_CKX_PASSWORD"},
 "tools":  ["ckx_verify_integrity", "ckx_read_meta", "ckx_capabilities",
            "ckx_estimate", "ckx_encrypt"],
 "deny":   ["ckx_decrypt*", "ckx_generate*", "*recipient*", "ckx_encrypt_to"]}
```

- **Allow-list is structure/ciphertext only** (design §8): verify, metadata,
  capabilities, estimates and *encrypting* can never return a plaintext byte.
  `ckx_decrypt*`, the whole recipient-keypair family and `ckx_encrypt_to` are
  named in `deny` as belt-and-braces — a decryption or a private key must
  never reach the model context. **Recovery stays on the manual Backup &
  Restore card (CKX-P1), where a human types the passphrase.**
- The passphrase reaches the child only as a resolved `$AIDOG_CKX_PASSWORD`
  env var (process env, then the profile `secrets.env` slot) — settings hold
  the slot NAME, never a value.
- `mcp_on` requires the Node >= 20 probe AND a set passphrase slot, and
  refuses with a 409 naming the exact reason otherwise; it also refuses when
  env `MYCORE_MCP` is set (the env override would silently win).
- The scheduled path does NOT go through MCP: `backup_run` is a core tool
  that drives the CLI directly (deterministic, no model-facing plaintext),
  so a scheduled backup keeps working even with MCP switched off.

Evidence: `mycore/tests/run.py::test_ckxbackup` (40 assertions incl. the
parse/deny round trip), `tests/web_console_p3.py` (agent lane),
`tmp/e2e_car8_ui.js` (22/22).

## SNIFF lane: sensing a bridge, read-side only (SNIFF car)

The SENSE board's `mcp` source kind borrows this registry instead of
bringing a protocol client of its own. One source names one bridge:

```jsonc
{ "id": "bridges", "kind": "mcp", "server": "ha-mcp", "enabled": true }
```

A sweep parses the registry and counts this process's live sessions for that
name — configuration and status only. It calls no tool, spawns no process
and opens no connection of its own (the core suite asserts all three).

Readings a rule may reference: `servers` (bridges declared overall),
`transport` (`stdio` | `http`), `sessions` (live sessions right now),
`tools` (tools such a session exposes), `filters` (allow + deny patterns),
`slots` (`$SLOT` credential references in env/headers — names counted,
never resolved). Status stays honest: `never-configured` (no such bridge in
the registry), `detached` (declared, no live session), `reachable` (a
session is open); `sessions == 0 for 30m` is the finding that says a
declared bridge went away.

### The three bridges (health / vehicle / devices)

We never install a third-party bridge for you: you run whatever you trust,
declare it in `mycore.mcp`, and the board watches it. Their **data** reaches
a P0 sweep through the `home` source (whatever Home Assistant aggregates) or
a `web` source (a local REST endpoint); calling a bridge's tools from a
sweep is a later, approval-gated phase. Typical hookups:

| Domain | Bridge you run (third-party, your choice) | Data route into a sweep |
|---|---|---|
| Health | a Google Health / Garmin / Withings / Huawei health bridge MCP | HA health integrations (`home`) or the provider REST API (`web`) |
| Vehicle | a TeslaMate MCP, an OBD-II MCP | TeslaMate or HA (`home`), TeslaMate API (`web`) |
| Devices | ha-mcp, a Zigbee2MQTT bridge, a 3D-printer bridge | Home Assistant (`home`) or the device's local API (`web`) |

Trust model applies unchanged: borrowed tools are not sandboxed, the
bridge's own rights bound it — which is exactly why this lane only reads
configuration and connection status.
