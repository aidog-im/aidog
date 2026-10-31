# Terminal Backends

Status: shipped in car 4b (`mycore/terminal.py`; tool wiring in
`mycore/tools.py::_run_bash`, settings validator in `web/server.py`).

`run_bash` keeps its exact contract — **one settings line decides where the
command executes**. The grammar lives in exactly one place
(`terminal.parse`): the web settings validator and the tool layer both
delegate to it, so they can never drift.

## Backend matrix (7, honestly stated)

| # | Backend | Spec | Status |
|---|---|---|---|
| 1 | Local shell | `""` / `local` | ✅ implemented — default; byte-identical historical `run_bash` (`/bin/sh`, cwd = repo root) |
| 2 | Docker | `docker:image=<name>` / `docker:container=<name>` | ✅ implemented — `docker run --rm <name> sh -c <cmd>` or `docker exec <name> sh -c <cmd>` |
| 3 | SSH | `ssh:<user>@<host>[:port][;identity=<abs>]` | ✅ implemented — OpenSSH `BatchMode=yes`, non-interactive |
| 4 | Modal | — | ❌ **not applicable** — managed cloud execution plane, deliberately not integrated (free-for-life rule: we ship no paid execution surface) |
| 5 | Daytona | — | ❌ **not applicable** — managed cloud sandboxes, same rule |
| 6 | Singularity | — | ❌ **not applicable** — hosted HPC/container sandbox service, same rule |
| 7 | Vercel Sandbox | — | ❌ **not applicable** — managed cloud sandbox, same rule |

Rows 4–7 are **structural non-adoption**: no adapter, no slot, no half-wired
code path — stated here so an empty row is never read as "coming soon"
(owner decision, car 4b). Nothing in this product introduces a paid
touchpoint, so paid execution planes are out by decree, not by oversight.

## `mycore.shell` grammar (settings `web/settings.json` → `mycore.shell`)

| Spec | Meaning |
|---|---|
| `""` (empty, missing, or unreadable file/JSON) | local backend — the safe default |
| `local` | local backend, explicit |
| `docker:image=<name>` | fresh `--rm` container per call |
| `docker:container=<name>` | exec into an already-running container |
| `ssh:<user>@<host>` | SSH on port 22 |
| `ssh:<user>@<host>:<port>` | SSH on a custom port (1–65535) |
| `ssh:<user>@<host>[:port];identity=<abs path>` | SSH with a key file (absolute path, ≤ 256 chars) |

Shape rules (all enforced by `terminal.parse`, max **256 chars**, one line):
docker name = `[A-Za-z0-9._:/-]`, 1–120 chars, exactly one key (`image=` or
`container=`); ssh user = `[A-Za-z0-9_.-]`, 1–32 chars and **required**;
host 1–253 chars; at most one `;identity=` suffix — **any other suffix (notably
`password=`) is refused at parse time**, and the error says passwords are
never accepted. Parse errors are English, describe the *expected shape* only,
and never echo the submitted input (injection-echo hygiene).

## Hot switch

`tools._shell_spec()` re-reads `web/settings.json` on **every** `run_bash`
call — no in-process cache. Edit the file (or `POST /api/settings` with the
`mycore` partition) and the next call already runs on the new backend. A
missing file / bad JSON / missing key reads as `""` = local: fail-safe, never
a crash.

## Output contract (byte-stable)

Every backend returns the historical `run_bash` contract verbatim:

```
exit=<code>
<stdout>
--- stderr ---      (block present only when stderr is non-empty)
<stderr>
```

with trailing whitespace rstripped.

- **Non-zero exit, refused connection, missing container = a RESULT** (the
  `exit=N` line), never an exception — same as it always was.
- Only backend-level problems raise `TerminalError`, which the tool layer
  maps to `ToolError`: missing binary (`docker not found in PATH (install
  docker or set mycore.shell to local)`), timeout (`command timed out after
  Ns`), bad arguments.
- No per-backend truncation here — the single tool cap
  (`MAX_RESULT_CHARS` in `tools.finalize_result`) applies uniformly to all
  backends, exactly as before.

## SSH safety

- `BatchMode=yes` — **never prompts interactively**: a missing key fails
  fast and honestly, and an interactive auth prompt can never stall a call
  (a refused connection stays what it always was — a result: `exit=255`).
- `StrictHostKeyChecking=accept-new` — pin new hosts, still fail loudly on a
  changed key.
- Identity = **key file path only**; there is no password field in the
  grammar at all (see above), and no passphrase is ever accepted or stored.
- The `sh -c` payload is wrapped with `shlex.quote` so ssh's post-destination
  argv join cannot re-split it — one script survives as one `-c` argument.

## Docker notes

- No cwd migration: containers keep their own workdir.
- No environment/proxy passthrough (`docker run`/`exec` both stay minimal) —
  no host variable crosses into the container.

## Settings write path

`POST /api/settings`, authenticated, `mycore` partition with `shell` in
`MYCORE_KEYS`: ≤ 256 chars on one line, then **`terminal.parse` decides**.
Any grammar violation → **HTTP 400** carrying `mycore.shell: <expected-shape
reason>` — the server never re-implements the grammar.

## Testing evidence

- `mycore/tests/run.py::test_terminal` — **38 checks**: the 7 legal specs,
  11 illegal specs refused with an anti-echo canary (input never appears in
  the error), the byte-stable local contract (echo, stderr block, rstrip),
  bad args/timeout → `TerminalError`, PATH-emptied missing-binary failures
  for docker and ssh, monkeypatched `_shell_spec` hot-switch routing (the
  real `web/settings.json` is never touched), and the tool description
  naming `mycore.shell`.
- `tests/web_console_p3.py` — **7 shell-partition checks** (docker spec →
  200 with `capabilities.terminal` reflecting it, ssh spec → 200, `bogus` →
  400 naming `shell`, `password=` → 400 never accepted, empty → 200 back to
  `backend:"local"` / `spec_set:false`) plus the `terminal` catalog block
  (kind only — the raw spec, user, host and identity path never serialize).
- Real wire (a real Docker ubuntu run, ssh argv shape + real refused
  connection, validator leg) lives in the repo-local E2E script
  `tmp/e2e_car4b.py` (tmp harness, outside the gate suites).

Suite totals after car F: core **981/981**, smoke **146/146**.
