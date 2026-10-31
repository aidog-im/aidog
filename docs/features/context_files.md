# Feature spec — Project context files (auto-discovery)

Car 1-D1 (v0.5.22). Lets a deployment point the model at one or more project
directories whose instruction files (`AGENTS.md`, `CLAUDE.md`,
`AGENTS.local.md`) are injected into the system stack for every turn.

## Why it is opt-in (design red line)

The kernel assembles a fixed body layer (constitution → curated memory →
learned slot → skill hint, `mycore/assembly.py`). A repository root usually
carries internal operator rules, not model instructions — auto-injecting the
repo's own root `AGENTS.md` would burn tokens every turn and feed private
discipline to the model. Therefore:

- **Off by default**: no configured dir = today's stack, byte-identical
  (existing callers and their stack-shape tests are unchanged).
- **Explicit directories only**: the operator opts a directory in; nothing is
  discovered by walking the filesystem.
- **Present-only**: a configured dir whose files are missing/empty adds
  nothing and never a "missing" notice (a project without `CLAUDE.md` is the
  normal state — same philosophy as the optional learned slot).

## Configuration (precedence: env > CONSOLE settings > off)

| Source | Form |
|---|---|
| env `AIDOG_CONTEXT_DIRS` | dirs separated by the OS pathsep (`:` / `;`) |
| settings `mycore.context_dirs` | same, single line, ≤256 chars, empty = off |

Resolution lives in `assembly.resolve_context_dirs()` (never raises — an
unreadable/invalid settings file is a silent off; a configured dir that does
not exist is dropped silently and takes effect on the next turn). Turn paths
(`mycore/cli.py`, `mycore/service.py` chat + cron-prompt legs, `web/server.py`
console chat) pass the resolved dirs explicitly; `build_system_stack()` keeps
`context_dirs=()` as its default so the pure body layer stays testable.

## Injection behavior

- Order: configured dirs in order; within a dir, `CONTEXT_FILE_NAMES` order —
  `AGENTS.md`, `CLAUDE.md`, `AGENTS.local.md` (each present file injected).
- Each file becomes one system message prefixed with a provenance header
  `[project context] <dir>/<name>:` so the model can tell operator-configured
  context from the body layer.
- Each file reuses the per-slot cap (`MAX_SLOT_CHARS` = 8000) with the same
  honest `[truncated at N chars]` marker; unreadable/empty files are skipped.
- The skill-hint entry stays LAST in the stack (assembly invariant asserted
  by `mycore/tests/run.py::test_assembly`).

## Honest limits

- Coverage is per configured directory only — no traversal, no git-root
  guessing, no per-chat project switching in v1.
- `run_bash` work happens outside this path: files a shell command writes are
  not re-discovered mid-turn (the stack is rebuilt each turn).
- The console settings validator checks form only (single line, size, no
  control chars); existence is checked at turn time, not at write time.

## Tests / gates

`python3 -m mycore.tests.run` — `test_context_files()` covers injection +
order + header + present-only + truncation + `AGENTS.local.md`, the default
off shape, and `resolve_context_dirs` precedence (env > settings > corrupt
settings = off, dedupe, missing dir dropped).

## Related

- Stack assembly: `mycore/assembly.py` (module doc lists slots 1/2/2b/2c/3)
- Settings validation: `web/server.py` (`MYCORE_KEYS`), `web/settings.example.json`
- Skill frontmatter compatibility (same car, 1-D3): `docs/features/memory_and_routing.md`
