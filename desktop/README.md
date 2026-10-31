# aidog-desktop -- I'm AiDog desktop shell

An Electron shell around the **local** I'm AiDog web console:

- **Main window** (1280x860) loads the local console at `http://127.0.0.1:8790/`
  (port honors `AIDOG_WEB_PORT`).
- **HUD window** (460x64, frameless, transparent, always-on-top, top-right)
  loads `/static/hud.html` -- opacity / pin / click-through controls.
- **System tray**: open console, show HUD, always-on-top checkbox, quit.
- **Close = hide to tray**; only tray *Quit* exits (and stops a console the
  shell spawned itself -- a pre-existing console is never killed).
- Repo discovery: `AIDOG_REPO` env -> dev checkout next to this app -> cwd and
  its parent -> last-known cache in the Electron userData dir -> honest
  `error.html` page.
- Opacity / always-on-top / click-through over a `contextIsolation` bridge
  (`window.aidogShell`, see `preload.js`).

This package is a **shell only**: it contains no Python and no web assets.
A complete local install is required for full function.

## Run (dev)

```sh
npm start
```

Needs a display. On root / WSL you must pass `--no-sandbox`:

```sh
npx electron . --no-sandbox
```

## Selftest

```sh
npm run selftest          # electron . --selftest --no-sandbox
```

Ensures the console (attach or spawn), opens both windows, asserts the HUD
bridge (`window.aidogShell`) and an IPC opacity roundtrip
(`setOpacity(0.7)` -> `getOpacity() === 0.7`), then prints exactly one line:

```
SELFTEST {"ok":true,"repo":"...","console":"attached","preload":true,"ipc":true}
```

Exit code 0/1, hard cap ~30s. It never kills a console it did not spawn.

## Tests

```sh
npm test                  # node --test test/*.test.mjs (pure fakes, no ports)
```

## Pack (Linux)

```sh
npm run pack:dir          # electron-builder --linux --dir  -> dist/linux-unpacked/
npm run pack:tar          # electron-builder --linux tar.gz -> dist/*.tar.gz
```

Packaged output is still a shell: set `AIDOG_REPO` (or start it from the
install directory) so it can find the local console.

## Honest notes

- **Linux is verified in this environment** (WSLg, Electron 45). Windows and
  macOS were **not built or verified here**.
- No `linux.icon`: the project favicon is 128px (too small for the 256px
  Linux icon requirement), so the packaged app uses the **default Electron
  icon**.
- Re-installing Electron binaries behind a proxy requires
  `ELECTRON_GET_USE_PROXY=1` (plus the usual `GLOBAL_AGENT_HTTPS_PROXY` /
  `HTTPS_PROXY`).
- `setOpacity` support varies by platform; the shell always remembers the
  requested value per window and applies it best-effort.
