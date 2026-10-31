# `execute_code` — folded multi-step execution (car 2-D7, v0.5.23)

One tool, two shapes — the model must provide **exactly one** of `steps` or
`code` (anything else is an honest `ERROR:`). Both collapse N model round
trips into one tool dispatch: fewer tokens, fewer latency hops.

## Shape 1: `steps` — fold a tool-call sequence

```
execute_code(steps = [
  {"name": "read_file", "args": {"path": "a.py", "limit": 50}},
  {"name": "run_bash",  "args": {"command": "pytest -q"}},
  {"name": "grep",      "args": {"pattern": "TODO"}}])
```

- ≤8 steps per call, dispatched **in order against the same registry**
  (same allowed-roots, same checkpoint hooks, same honest `ERROR:` contract).
- Each result is folded into a labelled block: `[i/N name] ok|ERROR`.
- Per-step preview cap 2500 chars (folded output always fits the
  24000-char tool budget).
- **No nested folding**: a step naming `execute_code` or `delegate` is
  refused up front (the fold is always a leaf).
- A failing step does not abort the fold — later steps still run (static
  args, so one failure cannot corrupt the rest).

## Shape 2: `code` — one-shot Python with helpers

```
execute_code(code = "print(read('data.txt')[:200])", timeout = 30)
```

- Runs in a **child process** (`python -m mycore.execute_code --child …`),
  so a crash/hang cannot take down the loop; timeout 1–120s (default 120),
  source cap 30000 chars, output reported exactly like `run_bash`
  (`exit=N` + stdout + `--- stderr ---` on failure, or an honest
  `ERROR: code timed out after Ns`).
- Helper API: `read/grep/glob/write/edit/run/fetch` — each wired to the
  same registry implementations, so **allowed-roots are enforced** and
  `write`/`edit` take **pre-write checkpoints** (state_dir threaded through).
- **Trust model (no sandbox claim)**: raw Python inside the script can do
  anything `run_bash` already allows (same user, same process). The helpers
  enforce the path policy; plain `os.*` does not — exactly like a shell
  redirect already escapes `write_file`. Documented, not hidden.

## Why both shapes

`steps` is the token-saver for known tool sequences (the folded multi-step
win); `code` covers computed/looped work that a fixed step
list cannot express, with the helper API keeping roots + checkpoints intact.

## Tool-output compression tier (car 4a-M3, registry-wide)

Every tool result passes `tools.finalize_result` before it reaches the model
(affects all tools, not only `execute_code`):

1. **above 6000 chars** → a compression pass first: trailing whitespace per
   line, runs of 3+ blank lines collapsed to one, and consecutive identical
   lines folded to `first line` + `... [×N identical lines]` (markers
   themselves never re-fold, so the pass is idempotent);
2. the result now carries one honest single-line note when anything
   changed — `... [compressed {before}→{after} chars]` (same style as the
   truncation marker, never a silent shrink);
3. **still >24000 chars** → the existing hard cap applies unchanged
   (`... [truncated at 24000 chars]`, appended after the compression note);
   below the cap nothing else changes.

Whitespace/echo folding only — no content line is invented or dropped
beyond exact duplicates; empty and single-line bodies pass through
untouched; results at or under the threshold are byte-identical to before.
