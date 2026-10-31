# Feature spec — night-watch missions (mission / autonomous ops)

Status: **implemented** (2026-10-26, shipped with the v0.5.44 release train 1
minimal closed loop; the same day the v0.5.45 train 2 "orchestration and
board" increments landed, see §7; the same day the v0.5.46 train 3
"night-watch hardening" increments landed, see §8).
Design anchors: `docs/design/autonomous_ops_design.md` §3 (operation flow)
and §5 train 1 / train 2 acceptance gates. This page is the 32nd capability
document, covering the mission lifecycle, the three reporting tiers, the
budget guardrails, the API/settings surface, and the red-line relations.

**Scope**: train 1 = minimal closed loop (set the goal → serial subtasks
executed slice by slice → three-tier reporting → done/sealed); train 2 =
orchestration and board (milestone decomposition, delegate parallelism,
`degraded` **inbound edge**, configurable and frozen budgets, `GET ?id=`
details, console board + read-only orchestration view, §7);
**train 3 = night-watch hardening** (`paused_approve` inbound edge wired to
the web3 approval gate, watchdog heartbeat alerts, `mission_tick` hook, quiet
hours and minimum interval, the three side-A constraints, §8).
No channel and never stalled: the last level of the four-level report
delivery fallback is "log only".

Companion pages: [capabilities.md](capabilities.md) (capability catalog
contract), [natural_language_cron.md](natural_language_cron.md) (the
same-origin timer leg and delivery fallback),
[evolution_loop.md](evolution_loop.md) (the sibling service leg),
[console_chat.md](console_chat.md) (the run_turn reused by slice execution),
[event_hooks.md](event_hooks.md) (including the train 3 `mission_tick`
event), [frontend_coverage.md](frontend_coverage.md) (the console settings
surface master matrix).

## 1. Lifecycle (five-state state machine)

Registry: `<state>/missions.json` (atomic write via tmp + `os.replace`,
history capped at 50, **at most one active at a time**); event stream:
`<state>/mission_events.jsonl` (append-only, kind ∈ `state` / `slice` /
`report` / `sealed`).

```
planned → running ⇄ paused_user          (console pause/resume)
             ├→ done                      (all subtasks done, or the model declares done)
             ├→ degraded ⇄ running        (GAP inbound edge; next subtask start / UNBLOCK
             │                             returns to running — train 2 on)
             └→ sealed                    (evidence-backed seal: stop, budget ceiling hit)
running → paused_approve ⇄ running        (web3 subtask order awaiting approval: that subtask
                                            is awaiting_approval, the rest keep running;
                                            clearing approval auto-returns to running, train 3 on, §8)
```

- `done` and `sealed` are the **only two terminal states**; `sealed` must
  carry evidence (at least one entry) and is never sealed empty.
- Anything outside the legal edges returns 409 (e.g. `paused_user → done` is
  illegal: it can only finish via the executor after a resume, or be stopped
  and sealed directly).
- `planned` is where create lands; a console start immediately advances to
  `running`; the executor tick carries one more defensive promote.
- **pause is allowed from `running | degraded`** (relaxed in train 2),
  resume only from `paused_user`; the degraded inbound edge is detailed in
  §7; **under `paused_approve`, pause/resume always return 409** (detail
  reports the allowed states, train 3, E2E L6).

## 2. Slice execution (the night-watch loop)

A new service leg `_maybe_tick_missions` (`mycore/service.py`, sibling to
the cron leg, ~5 s throttle):

1. **Persist the mark before executing**: slice counters and the tick
   timestamp are persisted first, so a crash loses at most one advancement
   and never re-executes (the same philosophy as the cron leg).
2. **One slice = one `run_turn`** (`slice_iters` defaults to 25): the slice
   prompt carries only the goal + current subtask + registered gaps +
   capability manifest + method-exhaustion checklist — **never the full
   history** (context budget rule).
3. **Plan slice → milestones + serial queue**: the model decomposes on
   `MILESTONE:`/`SUBTASK:` lines (train 2 structured plan: no MILESTONE =
   single-milestone bucket); with no markers it falls back to a single task
   = the goal itself; plan slices ignore `MISSION DONE`.
4. **Resumable checkpoints**: progress lives in the registry + checkpoint,
   never in memory; after a service restart, catch-up is decided by
   `last_tick` (>600 s logs one `MISSION_CATCHUP` line).
5. Slice output is folded into the state machine per the marker protocol,
   and the event stream records `slice` (n/mode/stop/preview, with the
   preview passing the exit scrub).

**Model marker protocol** (ASCII, line-leading, tolerates `- ` bullets,
fail-soft — a missing marker simply leaves the subtask in flight, with the
budget guardrails as the backstop):

| Marker | Effect |
|---|---|
| `MILESTONE: <title>` | Plan-slice grouping header: following `SUBTASK:` lines belong to that milestone; stray subtasks before the first milestone go into the default **Plan** bucket (train 2) |
| `SUBTASK: <text>` | The plan slice breaks out one serial subtask |
| `TASK DONE` / `MISSION DONE` | Current subtask done / the whole goal achieved |
| `GAP: <text>` | Declares a degraded path (triggers a degraded report) |
| `METHOD: <text>` / `TRIED: <text>` | Adds an entry to / marks an item on the method-exhaustion checklist |
| `BLOCKER: <text>` | A blocker (reported on the all/milestones tiers, beyond the no-throttle protection) |
| `NEED DECISION: <text>` | PAID/AUTHORIZED/DECISION items only: that subtask suspends, **all other subtasks continue immediately** |
| `UNBLOCK: <tN>` | Un-suspends a suspended subtask |

### Awareness layer (constitution + prompt layer, two layers)

- **Constitution**: `overlay/constitution/SOUL.md` gains a new
  `## Mission conduct (autonomous ops)` section with six clauses C0-C5
  (exhaust methods / never abandon midway / never ask midway / honest
  reporting including degradation / the honest capability hand-off hard rule
  §9 / a narrow escalation surface without stopping), placed between the
  Hard constitution and the Stance; the 9 hard rules are untouched, word for
  word.
- The **slice prompt** explicitly states "in this task you cannot obtain any
  answer, approval, or feedback — there is no one to ask; do not ask, do not
  wait" (the E2E asserts this against the **real request body**, item by
  item).
- **Layering is unchanged**: the constitution always sits at the very front
  of the system stack; mission goal text only enters the user layer (the E2E
  asserts the goal never appears in a system message).

## 3. Three-tier reporting (settings `mission.reporting`)

| Tier | Trigger (train 1 mapping) | Throttle (min_interval) |
|---|---|---|
| `all` | every subtask completion (`task_done`) + plan ready + degradation/blocker/anomaly | 120 s, **merged** into a single send within the window |
| `milestones` (**default**) | plan ready `plan_ready` (from train 2 on, the plan is a multi-milestone structure; milestone completion does not send on its own, subtask completion only on the `all` tier) + degradation/blocker/anomaly | 900 s |
| `final` | terminal states only (`done`/`sealed`) and needs-a-decision | unlimited |

- `decision` and `final` events **bypass the throttle** (never swallowed);
  off-tier events are suppressed outright and never enter the merge queue.
- **Message format** (reusing the counsel protocol): `[MISSION <id>] LEVEL
  fact → impact → next step`; the final report is fixed at four sections:
  **Done / Degraded gaps / Not finished / Suggestions** (for a sealed
  mission the "Not finished" section lists the evidence).
- **Four-level delivery fallback** (exactly the same chain as cron, with
  `_origin_deliver` as the single implementation): `origin(route:target or
  chat id)` → env/slot `MYCORE_WD_CHAT` → **log only** (`delivered=none`).
  Mission reports send no hooks (the observation event `mission_tick` was
  added in train 3, §8); a console `stop` final report **is not sent to the
  bot** (the response carries it back inline).
- **Zero credential echo**: all outbound reports pass through the single
  exit `mycore.mission.scrub()` — token/bearer/`sk-` shapes and absolute
  machine paths are replaced with placeholders (E2E sentinel scans find zero
  hits).

## 4. Budget guardrails (any one ceiling hit → automatic seal + report, never silent)

| Guardrail | Default | Range |
|---|---|---|
| `max_slices` | 40 | 1..10000 |
| `max_wall_hours` | 24 | 0.1..8760 (≤1 decimal place) |
| `max_daily_model_calls` | 200 (rolling on the Beijing calendar day) | 1..100000 |

- **Six settings keys (train 2 configurability + train 3's two keys)**: the
  settings `mission` block =
  `{reporting, max_slices, max_wall_hours, max_daily_model_calls,
  quiet_hours, min_interval}`;
  `mission.DEFAULT_SETTINGS` is resident, `/api/settings` reads and writes it
  normally, and range validation shares the source
  `mission.validate_mission_settings` / `mission.BUDGET_BOUNDS`.
- **start may carry a `budget{...}` override**: by default it takes the
  settings value at that moment; after the body override it passes the same
  source range validation; **the parsed values are frozen into the mission
  record** — changing settings after launch does not affect an
  already-started mission (E2E L5 asserts both frozen values 30 and 2
  individually).

On hitting a ceiling: the seal is persisted first (one evidence line
`budget guard <guard> reached (<used>/<limit>)`) → the event stream `sealed`
records the guard → the no-throttle final report goes out. After sealed,
**slices never run again**.

## 5. API and settings surface

| Endpoint | Auth | Contract |
|---|---|---|
| `GET /api/mission` | loopback, no auth | `{reporting, active: null\|row, recent[≤10], count}`; corrupted/missing → empty structure, never 500; **no-id behavior keeps the old 4-key list shape** (frontend compatibility, E2E asserted) |
| `GET /api/mission?id=<mid>` | loopback, no auth | `{mission: {row, plan, events[≤20 ascending], budget, budget_used}}`; the row carries train 3 fields `deadline/redlines/deliverable\|null` + `approvals:[{task, plans}]`, awaiting subtask rows carry `plans` (approval deep links); unknown id → 404; pre-train-2 legacy missions → `plan: null` (the board honestly shows "no plan yet", train 2) |
| `POST /api/mission` | `Bearer WEB_TOKEN` | `{action: start\|stop\|pause\|resume, goal?, id?, reporting?, budget?, deadline?, redlines?, deliverable?}` → 200 row; 400 validation · 401 · 404 unknown id · 409 conflict (start while one is already active / history at 50 / illegal state-machine transition), 409 carries `mission_id` or the reason; pause accepts `running \| degraded` (train 2); **under `paused_approve`, pause/resume always return 409 and detail reports the allowed states** (train 3); the three constraints are validated by a single grammar in `mission.create`, an illegal 400 detail starts with the field name |
| `POST /api/settings {"mission": {...}}` | `Bearer WEB_TOKEN` | `mission.reporting ∈ all\|milestones\|final` (default `milestones`) + train 3's two keys: `quiet_hours` (`""` or `HH:MM-HH:MM`, start ≠ end, may cross midnight) + `min_interval` (0..86400, 0 = tier default), unknown key / illegal value → 400 naming the key; validation shares the source with writes, `mission.validate_mission_settings` |
| `GET /api/capabilities` → `mission` | no auth | `{active, running, total, reporting}` summary — goal text never appears on the unauthenticated catalog |

- start semantics: `goal` is ≤4000 characters and non-empty; the
  `reporting` parameter **only overrides that mission's tier**, never the
  global setting; the F1 capability manifest is frozen at start (tool names
  + credential key names + **existence booleans**, web3
  `order_gate`/`withdrawals:false`/`paid_actions:false`, `mcp: disabled`).
- Row fields go through the fixed `public_row` allow-list:
  id/goal/state/reporting/timestamps/progress/slices/daily_calls/gaps/
  blocked/budget/evidence — **no origin, no manifest, no internal paths, no
  credentials**; `goal` is user input and is echoed verbatim.
- The mission board card (Lane F settings surface) reads and writes the same
  pair of endpoints above; detail and board share the `public_row`
  allow-list discipline (the E2E sentinel-scans the detail payload).

## 6. Red-line relations (condensed from design doc §6)

- **Zero withdrawal / on-chain transfer code**: mission adds no
  funds-outbound tool — the existing source assertions remain in force.
- **The web3 approval gate is fail-closed**: mission still only advances to
  prepare/plan; order-type subtasks suspend into `paused_approve` (wired in
  0.5.46 train 3; the E2E red-line assertion still **rejects execute without
  approval**), and the approval entry point and Bearer gate shape are
  unchanged.
- **Community MCP is permanently disabled**: `mcp: disabled` is hard-coded
  at manifest generation time; exhausting methods does not mean finding back
  doors (C3: a missing capability = a registered degradation).
- **Disk write scope**: slices reuse `execute_code` allowed-roots and the
  pre-write checkpoint snapshot; destructive actions are declarations, not
  executions.
- **Permanently free iron rule**: the capability surface has no paid tool
  (manifest `paid_actions:false`).
- **Never silent**: hitting any of the three guardrails, sealing, and
  needing a decision must all produce a report (down to log level at
  minimum).

## 7. Train 2 increments (0.5.45): orchestration and board

**(1) Milestone decomposition (structured plan)**: plan slices accept
`MILESTONE:` grouping lines; the registry stores
`plan = {milestones: [{id, title, subtasks: [tN…]}], generated_at}`, while
the flat `tasks[]` remains the single source of truth for state (the detail
projection maps `in_progress` to the board word `running`); stray subtasks
before any milestone go into the **Plan** bucket, and the old protocol
without MILESTONE degrades to a single milestone (train 1 compatible).
`METHOD:`/`TRIED:` in the same slice fold into the method-exhaustion
checklist and are read back in detail as `methods[{text, marked}]` +
`blockers[]`.

**(2) Delegate parallelism**: mission slices keep the full toolset
(including `delegate` ≤3, isolated context, recursion guardrails —
`mycore/subagent.py` untouched); `run_turn` binds this slice's chat into the
registry, so one slice can issue ≥2 delegate calls to advance multiple
subtasks; slice events gain a `delegate` counter field
(`mission.delegate_count(tool_log)`), which the board uses to display
parallel dispatch without parsing previews.

**(3) The degraded inbound edge (train 1 only kept the outbound edge)**:
`running → degraded` occurs when a new `GAP:` appears while the current
subtask is unfinished; **a single-point degradation does not stall (F4)** —
the queue and the execution leg proceed as usual; the next subtask start
(`set_current`) or an `UNBLOCK:` immediately returns `degraded → running`,
and the degraded subtask stays on record without being reworked; pause from
degraded is legal (→ `paused_user`), and after resume the gaps are preserved
as-is; slices execute normally in the `degraded` state.

**(4) Budget freezing**: the four settings keys + a start `budget{...}`
override (§4); parsed values are frozen into the mission record, after which
changing settings no longer affects already-started missions.

**(5) Board + orchestration view (console, Lane I)**: the recent/active rows
of the Missions card open a detail area (milestone groupings, five-state
subtask symbols, method-exhaustion details, event tail window, budget usage
and warnings, state badges including degraded); missionCard gains three
budget inputs (at start only the filled-in fields are submitted, left blank
= take settings); a new read-only `#orchCard` orchestration card
(five-source summary of tools/channels/web3/slots/skills + deep links to
existing controls, **no second set of switches**, data source =
`/api/capabilities` and other existing endpoints); locales gain
`mission.*`/`orch.*` 32 keys → packs **594/594** (train 3 adds 20 more →
**614/614**, see §8 (6)).

**(6) Train 2 acceptance gate evidence**: `tmp/e2e_mission_orch.py`
four-state E2E (§9) — decomposition → parallelism → degradation → sealing,
the full chain runs for real.

## 8. Train 3 increments (0.5.46): night-watch hardening

**(1) web3 `paused_approve` wiring (backend Lane K)**: when a successful
`exchange_prepare_order` inside a slice previews `pl_<12hex>` and the plan
is still `pending`, that subtask suspends as `awaiting_approval` (plan ids
recorded) and the `running → paused_approve` inbound edge opens (a second
order in the same window is only recorded, with no repeated transition;
inbound edges from degraded/paused_user/planned are always illegal);
`paused_approve` keeps executing slices as usual, with `set_current`
skipping suspended subtasks — **all other subtasks keep advancing tick by
tick** (F4). The service leg runs `observe_approvals` first on every tick
(approved/executed → the subtask returns to pending and is re-queued;
rejected/expired/cancelled → subtask degraded + gap + report, no stall;
pending/unknown/read error → keep waiting, a read failure never fabricates a
verdict; everything settled → `paused_approve → running`, event
`via=approval`). **The approval gate's shape is unchanged**
(prepare→approve→execute, Bearer, and the day's ledger all untouched);
`execute` without approval is still rejected; under `paused_approve`,
pause/resume always return 409 (detail reports the allowed states). Detail
gains `deadline/redlines/deliverable|null` +
`approvals:[{task, plans}]`, with awaiting subtask rows carrying `plans`
(deep-link data).

**(2) Watchdog heartbeat alerts**: `MISSION_STALE_SEC=900`; when an
active-state mission (running/degraded/paused_approve) has a
`last_progress_at` (legacy rows fall back to updated_at/created_at) older
than the threshold →
`⚠️ [MISSION HEARTBEAT] mission {id} has made no progress for {N} minutes ({state}) · {goal}`
goes through the existing notify path (the goal passes scrub, zero
credentials; dry_run only gates restarts, it does not suppress alerts);
in-memory throttling re-reports on `throttle_sec` (never-silent: a restart
re-reports immediately); resumed progress or entering a terminal state
silences it; a registry read failure is fail-soft.

**(3) `mission_tick` hook**: whitelist +1 event; exactly once per tick per
active mission (nothing sent when no mission is active; paused_user also
counts as active — the observation leg does not observe the model); the
child-process env carries only `AIDOG_HOOK_MISSION` (sanitized id≤32) /
`_MISSION_STATE` (enum) / `_MISSION_PROGRESS` (`d/t`) plus PATH/HOME — zero
echo, non-blocking, never times out, and illegal values are dropped
immediately (E2E L6 asserts the env captured from a real script key by key).

**(4) Quiet hours and minimum interval (two settings keys)**:
`mission.quiet_hours` (`""` = off, or `HH:MM-HH:MM`, start ≠ end, may cross
midnight) — inside the window only decision/final are let through (they
bypass and are sent **individually**, with the merge queue preserved);
everything else enters the queue and is **merged** and sent at the first
non-quiet point after the window; `mission.min_interval` (0..86400, 0 =
tier default 120/900/∞) and the window are both read live on every tick
(save = effective); an illegal value → 400 detail naming the key, and a
broken window is never silent (fail-soft falls back to off).

**(5) The three side-A constraints**: start optionally takes `deadline`
(ISO8601 and must be in the future; a naive time is read as +08:00) /
`redlines` (≤500 plain text) / `deliverable` (≤200 plain text); empty or
whitespace = not passed; validation lives in a single grammar in
`mission.create` (all start paths share the source), an illegal 400 detail
starts with the field name; the three constraints are injected into **both
the plan slice and the execution slice** prompts (the `Owner constraints`
section, F2 displays without waiting), detail/row echo them, default null.

**(6) Console (Lane K2)**: the Missions card gains a collapsible constraints
area (datetime-local → ISO8601 parameter, redlines textarea ≤500 with a
live counter, deliverable ≤200; only filled-in fields are sent, so the main
form is never crowded out); the detail area shows the three constraints
(deadline as relative time + red expiry text, redlines/deliverable text
blocks) and an **APPROVALS section** (a red `paused_approve` pending badge +
a list of each `{task, plans[]}`; clicking a plan id deep-links to the
existing Web3 panel and highlights the corresponding card, plus the copy
"approval resumes automatically after you finish in the Web3 panel; nothing
to do here" — zero console action after approval, the next tick auto-returns
to running); next to the tier, quiet_hours / min_interval controls appear
(change writes immediately via the same `/api/settings` mechanism, a 400
detail is displayed verbatim); the five state badges are completed
(paused_approve red vs degraded amber) + the awaiting_approval subtask
symbol; locales gain 20 `mission.*` keys → packs **614/614**; the existing
`mission_card_verify` / `mission_board_verify` probes both show 0 FAILED
(`<button>` inside `#missionCard` always 4, the three-tier select, and the
five-symbol set all unbroken).

**(7) Train 3 acceptance gate evidence**: `tmp/e2e_mission_pause.py` —
**E2E 73/73 PASS · 7 legs (L1–L7)**: L1 approval suspend/resume (prepare →
paused_approve + approvals deep-link data + decision goes live + other
subtasks keep advancing; console approve → next tick back to running →
advances to done + final report; red lines: execute without approval
rejected, the model's toolset has no approve path, the local mock received 0
order POSTs) · L2 heartbeat (900s+ stale → one alert via the notify path →
no repeat inside the throttle window → re-report after the window →
silenced as soon as progress resumes; dry_run on the whole time, zero
machine paths in the alert) · L3 quiet hours (inside the window
plan_ready/task_done queue with zero going live, decision let through
individually with the queue preserved, merged and sent at the first
non-quiet point after the window; both tiers tested live: min_interval 3600
and the tier default 120 — a 60s interval queues / 4000s, a 200s interval
sends) · L4 the three constraints (4 kinds of illegal 400s naming the field,
defaults null, legal values echoing all three fields, plan-slice request
body containing the three `Owner constraints` values) · L5 checkpoint resume
stress test (new process `MISSION_CATCHUP`, progress and plan structure not
lost, slice numbers continuous without reset, event stream strictly
append-only, final report still sent after restart) · L6 pause window
(pause/resume 409 reporting allowed states + mission_tick env asserted key
by key, exactly once per tick, zero echo) · L7 production
`secrets.env`/`web/settings.json` byte-identical + a full-run
sentinel/machine-path sweep + goal text always in the user layer. Isolation
posture same as train 1/2 (throwaway profile + stub model + stub TG + local
OKX book mock, no order endpoint). Also `tmp/lane_k_probe.py` **19/19**
(quiet-hours merging / three-constraint injection / heartbeat, three
probes). Run: `python3 tmp/e2e_mission_pause.py` (rc 0 = 0 fail).

## 9. Evidence (gates & tests)

- `python3 -m mycore.tests.run` → `test_mission`: **285 runtime checks**
  (legal/illegal state-machine edges including the degraded inbound edge and
  train 3 suspend/resume, registry atomicity and cap, marker-protocol
  folding including MILESTONE grouping, reporter three tiers + throttle +
  merge + bypass + quiet window + min_interval, the three budget guardrail
  seals, scrub, manifest, settings validation including the six keys,
  `public_row` allow-list including the three constraints and approvals,
  detail projection and the event tail truncated to the last 20 lines);
  full suite **1477/1477**.
- **`tmp/e2e_mission.py` (this train's acceptance gate, Lane G) — `E2E
  54/54 PASS` · 8 legs (L1–L8)**: L1 goal setting (three-tier settings
  read-back + illegal tier 400 + start/409/capabilities summary) · L2 real
  slices (event stream state+slice + board progress) · L3 one example each
  of the three reporting tiers + a 120 s window queued→merged send · L4 the
  done four-section report + stop sealing with evidence (and stop is silent
  on the wire) · L5 `max_slices=2` ceiling hit → sealed + evidence + final
  report · L6 per-request assertion that the system stack contains Mission
  conduct, the prompt contains "cannot obtain any answer", the goal only in
  the user layer · L7 exit sentinel scan (reports/board/event stream/model
  request body: zero credentials, zero machine paths) · L8 production
  `secrets.env` + `web/settings.json` byte-identical. Isolation posture: a
  separate throwaway profile + a separate console subprocess on its own
  port + a local stub model endpoint (real HTTP slices) + a stub TG
  transport capturing reports. Run: `python3 tmp/e2e_mission.py` (rc 0 = 0
  fail).
- **`tmp/e2e_mission_orch.py` (train 2 acceptance gate, Lane J) — `E2E
  63/63 PASS` · 6 legs (L1–L6)**: L1 decomposition (four settings keys
  written and read + start freezing the settings budget 30 + `GET ?id=`
  asserting three milestone groupings / Plan bucket / METHOD·TRIED marking /
  plan_ready already sent) · L2 parallelism (2 `delegate` dispatches in one
  exec slice really running 3 subagents against the stub, slice toolset
  includes delegate, fan-out covering ≥2 queued subtasks with the current
  subtask finishing in the same slice, slice event `delegate=2`) · L3
  degradation (GAP → state/subtask both degraded with the queue intact → the
  next slice passes through degraded, advances the next subtask, returns to
  running (F4) → a second GAP re-enters → pause from degraded legal / zero
  execution while paused / gaps preserved after resume, also pushing the
  event stream past 20 lines) · L4 sealing (`budget{max_slices:2}` ceiling
  hit → sealed + evidence carrying the counts + seal report sent, never
  silent; POST stop → `stopped via the console` and silent on the wire) ·
  L5 detail (shape row+plan/events/budget/budget_used, event tail = last 20
  lines of the stream ascending, changing settings to 7 leaves the started
  budget 30 untouched / override 2, unknown id 404, no-id 4-key legacy
  shape, detail sentinel scan) · L6 production `secrets.env` +
  `web/settings.json` byte-identical + a whole-run sentinel/machine-path
  sweep + goal text always in the user layer. Isolation posture exactly the
  same as train 1. Run: `python3 tmp/e2e_mission_orch.py` (rc 0 = 0 fail).
- **`tmp/e2e_mission_pause.py` (train 3 acceptance gate, Lane K2) — `E2E
  73/73 PASS` · 7 legs (L1–L7)**: see §8 (7) (the approval suspend/resume
  main line + heartbeat alerts + quiet hours/minimum interval both tested
  live + the three constraints + checkpoint resume stress test + 409/hook
  env + production byte-identity and sentinel sweep). Run:
  `python3 tmp/e2e_mission_pause.py` (rc 0 = 0 fail); assisted by
  `tmp/lane_k_probe.py` **19/19**.
*Development-repository only: `deploy/check_publish.py` is release tooling of
the development checkout, never part of a user install.*

- `python3 deploy/check_publish.py` → READY(0) (this page is in the allow
  domain: no machine paths/credentials/brand words);
  `python3 tests/source_lang_check.py` → zero CJK in the source code (this
  page sits outside the source-language gate, which scans `.py`/`.sh`/`.js`;
  repository docs are maintained in English per the i18n policy).

## 10. File index

| File | Responsibility |
|---|---|
| `mycore/mission.py` | state machine, registry, serial queue, marker protocol, budget guardrails, reporter (throttle/merge/bypass), `scrub`, `public_row`, settings validation |
| `mycore/service.py` | `_maybe_tick_missions` / `_tick_one_mission` advancement leg + `_mission_deliver` (reusing `_origin_deliver`'s four-level fallback) |
| `web/server.py` | `GET/POST /api/mission`, the settings `mission` section, `capabilities.mission` |
| `overlay/constitution/SOUL.md` | `## Mission conduct (autonomous ops)` C0-C5 |
| `mycore/tests/run.py` | `test_mission` unit tests |
| `tmp/e2e_mission.py` | train 1 acceptance gate (this page §9) |
| `tmp/e2e_mission_orch.py` | train 2 four-state acceptance gate (this page §9) |
| `tmp/e2e_mission_pause.py` | train 3 approval suspend/resume + night-watch hardening acceptance gate (this page §9) |
| `tmp/lane_k_probe.py` | train 3 three probes (quiet hours / three constraints / heartbeat) |
| `mycore/watchdog.py` | train 3 heartbeat alerts (`MISSION_STALE_SEC` / throttled re-report) |
| `mycore/hooks.py` | event whitelist + `mission_tick` env (zero echo) |
| `docs/design/autonomous_ops_design.md` | design doc (trains 1–5 phasing, full red-line table) |
