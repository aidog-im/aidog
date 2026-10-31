# Sub-agent delegation (`delegate`) — car 2-D6 (v0.5.23)

One `delegate` call hands 1–3 independent tasks to sub-agents that run **in
parallel threads** with an isolated context and an isolated tool set, so a
fan-out task costs the parent one tool result instead of N nested turns.

## Surfaces

- Model tool `delegate` (11th core tool, `mycore/tools.py`).
- Implementation: `mycore/subagent.py`; wiring: `loop.run_turn` binds the
  active turn's `chat` + budget into the registry (`registry.bind_turn`),
  the handler reads them (`registry.turn_binding`) — a sub-agent never holds
  credentials of its own.

## Guarantees

1. **Isolated context** — each sub-agent gets a fresh two-message transcript
   (one fixed system line + the task). The parent conversation never enters;
   the sub-agent's transcript never returns. The parent context pays only for
   the folded per-task block.
2. **Isolated tools** — default read-only set: `read_file`, `grep`, `glob`,
   `http_fetch`, `skill_route`. Write-capable tools are granted **only when
   named explicitly** in the `tools` argument (validated against the parent
   registry; an unknown name is an honest error, never a silent drop).
3. **Recursion guard** — `delegate` and `execute_code` can never be granted to
   a sub-agent (no nested delegation, no nested folding). Required also
   because `execute_code` closes over the registry it was built in, so
   re-using it under a subset would leak the parent tool set.
4. **Bounded fan-out** — at most 3 tasks per call (a 4th is an honest
   refusal), ≤12 model iterations per sub-agent, 6000 chars per task result.
5. **Failure isolation** — one sub-agent's model error becomes an honest
   `[i/N · model_error]` block; siblings still run and the fold still returns.
6. **Thread-safety** — checkpoint id allocation is serialized
   (`checkpoint._SNAP_LOCK`) and the cron tool is serialized
   (`tools._CRON_LOCK`), so parallel sub-agents writing/creating through
   granted tools cannot corrupt shared state.

## Example

```
delegate(
  tasks = ["Use read_file on mycore/loop.py and report its first line",
           "Use grep to count lines containing TurnResult in mycore/loop.py"],
  tools = []                 # read-only default; omit `tools` entirely
)
-> [1/2 · final · 1 tool call(s)]
   ...
   [2/2 · final · 1 tool call(s)]
   ...
```

## Not in scope (honest)

- No cross-task data sharing (tasks are independent by design).
- No sub-agent → user direct delivery (results always route through the
  parent's reply).
- No persistent sub-agent sessions; every `delegate` call starts fresh.
