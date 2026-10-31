# Desktop Client (Electron shell)

Status: shipped in car E (`desktop/`, v0.5.30). This page documents the
desktop shell only — the console it windows into is unchanged
([console_chat.md](console_chat.md)). All facts below were verified on this
machine (Linux/WSLg) on 2026-10-23; platforms we did not exercise are marked
as such, never claimed.

## 1. Purpose

A thin native window around the **local** web console, plus a transparent,
always-on-top HUD strip — the desktop half of the client architecture
proposed in `docs/research/aidog_voice_and_clients.md` §C. The shell adds no
product logic of its own: no model calls, no data stores, no endpoints. It is
built entirely with free, open-source tooling (free-for-life rule: no paid
tier of any kind is touched — no auto-update service, no telemetry).

## 2. Architecture

```
  Electron shell  (desktop/main.js)
  +--------------------------------------------------------------+
  | main window 1280x860  -->  http://127.0.0.1:8790/  (console) |
  | HUD window 460x64     -->  /static/hud.html (frameless,      |
  |                            transparent, always-on-top)       |
  | tray: open console / show HUD / pin / quit (only tray quits) |
  | preload.js --> window.aidogShell bridge (contextIsolation)   |
  +-----------------------------+--------------------------------+
                                | ensure console: probe /api/health
                                v
  +--------------------------------------------------------------+
  | console supervisor  (desktop/supervisor.js)                  |
  |   attach = a console is already up  -> leave it alone        |
  |   spawn  = python3 server.py in {repo}/web (ready-poll ~10s) |
  |   stop   = kill ONLY the child we spawned, on exit           |
  +-----------------------------+--------------------------------+
                                v
  +--------------------------------------------------------------+
  | local console server  (web/server.py)  :8790                 |
  +--------------------------------------------------------------+

  repo lookup (desktop/repo.js):
  AIDOG_REPO env -> dev-relative (dirname/..) -> cwd and cwd parent ->
  last-known cache in Electron userData -> miss: desktop/error.html
```

## 3. File map

| Path | Role |
|---|---|
| `desktop/main.js` | main process: repo resolution, supervisor wiring, main + HUD windows, tray, IPC, `--selftest` |
| `desktop/supervisor.js` | attach / spawn / stop-only-own; injectable fetch/spawn/port so tests never touch real ports |
| `desktop/repo.js` | candidate-order repo resolution with injectable fs/env |
| `desktop/preload.js` | `contextIsolation` bridge — `window.aidogShell` (opacity / pin / click-through / openConsole + change callbacks) |
| `desktop/error.html` | honest English guidance page shown when no repo is found |
| `desktop/assets/tray.png` | tray icon (128×128 png) |
| `desktop/test/*.test.mjs` | `node --test` — 15 checks (repo 7 + supervisor 8), all fakes, zero real ports |
| `desktop/package.json` | scripts + electron-builder `build` section |
| `desktop/README.md` | dev / selftest / pack quick reference |
| `web/static/hud.html` + `hud.js` | HUD control strip (console side, same-origin i18n) |
| `desktop/dist/` | build output (gitignored, never published) |

## 4. Commands

| Command | Result |
|---|---|
| `cd desktop && npx electron . --no-sandbox` | run the shell (root/WSL needs `--no-sandbox`) |
| `cd desktop && npm test` | `node --test` → **15/15** (repo 7 + supervisor 8; fakes, no ports) |
| `cd desktop && npx electron . --selftest --no-sandbox` | end-to-end selftest → one line `SELFTEST {"ok":true,...}` + rc 0 |
| `cd desktop && npx electron-builder --linux dir tar.gz` | package → `desktop/dist/linux-unpacked/aidog-desktop` + `desktop/dist/aidog-desktop-0.5.30.tar.gz` (≈114 MB) |

Selftest flow: ensure console (attach or spawn) → open both windows → assert
the HUD bridge exists and an IPC opacity roundtrip works
(`setOpacity(0.7)` → `getOpacity() === 0.7`) → print one JSON line → exit
(~30 s hard cap). It never touches a console it did not spawn itself.

## 5. Behavior matrix (verified 2026-10-23)

| Mode | Trigger | Observed result |
|---|---|---|
| **attach** | console already serving on 8790 | probe succeeds → `console:"attached"`; that server is left running on exit |
| **spawn** | no server, repo found (`AIDOG_WEB_PORT=18790` trial) | spawns `python3 server.py` in `{repo}/web` → `console:"spawned"`; child killed on exit — no leak |
| **no repo** | fresh `XDG_CONFIG_HOME`, cwd outside the repo, no `AIDOG_REPO` | `SELFTEST {"ok":false,"stage":"repo","repo":null}` rc=1; `error.html` shown with honest guidance |

## 6. Platform support & verification

| Platform | Built | Verified | Notes |
|---|---|---|---|
| **Linux (WSLg, `DISPLAY=:0`)** | ✅ `dir` + `tar.gz` | ✅ both the unpacked binary and the tar-extracted binary run `--selftest --no-sandbox` green | transparency needs a compositor; WSLg confirmed working |
| **Windows** | ❌ not built | ❌ not verified here | plain alpha transparency only (`setOpacity`), no `vibrancy` |
| **macOS** | ❌ not built | ❌ not verified here | `vibrancy` is set only on `darwin` (commented in `main.js`); never exercised here |

Transparency mechanics (as shipped): frameless window + `transparent:true`
(unfilled area is a hole) + `setOpacity(0.2–1.0)` — best-effort per platform
(try/catch; the per-window JS value is the `getOpacity` authority).
`vibrancy` is applied **only on darwin**; Linux/Windows fall back to plain
alpha transparency. Click-through uses
`setIgnoreMouseEvents(true, {forward:true})`. Always-on-top is
verified-at-implementation (tray menu + HUD window).

## 7. Locale / i18n

The HUD is served same-origin by the console and reads
`/static/i18n/{lang}.json` (localStorage `aidog_lang`, `en` fallback,
`data-i18n` pattern like `app.js`). Car E added **8 `hud.*` keys** in both
`en` and `zh` — packs are now **285/285**. Without the bridge
(`!window.aidogShell`) the HUD shows an "unsupported" hint and hides its
controls, so opening `hud.html` in a plain browser does not break.

## 8. Honest limitations

- **Shell-only package.** The build bundles no Python and no web assets. It
  needs a local install — `AIDOG_REPO` set, or cwd started near the install —
  otherwise it shows `error.html`. Full function = shell + local console.
- **Default icon.** The tray png is 128×128; electron-builder wants ≥256 for
  `linux.icon`, so no `linux.icon` is set and the package ships the default
  Electron icon. Cosmetic, stated rather than hidden.
- **No signing / notarization.** Distribution artifacts are local build
  products; nothing is code-signed or notarized for Windows/macOS stores.
- **No auto-update, no telemetry** added by the shell.
- **Windows/macOS: not built, not verified** in this environment — see §6.
- **Quit lives in the tray menu only** (closing the main window hides to
  tray). Deliberate: no quit button in the HUD page, and no quit bridge API
  was invented for it.
- **Electron reinstall behind a proxy:** binary download needs
  `ELECTRON_GET_USE_PROXY=1 GLOBAL_AGENT_HTTPS_PROXY=$https_proxy node node_modules/electron/install.js`.
