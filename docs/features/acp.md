# Agent Client Protocol (ACP) v1 Server (stdio)

Status: shipped in car 4a (`mycore/acp.py`, M2b). Run it as
`python3 -m mycore.acp` — an ACP client spawns the process and speaks
newline-delimited JSON-RPC 2.0 over stdin/stdout.

The server exposes the same agent loop the console chat uses (system stack,
tool registry, compaction budget, failover chain) behind the ACP request
surface. Protocol facts (method names, field names, error codes, capability
enums) were implemented against the official v1 JSON schema before coding.

## Capability surface (what v1 declares — nothing more)

| Area | v1 behavior |
|---|---|
| `initialize` | `protocolVersion` must be `1`; anything else → JSON-RPC error `-32602` naming the implemented version (no downgrade dance). Result carries `agentCapabilities`, `authMethods: []`, `agentInfo {name, version}` (version = repo `VERSION`). |
| Session lifecycle | `session/new` (requires an absolute `cwd`; optional `additionalDirectories` must also be absolute) → `sessionId`. `loadSession` is declared **false**: sessions live only for this process, no session/load. |
| `session/prompt` | Content blocks: `text` and `resource_link` baseline (declared `promptCapabilities`: image/audio/embeddedContext all false). Runs on a worker thread; streams `session/update` notifications (`agent_message_chunk`, `tool_call`, `tool_call_update`) **before** the prompt result. Result `stopReason`: `end_turn` / `cancelled` / `max_turn_requests`; model failures answer with a vetted JSON-RPC error (no endpoint/key detail). |
| `session/cancel` | Client notification; mid-prompt cancel ends the turn with `stopReason: "cancelled"`. Unknown sessionId is ignored (logged, never crashes). |
| fs proxy | **Direction: agent → client.** When the client advertises both `fs.readTextFile` and `fs.writeTextFile` in `initialize`, the session's file trio (`read_file`/`write_file`/`edit_file`) is re-routed through `fs/read_text_file` / `fs/write_text_file` requests to the client. The agent still enforces its own allowed-roots policy on top (defense in depth). |
| terminal | **Not declared, not implemented** — v1 sends no `terminal/*`. Honest gap: no shell session support over ACP. |
| MCP / prompts | `mcpCapabilities` declared false; client-supplied `mcpServers` in `session/new` are accepted and ignored (our MCP comes from our own settings). |

Concurrency model: the read loop owns stdin while prompts run on a worker
thread, so client responses to outbound fs requests and `session/cancel`
notifications are processed **while** a turn is in flight (pending-id future
map; request vs. response disambiguated by the presence of `method`, so the
id spaces never collide).

## Configuration

Same precedence as the CLI/service — no ACP-specific knobs:

- env `MYCORE_*` (model id, base URL, MCP config) >
- `web/settings.json` `mycore` block (model, base_url, upstreams, budget,
  context_dirs, mcp) >
- built-in defaults.

`python3 -m mycore.acp --help` prints usage on stderr and exits 0.

## Boundaries (v1, stated as-is)

- **No session persistence**: `loadSession=false`; restart the process and
  the conversation is gone (by design, not a bug).
- **No terminal, no image/audio prompts, no embedded context blocks** —
  anything not in `promptCapabilities` is skipped honestly rather than
  silently faked.
- **Client-owned filesystem on writes**: with fs caps advertised, the agent
  does not touch its local copy of a write target — the client applies the
  bytes. The pre-write checkpoint safety net snapshots the local view only
  when the path is visible locally (common co-located deployment); a remote
  client path has nothing to snapshot (documented here, no illusion of a
  rollback the agent cannot perform).
- **fs waits are bounded** (`fs_timeout`, default 10s): an unresponsive or
  refusing client becomes an honest failed `tool_call_update` fed back to
  the model — the turn keeps going, never deadlocks.
- **stdout carries only JSON-RPC frames**; logs go to stderr. EOF (or
  interrupt) fails pending waiters, cancels sessions, joins prompt workers
  with a bounded timeout, and exits 0.

## Testing evidence

`mycore/tests/run.py::test_acp` — **27 offline checks** (fake chat
injection, zero network): initialize handshake/version gate (+ non-integer
version, non-object params), `session/new` validation, prompt result/error
shapes, `session/update` frame order (tool_call → tool_call_update → chunk →
result), cancel (mid-prompt + unknown session), fs read/write proxy
request-response against a scripted client (including client refusal and
no-answer timeout honesty), EOF mid-prompt bounded exit, and a real
subprocess run asserting stdout is JSON-RPC-only with parse errors answered.
Suite total after car 4a M2b: **718/718**.
