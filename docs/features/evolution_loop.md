# Feature spec — Institutional self-evolution loop (task 2 / Phase A)

Status: **implemented** (2026-10-19). Design anchor:
`docs/design/im_aidog_product_design.md` differentiation #3 — prior-art products
ship `/learn` as a feature; this is a **system**: learn → reflect
(daily/weekly/monthly) → evolution log → lint gate, growing more familiar
with use.

**Honest boundary (owner red line):** the loop exists as code + gates + tests
only after this lands. The cron leg is **off by default** (`evolution.enabled
= false`, fail-closed), reflect drafts are **fact skeletons with a Human
conclusion placeholder** — conclusions are never generated, only written by
the owner after review.

## 1. The four stages (all in `mycore/evolution.py`)

| Stage | Entry points | Artifact |
|---|---|---|
| **learn** | `/learn` (TG), `POST /api/learn` (console) | skill → `overlay/skills/<slug>/SKILL.md` (frontmatter generated) · memory → `overlay/memory/EVOLUTION_learned.md` (appended entry) |
| **reflect** | `/reflect`, `POST /api/evolution`, cron leg, `python3 -m mycore.evolution --reflect <period>` | `overlay/evolution/reflect/<date>.<period>.md` (fact skeleton + Human conclusion placeholder) |
| **log** | appended automatically on every accepted artifact | `overlay/evolution/EVOLUTION.md` (append-only → git-traceable) |
| **lint** | runs **before** every write; `--lint-tree` re-verifies stored artifacts | rule-id rejections; rejects change nothing |

### Lint gate (write-time, `lint_text` + per-kind structure rules)

| Rule id | Rejects |
|---|---|
| `secret-assignment` | `key = <12+ char value>` shapes for api_key/secret/token/password… |
| `secret-shape` | `sk-…`, `ghp_…`, `xox…-`, `AKIA…` shaped tokens |
| `private-key` | `-----BEGIN … PRIVATE KEY-----` blocks |
| `bearer` | `Bearer <20+ char>` values |
| `machine-path` | `/mnt/`, `/home/`, `/root/` substrings (publish-gate class) |
| `windows-path` | `C:\`-style drive paths |
| `bot-handle` | `@my_*_bot` personal handles |
| `name-slug` / `description` / `empty` / `size-cap` / `frontmatter` | structure: lowercase slug `a-z0-9._-` (≤64), one-line desc ≤200, non-empty, body ≤16 KB / memory ≤4 KB, no self-authored frontmatter |
| `exists` / `duplicate` / `io` / `name-mismatch` | collisions and write failures |

Rejects are answered to the caller (localized rule labels via `rule.*`
locale keys) and logged to `service.log` / console stderr **only** — they
never enter the tracked evolution log (a rejected secret must not be echoed
anywhere).

**skill_route pre-screen** (before any skill write): TOP1 against an
*existing* skill scoring ≥ 10 on the candidate's `name + description` →
`duplicate` rejection; score ≥ 4 → reported as a pre-screen note; same-dir
hit against an *external* skill root → rejection. Router failure degrades to
"pre-screen unavailable" (learn stays available, the note says so).

### Reflect windows (Beijing time, fixed UTC+08:00)

| Period | Window | State key | Fires |
|---|---|---|---|
| daily | today 00:00 → now | `YYYY-MM-DD` | after `daily_at`, once per Beijing day |
| weekly | this ISO week's Monday → now | date of the latest `weekly_day` | on/after that weekday, once per occurrence |
| monthly | this month's 1st → now | `YYYY-MM` | on/after `monthly_day` (1..28), once per month |

A missed window **catches up** on the next opportunity (never silently
skipped); each window reflects at most once. State lives in
`<state>/evolution_state.json` (env `AIDOG_MC_STATE`, else
`<repo>/data/mycore_service` — gitignored, next to the chat-service state).

Draft contents are **facts only**: evolution entries in the window, skill
list, memory volumes, git commits in the window (honest
`git history unavailable` outside a repo), other drafts on file — then two
pending sections (`Signals`, `Human conclusion (owner)`).

## 2. Surfaces

- **Telegram** (`mycore/service.py`, allowlist-gated like every command):
  - `/learn memory <text>` · `/learn skill <slug> :: <description> :: <body>`
  - `/reflect [daily|weekly|monthly]` — explicit trigger, same code path as
    cron (the window key is marked done either way)
  - `/evolution` — schedule, due-now, last runs, counts, log tail
- **Console**: `GET /api/evolution` (read-only status), `POST /api/learn`
  and `POST /api/evolution` (Bearer `WEB_TOKEN`, same contract as settings
  writes) + the **EVOLUTION** card (schedule badge, log tail, learn form,
  reflect button).
- **CLI/ops**: `python3 -m mycore.evolution [--status|--reflect P|--lint-tree] [--json]`.

## 3. Cron leg (dual-driven scheduling)

`web/settings.json` → `evolution` partition (validated by the same strict
allow-list as every settings key; the example file ships the block with
`enabled: false`):

```json
"evolution": { "enabled": false, "daily_at": "09:10",
               "weekly_day": "sun", "monthly_day": 1 }
```

The chat-service poll loop evaluates `is_due()` once a minute
(`REFLECT_CHECK_SEC`) and calls the same `reflect()` the triggers use.
Invalid/absent config = **disabled** (fail-closed). Enabling the partition in
the console settings page is the switch — and since the frontend coverage
car (0.5.39) the EVOLUTION view actually carries that switch: `#evoEnabled`,
`#evoDailyAt`, `#evoWeeklyDay`, `#evoMonthlyDay` + `#evoSaveBtn`, prefilled
from `GET /api/settings` and cross-checked against the ACTIVE schedule
(`#evoCrossNote` says "form and active schedule differ" instead of guessing).

## 4. Learned memory slot

`mycore/assembly.py` appends `overlay/memory/EVOLUTION_learned.md` to the
system stack **only when the file exists** (fresh installs: silently absent —
never a "missing slot" notice; curated slots keep their notice behavior).

## 5. Gates & tests

- `python3 -m mycore.tests.run` → `test_evolution()` (unit: lint rules,
  learn/reflect/log/state/is_due/validation/status/lint_tree) + service
  command coverage.
- `python3 tests/web_console_p3.py` → evolution endpoint auth/validation.
- **Console coverage (frontend car, 0.5.39)**: schedule editor round trip.
  Evidence: `tmp/e2e_frontend_coverage.py` `L3` (write → `/api/evolution`
  reflects `enabled`/`daily_at`/`weekly_day`/`monthly_day`) and
  `tmp/e2e_frontend_ui.js` `U3` (save → status line re-rendered + Bearer
  read-back) — see [frontend_coverage.md](frontend_coverage.md).
- `python3 tests/source_lang_check.py` → locale packs parity for all new keys.
- `python3 deploy/check_publish.py` → evolution artifacts are tracked under
  `overlay/**` (allow-classified) and pass the leak scan because the lint
  gate enforces the same classes at write time.
