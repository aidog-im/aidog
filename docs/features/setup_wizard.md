# Feature spec — Setup wizard (task 2 / Phase B3)

Status: **implemented** (2026-10-20, ships in v0.5.21). Design anchor: the
task-2 roadmap slice owner-approved 2026-10-20 and competitive gap §4.2-6
("installation experience": prior-art products ship polished one-shot onboarding,
we were an ops-grade CLI that left you staring at `$EDITOR secrets.env`).

**Honest boundary:** this is a guided *CLI* wizard, not a GUI/one-click
installer package. Public-distribution packaging remains open (§4.2-6 still
partially open). Everything it writes lives in the same machine-local,
gitignored files the installer already used — no new state, no new surface.

## 1. Entry points (`deploy/aidog_setup.py`, dispatched by `aidog setup`)

| Command | Behavior | Exit |
|---|---|---|
| `aidog setup` | interactive wizard: guide every credential slot + model endpoint, then optionally run the installer (doctor + boot + verify) | installer's `0/2/1` if run, else `0` ready / `2` pending / `1` error |
| `aidog setup --check` | read-only status report — creates nothing, writes nothing | `0` ready · `2` pending · `1` broken format / invalid `settings.json` |
| `aidog setup --yes` | non-interactive: the idempotent installer (`deploy/install.sh`) | installer's `0/2/1` |
| `aidog setup --yes --no-boot` | provision missing templates + slot report, never boots | `0` / `2` |
| `aidog setup --no-boot` | wizard without the final boot question | `0` / `2` / `1` |

A **non-TTY `aidog setup` with no flags falls back to `--yes`**, so scripts
and old docs keep the provision + boot + verify behavior and never block on a
prompt. Unknown flags exit `2` (argparse).

## 2. The wizard loop

Slot order (matches the installer's report order):

| Slot | Required | Validation |
|---|---|---|
| `WEB_TOKEN` | yes | `\S{16,}` (any long random string) |
| `MYCORE_TG_TOKEN` | yes | `\d{4,}:[A-Za-z0-9_-]{20,}` (BotFather format) |
| `AIDOG_TG_ALLOWED_USERS` | yes | `\d+(,\d+)*` (comma-separated ids, no spaces) |
| `MODEL_API_KEY` | no | `\S{8,}`; empty input = leave empty (open endpoints need none) |
| `AIDOG_GH_PAT` | no | `\S{20,}`; empty = skip |
| `MYCORE_WD_CHAT` | no | `-?\d{5,20}`; empty = skip (log-only alerting) |
| `mycore.base_url` | yes | `https?://\S+` (shown, not secret) |
| `mycore.model` | yes | non-empty token (shown, not secret) |

Rules the tests lock (regression wall in `tests/setup_wizard_b3.py`):

1. **Never overwrite** — a filled slot is reported `NAME: set (len N) — kept`
   and is not prompted again; its bytes stay identical (rotate by editing
   `secrets.env` yourself).
2. **Zero echo** — secrets are read with hidden input on a TTY; output
   carries slot *names* and *lengths* only. Piped fixture values never appear
   in stdout+stderr (asserted).
3. **Validate, then retry 3×** — an invalid answer prints the reason and the
   expected format (never the entered value), re-prompts up to 3 attempts,
   then leaves the slot empty: pending stays **fail-closed**.
4. **Read back every write** — `secrets.env` writes are own-line
   `KEY=value` replacements (comments and unrelated lines byte-preserved,
   atomic tmp + `os.replace`, mode 0600 best-effort) verified by re-reading;
   `settings.json` is re-parsed after write. The summary re-runs the full
   format check (`own-line KEY=value` per line, violations reported by line
   *number* only).
5. **Graceful EOF** — input ending early (empty stdin, pipe closed) stops
   prompting, still prints the summary, exits by slot state; no traceback.
6. **Optional finish** — the wizard asks `run doctor + boot + verify now?
   [y/N]`; `y` delegates to `deploy/install.sh` with its exit code
   propagated, anything else skips (hint printed). `--no-boot` omits the
   question entirely.

## 3. `--check` semantics (the report cards)

- reports every slot: `set (len N)` / `EMPTY (required)` /
  `set (len N) — INVALID format`, plus `secrets.env format` verdict and the
  configured `base_url`/`model`;
- broken own-line syntax → exit `1` with the violating line numbers (never
  contents); invalid `settings.json` JSON → exit `1`;
- required slot empty → exit `2` with the pending names;
- all required filled + format clean → exit `0`;
- **creates nothing** — a fresh checkout stays a fresh checkout (asserted).

## 4. Tests

`python3 tests/setup_wizard_b3.py` → **80/80** (offline, stdlib, every write
confined to a throwaway fake repo under `/tmp`; the real `secrets.env` /
`web/settings.json` are never touched — `REPO` self-derives from the copied
script's location, and the stub `install.sh` records invocation + propagates
a controlled exit code). Registered as a quality gate in `HANDOVER.md §3`
and `PRODUCT_HANDOFF.md §3.4`.
