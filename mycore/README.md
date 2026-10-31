# mycore — the self-developed kernel (P4+ engineering line)

Model-agnostic agent loop + minimal toolset + simple compaction, written from
scratch in Python stdlib only (zero third-party dependencies). Owner-approved
scope: design doc §14.3 — loop + 8 tools + simple compaction + TG adapter +
watchdog, developed under dual-brain A/B against the transitional kernel;
extended by owner approval (Phase B2, v0.5.20) with the 9th tool `cron` and
the `scheduler.py` module (natural-language cron), then by alignment car 2
(v0.5.23) with `delegate` + `execute_code` (11 core tools total) and the
optional `mcp.py` client (external tools attach dynamically when configured).

**Status: P4-1a scaffold** — loop, model client, tool registry, compaction
skeleton, offline test suite. The transitional kernel keeps running in
production until P5 swap criteria are met (design §14.3); nothing here is wired
into the live stack yet.

## Layout

```
mycore/
├── __init__.py
├── model.py     # OpenAI-compatible chat client (urllib, stdlib) — key from
│                #   env only, never echoed/logged; injectable transport for tests
├── loop.py      # agent loop: messages -> model -> tool_calls -> execute -> repeat
│                #   bounded iterations, honest tool errors, stop on final text
├── tools.py     # tool registry (OpenAI JSON-schema shape) + the 11 core tools
│                #   (9th `cron`; car 2 adds `delegate` + `execute_code`; MCP
│                #   tools attach dynamically when a server is configured)
├── compact.py   # simple compaction: budgeted truncation of old turns
├── assembly.py  # body layer -> system stack (constitution/memory always first,
│                #   missing files -> honest notices, digest for A/B provenance)
├── session.py   # JSONL turn persistence (append-only, crash-tail healing)
├── tg.py        # Telegram adapter: send/getMe + getUpdates loop with a HARD
│                #   guard — polling refuses the live gateway's bot token
│                #   (one token = one poller; dedicated MYCORE_TG_TOKEN required)
├── watchdog.py  # supervision: probe/throttle/heartbeat, dry-run shadow by
│                #   default, --execute for live restarts (owner-authorized)
├── service.py   # P5-1 production TG chat service: getUpdates poll -> fail-closed
│                #   allowlist auth -> per-chat JSONL session + slash commands ->
│                #   reply; single-instance pidfile, redacting service.log,
│                #   --once / --dry-run (offline-tested; not yet wired to boot)
├── scheduler.py # natural-language cron (Phase B2): job registry
│                #   <state>/cron_jobs.json, one spec grammar for /cron + the
│                #   `cron` tool, due/catch-up math, Beijing fixed +08:00
├── subagent.py  # car 2-D6: isolated parallel delegation (<=3 tasks, fresh
│                #   context, read-only default tool set, recursion guard)
├── execute_code.py  # car 2-D7: folded multi-step execution (steps fold +
│                #   one-shot Python child with helpers, allowed-roots bound)
├── mcp.py       # car 2-D5: minimal MCP client (stdio + HTTP JSON-RPC,
│                #   per-tool allow/deny filters, fail-soft attach + notes)
├── cli.py       # one-shot runner: python3 -m mycore.cli "prompt" (A/B harness entry)
└── tests/       # offline: fake transports, no network, no credentials
```

## Contracts

1. **Model boundary**: the loop only sees `chat(messages, tools) -> dict` in
   OpenAI `chat.completions` response shape. Any provider adapter that can
   produce that shape plugs in; tests inject a scripted fake.
2. **Credential rule**: API keys come from environment variables read at call
   time; they never appear in argv, logs, reprs, exceptions, or responses.
3. **Tool contract**: handlers return `str` (result or `ERROR: ...`); exceptions
   are captured and converted to `ERROR:` strings — a failing tool never crashes
   the loop.
4. **Path safety**: file tools resolve paths and refuse targets outside the
   allowed roots (repo + system temp by default); refusal is an honest
   `ERROR: outside allowed roots`, not a silent truncation.
5. **Iteration bound**: `max_iters` (default 25) stops runaway tool chains with
   an honest final message instead of looping forever.
6. **Compaction**: when accumulated message bytes exceed the budget, the oldest
   non-system turns are folded into a one-line summary marker; system/constitution
   messages are never dropped (three-layer rule: kernel swappable, body is us).

## Test discipline

```bash
python3 -m mycore.tests.run     # or: python3 mycore/tests/run.py
```

Everything runs offline against a scripted fake model; no network, no
credentials, deterministic. English code/comments/CLI output per repo i18n
policy.

## Test endpoint (owner decision, 2026-10-18)
Defaults target the **local gateway group E (`127.0.0.1:3460`) with
`mimo-v2.6-flash`** — owner: "test the embodied system with local-3460
mimo-v2.6-flash". The local gateway requires an Authorization header present
but no real secret on loopback (placeholder sent only to loopback hosts; a
custom `api_key_env` still wins when set). Overrides:

```bash
MYCORE_BASE_URL=... MYCORE_MODEL=... python3 -m mycore.cli "prompt"
```

**User-configurable (P4-1b)**: endpoint/model become a CONSOLE settings block
(`mycore.base_url` / `mycore.model`, allow-listed like `usage.*`) once the
product config schema lands — until then env vars are the override path.

## Production chat service (P5-1)

`mycore/service.py` turns the pieces above into the production Telegram chat
service (offline-tested in `mycore/tests/run.py`; **not yet wired** into
boot/watchdog/console — that is P5-2..6):

```bash
python3 -m mycore.service            # long-poll getUpdates loop
python3 -m mycore.service --once     # handle at most one pending update, then exit
python3 -m mycore.service --dry-run  # full loop, outbound sends suppressed (below)
```

**Environment / startup gates** (checked in this order; each refuses to start
before consuming anything):

| Gate | Env | On failure |
|---|---|---|
| fail-closed allowlist | `AIDOG_TG_ALLOWED_USERS` = comma-separated TG user ids; **unset/empty refuses to start** (never answer an unauthenticated chat) | exit 3 |
| single-instance pidfile | `<state>/mycore_service.pid` — a live pid refuses a second copy (one getUpdates consumer per token) | exit 2 |
| dedicated polling bot | `MYCORE_TG_TOKEN`, via the hard guard in `tg.py` (must differ from the gateway's token) | exit 4 |
| state dir | `AIDOG_MC_STATE` (default `<repo>/data/mycore_service`) | — |

Every incoming update passes the allowlist gate first — unauthorized senders
are never answered (only a throttled, chat-id-redacted WARN hits the log), and
unauthenticated `/commands` are ignored the same way.

**State layout** (`AIDOG_MC_STATE`, default `data/mycore_service/`):

```
mycore_service.pid        single-instance lock (live pid refuses, stale pid taken over)
service.log               append log, size-rotate ~1MB keep 1; chat ids as id:<last4>;
                          never token/secret/machine-path values
sessions/<chat_id>.jsonl  per-chat history (mycore.session; system stack rebuilt per turn)
sessions/<chat_id>.model  per-chat /model override — a sidecar file, because
                          session.py only writes meta at file creation
sessions/archive/         sessions rotated by /reset
```

**Commands** (allowlist-gated, parsed only when text starts with `/`):

- `/help` — list commands with one-line descriptions
- `/reset` — archive this chat's session into `sessions/archive/`, start fresh
  (the `/model` override is kept — it is chat-scoped, not history)
- `/model` — show current model id + endpoint host (never the key);
  `/model <id>` sets this chat's override; allowed ids = `web/settings.json`
  `mycore.models` when present, else only the currently configured id
- `/status` — one line: session turn count, current model, service uptime
- any other `/command` — one-line hint to `/help`

**Dry-run semantics (precise)**: `--dry-run` runs the FULL loop — it is a real
`getUpdates` consumer (pidfile + token guard apply), auth/commands/turns all
evaluate and log — but skips every outbound `sendMessage`; the would-be reply
is printed to stdout (path-scrubbed preview) and logged as `DRYRUN_SEND`.
**Offsets advance**: fetched updates are acknowledged and will not be
delivered again, so use dry-run only when consuming pending updates on the
second bot is acceptable.

**Error honesty**: empty model output becomes `(no output — see logs)`; model
failures become one short honest line with no stack traces, absolute paths, or
engine names (details go to `service.log` only).

**Not yet wired (later P5 phases)**: `deploy/boot_services.sh` bring-up,
watchdog retarget, console panels/events/doctor, live E2E on the second bot —
see `docs/p5/swap_gap_assessment.md`. Also deferred: `web/server.py`'s
settings validator (`MYCORE_KEYS`) does not yet accept `models`/`budget`, so
those two keys are read from `web/settings.json` as-is until that phase.

## Roadmap

- **P4-1a (shipped 0.4.3)**: loop + model client + 8 tools + compaction skeleton + tests
- **P4-1b (shipped 0.4.4)**: session JSONL, body-layer assembly, in-loop compaction,
  user-configurable model endpoint via CONSOLE settings (owner 2026-10-18)
- **P4-1c (shipped 0.4.5)**: TG adapter (send/getMe live-verified; poll guard
  refuses the live bot) + watchdog (shadow dry-run cycle live-verified,
  `--execute` for real restarts — owner authorized process switches 2026-10-18).
  Config: `deploy/watchdog.mycore.example.json` -> machine copy `*.local`.
  Running the poll loop against production awaits the second bot token.
- P4-2: dual-brain A/B runs (tests/ab_protocol.md harness, mycore as candidate)
- **P5-1 (built, offline-tested)**: production chat service `service.py` —
  bring-up/supervision/observability/cutover = P5-2..6
