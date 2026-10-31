# Feature spec — Profiles (one machine, many dogs)

Status: shipped in car F (`lib/profile_paths.py`, `deploy/profile_ctl.sh`,
`aidog profile` CLI, `tests/profile_f.py`, v0.5.31 line). This page documents
the multi-profile runtime isolation delivered against design §13.1
(`docs/design/im_aidog_product_design.md` §13.1). All numbers below are the
ones the gates actually report; platforms and legs we did not exercise are
marked as such, never claimed.

## 1. Purpose

Named, fully isolated runtime directories on **one machine** — each profile is
its own "dog": own config, own credentials, own state, own brain. This is
deliberately **not SaaS multi-tenancy**: no accounts, no roles, no permission
system — every profile is a first-class local install, and each profile's
owner runs it from their own directory. The anti-goal is the documented
cross-profile clobber class (a typo silently resolving into another profile's
brain); hence the fail-closed rules in §4.

## 2. Model

```
  AIDOG_PROFILE=<name>            (env; unset/empty = DEFAULT profile)
        |
        v
  <repo>/profiles/<name>/
    settings.json   per-profile product config (same schema as web/settings.json)
    secrets.env     per-profile credential slots (own bot token / WEB_TOKEN / allowlist)
    state/          sessions/ cron_jobs.json evolution_state.json checkpoints/
                    cursors service.log boot.log web.log pidfile lang.json
    body/           constitution/SOUL.md  memory/{MEMORY,USER}_curated.md
                    memory/EVOLUTION_learned.md  skills/ (learned)  evolution/
    images/         per-profile generated media
    profile.json    metadata: name / web_port / created   (written LAST by create)

  shared read-only:  overlay/locales (i18n packs) · overlay/skills (shipped)
                     overlay SOUL/memory templates (create's seeding source)
```

- **Default profile = zero migration.** With `AIDOG_PROFILE` unset, every seam
  resolves to the exact pre-car-F path (`web/settings.json`, `secrets.env`,
  `data/mycore_service`, `overlay/*`, `data/images`) — byte-identical legacy
  behavior, locked by the suite.
- Name whitelist: `^[a-z0-9][a-z0-9_-]{0,31}$` — safe for tmux suffixes,
  shell interpolation and path joins; no separators, dots or uppercase.
- Env seams (re-read on every call, never cached): `AIDOG_PROFILE`,
  `AIDOG_PROFILES_DIR` (root, default `<repo>/profiles`), `AIDOG_MC_STATE`.

## 3. Isolation axes (each independent)

| Axis | Default profile | Named profile |
|---|---|---|
| State: sessions, cron, evolution state, checkpoints, service/web logs | `data/mycore_service` | `<profile>/state/` |
| Product settings | `web/settings.json` | `<profile>/settings.json` |
| Credentials (bot token, `WEB_TOKEN`, allowlist) | `secrets.env` | `<profile>/secrets.env` — **its own bot token, mandatory** |
| Body: SOUL, curated memory, learned memory, evolution log | `overlay/*` | `<profile>/body/*` |
| Learned skills | `overlay/skills` shared | `<profile>/body/skills` **prepended**; shipped `overlay/skills` always appended below (`skill_roots` order preserved) |
| Generated media | `data/images` | `<profile>/images` |

Process-level caches (token cache, image outbox, model tiers) live inside each
service process, so the one-process-per-profile model gives this separation for
free — no audit needed, no shared mutable module state.

**Why each profile needs its own bot token:** two pollers on one token fight
over update offsets. `run_service.sh --check` enforces this before anything
starts (§4), and `aidog profile create` prints the warning without ever
echoing a value.

## 4. Fail-closed contracts

| Condition | Behavior |
|---|---|
| Invalid `AIDOG_PROFILE` (Python) | `lib.profile_paths.ProfileError` (a `ValueError`) — raised by `profile_name()` and propagated (e.g. `skill_roots` propagates it; asserted, not swallowed) |
| Invalid `AIDOG_PROFILE` (shell) | `run_service.sh` → **rc 5**; `profile_ctl.sh` → **rc 2** — a bad name never reaches a path |
| Profile with empty allowlist / token slots | `run_service.sh --check` → **rc 3 / rc 4** — never starts |
| State-root precedence | `AIDOG_MC_STATE` (explicit env) > `<profile>/state` > `data/mycore_service` |
| Empty/unset profile name | default profile, no raise, legacy paths |

Silent fallback to the default profile's runtime is never an outcome: the
default layer is additionally scanned for profile-marker bytes by the suite
(the anti-crosstalk wall).

## 5. CLI & operations surface

| Surface | Contract |
|---|---|
| `aidog profile list` | table of DEFAULT + every validly named dir: state-dir / secrets / settings / `web_port`; ends with `N profile(s) · active: <AIDOG_PROFILE or DEFAULT>` |
| `aidog profile create <name>` | **scaffold first, seed second, `profile.json` LAST** (a crash mid-seed never leaves a profile `list`/`status` would adopt): dirs → `settings.json` byte-copied from `web/settings.json` → `secrets.env` written as an **empty 3-slot template** (`WEB_TOKEN=` / `MYCORE_TG_TOKEN=` / `AIDOG_TG_ALLOWED_USERS=`; the repo's real `secrets.env` is never read) → SOUL + both curated memory files seeded from `overlay/` → `profile.json` `{name, created, web_port}` with the first free port ≥8791. Duplicate and invalid names refused (rc≠0); `default` is reserved |
| `aidog profile rm <name> [--force]` | refuses when `state/` holds files unless `--force` (stop first: `profile_ctl.sh stop <name>`); default profile has no dir and can never be removed; `--dry-run` supported |
| `bash deploy/profile_ctl.sh start\|stop\|status <name>` | `start` gates on `run_service.sh --check` (credentials never touch argv), then tmux pair `aidog_svc_<name>` (service, own state root) + `aidog_web_<name>` (console on `profile.json` `web_port`); `stop` kills **exactly those two session names** (never a broad `pkill`); `status` prints session state + probes **`/api/health` and asserts `status == "ok"`** so a foreign process squatting the port does not read as up. The default stack (`aidog_web`/`aidog_svc`/`aidog_mwd`, owned by `boot_services.sh`) is only ever read, never touched |
| `profile_ctl` exit codes | **0** ok · **1** usage/state error · **2** bad name · **3** not provisioned · **4** no bot token · **5** profile dir missing |
| `/api/status` | carries `"profile": <active name>` (empty string = default); the console header appends `· <name>` to the status summary only when non-empty — default output is byte-for-byte pre-car-F, and no locale key was added (bare noun) |

## 6. Verification

| Gate | Command | Result |
|---|---|---|
| Isolation suite (offline, stdlib, writes confined to `/tmp`) | `python3 tests/profile_f.py` | **63/63** — path seams 12 · dual-profile subprocess cross-check 24 · stack + skill roots 8 · `aidog profile` CLI 12 · `run_service.sh` semantics 5 · end-of-suite default-layer scan 2 |
| Car-F E2E (real machine, temporary script) | `python3 tmp/e2e_car_f.py` | **18/18** — 18 legs incl. baseline/final byte-identical default layer, profile console on 18791–18795, token-gated POST, fail-closed `--check` rc 3/4/5 |
| Full gate line (car G, v0.5.32) | see HANDOVER §3 | core **1020/1020** · smoke **154/154** · router **30/30** · memory **58/58** · setup **99/99** · profile **63/63** · desktop **15/15** · publish **READY(0)** |

The suite's core leg runs **two real `ChatService` child processes**
(alice/bob, offline fake transport + injected echo chat), each doing
`/learn` memory, `/cron add`, `/learn` skill, `/reflect` and one plain turn
with a unique marker token: every artifact must land under its own
`<root>/<name>/`, each tree must carry zero bytes of the other, and the whole
default layer must stay byte+mtime unchanged with zero profile-marker bytes —
re-checked once more after all legs.

## 7. Honest boundaries

- **Deploy scripts hard-code `<repo>/profiles/<name>`.** `run_service.sh` and
  `profile_ctl.sh` do **not** honor `AIDOG_PROFILES_DIR` — that env is a
  `lib/profile_paths` seam only, used by the tests. (The E2E bridges its tmp
  profile with a transient gitignored symlink, removed in cleanup.)
- **`aidog setup`, doctor, update and the watchdog still operate on the
  default profile only.** The watchdog monitors the default target
  port/paths; profile instances are not watched. Not hidden, not claimed.
- **Desktop shell attach on 8790 = default profile.** The console it windows
  into is whatever serves 8790 (see [desktop_client.md](desktop_client.md)).
- **Second-bot dual live chat = pending.** Requires a second bot token; the
  E2E prints `SKIP (pending second bot)` rather than faking the leg.
- **Profile console writes answer 503 while `WEB_TOKEN` is empty** — the
  Bearer gate names the unconfigured slot instead of opening up. Same honest
  503/401 contract as the default console.
- **Not multi-tenant SaaS**: no roles, no per-user permissions, no resource
  quotas — one machine, many independent profiles (design §13.1 scope).
