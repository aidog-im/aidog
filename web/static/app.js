/* I'm AiDog console — HUD logic (status ring, reactor, advisory takeover,
   typewriter stream, settings writes with access token, update banner).
   All user-facing strings come from locale packs; upstream names: zero. */
"use strict";

const LANG_KEY = "aidog_lang";
const TOKEN_KEY = "aidog_token";
const params = new URLSearchParams(location.search);
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

let dict = {};
let settings = { refresh_sec: 10, sound: false, voice_volume: 80, alert_preview: "off" };
let statusData = null, usageData = null;
let ringBuilt = false;
let bootDone = false, audioCtx = null;
let voiceReady = false, voiceBusy = false, voiceEngine = null, voiceError = "";
let voiceLastText = "", voiceLastAt = 0;

async function refreshVoiceCap() {   // capability at load (#4: not only on tap)
  try { voiceEngine = await fetch("/api/voice").then(r => r.json()); }
  catch (e) { voiceEngine = { available: false, engine: null }; }
  renderVoice();
}
let lastAdvLevel = null, dismissedLevel = null;
let typedKeys = new Set(), typedT = null, typingNow = false;
const evNodes = new Map();

function t(key, fallback) { return dict[key] !== undefined ? dict[key] : (fallback || key); }

/* ------------------------------------------------------------------ i18n -- */
async function loadLang(lang) {
  try {
    const res = await fetch(`/static/i18n/${lang}.json`);
    dict = await res.json();
  } catch (e) { dict = {}; }
  document.documentElement.lang = lang;
  const btn = document.getElementById("langBtn");
  if (btn) btn.textContent = t("ctl.langBtn", lang === "en" ? "ZH" : "EN");
  document.querySelectorAll("[data-i18n]").forEach(el => {
    el.textContent = t(el.dataset.i18n, el.textContent);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach(el => {
    el.placeholder = t(el.dataset.i18nPh, el.placeholder);
  });
  document.querySelectorAll("[data-i18n-t]").forEach(el => {
    el.title = t(el.dataset.i18nT, el.title);
  });
  document.querySelectorAll("[data-i18n-alt]").forEach(el => {
    el.alt = t(el.dataset.i18nAlt, el.alt);
  });
  applyStaticText();
  renderStatus(); renderUsage(); renderVersion(); renderVoice();
  refreshEventTexts();
}

function applyStaticText() {
  const set = (id, key, fb) => { const el = document.getElementById(id);
    if (el) el.textContent = t(key, fb); };
  set("title", "app.title", "I'M AIDOG");
  set("subtitle", "app.subtitle", "situational console");
  set("footnote", "app.footnote", "loopback · free forever");
}

/* ------------------------------------------------------------------ boot -- */
const BOOT_LINES = [
  ["> aidog kernel ......... ", "ok"],
  ["> memory core .......... ", "ok"],
  ["> status ring .......... ", "ok"],
  ["> advisory protocol ..... ", "ok"],
  ["> channel link ......... ", "warn"],
  ["> SYSTEM ONLINE", "ok"],
];

function runBoot() {
  const boot = document.getElementById("boot");
  const log = document.getElementById("bootLog");
  const fill = document.getElementById("bootFill");
  const finish = () => { if (bootDone) return; bootDone = true;
    boot.classList.add("done"); setTimeout(() => boot.remove(), 600); };
  const skip = params.has("skipboot") || reduced || sessionStorage.getItem("aidog_booted");
  if (skip) { sessionStorage.setItem("aidog_booted", "1"); boot.remove(); bootDone = true; return; }
  document.getElementById("bootSkip").addEventListener("click", finish);
  let i = 0;
  const step = () => {
    if (i >= BOOT_LINES.length) {
      sessionStorage.setItem("aidog_booted", "1");
      setTimeout(finish, 420); return;
    }
    const [txt, cls] = BOOT_LINES[i];
    const line = document.createElement("span");
    line.className = cls; line.textContent = txt;
    log.appendChild(line);
    let c = 0;
    const type = setInterval(() => {
      line.textContent = txt.slice(0, ++c);
      if (c >= txt.length) {
        clearInterval(type);
        if (txt.endsWith(" ")) {  // dotted label lines get a status badge
          const badge = document.createElement("span");
          badge.className = cls; badge.textContent = cls === "ok" ? "ONLINE\n" : "LINKED\n";
          line.after(badge);
        }
        fill.style.width = Math.round(((i + 1) / BOOT_LINES.length) * 100) + "%";
        i++; setTimeout(step, 120);
      }
    }, 14);
  };
  step();
}

/* ----------------------------------------------------------------- sound -- */
function beep(freq = 760, dur = 0.07) {
  if (!settings.sound) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.frequency.value = freq; o.type = "square";
    g.gain.setValueAtTime(0.04, audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + dur);
    o.connect(g).connect(audioCtx.destination);
    o.start(); o.stop(audioCtx.currentTime + dur);
  } catch (e) { /* autoplay policy: silent */ }
}

/* ---------------------------------------------------------------- voice --
   Phase ② owner-approved pilot: spoken advisory lines. Playback order:
   server edge-tts (/api/say) -> local speechSynthesis fallback -> give up
   honestly (orb shows the failure state, never pretends to have spoken).
   Autoplay policy: playback only arms after a user gesture (orb tap). */

function voiceVolume() {
  const v = parseInt(settings.voice_volume, 10);
  return Math.max(0, Math.min(100, isNaN(v) ? 80 : v)) / 100;
}

function renderVoice() {
  const orb = document.getElementById("voiceOrb");
  if (!orb) return;
  const st = document.getElementById("voiceState");
  const sub = document.getElementById("voiceSub");
  orb.classList.remove("locked", "armed", "speaking", "offline", "muted");
  st.classList.remove("warn", "err");
  const engineName = voiceEngine && voiceEngine.available ? voiceEngine.engine : null;
  let key, cls, subText;
  if (voiceBusy) {
    cls = "speaking"; key = "voice.state.speaking";
    subText = t("voice.state.speakingSub", "synthesizing…");
  } else if (!voiceReady) {
    cls = "locked"; key = "voice.state.locked";
    subText = t("voice.state.lockedSub", "tap the orb to unlock voice");
  } else if (engineName === null && voiceEngine) {
    cls = "offline"; key = "voice.state.offline"; st.classList.add("warn");
    subText = t("voice.state.offlineSub", "engine offline — local voice fallback");
  } else if (!settings.sound) {
    cls = "muted"; key = "voice.state.muted"; st.classList.add("warn");
    subText = t("voice.state.mutedSub", "auto announcements off (UI sound)");
  } else {
    cls = "armed"; key = "voice.state.armed";
    subText = engineName
      ? t("voice.state.armedSub", "engine: {e}").replace("{e}", engineName)
      : t("voice.state.armedLocal", "local voice ready");
  }
  if (voiceError) {          // honest one-shot failure notice (then reset)
    cls = cls === "speaking" ? cls : "offline";
    st.classList.add("err");
    subText = voiceError;
    voiceError = "";
  }
  orb.classList.add(cls);
  orb.setAttribute("aria-pressed", String(voiceReady));
  st.textContent = t(key, key.split(".").pop().toUpperCase());
  // engine label on EVERY state (#4 owner feedback): probing / name / absent
  const eng = voiceEngine
    ? (voiceEngine.available ? voiceEngine.engine
                             : t("voice.engine.absent", "engine absent"))
    : t("voice.state.probing", "probing engine…");
  sub.textContent = subText + " · " + eng;
  orb.title = t("voice.tip", "tap to arm voice");
}

async function playBuf(buf) {
  const url = URL.createObjectURL(new Blob([buf], { type: "audio/mpeg" }));
  try {
    await new Promise((resolve, reject) => {
      const a = new Audio(url);
      a.volume = voiceVolume();
      a.onended = resolve;
      a.onerror = () => reject(new Error("audio decode/play failed"));
      a.play().catch(reject);
    });
  } finally { URL.revokeObjectURL(url); }
}

function speakLocal(text) {   // zero-dependency fallback (browser TTS)
  return new Promise((resolve) => {
    try {
      if (!("speechSynthesis" in window)) return resolve(false);
      const u = new SpeechSynthesisUtterance(text);
      u.lang = document.documentElement.lang === "zh" ? "zh-CN" : "en-GB";
      u.volume = voiceVolume();
      u.onend = () => resolve(true);
      u.onerror = () => resolve(false);
      speechSynthesis.cancel();
      speechSynthesis.speak(u);
    } catch (e) { resolve(false); }
  });
}

async function speak(text) {
  if (!text || voiceBusy) return false;      // one utterance at a time
  const now = Date.now();
  if (text === voiceLastText && now - voiceLastAt < 8000) return false;  // anti-repetition
  voiceLastText = text; voiceLastAt = now;
  voiceBusy = true; renderVoice();
  try {
    const res = await fetch("/api/say", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 200), lang: document.documentElement.lang }),
    });
    if (res.ok) { await playBuf(await res.arrayBuffer()); return true; }
    const body = await res.json().catch(() => ({}));
    if (body.error) voiceError = String(body.error).slice(0, 80);
    return await speakLocal(text);           // engine down/rate-limited: go local
  } catch (e) {
    return await speakLocal(text);           // network/endpoint failure: go local
  } finally {
    voiceBusy = false; renderVoice();
  }
}

async function armVoice() {
  voiceReady = true;                          // must happen inside a user gesture
  if (!voiceEngine) await refreshVoiceCap();
  renderVoice();
  speak(t("voice.greet", "Voice channel online."));
}

/* ------------------------------------------------------------------ ring -- */
const RING_R = 168, CX = 210, CY = 210;

function buildRing() {
  const nodes = document.getElementById("ringNodes");
  const ticks = document.getElementById("ringTicks");
  nodes.innerHTML = ""; ticks.innerHTML = "";
  for (let k = 0; k < 24; k++) {
    const a = (k / 24) * Math.PI * 2 - Math.PI / 2;
    const r1 = k % 6 === 0 ? 156 : 162, r2 = 172;
    const l = document.createElementNS("http://www.w3.org/2000/svg", "line");
    l.setAttribute("x1", CX + r1 * Math.cos(a)); l.setAttribute("y1", CY + r1 * Math.sin(a));
    l.setAttribute("x2", CX + r2 * Math.cos(a)); l.setAttribute("y2", CY + r2 * Math.sin(a));
    l.setAttribute("class", "tickmark"); ticks.appendChild(l);
  }
  (statusData && statusData.panels ? statusData.panels : []).forEach((p, idx) => {
    const a = (idx / 5) * Math.PI * 2 - Math.PI / 2;
    const x = CX + RING_R * Math.cos(a), y = CY + RING_R * Math.sin(a);
    const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
    g.setAttribute("class", "node-g"); g.dataset.id = p.id;
    const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    ring.setAttribute("cx", x); ring.setAttribute("cy", y); ring.setAttribute("r", 13);
    ring.setAttribute("class", "node-ring");
    const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y); dot.setAttribute("r", 5);
    dot.setAttribute("class", "node-dot");
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    const lr = RING_R + 26;
    // clamp horizontal drift so edge labels never clip the viewBox
    label.setAttribute("x", Math.max(44, Math.min(376, CX + lr * Math.cos(a))));
    label.setAttribute("y", CY + lr * Math.sin(a) + 4);
    label.setAttribute("class", "node-label");
    label.textContent = p.id.toUpperCase();
    g.append(ring, dot, label); nodes.appendChild(g);
  });
}

/* ---------------------------------------------------------------- status -- */
function renderStatus() {
  if (!statusData) return;
  const panels = statusData.panels || [];
  if (!ringBuilt && panels.length) { buildRing(); ringBuilt = true; }
  const adv = statusData.advisory || { level: "info", reasons: [] };

  // ring node states
  panels.forEach(p => {
    const g = document.querySelector(`.node-g[data-id="${p.id}"]`);
    if (!g) return;
    g.classList.remove("up", "down", "unknown"); g.classList.add(p.state);
    g.querySelector(".node-dot").classList.toggle("down", p.state === "down");
    g.querySelector(".node-dot").classList.toggle("unknown", p.state === "unknown");
  });
  const up = panels.filter(p => p.state === "up").length;
  document.getElementById("ringState").textContent =
    up === panels.length ? t("ring.all", "ALL SYSTEMS NOMINAL")
                         : t("ring.degraded", "SYSTEM DEGRADED");
  document.getElementById("ringSub").textContent =
    t("ring.sub", "{n}/5 ONLINE").replace("{n}", up) + " · " +
    (statusData.version || "");

  // advisory chip + ticker
  const chip = document.getElementById("advChip");
  chip.className = "chip adv-" + adv.level;
  chip.textContent = t("adv." + adv.level, adv.level.toUpperCase());
  const ticker = document.getElementById("ticker");
  ticker.className = "ticker " + adv.level;
  const tickText = document.getElementById("tickerText");
  if (adv.level === "info") {
    tickText.textContent = t("ticker.ok", "All monitored systems nominal — advisory protocol idle.");
  } else {
    tickText.textContent = t("ticker." + adv.level,
      adv.level === "urgent" ? "Intervention required." : "Attention advised.");
  }
  document.getElementById("reasonList").innerHTML =
    (adv.reasons || []).map(reasonHtml).join("<br>");

  // uptime / reboot chip
  const sys = statusData.system || {};
  const boot = document.getElementById("uptimeChip");
  if (sys.boot_ts) {
    const d = new Date(sys.boot_ts * 1000);
    const hh = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    const upH = Math.floor(sys.uptime_sec / 3600), upM = Math.floor((sys.uptime_sec % 3600) / 60);
    boot.textContent = t("head.boot", "BOOT") + " " + hh + " · UP " + upH + "h" +
      String(upM).padStart(2, "0") + "m";
    if ((sys.reboots || []).length) {
      boot.style.color = "var(--amber)"; boot.style.borderColor = "rgba(255,176,32,.6)";
      boot.title = t("head.rebootSeen", "reboot detected (see stream)");
    }
  }

  // mini panels
  document.getElementById("panels").innerHTML = panels.map(p => `
    <div class="mini ${p.state}">
      <div class="m-name">${escapeHtml(t("panel." + p.id, p.id))}</div>
      <div class="m-state">${escapeHtml(t("state." + p.state, p.state.toUpperCase()))}</div>
      <div class="m-detail">${escapeHtml(p.detail || "")}</div>
    </div>`).join("");

  // memory gauges
  const mem = statusData.memory || {};
  const pct = (v, l) => Math.min(100, Math.round((v / l) * 100));
  document.getElementById("memBar").style.width = pct(mem.curated_chars || 0, mem.curated_limit || 2200) + "%";
  document.getElementById("memNum").textContent = `${mem.curated_chars || 0}/${mem.curated_limit || 0}`;
  document.getElementById("userBar").style.width = pct(mem.user_chars || 0, mem.user_limit || 1375) + "%";
  document.getElementById("userNum").textContent = `${mem.user_chars || 0}/${mem.user_limit || 0}`;

  // channel table
  const cfg = statusData.config || {};
  const rows = [
    [t("config.provider", "Provider"), cfg.model_provider],
    [t("config.endpoint", "Endpoint"), cfg.model_base_url],
    [t("config.model", "Model"), cfg.model_default],
    [t("config.fallback", "Fallback chain"), cfg.fallback_enabled ? t("common.on", "enabled") : t("common.off", "off")],
    [t("config.telemetry", "Telemetry"), cfg.telemetry_off ? t("config.telemetry_off", "disabled") : "⚠ CHECK"],
    [t("config.skills", "External skills"), (cfg.external_skill_dirs || []).length],
  ];
  document.getElementById("cfgTable").innerHTML = rows.map(([k, v]) =>
    `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v == null ? "—" : String(v))}</td></tr>`).join("");

  // audio on level escalation (beep) + spoken line (Phase ② voice;
  // spoken only when armed and UI sound is on — owner: sound switch reused)
  if (lastAdvLevel && lastAdvLevel !== adv.level) {
    beep(adv.level === "urgent" ? 420 : adv.level === "warn" ? 620 : 880, 0.1);
    if (voiceReady && settings.sound && !voiceBusy) {
      const line = adv.level === "info" ? t("ticker.ok", "All systems nominal.")
        : adv.level === "urgent" ? t("ticker.urgent", "Intervention required.")
        : t("ticker.warn", "Attention advised.");
      speak(line);
    }
  }
  lastAdvLevel = adv.level;

  applyAlert(adv);
}

function applyAlert(adv) {
  const forced = (params.get("alert") || settings.alert_preview || "off").toLowerCase();
  const isPreview = forced === "urgent" || forced === "warn";
  const effective = isPreview ? forced : adv.level;
  const layer = document.getElementById("alertLayer");
  const show = effective === "urgent" && dismissedLevel !== forced + "|" + (adv.reasons || []).join(";");
  const newlyShown = show && layer.hidden;   // speak once per takeover, not per poll
  layer.hidden = !show;
  document.getElementById("previewTag").hidden = !isPreview;
  if (show) {
    const reasons = (adv.reasons || []);
    document.getElementById("alertReasons").innerHTML =
      (reasons.length ? reasons : [t("alert.previewReason", "forced preview state")])
        .map(reasonLi).join("");
    beep(420, 0.12);
    if (newlyShown && voiceReady && settings.sound) {
      speak(t("alert.speak", "Urgent. Intervention required."));
    }
  }
}

document.addEventListener("keydown", e => {
  if (e.key === "Escape") {
    const adv = (statusData && statusData.advisory) || { reasons: [] };
    const forced = (params.get("alert") || settings.alert_preview || "off").toLowerCase();
    dismissedLevel = forced + "|" + (adv.reasons || []).join(";");
    document.getElementById("alertLayer").hidden = true;
  }
});
document.addEventListener("click", e => {
  const layer = document.getElementById("alertLayer");
  if (!layer.hidden && (layer.contains(e.target)) && !e.target.closest(".alert-box") === false) {
    layer.hidden = true;
  } else if (!layer.hidden && e.target === layer) {
    layer.hidden = true;
  }
});

/* ---------------------------------------------------------------- reactor -- */
/* --------------------------------------------------------------- usage -- */
function dispInfo() {
  // cost is computed in source CNY; display converts per /api/usage `display`.
  // Honest fallback: FX unavailable -> show source CNY + rate-unavailable note.
  const d = (usageData && usageData.display) || null;
  const rate = d && typeof d.rate_cny_per_unit === "number" && d.rate_cny_per_unit > 0
    ? d.rate_cny_per_unit : null;
  if (!d) return { sym: "¥", code: "CNY", rate: 1, fxOff: false };
  if (rate) return { sym: d.symbol, code: d.currency, rate, fxOff: false };
  return { sym: "¥", code: "CNY", rate: 1, fxOff: d.currency !== "CNY" };
}
function fmtCost(cny) {
  const d = dispInfo();
  const v = cny / d.rate;
  const n = v >= 100 ? Math.round(v).toLocaleString()
    : v >= 1 ? v.toFixed(1) : v.toFixed(2);
  return d.sym + n;
}

function renderUsage() {
  if (!usageData) return;
  document.getElementById("usageLive").hidden = !usageData.available;
  const today = usageData.today, ref = usageData.reference;
  const price = usageData.pricing || {};
  const arc = document.getElementById("rArc");
  const C = 465; // 2πr, r=74
  const d = dispInfo();
  const unitEl = document.getElementById("rCostUnit");
  const setUnit = () => {
    unitEl.textContent = (d.code === "CNY" ? t("usage.unitCny", "CNY TODAY")
                                           : t("usage.unit", "{c} TODAY").replace("{c}", d.code));
  };
  if (!today) {
    document.getElementById("rCost").textContent = "—";
    setUnit();
    arc.style.strokeDashoffset = C;
    document.getElementById("usageStats").innerHTML = "";
    document.getElementById("usageNote").textContent = t("usage.off", "usage source unavailable");
    return;
  }
  document.getElementById("rCost").textContent = fmtCost(today.cost);
  setUnit();
  let charge = 0;
  if (ref && ref.avg_cost > 0) charge = Math.min(1.35, today.cost / ref.avg_cost);
  const shown = Math.min(1, charge);
  arc.style.strokeDashoffset = String(C * (1 - shown));
  arc.classList.toggle("hot", charge > 0.85 && charge <= 1.1);
  arc.classList.toggle("over", charge > 1.1);
  const st = (k, v) => `<div><div class="k">${escapeHtml(t(k[0], k[1]))}</div><div class="v">${escapeHtml(v)}</div></div>`;
  document.getElementById("usageStats").innerHTML =
    st(["usage.req", "REQUESTS"], String(today.requests)) +
    st(["usage.err", "ERRORS"], `${today.errors} (${today.requests ? Math.round(today.errors / today.requests * 100) : 0}%)`) +
    st(["usage.avg", "AVG LATENCY"], (today.avg_ms / 1000).toFixed(1) + "s") +
    st(["usage.pool", "ACTIVE KEYS"], String(usageData.pool ? usageData.pool.keys_active : "—"));
  const numOf = (cny) => {   // vs7 puts the symbol separately via {s}
    const v = cny / d.rate;
    return v >= 100 ? Math.round(v).toLocaleString()
      : v >= 1 ? v.toFixed(1) : v.toFixed(2);
  };
  document.getElementById("usageNote").textContent =
    (ref ? t("usage.vs7", "charge = today ÷ 7-day avg ({a} {s}/day)")
          .replace("{a}", numOf(ref.avg_cost))
          .replace("{s}", d.sym)
       : t("usage.noRef", "no 7-day baseline yet")) +
    (price.available ? "" : " · " + t("usage.noPrice", "pricing unavailable")) +
    (d.fxOff ? " · " + t("usage.fxOff", "rate unavailable — source CNY shown") : "");
}

/* ---------------------------------------------------------------- events -- */
function evKey(e) { return `${e.t}|${e.src}|${e.text}`; }

function eventText(e) {
  if (e.code && t("event." + e.code, null)) {
    return t("event." + e.code)
      .replace(/\{src\}/g, t("panel." + e.src, e.src))
      .replace("{v}", (statusData && statusData.version) || "");
  }
  return e.text;
}

function renderEventsWindow(force) {
  const data = window.__events || { events: [] };
  const list = data.events.slice(0, 10);
  const box = document.getElementById("stream");
  const keep = new Set(list.map(evKey));
  let fresh = list.filter(e => !evNodes.has(evKey(e)));
  // oldest first, newest prepended LAST so the newest line sits on top
  fresh.slice().reverse().forEach((e, _i) => {
    const key = evKey(e);
    const el = document.createElement("div");
    el.className = "ev " + e.level;
    const time = new Date(e.t * 1000);
    const hh = String(time.getHours()).padStart(2, "0") + ":" +
               String(time.getMinutes()).padStart(2, "0") + ":" +
               String(time.getSeconds()).padStart(2, "0");
    el.innerHTML = `<span class="t">${hh}</span>` +
      `<span class="lv">${escapeHtml(t("adv." + (e.level || "info"),
        (e.level || "info").toUpperCase()))}</span>` +
      `<span class="src">${escapeHtml(t("panel." + e.src, e.src))}</span>` +
      `<span class="msg"></span>`;
    const msg = el.querySelector(".msg");
    const text = eventText(e);
    const isNewest = e === fresh[0] && !typingNow && !reduced && e.t > (typedT || 0);
    el.__ev = e;
    box.prepend(el);
    evNodes.set(key, el);
    if (isNewest) {
      typingNow = true; typedT = e.t;
      el.classList.add("typing");
      let i = 0;
      const iv = setInterval(() => {
        msg.textContent = text.slice(0, ++i);
        if (i >= text.length) {
          clearInterval(iv); el.classList.remove("typing");
          typingNow = false; typedKeys.add(key);
          msg.textContent = eventText(e);  // re-evaluate: locale may have swapped mid-type
        }
      }, 16);
    } else {
      msg.textContent = text;
    }
  });
  const box2 = document.getElementById("stream");
  while (box2.children.length > 10) box2.lastChild.remove();
  // prune nodes whose event dropped out of the window
  const keep2 = new Set(list.map(evKey));
  for (const [key, node] of evNodes) {
    if (!keep2.has(key)) { node.remove(); evNodes.delete(key); }
  }
}

/* re-render every stream line after a locale swap (first paint may run before
   the dictionary loads — keys would otherwise stick as literal text) */
function refreshEventTexts() {
  for (const [, node] of evNodes) {
    if (!node.__ev || node.classList.contains("typing")) continue;
    const msg = node.querySelector(".msg");
    if (msg) msg.textContent = eventText(node.__ev);
  }
}

/* -------------------------------------------------------------- armory --- */
async function refreshSkills() {
  try {
    const d = await fetch("/api/skills").then(r => r.json());
    const grid = document.getElementById("galGrid");
    const count = document.getElementById("galCount");
    const list = d.skills || [];
    count.textContent = list.length ? `×${list.length}` : "";
    if (!list.length) {
      grid.innerHTML = `<div class="gal-empty">${escapeHtml(t("gal.empty", "no skills mounted"))}</div>`;
      return;
    }
    grid.innerHTML = list.map(s => `
      <div class="gal-card" data-id="${escapeHtml(s.id)}" tabindex="0">
        <span class="gal-tag ${escapeHtml(s.source)}">${escapeHtml(
          t("gal." + s.source, s.source))}</span>
        <div class="gal-name">${escapeHtml(s.name)}</div>
        <div class="gal-desc">${escapeHtml(s.desc || "—")}</div>
      </div>`).join("");
    grid.querySelectorAll(".gal-card").forEach(el => {
      const flip = () => el.classList.toggle("open");
      el.addEventListener("click", flip);
      el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") {
        e.preventDefault(); flip(); } });
    });
  } catch (e) { /* keep last render */ }
}

/* ------------------------------------------------------------ tool strip -- */
function pad(n) { return String(n).padStart(2, "0"); }

function refreshTools() {
  const now = new Date();
  document.getElementById("toolBj").textContent =
    "BJ " + pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
  document.getElementById("toolUtc").textContent =
    "UTC " + pad(now.getUTCHours()) + ":" + pad(now.getUTCMinutes());
  const sys = (statusData && statusData.system) || {};
  if (sys.uptime_sec != null) {
    const h = Math.floor(sys.uptime_sec / 3600),
          m = Math.floor((sys.uptime_sec % 3600) / 60);
    document.getElementById("toolUp").textContent = `UP ${h}h${pad(m)}m`;
  }
  // next daily heartbeat: 09:00 Beijing (UTC+8), client-side countdown
  const bjNow = new Date(now.getTime() + (now.getTimezoneOffset() + 480) * 60000);
  const next = new Date(bjNow); next.setHours(9, 0, 0, 0);
  if (bjNow >= next) next.setDate(next.getDate() + 1);
  const diff = next - bjNow;
  const hbH = Math.floor(diff / 3600000), hbM = Math.floor((diff % 3600000) / 60000);
  document.getElementById("toolHb").textContent = `HB ${hbH}h${pad(hbM)}m`;
  const today = usageData && usageData.today;
  const el = document.getElementById("toolToday");
  if (today) {
    el.textContent = t("tool.today", "TODAY") + ` ${today.requests} ` +
      t("tool.req", "req") + " · " + fmtCost(today.cost);
    el.classList.remove("idle");
  }
}

/* --------------------------------------------------------------- version -- */
let versionData = null;
function renderVersion() {
  const banner = document.getElementById("updateBanner");
  if (!versionData) { banner.hidden = true; return; }
  if (versionData.update_available &&
      sessionStorage.getItem("aidog_upd_dismiss") !== versionData.latest) {
    banner.hidden = false;
    document.getElementById("updateText").textContent =
      t("upd.text", "UPDATE v{v} AVAILABLE").replace("{v}", versionData.latest) +
      (versionData.notes ? " — " + versionData.notes.slice(0, 90) : "");
  } else banner.hidden = true;
}

/* -------------------------------------------------------------- settings -- */
async function loadSettings() {
  try {
    const s = await fetch("/api/settings").then(r => r.json());
    settings = Object.assign(settings, s);
    document.getElementById("soundToggle").setAttribute("aria-pressed", String(!!settings.sound));
    const vol = document.getElementById("voiceVol");
    if (vol) vol.value = String(settings.voice_volume ?? 80);
    const curSel = document.getElementById("curSelect");
    if (curSel) curSel.value = settings.currency || "USD";
    const fxIn = document.getElementById("fxInput");
    if (fxIn) fxIn.value = String(settings.fx_rate ?? 0);
    const myc = settings.mycore || {};
    const mycModel = document.getElementById("mycModel");
    if (mycModel) mycModel.value = myc.model || "";
    const mycUrl = document.getElementById("mycUrl");
    if (mycUrl) mycUrl.value = myc.base_url || "";
    document.getElementById("alertSelect").value = settings.alert_preview || "off";
    document.getElementById("refreshSelect").value = String(settings.refresh_sec || 10);
    const stored = localStorage.getItem(LANG_KEY);
    if (!stored && settings.language && settings.language !== document.documentElement.lang) {
      loadLang(settings.language);
    }
    if (!settings.language) settings.language = document.documentElement.lang;
  } catch (e) { /* defaults stand */ }
  const tok = localStorage.getItem(TOKEN_KEY);
  if (tok) document.getElementById("tokenInput").value = "";
}

async function saveSettings() {
  const state = document.getElementById("saveState");
  const input = document.getElementById("tokenInput");
  if (input.value) localStorage.setItem(TOKEN_KEY, input.value.trim());
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const payload = {
    language: document.documentElement.lang,
    refresh_sec: parseInt(document.getElementById("refreshSelect").value, 10),
    sound: document.getElementById("soundToggle").getAttribute("aria-pressed") === "true",
    voice_volume: parseInt(document.getElementById("voiceVol").value, 10) || 0,
    currency: (document.getElementById("curSelect") || {}).value || "USD",
    fx_rate: parseFloat((document.getElementById("fxInput") || {}).value) || 0,
    mycore: {
      model: ((document.getElementById("mycModel") || {}).value || "").trim() || "mimo-v2.6-flash",
      base_url: ((document.getElementById("mycUrl") || {}).value || "").trim() || "http://127.0.0.1:3460/v1",
    },
    alert_preview: document.getElementById("alertSelect").value,
  };
  if (!token) {
    state.className = "save-state err";
    state.textContent = t("save.needToken", "token required");
    return;
  }
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      state.className = "save-state err";
      state.textContent = t("save.badToken", "401 · unauthorized");
      return;
    }
    const body = await res.json();
    if (!res.ok) {
      state.className = "save-state err";
      state.textContent = (body.details || [body.error || "error"]).join("; ").slice(0, 60);
      return;
    }
    settings = Object.assign(settings, body.settings || payload);
    state.className = "save-state";
    state.textContent = body.changed ? t("save.ok", "saved ✓") : t("save.same", "no change ✓");
    input.value = "";
    beep(880, 0.06);
    scheduleRefresh();
    refreshUsage();            // currency/fx may have changed (server cache cleared)
  } catch (e) {
    state.className = "save-state err";
    state.textContent = String(e).slice(0, 50);
  }
}

/* --------------------------------------------------------------- polling -- */
let statusTimer = null, usageTimer = null;

async function refreshStatus() {
  try {
    statusData = await fetch("/api/status").then(r => r.json());
    renderStatus();
    document.getElementById("ts").textContent = statusData.time_utc || "";
  } catch (e) { document.getElementById("ts").textContent = "status error"; }
}

async function refreshUsage() {
  try {
    usageData = await fetch("/api/usage").then(r => r.json());
    renderUsage();
  } catch (e) { /* keep last */ }
}

async function refreshEvents() {
  try {
    window.__events = await fetch("/api/events").then(r => r.json());
    renderEventsWindow();
  } catch (e) { /* keep last */ }
}

async function refreshVersion() {
  try {
    versionData = await fetch("/api/version").then(r => r.json());
    renderVersion();
  } catch (e) { /* keep last */ }
}

function scheduleRefresh() {
  const ms = Math.max(5, parseInt(settings.refresh_sec, 10) || 10) * 1000;
  clearInterval(statusTimer);
  statusTimer = setInterval(refreshStatus, ms);
}

function tickClock() {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("en-GB");
  refreshTools();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* localized "<panel>: <state>" advisory reason line (server sends raw ids) */
function reasonHtml(r) {
  const m = String(r).match(/^([a-z0-9_]+):\s*(.+)$/i);
  if (!m) return escapeHtml(r);
  return escapeHtml(t("panel." + m[1], m[1]) + ": " + t("state." + m[2], m[2]));
}
function reasonLi(r) { return `<li>&gt; ${reasonHtml(r)}</li>`; }

/* ------------------------------------------------------------------ wire -- */
document.getElementById("langBtn").addEventListener("click", () => {
  const next = document.documentElement.lang === "en" ? "zh" : "en";
  localStorage.setItem(LANG_KEY, next);
  loadLang(next);
});
document.getElementById("soundToggle").addEventListener("click", e => {
  const b = e.currentTarget;
  b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
  settings.sound = b.getAttribute("aria-pressed") === "true";
  renderVoice();
  beep(660, 0.05);
});
const voiceVolEl = document.getElementById("voiceVol");
if (voiceVolEl) voiceVolEl.addEventListener("input", e => {
  settings.voice_volume = parseInt(e.target.value, 10) || 0;
});
const voiceOrbEl = document.getElementById("voiceOrb");
if (voiceOrbEl) voiceOrbEl.addEventListener("click", () => armVoice());
document.getElementById("saveBtn").addEventListener("click", saveSettings);
document.getElementById("refreshSelect").addEventListener("change", scheduleRefresh);
document.getElementById("updateDismiss").addEventListener("click", () => {
  if (versionData && versionData.latest) sessionStorage.setItem("aidog_upd_dismiss", versionData.latest);
  document.getElementById("updateBanner").hidden = true;
});
document.getElementById("alertSelect").addEventListener("change", e => {
  settings.alert_preview = e.target.value; dismissedLevel = null;
  if (statusData) applyAlert(statusData.advisory);
});

const startLang = localStorage.getItem(LANG_KEY) ||
  new URLSearchParams(location.search).get("lang") || "en";
runBoot();
loadLang(startLang);
loadSettings().then(() => { scheduleRefresh(); renderVoice(); });
refreshStatus(); refreshUsage(); refreshEvents(); refreshVersion(); refreshSkills();
refreshVoiceCap();   // engine status visible before the first orb tap (#4)
setInterval(tickClock, 1000); tickClock();
setInterval(refreshUsage, 60000);
setInterval(refreshEvents, 10000);
setInterval(refreshVersion, 300000);
