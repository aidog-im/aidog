'use strict';
// I'm AiDog desktop shell -- Electron entry point.
//
// Flow: resolve local install repo (repo.js) -> ensure local console
// (supervisor.js: attach if healthy, spawn if not) -> main console window
// + HUD control window + system tray.
//
// Honest platform notes:
//   - Frameless transparent windows work on Windows and Linux (Linux needs
//     a compositor; verified here under WSLg). macOS not verified here.
//   - vibrancy is applied only on macOS (process.platform === 'darwin');
//     non-mac platforms fall back to plain alpha transparency.
//   - BrowserWindow.setOpacity is best-effort: the shell always remembers
//     the requested value per window (authoritative for getOpacity) and
//     applies it via setOpacity inside try/catch.

const { app, BrowserWindow, Tray, Menu, ipcMain, screen } = require('electron');
const path = require('path');
const { resolveRepo, baseUrl } = require('./repo');
const { ensureConsole, stopSpawned } = require('./supervisor');

const argv = process.argv.slice(1);
const SELFTEST = argv.includes('--selftest');
const NO_HUD = argv.includes('--no-hud');
const SELFTEST_LOAD_MS = 25000;
const SELFTEST_HARD_MS = 28000;

const state = {
  repo: null,
  base: null,
  console: null, // ensureConsole result: { mode, base, child? }
  mainWin: null,
  hudWin: null,
  tray: null,
  clickThrough: false,
  quitting: false,
  mainLoad: null, // settle-timing promises for selftest
  hudLoad: null,
  selftestTimer: null,
  selftestDone: false,
};

function log(msg) {
  if (SELFTEST) return; // keep selftest stdout to exactly one line
  console.error(`[aidog-desktop] ${msg}`);
}

function isAlive(win) {
  return !!(win && !win.isDestroyed());
}

// ---------------------------------------------------------------------------
// IPC
// ---------------------------------------------------------------------------

function windowOf(event) {
  try {
    return BrowserWindow.fromWebContents(event.sender);
  } catch {
    return null;
  }
}

function send(win, channel, value) {
  if (isAlive(win)) {
    try {
      win.webContents.send(channel, value);
    } catch {
      /* window may be mid-destroy */
    }
  }
}

function clampOpacity(v) {
  const x = Number(v);
  if (!Number.isFinite(x)) return 1.0;
  return Math.min(1.0, Math.max(0.2, x));
}

function setupIpc() {
  // Opacity -- per window (main window and HUD each have their own).
  ipcMain.handle('shell:get-opacity', (event) => {
    const win = windowOf(event);
    if (!win) return 1.0;
    return typeof win._aidogOpacity === 'number' ? win._aidogOpacity : 1.0;
  });
  ipcMain.handle('shell:set-opacity', (event, value) => {
    const win = windowOf(event);
    const x = clampOpacity(value);
    if (win) {
      win._aidogOpacity = x;
      try {
        win.setOpacity(x);
      } catch (err) {
        log(`setOpacity unsupported here: ${err.message}`);
      }
      send(win, 'shell:opacity-changed', x);
    }
    return x;
  });

  // Always on top -- for the MAIN window preload it controls the main window,
  // for the HUD preload it controls the HUD window.
  ipcMain.handle('shell:get-always-on-top', (event) => {
    const win = windowOf(event);
    return isAlive(win) ? !!win.isAlwaysOnTop() : false;
  });
  ipcMain.handle('shell:set-always-on-top', (event, flag) => {
    const win = windowOf(event);
    const on = !!flag;
    if (isAlive(win)) win.setAlwaysOnTop(on);
    syncTrayChecks();
    send(win, 'shell:always-on-top-changed', on);
    return on;
  });

  // Click-through -- ALWAYS targets the HUD window (the main console window
  // stays fully interactive no matter who asks).
  ipcMain.handle('shell:get-click-through', () => state.clickThrough);
  ipcMain.handle('shell:set-click-through', (_event, flag) => {
    state.clickThrough = !!flag;
    if (isAlive(state.hudWin)) {
      try {
        if (state.clickThrough) {
          state.hudWin.setIgnoreMouseEvents(true, { forward: true });
        } else {
          state.hudWin.setIgnoreMouseEvents(false);
        }
      } catch (err) {
        log(`setIgnoreMouseEvents failed: ${err.message}`);
      }
    }
    send(state.mainWin, 'shell:click-through-changed', state.clickThrough);
    send(state.hudWin, 'shell:click-through-changed', state.clickThrough);
    return state.clickThrough;
  });

  // openConsole -- show + focus the main window (create it if missing).
  ipcMain.handle('shell:open-console', () => {
    openConsole();
    return true;
  });
}

function openConsole() {
  if (!isAlive(state.mainWin)) {
    createMainWindow();
  }
  const win = state.mainWin;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

// ---------------------------------------------------------------------------
// Windows
// ---------------------------------------------------------------------------

function commonWebPrefs() {
  return {
    preload: path.join(__dirname, 'preload.js'),
    contextIsolation: true,
    nodeIntegration: false,
  };
}

// Attach the load-tracking listeners BEFORE load starts so a fast
// did-finish-load cannot be missed.
function trackLoad(win, which) {
  const promise = new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error(`${which} window load timeout`));
    }, SELFTEST_LOAD_MS);
    const onDone = () => {
      cleanup();
      resolve();
    };
    const onFail = (_event, code, desc, _url, isMainFrame) => {
      if (isMainFrame === false) return; // subframe failures are not ours
      cleanup();
      reject(new Error(`${which} window did-fail-load ${code} ${desc}`));
    };
    const cleanup = () => {
      clearTimeout(timer);
      win.webContents.removeListener('did-finish-load', onDone);
      win.webContents.removeListener('did-fail-load', onFail);
    };
    win.webContents.on('did-finish-load', onDone);
    win.webContents.on('did-fail-load', onFail);
  });
  if (which === 'main') state.mainLoad = promise;
  else state.hudLoad = promise;
  return promise;
}

function createMainWindow(url) {
  const win = new BrowserWindow({
    width: 1280,
    height: 860,
    show: false,
    backgroundColor: '#fcfcfa',
    webPreferences: commonWebPrefs(),
  });
  win._aidogOpacity = 1.0;
  win.on('close', (event) => {
    // Closing the main window hides it to the tray instead of quitting.
    // State is remembered; reopen from the tray menu.
    if (!state.quitting) {
      event.preventDefault();
      win.hide();
    }
  });
  if (url) trackLoad(win, 'main').catch((err) => log(err.message));
  state.mainWin = win;
  return win;
}

function createHudWindow(url) {
  const win = new BrowserWindow({
    width: 460,
    height: 64,
    frame: false,
    transparent: true,
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    show: false,
    alwaysOnTop: true,
    webPreferences: commonWebPrefs(),
    // vibrancy only affects macOS; every other platform falls back to plain
    // alpha transparency from `transparent: true` (honest, no fake claim).
    ...(process.platform === 'darwin' ? { vibrancy: 'ultra-dark' } : {}),
  });
  win._aidogOpacity = 1.0;
  win.setAlwaysOnTop(true);
  // Default position: top-right of the primary display work area.
  const area = screen.getPrimaryDisplay().workArea;
  win.setBounds({
    x: Math.max(area.x, area.x + area.width - 460 - 16),
    y: area.y + 16,
    width: 460,
    height: 64,
  });
  win.on('close', (event) => {
    if (!state.quitting) {
      event.preventDefault();
      win.hide();
    }
  });
  trackLoad(win, 'hud').catch((err) => log(err.message));
  state.hudWin = win;
  if (url) win.loadURL(url);
  return win;
}

function showErrorPage(detail) {
  const win = createMainWindow();
  win.loadFile(path.join(__dirname, 'error.html')).then(() => {
    if (!detail) return;
    const text = JSON.stringify(String(detail)); // safe JS string literal
    win.webContents
      .executeJavaScript(
        `(() => { const el = document.getElementById('detail'); if (el) el.textContent = ${text}; })()`
      )
      .catch(() => {});
  });
  win.show();
  return win;
}

// ---------------------------------------------------------------------------
// Tray
// ---------------------------------------------------------------------------

function syncTrayChecks() {
  buildTrayMenu();
}

function buildTrayMenu() {
  if (!state.tray) return;
  const hasHud = isAlive(state.hudWin);
  const menu = Menu.buildFromTemplate([
    { label: 'Open console', click: () => openConsole() },
    {
      label: 'HUD controls',
      enabled: hasHud,
      click: () => {
        if (!isAlive(state.hudWin)) return;
        state.hudWin.show();
        state.hudWin.focus();
      },
    },
    { type: 'separator' },
    {
      label: 'Always on top',
      type: 'checkbox',
      checked: isAlive(state.mainWin) ? !!state.mainWin.isAlwaysOnTop() : false,
      click: (item) => {
        const on = !!item.checked;
        if (isAlive(state.mainWin)) state.mainWin.setAlwaysOnTop(on);
        send(state.mainWin, 'shell:always-on-top-changed', on);
      },
    },
    { type: 'separator' },
    { label: 'Quit', click: () => quitApp() },
  ]);
  state.tray.setContextMenu(menu);
}

function createTray() {
  let tray;
  try {
    tray = new Tray(path.join(__dirname, 'assets', 'tray.png'));
  } catch (err) {
    log(`tray unavailable: ${err.message}`);
    return null;
  }
  tray.setToolTip("I'm AiDog desktop shell");
  try {
    tray.on('click', () => openConsole());
  } catch {
    /* click-to-open is not supported on every platform */
  }
  state.tray = tray;
  buildTrayMenu();
  return tray;
}

// ---------------------------------------------------------------------------
// Quit
// ---------------------------------------------------------------------------

function quitApp() {
  state.quitting = true;
  app.quit();
}

function finishSelftest(ok, payload) {
  if (state.selftestDone) return;
  state.selftestDone = true;
  if (state.selftestTimer) clearTimeout(state.selftestTimer);
  process.stdout.write(`SELFTEST ${JSON.stringify(payload)}\n`);
  state.quitting = true;
  stopSpawned(state.console); // only kills a child we spawned; attached = no-op
  app.exit(ok ? 0 : 1);
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  if (SELFTEST) {
    state.selftestTimer = setTimeout(() => {
      finishSelftest(false, { ok: false, stage: 'timeout', error: `>${SELFTEST_HARD_MS}ms` });
    }, SELFTEST_HARD_MS);
  }

  const resolved = resolveRepo({
    env: process.env,
    devDir: path.join(__dirname, '..'),
    cwd: process.cwd(),
    cacheDir: app.getPath('userData'),
  });
  state.repo = resolved.repo;
  state.base = resolved.base;

  if (!state.repo) {
    // No local install known: honest error page; no HUD, tray offers Quit.
    showErrorPage('Local console not found: no web/server.py found near the app, cwd, or AIDOG_REPO.');
    createTray();
    if (SELFTEST) {
      finishSelftest(false, { ok: false, stage: 'repo', repo: null });
    }
    return;
  }

  let consoleState;
  try {
    consoleState = await ensureConsole({ repo: state.repo, base: state.base });
  } catch (err) {
    showErrorPage(err.message);
    createTray();
    if (SELFTEST) {
      finishSelftest(false, { ok: false, stage: 'console', repo: state.repo, error: err.message });
    }
    return;
  }
  state.console = consoleState;
  state.base = consoleState.base;

  const main = createMainWindow();
  const loadMain = trackLoad(main, 'main'); // listeners before loadURL
  main.loadURL(state.base + '/');
  main.once('ready-to-show', () => main.show());

  let loadHud = Promise.resolve();
  if (!NO_HUD || SELFTEST) {
    const hud = createHudWindow(`${state.base}/static/hud.html`);
    loadHud = state.hudLoad;
    void hud;
  }
  createTray();

  if (SELFTEST) {
    try {
      await Promise.all([loadMain, loadHud]); // 25s total load budget
      const preloadOk = await state.hudWin.webContents.executeJavaScript(
        `typeof window.aidogShell === 'object' && window.aidogShell !== null`
      );
      const roundtrip = await state.hudWin.webContents.executeJavaScript(`(async () => {
        await window.aidogShell.setOpacity(0.7);
        return await window.aidogShell.getOpacity();
      })()`);
      const ipcOk = Math.abs(Number(roundtrip) - 0.7) < 1e-6;
      const ok = preloadOk === true && ipcOk;
      finishSelftest(ok, {
        ok,
        repo: state.repo,
        console: consoleState.mode,
        preload: preloadOk === true,
        ipc: ipcOk,
      });
    } catch (err) {
      finishSelftest(false, {
        ok: false,
        stage: 'selftest',
        repo: state.repo,
        console: consoleState.mode,
        error: err.message,
      });
    }
  }
}

app.whenReady().then(() => {
  setupIpc();
  boot().catch((err) => {
    if (SELFTEST) {
      finishSelftest(false, { ok: false, stage: 'boot', error: err.message });
    } else {
      log(`boot failed: ${err.message}`);
      showErrorPage(err.message);
      createTray();
    }
  });
});

// macOS: recreate/focus the console window on dock click.
app.on('activate', () => {
  openConsole();
});

// Windows/Linux: keep running in the tray; nothing to do on all-closed.
app.on('window-all-closed', () => {
  /* hidden-to-tray model -- Quit comes from the tray menu */
});

app.on('will-quit', () => {
  // Kill a spawned child (and only that) on quit too.
  stopSpawned(state.console);
});
