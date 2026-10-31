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

## Per-task model selection (car 4a-M3)

Each `tasks` item is a plain string **or** an object `{task, model}`:

```
delegate(tasks = ["summarize this log",
                  {task: "draft the changelog entry", model: "big-model"}])
```

Rules (no silent fallback, ever):

- `model` must appear in the settings whitelist `web/settings.json`
  `mycore.models` — one source of truth, editable from the console since
  0.5.40 (Settings → Model `#mycModels`, schema-validated ≤32 single-line
  ids; `[]` = whitelist off). Unknown id → that task alone returns
  `[i/N] error: model '…' is not in the mycore.models whitelist (…)`;
  siblings still run.
- No `mycore.models` list stored → an explicit not-configured
  error block (the task does not silently inherit the default model).
- The override swaps the model **name only** on the turn's own connection —
  base URL and credentials stay the bound parent's (`bind_turn` path);
  sub-agents never read credentials.
- Malformed entries (object without a non-empty `task`, non-string `model`)
  are honest `ERROR:` refusals at validation time; the legacy string-only
  form is unchanged.

## Not in scope (honest)

- No cross-task data sharing (tasks are independent by design).
- No sub-agent → user direct delivery (results always route through the
  parent's reply).
- No persistent sub-agent sessions; every `delegate` call starts fresh.

## Evidence

- Whitelist carrier + console editor (0.5.40): `tmp/e2e_activation.py` `L2`
  (accept/reject matrix + the raw-file consumer `subagent._allowed_models`
  reading the same document) and `tmp/e2e_activation_ui.js` `U6`
  (`#mycModelAdd` chips add → server read-back) — see
  [frontend_coverage.md](frontend_coverage.md) §4.
