# SNIFF — the sense & proactive-discovery engine (SENSE board)

Status: shipped in the SNIFF car (P0). Design authority:
[../design/sniff_engine_plan.md](../design/sniff_engine_plan.md);
borrowed-bridge hookup notes live in [mcp.md](mcp.md).

**嗅探 = the product's nose.** The agent sweeps read-only sources on an
interval, evaluates bounded rules over the readings a sweep just produced,
records durable, deduplicated *findings* — and **speaks first** only when a
finding earns it. Sense → sweep → finding → speak, never another chat window.

## Sources (three kinds, all read-only)

| Kind | Reads | Configuration | Never does |
|---|---|---|---|
| `home` | `GET <url>/api/states` filtered by entity globs (Home Assistant as the hub for health/device/vehicle entities) | `url` + `token` (a **slot NAME**, e.g. `HA_TOKEN`) + `entities` globs + `rate_cap_per_min` | no service call, no state write |
| `web` | one `GET` per sweep: a page hash plus an optional regex extraction | `url` + `regex` + `field` | no POST, no form, no session |
| `mcp` | the bridge registry (`settings.mycore.mcp`) + this process's live sessions for one named server | `server` (the bridge name) | **never calls a tool, never spawns a process, never opens a connection** |

Honest statuses (exactly these words, on every source row):
`disabled` · `pending` (configured, first sweep not run yet) · `reachable` ·
`stale` (no successful read for 1 h) · `error` · `never-configured` (no
credential slot / no such bridge) · `rate-capped` · `detached` (an `mcp`
bridge that is declared but has no live session). A source we did not reach
is never reported as reachable, and a failed sweep yields **no readings** —
stale data never fires a rule.

Readings an `mcp` rule may reference: `servers` (bridges declared),
`transport` (`stdio` | `http`), `sessions` (live sessions right now),
`tools`, `filters` (allow + deny patterns), `slots` (`$SLOT` references,
names counted, never resolved). So `sessions == 0 for 30m` is the finding
that says a declared bridge went away.

## Rule grammar (bounded, no eval of free text)

```
sensor.blood_pressure > 140 and time > 23:00 for 10m
binary_sensor.door_contact changed
page_hash != 'abc123' or price in [100, 110]
```

- Operators: `== != < <= > >= in changed`, composed with `and` / `or`
  (`and` binds tighter), plus a trailing `for <N>m|Nmin|Nhours` sustain
  window — a condition must hold for N minutes before it earns a finding.
- `time` is a Beijing (UTC+08:00) minutes-since-midnight literal, the same
  clock the cron lanes use.
- A fixed lexer + recursive-descent parser; the **first** error is reported
  with the rule id and the offending position, and a bad rule is isolated
  (`rule_errors`) instead of silencing the board. Nothing in the text is
  ever executed — asserted by the core suite.

Settings live under `mycore.sniff` (`enabled`, `pause`, `interval_s`
10..86400, `quiet_hours`, `digest_cap`, `sources`, `rules`); the console
write gate parses the whole partition first-error and refuses a credential
**value** (slot grammar), a non-http url, an unknown field or a rule that
names an unconfigured source.

## Findings (local, bounded, provenance-tagged)

`<state>/sniff_findings.json` — gitignored runtime state; personal signals
never leave the machine through the engine. Same signature (source + rule +
value) = same observation: `first_seen` is preserved, `count` advances and
severity escalates to the loudest seen. Log is bounded (500 rows, oldest
dropped); payloads are field-whitelisted and truncated, and **only the
readings a rule names** can enter a payload.

## Delivery — speak first, earn it

The service's SENSE leg (60 s cadence, `mycore/service.py`) runs the sweep
and then hands the batch to the **existing cron delivery chain**
(origin → `route:target` → `MYCORE_WD_CHAT` → log-only). Gates, in order:

1. **`enabled` / global `PAUSE`** — a paused board neither sweeps nor speaks.
2. **Quiet hours** (`HH:MM-HH:MM`, Beijing, midnight-wrapping) — the batch
   waits and merges into the first non-quiet window; a malformed window is
   treated as off, so it can never swallow a finding.
3. **`digest_cap`** findings per window (bounded again at 15 rows / 3500
   chars), oldest first; the remainder speaks in the next window.
4. **Per-rule `speak`** — a rule without `speak` records board-only and is
   never spoken.

Batches are marked delivered **before** the send (cron/mission mark-first
philosophy: a crash loses one batch, never duplicates one), and a delivered
observation of the same signature never speaks twice. The message is data,
not chatter: a localized header (`SNIFF · n new finding(s)`) plus one
`[severity] source — rule → trigger` line per finding.

## Console board

Fourth sidebar group **SENSE** → `#view-sniff`: sources (honest badge, read
count, last-read age, per-minute cap, enable/remove), rules (condition,
severity, `reports` marker), the findings timeline (severity, summary, age,
count, ACK) and controls (SWEEP NOW, PAUSE, digest cap) — plus composers
for a source (kind dropdown: `home` / `web` / `mcp`) and a rule. Deep link:
`/?view=sniff`.

## Boundaries (honest)

- **Read-only by construction**: no service-call or actuation path exists in
  the engine at all — the absence is a test assertion (the same trick the
  exchange withdrawal surface uses). Actuation is a later, approval-gated phase.
- **The mcp lane observes, never drives**: configuration and connection
  status only; a bridge's data reaches P0 through `home`/`web`. See
  [mcp.md](mcp.md).
- **No eval, no regex-driven execution of rule text**; secrets are slot
  names only and every error line is scrubbed and bounded.
- **No new process, no paid tier, no cloud**: everything runs inside the
  existing service loop, and the test suite runs offline against mock
  transports.

## Gates

- `python3 -m mycore.tests.run` — the `sniff:` lane (registry first-error,
  rule parser, findings dedup/caps, hermetic home/web/mcp sweeps, delivery
  seam, the service SENSE leg, and the read-only absence assertions).
- `python3 tests/web_console_p3.py` — the `mycore.sniff` settings partition
  (first-error 400s, slot grammar) and the `GET/POST /api/sniff` board API.
- `PW_PATH=… node tmp/probe_restyle_ui.js` — R27/R28/R29: the SENSE deep
  link, sweep-now wiring, and the per-kind source composer.
- `python3 lib/i18n.py --validate` — locale pack parity for the board keys.
