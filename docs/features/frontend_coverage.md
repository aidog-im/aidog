# Frontend coverage matrix (console face, config carrier, evidence)

Status: **implemented** — frontend coverage car, release **0.5.39**, extended
by the activation & operations car, release **0.5.40** (§4).
This is the 31st capability page and the index that ties the three Phase-0
audits (`tmp/audit_backend.md`, `tmp/audit_frontend.md`,
`tmp/audit_features.md`) to what the console can actually do **after this
car**: every `POST /api/settings` key now has a control, every runtime
credential slot has an editor, and each claim below is backed by a named
automated leg.

Companion pages: [capabilities.md](capabilities.md) for the catalog
contract, [event_hooks.md](event_hooks.md), [channels.md](channels.md) and
[channels_domestic.md](channels_domestic.md) for the route contracts,
[exchanges.md](exchanges.md) for the order gate, [mcp.md](mcp.md),
[tool_toggles.md](tool_toggles.md), [terminal_backends.md](terminal_backends.md),
[context_files.md](context_files.md), [model_failover.md](model_failover.md),
[evolution_loop.md](evolution_loop.md), [natural_language_cron.md](natural_language_cron.md).

## How to read this table

| Column | Meaning |
|---|---|
| Config carrier | the concrete key / slot / env var the feature reads |
| Console state (after this car) | `configurable` = the console can change it · `read-only` = the console renders state but cannot change it (and that is by design) · `designed elsewhere` = the carrier is CLI/env by design — see the second table, **not** a console gap · `not applicable` = the feature has no config carrier to expose |
| E2E evidence | legs of `tmp/e2e_frontend_coverage.py` (backend, isolated profile — `L1..L8`) and `tmp/e2e_frontend_ui.js` (Playwright, isolated console — `U1..U7`), plus the activation car's `tmp/e2e_activation.py` (`L1..L10`) and `tmp/e2e_activation_ui.js` (`U0..U9`) — same letters, different file, so every cell names the file it means — plus the standing gate suites |

Values are compared inside the test process and never printed; no credential
value appears in any payload, DOM, log or failure detail.

## 1. Main matrix

| Feature | Config carrier (concrete keys / slots) | Console state (after this car) | E2E evidence | Notes |
|---|---|---|---|---|
| browser_automation | env `AIDOG_BROWSER_BIN` (binary resolution is designed-env — see §2); artifacts `data/browser/` | **read-only** — Overview `#capGrid` renders `browser.available` | `U5` (system card rows) · gates: `run.py::test_browser`, `tmp/e2e_car4b.py` | No settings key and no slot exist for the binary, by design |
| capabilities | none — read-only loopback catalog | **rendered** — Overview `#capGrid` (tools / skills / channels / hooks / cron / browser / terminal / local models / MCP / venues / web3 caps / env overrides), `env_overrides` + `mycore.known_tools` from 0.5.39; **0.5.40 adds `#doctorRunBtn` → `POST /api/doctor/run`** (summary + verbatim output) | `L1` `L3` `L5` (shape + zero-echo + env names) · `U5` (grid rows) · `tmp/e2e_activation.py` `L6` + `tmp/e2e_activation_ui.js` `U4` (doctor) · `tests/web_console_p3.py` | The catalog stays an unauthenticated endpoint; the console is now its display face *and* its self-check button |
| channels | settings `channels.{discord,slack,email,webhook,dingtalk,feishu,wechat,whatsapp,signal}` · slots `CHANNEL_DISCORD_TOKEN` `CHANNEL_SLACK_TOKEN` `CHANNEL_SMTP_USER/PASS` `CHANNEL_IMAP_USER/PASS` `CHANNEL_WEBHOOK_SECRET` `CHANNEL_WHATSAPP_TOKEN` `CHANNEL_SIGNAL_TOKEN` | **configurable** — Settings `#chanList` 9-route form + `#chanTest*` send test; slots in the credential editor | `L3` (row flips enabled/configured) · `L4` (409 disabled / 409 unconfigured) · `U1` (form → server read-back) · `U6` (zh) | Previously no `/api/channels` fetch and no slot face existed in the console |
| channels_domestic | settings `channels.{dingtalk,feishu,wechat}` · slots `CHANNEL_DINGTALK_WEBHOOK/SECRET` `CHANNEL_FEISHU_APP_ID/SECRET` `CHANNEL_WECHAT_APPID/SECRET` | **configurable** — same form + send test; slots editable | `L3` `L4` `U1` `U6` | Send reasons name slots/settings keys, never values |
| connectors | slots `LINEAR_API_KEY` `NOTION_API_KEY` · test seams env `AIDOG_LINEAR_URL` `AIDOG_NOTION_URL` | **configurable (credentials)** — both rows in the credential editor (`connectors` group); presence also in `capabilities.media.connectors` | `L1` (roundtrip + group listing) · `U2` (set/clear + DOM zero-echo) | Tool stays read-only search by design |
| console_chat | `WEB_TOKEN` (Settings → Access) · settings `mycore.{model,base_url,models,budget,…}` | **configurable** — 0.5.40 adds the `#mycModels` whitelist + `#mycBudget` editors, and a console save now preserves unknown stored keys | `tmp/e2e_activation.py` `L2` (schema matrix + raw consumer + unknown-key survival) · `tmp/e2e_activation_ui.js` `U6` (save → read-back) · `tests/web_console_p3.py`, `tmp/e2e_shell.js` | The chat model key resolves env → secrets slot now (`mycore/model.py::_headers`), so a console-written `MODEL_API_KEY` reaches the client — `tmp/e2e_activation.py` `L8` |
| context_files | env `AIDOG_CONTEXT_DIRS` > settings `mycore.context_dirs` | **configurable** — Settings `#mycContextDirs` (pathsep list) | `L3` (write → `GET /api/settings` + failover config) · `U1` (save payload) | Validator checks form only; existence is checked at turn time |
| event_hooks | settings top-level `hooks` (6 events, `cmd` argv + `timeout`) | **configurable** — Settings `#hookList` editor (one row per event, argv-per-line, 1..30 s timeout, count chip) | `L3` (`hooks` write → `capabilities.hooks.events`) · `U1` (row → server read-back) · `U6` (zh) | Child process still gets only `PATH`/`HOME` — no credential ever crosses |
| evolution_loop | settings `evolution.{enabled,daily_at,weekly_day,monthly_day}` · CLI / TG triggers | **configurable** — EVOLUTION schedule editor (`#evoEnabled` `#evoDailyAt` `#evoWeeklyDay` `#evoMonthlyDay`) cross-checked against the active schedule | `L3` (write → `/api/evolution`) · `U3` (save → status line + read-back) | The partition used to be API-only: "the console settings page is the switch" is now literally true |
| exchanges | slots `OKX_API_KEY/SECRET/PASSPHRASE` `KRAKEN_API_KEY/SECRET` `BINANCE_API_KEY/SECRET` · settings `web3.{auto_usd_per_tx,auto_usd_daily}` | **configurable** — EXCHANGE approval panel (unchanged) + the new cap editor `#exCapPerTx` `#exCapDaily`; venue slots in the credential editor | `L1` (slot rows) `L3` (caps → `capabilities.web3`) · `U4` (save → chips + catalog) | Withdrawals / on-chain transfers remain absent by construction |
| execute_code | none — model tool bounded by the registry | **not applicable** — no settings key / slot exists to expose | not covered by this car · `run.py::test_execute_code` | Deliberately not a console feature |
| image_generation | env `AIDOG_IMAGE_URL` > settings `media.image_*` · slot `IMAGE_API_KEY` (→ `MODEL_API_KEY`) | **configurable** — Settings → Media endpoint/model + `IMAGE_API_KEY` row in the credential editor | `L1` (slot listed + roundtrip) · `U2` (editor) | Panel 503 still shows the real reason verbatim |
| mcp | env `MYCORE_MCP` > settings `mycore.mcp` (JSON array ≤4000, `$SLOT` references) | **configurable** — Settings `#mycMcp` textarea | `L3` (write → `capabilities.mycore.mcp_servers.names`) | Server config never carries a credential value — slot names only |
| memory_and_routing | env `AIDOG_MEMORY_HOME/SRC` `AIDOG_SKILL_DIRS` · `deploy/memory_sources.local` · CLI `./aidog export\|import\|list` | **read-only** — memory gauges + skill gallery unchanged; `#capGrid` now adds the skill count | `U5` (skill-count row) · gates: `tests/skill_route_p4.py`, `tests/memory_persist_p4.py` | Budgets/console export UI is still an open checkbox in that page — this car did not claim it |
| model_failover | env `MYCORE_UPSTREAMS` > settings `mycore.upstreams` · `key_env` slot names | **configurable** — Settings `#mycUpstreams` (grammar-validated) | `L3` (write → `upstreams_configured`) · `U1` (save payload) · `tmp/e2e_activation.py` `L8` (key: env wins → slot fallback → placeholder) | Per-key cooldown state is still not rendered (documented, honest); a `key_env` slot value finally reaches the client (0.5.40 fix) |
| natural_language_cron | state `<state>/cron_jobs.json` · CLI `python3 -m mycore.scheduler` · TG `/cron` · model tool `cron` | **read-only list + delete** — Overview `#cronList` from `GET /api/cron`; 0.5.40 adds a per-row `#cronDelBtn` (confirm → `POST /api/cron {action:"del"}`) | `L6` (seed → count/fields → CLI delete) · `tmp/e2e_activation.py` `L7` (console del → count 0 → 404 → no `add`) · `U5` (list renders) · `tmp/e2e_activation_ui.js` `U5` (row disappears) | Job **creation** stays natural-language/CLI/TG by design — still no create button |
| openai_api_server | slot `OPENAI_API_TOKEN` (own line, independent of `WEB_TOKEN`) | **configurable (credential)** — `gateway` group row in the credential editor; 503/401 semantics unchanged | `L1` (slot listed + roundtrip) · `U2` (editor) | Gateway behavior itself is not a settings surface |
| profiles | env `AIDOG_PROFILE` `AIDOG_PROFILES_DIR` `AIDOG_MC_STATE` · CLI `aidog profile …` | **read-only** — profile name appended to the status line (unchanged) | not re-exercised by this car · `tests/profile_f.py`, `tmp/e2e_car_f.py` | Runtime path seam resolves at launch — a console switch would fork state |
| subagents | settings `mycore.models` (same whitelist as `/model`) · `delegate` tool | **configurable** — Settings `#mycModels` comma list (0.5.40, schema-validated ≤32 ids); the per-turn model choice stays in the picker/CLI | `tmp/e2e_activation.py` `L2` · `tmp/e2e_activation_ui.js` `U6` | Previously listed as "designed elsewhere" — the list itself is console-managed now |
| terminal_backends | settings `mycore.shell` (local / docker / ssh grammar) | **configurable** — Settings `#mycShell` | `L3` (write → `capabilities.terminal.backend`) · `U1` (field → read-back) | The raw spec never serializes into capabilities |
| tool_toggles | env `MYCORE_TOOLS_OFF` > settings `mycore.tools_off` (1..32) | **configurable** — Settings `#toolsOffList` checkbox grid rendered from `capabilities.mycore.known_tools` (25 names) | `L3` (write → `capabilities.tools` shrinks) | Unknown names are refused before any write |
| tts_engines | env `AIDOG_TTS_URL` > settings `media.tts_*` · slot `TTS_API_KEY` | **configurable** — Settings → Media engine/endpoint/model + `TTS_API_KEY` row | `L1` `U2` | `media.tts.byok` presence is now visible through the slot row |
| voice_announcements | settings `voice_volume` `alert_preview` `sound` | **configurable** (unchanged) | not re-exercised by this car · `tests/web_console_p3.py` voice legs | — |
| voice_session | settings `media.stt_model` `media.wake_phrases` | **configurable** (unchanged) | not re-exercised by this car · `run.py::test_stt`, `tmp/e2e_car4c.py` | No credential slot exists for STT |
| zero_key_onboarding | env `AIDOG_LOCAL_MODEL_PROBES` · wizard writes `mycore.{base_url,model}` | **read-only** — detected-local-model count in `#capGrid`; since 0.5.40 the activation card reports endpoint reachability and deep-links the fix to `#mycUrl` | `U5` (system card rows) · `tmp/e2e_activation.py` `L1` + `tmp/e2e_activation_ui.js` `U1` (endpoint row → FIX) · gates: `run.py::test_localmodels`, `tmp/e2e_car4b.py` | Keyless adoption still happens through `aidog setup` — the console points at the place, it does not adopt |

## 2. Designed CLI/env — not console gaps

These carriers are CLI/env **by design**. Their absence from the console is a
documented boundary, not missing coverage:

| Feature | Carrier (by design) | Console state | Notes |
|---|---|---|---|
| checkpoints | env `AIDOG_MC_STATE` · TG `/checkpoint` · CLI `python3 -m mycore.checkpoint --list/--restore` | designed elsewhere | Pre-write byte snapshots; restore is an explicit operator action |
| setup_wizard | CLI `aidog setup` writes 6 slots + `mycore.{base_url,model}` | designed elsewhere **+ console mirror (0.5.40)** — Overview `#actCard` renders the wizard's own `--check` semantics (required/format/endpoint/service, `ready` = the same 0/2/1 pending rule) with fix deep-links; the guided *writing* flow stays CLI | Guided CLI wizard, no GUI installer; zero-echo asserted by `tests/setup_wizard_b3.py`, pattern parity by `tmp/e2e_activation.py` `L1` and `tests/web_console_p3.py` |
| site_visibility | Cloudflare Pages + KV via `deploy/deploy_site.sh` | designed elsewhere | Marketing site is a separate surface with its own checks |
| desktop_client | env `AIDOG_REPO` `AIDOG_WEB_PORT` · `npx electron .` | designed elsewhere | The shell *hosts* the console; the HUD is its own page |
| acp | env `MYCORE_*` + `python3 -m mycore.acp` | designed elsewhere | Stdio JSON-RPC server for external ACP clients |
| memory export/import | CLI `./aidog export\|import\|list`, `python3 deploy/aidog_memory.py sync\|status` | designed elsewhere | File bundles are an operator action; the console shows state only |
| browser binary | env `AIDOG_BROWSER_BIN` (authoritative) → Playwright cache → `PATH` | designed elsewhere | Machine path read at tool registration; `#capGrid` shows availability only |

## 3. Console coverage after 0.5.39

**New endpoints**

| Endpoint | Auth | Contract |
|---|---|---|
| `GET /api/secrets` | loopback, optional `Bearer WEB_TOKEN` | `{"slots":[{name, group, set, length}], "writable": bool}` — 32 writable slots in six groups (`core` `gateway` `web3` `channels` `media` `connectors`) plus `WEB_TOKEN` presence-only (`writable:false`); `length` is `null` unless a valid Bearer is supplied — invalid/absent token omits lengths, never a 401 |
| `POST /api/secrets` | `Bearer WEB_TOKEN` (503 when the slot is absent) | `{name, value}` → `{ok, changed, slot{name, group, set, length}}`; `value:""` clears; atomic own-line rewrite, mode 0600; 400 on unknown name / `WEB_TOKEN` / control chars / >4096 / leading-trailing whitespace — all before any write |
| `GET /api/cron` | loopback, no auth | `{"jobs":[{id,type,spec,text,kind,source,origin,created_at,last_fired,done,due,next}], "count": N}` from the scheduler registry; fixed field whitelist (no absolute path), corrupt/absent file → `{"jobs":[],"count":0}` |
| `GET /api/capabilities` (extended) | loopback, no auth | top-level `env_overrides` (env var **names** only) + `mycore.known_tools` (25 names, the checkbox grid source) |
| `GET /api/settings` (extended) | loopback, no auth | always carries the `evolution` (4 keys) and `hooks` partitions now — absence-as-default is gone |

**New controls**

| View | Control ids | Writes |
|---|---|---|
| Settings → Credentials | `#credList` rows (`data-slot`), `.cred-input` `.cred-set` `.cred-clear` `.cred-status`, `#credTokenHint` | `POST /api/secrets` |
| Settings → Channels | `#chanList` 9-route form (`[data-chan-route]`, `input.chan-enabled`, `[data-chan-field]`), `#chanTestRoute/#chanTestTarget/#chanTestText/#chanTestBtn/#chanTestMsg` | `POST /api/settings {channels}` + `POST /api/channels/send` |
| Settings → Event hooks | `#hookList` (`[data-hook-event]`, `.hook-cmd`, `.hook-timeout`, `[data-hook-count]`) | `POST /api/settings {hooks}` (dirty events only) |
| Settings → Usage | `#usageMetricsUrl` `#usagePricingCfg` `#usageLogDir` | `POST /api/settings {usage}` |
| Settings → Model | `#mycContextDirs` `#mycUpstreams` `#mycShell` `#mycMcp` `#toolsOffList` `#envOverrideChip` | `POST /api/settings {mycore}` |
| Evolution | `#evoEnabled` `#evoDailyAt` `#evoWeeklyDay` `#evoMonthlyDay` `#evoSaveBtn` `#evoSaveState` (+ `#evoCrossNote`) | `POST /api/settings {evolution}` |
| Exchange | `#exCapPerTx` `#exCapDaily` `#exCapSaveBtn` `#exCapState` (chips `#exThreshold` repaint) | `POST /api/settings {web3}` — both keys always |
| Overview | `#capGrid` (kv rows) · `#cronList` (read-only job rows) | none — display only |

Only blocks the operator actually edited join the save payload (dirty
tracking), so the legacy 16-key payload keeps byte-identical behavior.

**Also from this car:** `secrets.env.example` now documents the 20 runtime
slots it was missing (`OPENAI_API_TOKEN`, `CHANNEL_WEBHOOK_SECRET` + 14
`CHANNEL_*`, `TTS_API_KEY`, `IMAGE_API_KEY`, `LINEAR_API_KEY`,
`NOTION_API_KEY`) plus `MYCORE_WD_CHAT` and `AIDOG_CF_DNS_TOKEN` as
commented, zero-value lines, and marks the dead legacy slots `# legacy — no
code reader`; `MYCORE_WD_CHAT` finally resolves at runtime (process env →
own-line slot → `""`, `mycore/envslots.py`).

**Credential-slot editor — security contract**

- **Echo = name + presence + length.** A slot value never serializes in a
  response, a log line, an error, an exception or the DOM. `GET` without a
  valid Bearer omits `length` entirely (presence stays readable like the rest
  of the catalog); `POST` answers `name/group/set/length` only.
- **Gate order:** `Bearer WEB_TOKEN` (503 slot missing / 401 mismatch) →
  body cap → name allowlist (unknown name / `WEB_TOKEN` not writable → 400)
  → value rules (single line, ≤4096, no surrounding whitespace) → atomic
  write. Nothing is written before every check passes.
- **Persistence:** own-line rewrite of `secrets.env` — every other line is
  preserved byte-exact, temp file in the same directory, `os.replace`, mode
  `0600`. `""` clears (the own-line reader treats empty as absent).
- **`WEB_TOKEN` is presence-only** (`writable:false`): the gate never rewrites
  itself — rotate it by hand (the UI hint says so).
- **Client side:** the input is cleared the moment the write is accepted, the
  value is never copied into another node, attribute, `localStorage` or
  payload, and the status line shows `name · N chars` / `empty` only.

## 4. Activation & operations (0.5.40)

The activation car gives the console the "am I ready?" face and the three
operator actions the 0.5.39 walkthrough still had to reach a terminal for.
Everything below is additive; the security contract of §3 is unchanged
(Bearer `WEB_TOKEN` on every write, values never serialized).

**New endpoints**

| Endpoint | Auth | Contract |
|---|---|---|
| `GET /api/setup` | loopback, no auth | `{"ready": bool, "items": [6 rows], "service": "running\|pending(fail-closed)\|stopped", "model": {endpoint, reachable}}`; fixed item order `web_token, model_key, tg_token, tg_allowlist, model_endpoint, service`; each row `{id, required, ok, fmt, state, hint, hint_en}` carries **presence/format words only** (`set\|empty\|invalid`, `reachable\|unreachable\|empty\|invalid`, service `running\|pending(fail-closed)\|stopped`); `fmt` is `null` only on the service row; an empty slot is `fmt:true, ok:false` (pending — mirrors `aidog setup --check`), a non-empty bad value is `fmt:false, state:invalid`; `ready` = every **required** row ok (the service row never counts — a bot may be deliberately off); `hint` is an i18n key (`act.hint.*`) plus an English fallback |
| `POST /api/service/restart` | `Bearer WEB_TOKEN` | `{target:"service"}` → 200 `{ok,target}`; **400** bad target · **409** `{error:"chat service not running under tmux", target}` when the session is absent · **500/504** start failures · 401/503. Restarts ONLY the chat-service tmux session (`aidog_svc`, or `aidog_svc_<profile>` + `profile_ctl.sh start <name>` for a named profile) — the console never restarts itself; logs carry session name + return code only |
| `POST /api/update/apply` | `Bearer WEB_TOKEN` | **202** `{ok,queued}` — `deploy/aidog_update.py apply` queued as a DETACHED child, output appended to `data/update_apply.log`; the console may die mid-apply (that is the design); the body is drained, never logged; the E2E only proves the 401 gate (apply is a release-time operation) |
| `GET /api/version?fresh=1` | loopback | same payload as the cached read, 300s cache bypassed for the CHECK button |
| `POST /api/doctor/run` | `Bearer WEB_TOKEN` | 200 `{checks, fail, warn, summary, rc, lines[:80]}` — doctor's own `N checks · X FAIL · Y WARN` line parsed verbatim; a non-zero exit is still 200 (FAIL/WARN are honest output, not an error); 504 on the 60s cap |
| `POST /api/cron {action:"del", id}` | `Bearer WEB_TOKEN` | 200 `{ok,id,count}` · **404** unknown id · **400** (only `del` — there is deliberately **no `add`**: creation stays natural-language/CLI/Telegram) · 401 |

**Schema + bug fixes**

- `mycore.models` (≤32 single-line ids, `[]` legal = whitelist off) and
  `mycore.budget` (int 4000..1000000, bool refused) joined `MYCORE_KEYS`, so
  the raw-file consumers (`service._budget`, `service._allowed_models`,
  `subagent._allowed_models`) read the same document the console writes.
- **Settings write preserves unknown keys** (bug fix): the merge lands on the
  RAW parsed file document, never on `load_settings()`'s filtered view — a
  hand-written `zzz_custom` survives a console save while `GET` stays the
  validated view.
- **Model key resolution** (bug fix): `mycore/model.py::_headers` resolves
  `os.environ.get(name) or <own-line secrets slot>` — a key written through
  the console finally reaches the chat client. Env still wins, the loopback
  placeholder and the unauthenticated-remote rules are unchanged, and the
  value is never logged, repr'd or serialized.
- **Slot write formats**: `POST /api/secrets` checks the mirrored wizard
  pattern for `AIDOG_TG_ALLOWED_USERS` (`^\d+(,\d+)*$`; empty = clear, legal)
  and refuses with `invalid format for <slot> … (value not shown)` **before**
  writing. `SLOT_PATTERNS` are parity-tested against `deploy/aidog_setup.py
  SLOTS` so the server and the wizard cannot drift.

**New controls**

| View | Control ids | Writes |
|---|---|---|
| Overview → ACTIVATION (first card) | `#actCard` `#actBadge` `#actStart` `#actList [data-act-item]` `[data-act-fix]` `#actRecheck` `#actMsg` · bot block `#actBotState` `#svcRestartBtn` `#svcRestartMsg` | read `GET /api/setup` + `POST /api/service/restart` |
| topbar / update banner | `#updateCheckBtn` `#updToast` · `#updateApplyBtn` rendered **only** while `update_available === true` | `GET /api/version?fresh=1` + `POST /api/update/apply` |
| Overview → SYSTEM | `#doctorRunBtn` (disabled while running) `#doctorMsg` `#doctorOut` (≤80 lines, scrollable) | `POST /api/doctor/run` |
| Overview → CRON | per-row `#cronDelBtn[data-job]` (confirm) + the `cron.createHint` line | `POST /api/cron {action:"del"}` |
| Settings → Model | `#mycModels` `#mycBudget` (join the payload only when dirty; budget is never sent empty) | `POST /api/settings {mycore}` |
| Settings → save row | `#saveState[data-restart-hint="1"]` (`ctl.restartSvcHint`) + `cred.restartHint` on the bot/allowlist credential rows | none — honest "restart to apply" semantics for registry-at-start keys |

**Fix deep-links** (`ACT_FIX` in `app.js`): slot rows → `?view=settings` +
scroll/focus to `#credList[data-slot=…]`, `model_endpoint` → `#mycUrl`,
`service` → `#svcRestartBtn`. `#actCard` stays hidden when `/api/setup` 404s
or returns a bad shape — an honest absence beats a guessed state — and the
restart flow polls `/api/status` up to 20s, rendering 409 / failure /
timeout as three distinct honest lines with the server error verbatim.

**Security contract (extended, never weakened)** — no credential value
serializes in `/api/setup`, in the doctor payload, in the restart/update
responses, or anywhere in the DOM (proved by sentinel sweeps); every new
write endpoint is Bearer-gated (`/api/setup` and `/api/version` stay
loopback reads); refusals name the slot/pattern/rule and never the value;
the restart handler logs a tmux session name and a return code only.

## 5. Evidence

- **`tmp/e2e_frontend_coverage.py`** — isolated profile (own `secrets.env` +
  settings copy + own port; production `secrets.env`/`web/settings.json`
  byte-compared in teardown), **62 checks · 8 legs**: L1 secrets roundtrip +
  zero-echo sweep (16) · L2 400/401/503 refusals (10) · L3 settings →
  behavior (14) · L4 channel send 409 both ways (4) · L5 `env_overrides`
  name-only (3) · L6 `/api/cron` seed/list/delete (7) · L7 `wd_chat_target`
  three states (3) · L8 production console healthy + files byte-identical
  (3). Run: `python3 tmp/e2e_frontend_coverage.py` (rc 0 = 0 fail).
- **`tmp/e2e_frontend_ui.js`** — Playwright against an isolated console
  spawned by the script: **33 checks · U0–U7** — settings save round trip,
  credential set/clear + DOM/storage value scan, evolution editor, exchange
  caps, `#capGrid`/`#cronList`, zh pack parity, zero `pageerror`, production
  health. Run: `PW_PATH=<playwright node path> node tmp/e2e_frontend_ui.js`.
- **`tmp/e2e_activation_ui.js`** — Playwright against an isolated **named**-
  profile console spawned by the script (so the RESTART button can only hit
  the absent `aidog_svc_<name>` session — it can never kill production):
  **50 checks · U0–U9** — activation card fresh → FIX deep-link → RE-CHECK,
  restart confirm → honest 409, update CHECK toast + hidden APPLY, doctor
  click → summary + running state, cron row delete, `#mycModels`/`#mycBudget`
  save → read-back + restart-hint chip, action buttons (channel SEND TEST
  409 detail, seeded plan APPROVE/REJECT flip, LEARN + REFLECT round trip),
  zh pack parity + DOM credential scan, zero `pageerror`, production health.
  Run: `PW_PATH=<playwright node path> node tmp/e2e_activation_ui.js`.
- **`tmp/e2e_activation.py`** (activation car, 0.5.40) — isolated profile,
  never production: **81 checks · 10 legs (L1–L10)** — L1 `/api/setup`
  fresh → filled (mock model endpoint) → invalid allowlist + wizard pattern
  parity (23) · L2 models/budget accept-reject matrix + raw-file consumer +
  unknown-key survival (13) · L3 allowlist format matrix + zero-echo sweep
  (9) · L4 restart 401/400/409 on a profile with no tmux session (5) · L5
  `?fresh=1` shape + apply 401 with the apply log untouched (5) · L6 doctor
  summary + no sentinel leak (5) · L7 cron seed → delete → 404 (7) · L8 model
  key env/slot/placeholder in-process (5) · L9 **production restart positive,
  run last, with `boot_services.sh start` recovery** (5) · L10 production
  health + byte identity (3). Run: `python3 tmp/e2e_activation.py` (rc 0 = 0
  fail).
- **`tmp/e2e_settings_matrix.py`** (settings-matrix addendum, 0.5.40
  close-out of reconcile §5) — isolated profile, never production: **57
  checks · 6 legs (L1–L6)** — L1 `mycore.base_url`/`mycore.model` write →
  live `/api/capabilities` `mycore.model` + a legal `upstreams` chain flips
  `upstreams_configured` + a grammar-broken one refused 400 with the exact
  rule (9) · L2 the 7 `media.*` keys → `capabilities.media` (`stt.model=small`,
  `tts.active=byok` + usable, `image.configured`) + `wake_phrases` read-back
  (7) · L3 the 7 top-level keys (`language` `refresh_sec` `sound`
  `voice_volume` `alert_preview` `currency` `fx_rate`) → per-key read-back
  + `/api/usage` `display_fx` CNY native path with the structure intact
  (12) · L4 `MODEL_API_KEY` / `CHANNEL_SLACK_TOKEN` / `TTS_API_KEY`
  set → length → clear (`set:false`) round trips with a zero-sentinel sweep
  over the responses and `/api/capabilities`, then a byte-restored
  `secrets.env` (16) · L5 `mycore.context_dirs` + `mycore.mcp` →
  `mcp_servers.count/names`, broken JSON 400, both cleared (9) · L6
  production health + `secrets.env`/`web/settings.json` byte identity (3).
  Run: `python3 tmp/e2e_settings_matrix.py` (rc 0 = 0 fail).
- **`tmp/e2e_settings_matrix_ui.js`** — Playwright against an isolated
  named-profile console spawned by the script: **17 checks · U0–U6** — U1
  `#mycContextDirs` + a legal `#mycMcp` array → SAVE → Bearer
  `GET /api/settings` read-back (+ the restart-hint chip), U2 a broken JSON
  array → the server's 400 detail rendered verbatim in `#saveState` with the
  store untouched (all-or-nothing), U3 zh repaint of the new and a
  pre-existing control, U4 no token/slot value in the DOM or any input
  value, U5 zero `pageerror`, U6 production console health. Run:
  `PW_PATH=<playwright node path> node tmp/e2e_settings_matrix_ui.js`.
- Standing gates (after the mission car): `mycore/tests/run.py` **1477** ·
  `tests/web_console_p3.py` **234** · `tests/source_lang_check.py` **0/67**
  (locale packs **614/614**). The 0.5.39 baseline of this page was
  1168 · 197 · 480/480.
