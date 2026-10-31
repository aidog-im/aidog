# Locale Packs (i18n)

Single source of truth for every user-visible string in the product —
Telegram service replies (`/help`, `/reset`, `/model`, `/status`, `/lang`,
honest error lines) and web console UI. Source code never hard-codes a UI
string (AGENTS.md → Internationalization Policy).

## Format: JSON (decision)

The product is **stdlib only, zero third-party dependencies** — no PyYAML.
Every pack is therefore `overlay/locales/<lang>.json` (a flat
`key -> string` object). This replaces the transition-era plan of aligning
with an upstream `locales/*.yaml` overlay format; that plan is retired.

## Files

- `en.json` — **default locale** (international route)
- `zh.json` — Chinese (switchable at runtime)
- **New language = drop one `<lang>.json` next to these. Zero code changes.**

The filename stem is the language code (lowercase `[a-z][a-z0-9_-]{0,15}`,
e.g. `en`, `zh`, `de`). `lib/i18n.py` discovers packs by glob, so a new file
is picked up by every consumer (TG service, web console, lint) on next load.

## Keys vs values

- **Keys are English, dot-separated, and never translated**:
  `cmd.help.title`, `app.title`, `voice.state.speaking`.
- **Only values are localized.** Adding a language = writing value entries
  under the existing English keys.
- Placeholders (`{model}`, `{lang}`, `{n}`) must survive translation — a
  malformed placeholder degrades to the English template at runtime
  (`lib/i18n.py::fmt`), never to a broken reply.

## Resolution order

1. explicit user setting
   - TG service: `<state>/lang.json` (written by `/lang`)
   - web console: `localStorage` > web settings `language`
2. default `en`

Missing keys fall back **per key** to `en`, then to the caller's English
fallback; every fallback is recorded (`lib/i18n.py::missing()`) so a short
pack is diagnosable instead of silently shipping English.

## Switching languages

- **Telegram service**: `/lang` shows the current language and the available
  codes; `/lang <code>` switches it service-wide and persists
  `{"lang": "<code>"}` to `data/mycore_service/lang.json`. An unknown code
  changes nothing and answers with the honest available list; an absent or
  invalid settings file falls back to `en` (fail-soft, logged once).
- **Web console**: the header toggle (key `ctl.langBtn`), which fetches
  `/static/i18n/<code>.json` — served straight from this directory (the
  console keeps no static copy of a pack).

## Validation (regression wall)

```bash
python3 lib/i18n.py --validate       # key-set parity across packs; 1 = drift
python3 tests/source_lang_check.py   # parity + no CJK in product source; 1 = fail
```
