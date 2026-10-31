# Browser Automation

Status: shipped in car 4b (`mycore/browser.py`; the six tools register in
`mycore/tools.py::build_registry`, availability is also reported by
`GET /api/capabilities` — see [capabilities.md](capabilities.md)).

Six agent tools drive **one local headless Chromium/Chrome session** over the
Chrome DevTools Protocol. The whole path is stdlib-only: a minimal RFC 6455
WebSocket client we wrote ourselves — no Playwright/Selenium Python package,
no hosted browser service. Everything runs on this machine.

## Tools

| Tool | Arguments | Answer |
|---|---|---|
| `browser_open` | `url` | `loaded <url> · title=…` — starts the session on demand |
| `browser_snapshot` | — | page url/title/text plus interactive elements as `@eN` refs |
| `browser_click` | `ref` | `clicked @eN [tag] "label"` |
| `browser_type` | `ref`, `text`, `submit?` | `typed N chars into @eN [tag]` (+ ` + Enter` when `submit`) |
| `browser_screenshot` | — | absolute path of a PNG in `data/browser/` + byte size |
| `browser_close` | — | `browser closed (process and temp profile removed)` |

Every tool except `browser_open` refuses honestly when no session exists
(`no browser session — call browser_open first`) — nothing ever auto-starts a
browser behind the operator's back.

## Positional refs (`@eN`)

`browser_snapshot` lists interactive elements (links, buttons, inputs,
selects, ARIA roles, `onclick`, …) as **positional** refs: `@e1`, `@e2`, …

- **Refs are positions, not stable IDs.** Any DOM change (navigation, click,
  dynamic content) can reorder them — **re-snapshot after every DOM change**.
  Both the tool description and the snapshot header say so, and a stale ref
  fails loudly: `ref not found, re-snapshot (@eN does not exist in the
  current DOM — refs are positional)`.
- Ref parsing accepts `@eN`, `eN`, and a bare number; `@e0`, non-numeric and
  non-string refs are refused.
- Snapshot budget: page text cut at 8000 chars in-page, at most 200
  interactive items listed (the omitted remainder is counted explicitly), and
  the whole answer capped at **12000 chars** with a visible truncation line.

## Binary discovery (capability honesty)

| Order | Source | Behavior |
|---|---|---|
| 1 | `AIDOG_BROWSER_BIN` | **Authoritative.** When set, only this value is accepted; a set-but-missing/non-executable path is a **hard, honest failure** — never silently ignored, never falls through to the next source |
| 2 | Playwright cache glob | `$XDG_CACHE_HOME` (default `~/.cache`)`/ms-playwright/chromium-*/chrome-linux64/chrome`, newest build first — a *cache directory* only; the Python package is not required |
| 3 | `PATH` | `chrome`, `chromium`, `chromium-browser`, `google-chrome`, `google-chrome-stable` |

- `browser.available()` runs **discovery only — it never launches anything**,
  so `build_registry()` decides in milliseconds.
- **No binary → the six tools are not registered** (no fake capability);
  `registry.browser_note` carries the reason (`browser binary not found`) and
  `GET /api/capabilities` reports `browser.available: false`. The hidden tool
  name dispatches as an honest unknown-tool error.
- With a binary, launch is direct argv: `--headless --no-sandbox …` plus a
  throwaway temp profile and a free `--remote-debugging-port`; readiness is
  polled on `GET /json/version` (25 s cap — never `/json/new`, which modern
  Chrome requires PUT for), then `Target.attachToTarget` (flatten) + `Page.enable`.

## Session lifecycle

- **Singleton** behind a thread lock: one browser, one page session.
- **300 s inactivity watchdog** (15 s ticks) closes the session and deletes
  the temp profile; `atexit` cleanup covers process exit; `browser_close`
  removes both immediately.
- All CDP calls carry timeouts (startup 25 s, navigation/click settle 30 s).
  Every runtime failure is a `ToolError` carrying the **real** cause — status
  line, exception type, exit code, stderr tail — never a silent fake result.
- `browser_screenshot` writes `data/browser/shot-<ts>-<pid>-<seq>.png`
  (inside gitignored `data/`) and returns the absolute path + size; an empty
  file is an error, not a "success".

## URL policy

`http://` and `https://` only. `chrome:`, `chrome-extension:`, `about:`,
`file:`, `data:`, `javascript:`, `blob:`, `devtools:`, `view-source:`,
scheme-less and host-less URLs are refused **with the reason** (those schemes
reach browser UI or local files). The DevTools WebSocket itself only ever
connects to `127.0.0.1` / `localhost` / `::1`, `ws://` only.

## Security and honest boundaries

- **Fail-closed capability**: discovery failure *removes* the tools instead
  of degrading into broken tools; runtime failures surface as ERROR lines
  with the true cause.
- **No credentials, no accounts**: the session runs on a fresh temp profile
  (no user cookies, no sign-in), and the tool layer never passes any
  `secrets.env` value into the browser — the product defines no browser
  credential slot at all.
- **Local CDP only**: hosted/remote browser services (cloud CDP grids,
  browser-as-a-API vendors) are **structurally not adopted** — no adapter, no
  slot, nothing half-wired. Stated explicitly so silence is never read as
  support (owner decision, car 4b).
- **No shell in the tool path**: navigation, clicks and typing go over our
  own WebSocket/CDP frames; the browser process is launched directly as argv.

## Testing evidence

- `mycore/tests/run.py::test_browser` — **22 offline checks** (this section
  never launches a browser): URL policy (all blocked schemes/urls + malformed
  inputs), `@eN` ref parsing (valid and refused forms), env-authoritative
  discovery (dead `AIDOG_BROWSER_BIN` → unavailable + no tools + note set;
  valid override → all six registered, no note), and registry ↔ default
  discovery agreement on both host shapes (browser present/absent).
- `tests/web_console_p3.py` — the `browser` catalog block (availability bool
  only) and its consistency with the live registered tool list, inside the web
  smoke.
- Real CDP wire (launch → open → snapshot → click → screenshot → close, plus
  the missing-binary branch) lives in the repo-local E2E script
  `tmp/e2e_car4b.py` (tmp harness, outside the gate suites).

Suite totals after car 4c: core **959/959**, smoke **142/142**.
