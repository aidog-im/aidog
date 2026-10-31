/* I'm AiDog HUD control bar (desktop shell window) — opacity slider,
   always-on-top pin, click-through crosshair, open-console. Same i18n
   contract as app.js: localStorage `aidog_lang` (default en) -> fetch
   /static/i18n/<lang>.json -> apply [data-i18n] / [data-i18n-ph] /
   [data-i18n-t] / [data-i18n-alt], English defaults already in the HTML.
   No window.aidogShell bridge (plain browser): show the localized notice
   and hide the controls — this page must never throw.
   Note: there is no Quit here — Quit lives in the Electron tray menu
   (no bridge API for it; do not invent one).
   All strings come from locale packs; upstream names: zero. */
"use strict";

const LANG_KEY = "aidog_lang";           // same storage key as app.js
const REQUIRED = [
  "getOpacity", "setOpacity",
  "getAlwaysOnTop", "setAlwaysOnTop",
  "getClickThrough", "setClickThrough",
  "openConsole",
  "onOpacityChange", "onAlwaysOnTopChange", "onClickThroughChange"
];

const shell = (typeof window !== "undefined" && window.aidogShell) || null;
const bridgeOk = !!shell && REQUIRED.every(k => typeof shell[k] === "function");

const elBar = document.getElementById("hudBar");
const elNotice = document.getElementById("hudNotice");
const elSlider = document.getElementById("hudOpacity");
const elVal = document.getElementById("hudVal");
const elPin = document.getElementById("hudPin");
const elPass = document.getElementById("hudPass");
const elOpen = document.getElementById("hudOpen");

let dict = {};
let sliding = false;                     // true while the user drags the slider
                                         // (suppress remote opacity pushes)

function t(key, fallback) { return dict[key] !== undefined ? dict[key] : (fallback || key); }

/* ------------------------------------------------------------------ i18n -- */
function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n, el.textContent);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPh, el.placeholder);
  });
  document.querySelectorAll("[data-i18n-t]").forEach(el => {
    el.title = t(el.dataset.i18nT, el.title);
    if (el.tagName === "BUTTON") el.setAttribute("aria-label", el.title);
  });
  document.querySelectorAll("[data-i18n-alt]").forEach(el => {
    el.alt = t(el.dataset.i18nAlt, el.alt);
  });
}

async function loadLang(lang) {
  try {
    const res = await fetch(`/static/i18n/${lang}.json`);
    dict = await res.json();
  } catch (e) { dict = {}; }
  document.documentElement.lang = lang;
  applyI18n();
}

/* --------------------------------------------------------------- helpers -- */
function setPressed(btn, on) {
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("is-on", !!on);
}

function showNotice(key, fallback) {
  elBar.hidden = true;                   // hide the controls, never throw
  elNotice.dataset.i18n = key;           // fallback keeps working if pack misses
  elNotice.textContent = t(key, fallback);
  elNotice.hidden = false;
}

function setOpacityUI(v) {                // v: 0.2 .. 1.0
  const pct = Math.round(Math.min(1, Math.max(0.2, v)) * 100);
  elSlider.value = String(pct);
  elVal.textContent = pct + "%";
}

function safe(p) { if (p && typeof p.catch === "function") p.catch(() => {}); }

/* ------------------------------------------------------------------ wire -- */
function wire() {
  // slider <-> window opacity (apply on `input` = live while dragging)
  elSlider.addEventListener("pointerdown", () => { sliding = true; });
  elSlider.addEventListener("pointerup", () => { sliding = false; });
  elSlider.addEventListener("keydown", () => { sliding = true; });
  elSlider.addEventListener("change", () => { sliding = false; });
  elSlider.addEventListener("input", () => {
    const v = Number(elSlider.value) / 100;
    setOpacityUI(v);
    safe(shell.setOpacity(v));
  });

  // pin <-> always-on-top (aria-pressed + active class synced from push)
  elPin.addEventListener("click", () => {
    const next = elPin.getAttribute("aria-pressed") !== "true";
    setPressed(elPin, next);
    safe(shell.setAlwaysOnTop(next));
  });

  // crosshair <-> click-through. When active the window ignores mouse
  // events, so the Electron main process forwards them for this button —
  // keep the logic symmetric with the pin toggle.
  elPass.addEventListener("click", () => {
    const next = elPass.getAttribute("aria-pressed") !== "true";
    setPressed(elPass, next);
    safe(shell.setClickThrough(next));
  });

  elOpen.addEventListener("click", () => safe(shell.openConsole()));

  // push callbacks -> UI
  shell.onOpacityChange(v => { if (!sliding) setOpacityUI(Number(v)); });
  shell.onAlwaysOnTopChange(b => setPressed(elPin, b));
  shell.onClickThroughChange(b => setPressed(elPass, b));

  // initial state pulls
  safe(shell.getOpacity().then(v => setOpacityUI(Number(v))));
  safe(shell.getAlwaysOnTop().then(b => setPressed(elPin, b)));
  safe(shell.getClickThrough().then(b => setPressed(elPass, b)));
}

/* ------------------------------------------------------------------ boot -- */
(function boot() {
  const lang = localStorage.getItem(LANG_KEY) || "en";
  loadLang(lang);
  if (!bridgeOk) {
    // plain browser (hud.unsupported) vs. a shell whose bridge is partial
    // (hud.bridgeMissing) — both are localized, controls hidden either way.
    showNotice(shell ? "hud.bridgeMissing" : "hud.unsupported",
               shell ? "Desktop shell bridge unavailable."
                     : "Open this page from the desktop shell to use HUD controls.");
    return;
  }
  wire();
})();
