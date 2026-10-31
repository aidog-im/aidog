# Security Review Checklist (manual)

**Policy:** this checklist is reviewed by hand at each release train. It
documents existing repository disciplines only — no tooling or dependency
is installed by this document, and every claim below names the file or
command that verifies it. Status values:

- **Enforced** — implemented in code and covered by an executable gate.
- **In force (discipline)** — a documented rule plus code/tooling support,
  verified by inspection rather than a dedicated test.
- **Pending** — specified and scheduled for the current release train;
  re-verify after the merge before relying on it.

Nothing here asserts superiority over any third-party product; each entry
states only what this repository does.

---

## 1. Zero credential echo

- **Status:** In force (discipline + tooling).
- **What:** credential values live only in `secrets.env`; reports, logs,
  documents, screenshots and chat output reference slot **names and counts**
  only. Slot reads are own-line and never logged
  (`read_env_slot` in `web/server.py`). Chat service log lines pass chat
  ids through `redact_chat` (`id:<last4>`); the live event stream redacts
  token-shaped and `Bearer …` text (`sanitize_event_text` in
  `web/server.py`). The publish gate scans tracked files for
  filled-credential patterns.
- **Verify:**
  - `grep -n "never logged\|redacted" web/server.py`
  - `grep -n "redact_chat" mycore/service.py`
  - `python3 deploy/check_publish.py --json` (pattern `filled-credential`
    must be absent from the leak list)

## 2. Loopback by default

- **Status:** Enforced.
- **What:** the console server binds `127.0.0.1` only (`HOST` in
  `web/server.py`; startup prints "loopback only"). Default model-gateway
  and usage endpoints are loopback URLs, and settings validation rejects a
  non-loopback `usage.metrics_url`.
- **Verify:**
  - `grep -n "127.0.0.1" web/server.py`
  - `grep -n "loopback" web/server.py`

## 3. Allow-lists fail closed

- **Status:** Enforced.
- **What:**
  - Chat service: `AIDOG_TG_ALLOWED_USERS` unset/empty → startup refuses
    with exit 3; an all-invalid list yields the empty set → refuse to run
    (`mycore/service.py`).
  - Channel routes: `allow` empty or absent = deny every sender
    (deny logged, throttled) — `mycore/service.py`, schema comment in
    `web/server.py`.
  - Settings writes: keys outside the allow-list schema are rejected,
    all-or-nothing (`ALLOWED_TOP` / `validate_settings_patch` in
    `web/server.py`).
  - Scheduler: an unparseable/never-due clock is "never due" rather than
    "fire now" (`mycore/scheduler.py`); evolution clock parse failure also
    fails closed (`mycore/evolution.py`).
- **Verify:** `grep -rn "fail-closed" mycore/*.py web/server.py`

## 4. Bearer gates on write routes

- **Status:** Enforced (console + webhook); `/v1` gate pending (item 10).
- **What:** every state-changing console route requires
  `Authorization: Bearer <WEB_TOKEN>`: missing slot → **503 naming the
  slot**, wrong/absent bearer → **401**, comparison via
  `hmac.compare_digest`, and the value never appears in any response, log
  line or exception (`_require_token` in `web/server.py`). The inbound
  webhook route uses its **own** slot with the identical 503/401 contract
  (`_require_webhook_secret`).
- **Verify:**
  - `grep -n "_require_token\|_require_webhook_secret\|compare_digest" web/server.py`
  - `python3 tests/web_console_p3.py` (auth matrix checks)

## 5. Fail-silent vs. fail-loud boundary

- **Status:** Enforced (design boundary, code-backed per line below).
- **What — security-relevant paths fail LOUD:** auth failure → 401,
  unconfigured credential slot → 503 naming the slot, invalid input → 400
  with details, gate/test failure → non-zero exit, doctor finding → FAIL
  line, publish issue → BLOCKED verdict.
  **Convenience/observability paths fail SILENT by design:** a failed
  checkpoint snapshot never blocks the file write (safety net,
  `mycore/tools.py`), absent optional config keys are dropped
  present-only (`mycore/assembly.py`), a missing locale key falls back to
  the default pack and never blocks startup (`mycore/service.py`).
  Pending for this train: event hooks are specified fail-silent
  (hook failure must never block a turn; it lands in the event log only).
- **Verify:**
  - `grep -n "never blocks" mycore/tools.py`
  - `grep -n "silently\|present-only" mycore/assembly.py`
  - `grep -n "never blocks startup" mycore/service.py`

## 6. Pre-write checkpoints

- **Status:** Enforced.
- **What:** `write_file` / `edit_file` snapshot the previous bytes before
  writing (`mycore/checkpoint.py`: index entry = id, timestamp, file,
  size, sha256; raw-byte snapshots; rotation keeps the newest entries;
  restore re-snapshots the current file first so a restore is itself
  undoable). Coverage is honest: only these two tools snapshot — a shell
  command is not covered, and the module docstring says so.
- **Verify:**
  - `grep -n "snapshot" mycore/checkpoint.py mycore/tools.py`
  - `python3 -m mycore.tests.run` (checkpoint section, `test_checkpoint`)

## 7. Publish manifest: deny by default

- **Status:** Enforced.
- **What:** every git-tracked file must match an allow rule or a deny rule
  (deny wins); unclassified files block the verdict; required deny entries
  are tamper-guarded; a leak scan runs over every tracked file for the
  machine-path and personal-TG-identifier patterns (deny-classified included),
  and over allow-classified files for the filled-credential and brand/vendor
  patterns (the brand-token pattern whose constant lives in the tool itself;
  docs/research/* is the citation vault where quoted third-party names live).
  READY from this tool is **necessary, never sufficient** for public
  placement — that additionally requires the desensitization pass, a
  reviewed manifest and owner adjudication (`AGENTS.md`).
- **Verify:**
  - `python3 deploy/check_publish.py --json`
  - `python3 -c "import json;print(len(json.load(open('deploy/publish_manifest.json'))['deny']))"`

## 8. `secrets.env` privacy

- **Status:** Enforced (best-effort mode on Windows-mounted filesystems —
  documented limitation).
- **What:** `secrets.env` is created from `secrets.env.example` with
  `chmod 0600` best-effort (`deploy/aidog_setup.py`), is gitignored
  (`.gitignore` lists `secrets.env`, re-includes the example), and doctor
  checks the mode as `secrets.env.mode`, flagging `TOO OPEN`. On WSL/9p
  mounts where POSIX mode bits are not enforced, the file's protection is
  the Windows ACL instead — doctor reports this honestly rather than
  passing silently.
- **Verify:**
  - `python3 deploy/aidog_doctor.py` (look for `secrets.env.mode`)
  - `git check-ignore secrets.env && echo ignored`

## 9. One credential slot per surface

- **Status:** Enforced (newest slot pending — item 10).
- **What:** each surface reads its own slot; compromise or rotation of one
  does not silently grant another:

  | Slot | Surface |
  |---|---|
  | `WEB_TOKEN` | console write API (Bearer) |
  | `MYCORE_TG_TOKEN` | chat-service bot |
  | `AIDOG_TG_ALLOWED_USERS` | chat access list (fail-closed, empty = refuse) |
  | `MODEL_API_KEY` | model endpoint credential |
  | `AIDOG_GH_PAT` | release push — child environment only, never argv |
  | `CHANNEL_*` (7 slots, incl. `CHANNEL_WEBHOOK_SECRET`) | channel send/inbound legs |
  | `OPENAI_API_TOKEN` | OpenAI-compatible `/v1` gate — pending this train |

- **Verify:**
  - `grep -n "^[A-Z]" secrets.env.example` (slot names only — the example
    carries no values)
  - `grep -n "read_env_slot" web/server.py` (each gate names its slot)

## 10. Event hooks: argv, no shell (this train)

- **Status:** **Pending** until the current release train merges
  (`mycore/hooks.py` not yet present).
- **Spec (re-verify after merge):** hook commands are configured as argv
  **arrays** and executed **without a shell** (no string interpolation, no
  shell metacharacter expansion); events come from a fixed whitelist;
  per-hook timeout; a hook failure never blocks the conversation and is
  recorded in the event log only; hook output is never echoed into chat.
- **Verify (after merge):**
  - `grep -n "shell" mycore/hooks.py` — expect no `shell=True`
  - `grep -rn "hooks" web/settings.json`
  - `python3 tests/web_console_p3.py`

## 11. Brand-token scan (de-branding gate)

- **Status:** Enforced as a release gate.
- **What:** the brand-detection token is an own constant inside
  `deploy/check_publish.py` (which self-excludes from its own scan).
  Outside the documented exclusions (the tool itself, the internal
  citation vault under `docs/research/`, `*.log`, `.git/`) the token must
  have zero hits in tracked files.
- **Verify (development repository only):** `deploy/check_publish.py` embeds
  the token and is deliberately NOT part of the exported snapshot, so both
  commands below run in the development checkout. In this exported snapshot
  the guarantee is structural instead: the token-bearing file is denied an
  export, and the export was leak-scanned before it was published.
  - `rg -i "$(sed -n 's/^BRAND_TOKEN = "\(.*\)"$/\1/p' deploy/check_publish.py)"` —
    expect 0 hits outside the exclusions
  - `python3 deploy/check_publish.py --json` (leak list empty → READY)

---

## Review procedure

1. Run every **Verify** command in this document; fix or explicitly defer
   any red item (deferrals are recorded in the release notes, not here).
2. Items marked **Pending** must be flipped to Enforced in the same train
   that merges them — a stale "pending" is a stale checklist.
3. This file itself is subject to items 1, 7 and 11: it contains no
   credential values, no brand tokens, and no machine-specific paths.
