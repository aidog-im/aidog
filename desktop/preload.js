'use strict';
// Preload bridge for the I'm AiDog desktop shell.
// Loaded by BOTH the main console window and the HUD window
// (contextIsolation: true, nodeIntegration: false).
//
// Contract (window.aidogShell, all Promise/callback based):
//   isElectron, platform, getOpacity, setOpacity, getAlwaysOnTop,
//   setAlwaysOnTop, getClickThrough, setClickThrough, openConsole,
//   onOpacityChange, onAlwaysOnTopChange, onClickThroughChange
//
// Window targeting (documented behavior):
//   - opacity: applies to the window that owns this preload (main or HUD).
//   - always-on-top: for the MAIN window preload it controls the main
//     window; for the HUD preload it controls the HUD window.
//   - click-through: ALWAYS targets the HUD window (the main window is a
//     normal interactive console and is never click-through).
//   - openConsole: shows + focuses the main window (creating it if hidden).

const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel) {
  return (cb) => {
    if (typeof cb !== 'function') return () => {};
    const listener = (_event, value) => cb(value);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  };
}

contextBridge.exposeInMainWorld('aidogShell', {
  isElectron: true,
  platform: process.platform,

  getOpacity: () => ipcRenderer.invoke('shell:get-opacity'),
  setOpacity: (value) => ipcRenderer.invoke('shell:set-opacity', value),

  getAlwaysOnTop: () => ipcRenderer.invoke('shell:get-always-on-top'),
  setAlwaysOnTop: (flag) => ipcRenderer.invoke('shell:set-always-on-top', !!flag),

  getClickThrough: () => ipcRenderer.invoke('shell:get-click-through'),
  setClickThrough: (flag) => ipcRenderer.invoke('shell:set-click-through', !!flag),

  openConsole: () => ipcRenderer.invoke('shell:open-console'),

  onOpacityChange: subscribe('shell:opacity-changed'),
  onAlwaysOnTopChange: subscribe('shell:always-on-top-changed'),
  onClickThroughChange: subscribe('shell:click-through-changed'),
});
