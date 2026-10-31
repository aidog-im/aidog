# Feature spec — File checkpoints & rollback

Car 1-D2 (v0.5.22). The safety net for local agent writes: the two write
tools snapshot the **previous bytes** of a file before mutating it, and
`/checkpoint restore <id>` (or the ops CLI) puts them back.

## What is covered — honestly

| Path | Snapshot? |
|---|---|
| `write_file` (overwrite of an existing file) | ✅ previous bytes |
| `edit_file` (replace) | ✅ previous bytes |
| `write_file` creating a NEW file | ➖ nothing to roll back |
| `run_bash` (shell redirects, `sed -i`, …) | ❌ outside the registry's reach |

The write result names the checkpoint it made
(`wrote N chars to <path> · checkpoint #12`); an existing file that could
**not** be snapshotted (over the 1 MiB cap or unreadable) says so instead of
pretending (`· previous file not checkpointed (too large or unreadable)`).

## Store

- Location: `<state>/checkpoints/` (state = env `AIDOG_MC_STATE`, else
  `data/mycore_service` — same rule as the cron registry, gitignored).
- One append-only `index.jsonl` mapping `id → path, snapshot file, size,
  sha256, ts`; snapshot files hold **raw bytes** so restore is byte-exact.
- Rotation: newest `MAX_ENTRIES` (100) kept; older index lines and their
  snapshot files are deleted (same philosophy as the evolution/session
  archive rotation).
- Corrupt index lines are skipped (fail-soft); the sha256+size are verified
  on restore (a tampered snapshot is refused, never written back).

## Failure discipline (why two different rules)

- **Snapshot = fail-soft, always**: it runs *before* the write and can never
  block it (the write itself already has its own honest error path). A failed
  snapshot is reported in the tool result, never silent.
- **Restore = fail-loud**: it is the recovery path, so unknown id /
  missing-or-corrupt snapshot / io failure raise a chat-safe
  `CheckpointError` and are answered honestly — never a fake success.
- **Restore is itself undoable**: the current file content is snapshotted
  first, so `/checkpoint restore` can be rolled back again.

## Surfaces

| Surface | Entry |
|---|---|
| TG (and any command-capable chat) | `/checkpoint` (list newest 10) · `/checkpoint restore <id>` (bare `/checkpoint <id>` accepted) · in `/help` |
| Ops / headless | `python3 -m mycore.checkpoint --list [--json]` · `--restore <id>` (exit 0/1/2) |
| Web console chat | no command parsing in v1 — use the CLI (stated, not hidden) |

Replies scrub machine paths through the service's repo-relative rule (only a
file name outside the repo shows, never an absolute path).

## Limits

- Text/byte snapshots of single files only — no directory trees, no git
  integration, no cross-file transaction (an edit that touches several files
  gets several independent checkpoints).
- Cap: 1 MiB per file, 100 entries kept — beyond that the oldest go away
  (rotation, by design).
- `run_bash` mutations are not covered (table above).

## Tests / gates

`python3 -m mycore.tests.run` — `test_checkpoint()`: snapshot roundtrip with
sha256/size, present-only create, cap skip, newest-first list, byte-exact
restore + undo-of-the-undo, unknown-id and tampered-snapshot refusals,
corrupt-index fail-soft, rotation with file unlink, both write tools
reporting their checkpoint id, and the `/checkpoint` service leg (list,
restore, honest miss, usage, `/help` entry in both locale packs).

## Related

- Module: `mycore/checkpoint.py` (stdlib, scheduler-style CLI)
- Hooks: `mycore/tools.py::_snapshot_note` (write_file/edit_file)
- Command: `mycore/service.py::_checkpoint_command`, locale keys
  `cmd.checkpoint.*` (en/zh)
