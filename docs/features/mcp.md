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
- **Secrets are env slot NAMES only** (`$SLOT` resolved from the process
  environment at connect time; a missing/empty slot fails that server with an
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
  as `application/json` **or** `text/event-stream` (last `data:` JSON object);
  `Mcp-Session-Id` response header captured and replayed on later requests;
  HTTP ≥400 and JSON-RPC `error` objects surface as honest `ERROR:` lines.

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
