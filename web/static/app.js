/* I'm AiDog console — app-shell logic (side-nav views + ?view= deep links,
   service status cards, usage metrics, advisory takeover, typewriter stream,
   settings writes with access token, update banner).
   All user-facing strings come from locale packs; upstream names: zero. */
"use strict";

const LANG_KEY = "aidog_lang";
const TOKEN_KEY = "aidog_token";
const params = new URLSearchParams(location.search);
const QS_LANG = params.get("lang");  // one-off ?lang= override (beats saved settings.language for this view)
const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

let dict = {};
let settings = { refresh_sec: 10, sound: false, voice_volume: 80, alert_preview: "off" };
let statusData = null, usageData = null;
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
  mediaLangRefresh();          // voice session + image badge texts follow too
  settingsRelang();            // rows built by JS keep their pack strings too
  renderExchange();            // venue rows / probe labels follow the language too
  chatBadge(chatBusy);            // badge label follows the active language
  refreshEventTexts();
  setView(currentView, false);    // re-resolve the view title in the new language
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
  ["> service status ....... ", "ok"],
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

/* ---------------------------------------------------- media face (4c) ----
   Voice session: one independent MediaRecorder segment every 2.5s -> POST
   /api/stt (local contract, no token) -> wake-phrase match over normalized
   text -> short command capture -> the existing send chain -> the reply's
   first 200 chars through the existing /api/say chain (same volume logic).
   Image trial: POST /api/image with the existing Bearer token -> thumbnails
   from /api/media/<name>. Every failure shows its real reason — never a
   silent pretend. All new strings go through t(key, fallback). */

const VS_CYCLE_MS = 2500;       // one self-contained audio segment per tick
const VS_CMD_MS = 10000;        // command capture window after the wake phrase
const VS_REPLY_MAX_MS = 120000; // wait for the streamed reply before giving up
let capsData = null;
let capsSeen = false;        // B2: false until the FIRST catalog fetch settles
let vsOn = false, vsStream = null, vsRec = null, vsMime = "";
let vsPhase = "wake", vsCmdText = "", vsPending = false;
let vsCycleTimer = null, vsCmdTimer = null, vsReplyIv = null;
let imgBusy = false;

async function refreshCaps() {
  try { capsData = await fetch("/api/capabilities").then(r => r.json()); }
  catch (e) { capsData = null; }
  capsSeen = true;
  imgCapHint();
  renderExchange();            // exchange block rides the same capability fetch
  renderToolsOff();            // B1: tools_off grid + env-override note ride along
  renderEnvOverride();
  renderCapGrid();             // B2: Overview system card rides along too
  renderOrch();                // Lane I: orchestration summary repaints too
}

function mediaLangRefresh() {
  vsUi();
  if (!vsOn) vsState(t("voice.vs.idle", "mic off — tap MIC to listen"), false);
  imgCapHint();
}

function vsState(text, err) {
  const el = document.getElementById("vsState");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("err", !!err);
}

function vsLive(text) {
  const el = document.getElementById("vsLive");
  if (!el) return;
  el.textContent = text || "";
  el.hidden = !text;
}

function vsUi() {
  const btn = document.getElementById("micBtn");
  if (btn) {
    btn.setAttribute("aria-pressed", String(vsOn));
    btn.classList.toggle("primary", vsOn);
    btn.textContent = vsOn ? t("voice.vs.micStop", "STOP")
                           : t("voice.vs.micStart", "MIC");
  }
  const badge = document.getElementById("vsBadge");
  if (badge) {
    badge.textContent = !vsOn ? t("voice.vs.badgeIdle", "idle")
      : vsPhase === "cmd" ? t("voice.vs.badgeCmd", "command")
      : t("voice.vs.badgeListen", "listening");
    badge.classList.toggle("live", vsOn);
  }
}

function vsNormalize(s) {          // lower + punctuation/symbols -> spaces
  return String(s).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/\s+/g, " ").trim();
}

// Normalized text + per-char map back to the ORIGINAL string (so the wake
// matcher can slice the command out of the untouched transcript). Built
// char-by-char; a non-alnum run collapses to ONE space whose map entry points
// at the next original alnum char (trailing space = trimmed).
function vsNormMap(s) {
  const src = String(s);
  const chars = [], idx = [];
  let space = false;
  for (let i = 0; i < src.length; i++) {
    if (/[\p{L}\p{N}]/u.test(src[i])) {
      if (space && chars.length) { chars.push(" "); idx.push(i); }
      space = false;
      const lc = src[i].toLowerCase();
      chars.push(lc.slice(0, 1)); idx.push(i);
    } else if (chars.length) {
      space = true;
    }
  }
  return { norm: chars.join(""), map: idx, len: src.length };
}

function vsCompact(norm) {         // drop spaces; norm-index of every kept char
  const cp = [], idx = [];
  for (let i = 0; i < norm.length; i++) {
    if (norm[i] !== " ") { cp.push(norm[i]); idx.push(i); }
  }
  return { cp: cp.join(""), idx };
}

function vsEditDist(a, b, max) {   // bounded Levenshtein; > max => max + 1
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    prev = cur;
  }
  return prev[b.length];
}

function vsPhrasesRaw() {          // settings.media.wake_phrases, honest default
  const raw = settings.media && Array.isArray(settings.media.wake_phrases)
    ? settings.media.wake_phrases : [];
  const list = raw.filter(p => typeof p === "string" && p.trim())
                  .map(p => p.trim());
  return list.length ? list : ["hey aidog", "\u4f60\u597d\u827e\u591a"];
}

function vsPhrases() { return vsPhrasesRaw().map(vsNormalize).filter(Boolean); }

// Wake match over the normalized transcript: exact substring first, then a
// fuzzy pass on the space-stripped forms — ASR renders "Hey Aidog" as
// "Hey A-Dog" (and the zh default as a homophone variant), so bounded edit
// distance keeps the shipped defaults usable; tol = 2 for >= 8 chars else 1. Returns the
// original-text span AFTER the phrase, or null when the phrase is absent.
function vsMatchWake(src) {
  const nm = vsNormMap(src);
  for (const p of vsPhrases()) {
    if (!p) continue;
    const ei = nm.norm.indexOf(p);
    if (ei >= 0) {
      const e = ei + p.length;
      const orig = e < nm.map.length ? nm.map[e]
        : (nm.map.length ? nm.map[nm.map.length - 1] + 1 : nm.len);
      return { rest: String(src).slice(orig).trim() };
    }
    const cpP = p.replace(/ /g, "");
    if (!cpP) continue;
    const cpN = vsCompact(nm.norm);
    const tol = cpP.length >= 8 ? 2 : 1;
    let best = null;   // (dist, -w, i): tightest match, then longest window,
    //                   then earliest position — a short window with dist==tol
    //                   must not steal the span from a longer tighter one
    for (let w = Math.max(1, cpP.length - tol);
         w <= cpP.length + tol; w++) {
      for (let i = 0; i + w <= cpN.cp.length; i++) {
        const d = vsEditDist(cpN.cp.substr(i, w), cpP, tol);
        if (d > tol) continue;
        if (!best || d < best.d || (d === best.d && w > best.w)) {
          best = { d, w, i };
        }
      }
    }
    if (best) {
      const e = best.i + best.w;
      const orig = e < cpN.idx.length ? nm.map[cpN.idx[e]] : nm.len;
      return { rest: String(src).slice(orig).trim() };
    }
  }
  return null;
}

function vsWakeHit(text) { return vsMatchWake(text) !== null; }

function vsRestAfter(text) {       // words spoken after the wake phrase
  const m = vsMatchWake(text);
  return m ? m.rest : "";
}

async function vsToggle() { if (vsOn) vsStop(); else await vsStart(); }

async function vsStart() {
  if (vsOn) return;
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia ||
      typeof MediaRecorder === "undefined") {
    vsState(t("voice.vs.noSupport",
              "this browser cannot record audio (no MediaRecorder)"), true);
    return;
  }
  if (capsData && capsData.media && capsData.media.stt &&
      capsData.media.stt.available === false) {
    vsState(t("voice.vs.sttDown",
              "speech-to-text unavailable on the server — voice session not started"),
            true);
    return;
  }
  let stream = null;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    vsState(t("voice.vs.noMic",
              "microphone access denied — allow it in the browser and retry"), true);
    return;
  }
  const cands = ["audio/webm;codecs=opus", "audio/webm",
                 "audio/ogg;codecs=opus", "audio/mp4"];
  let mime = "";
  try {
    mime = cands.find(m => MediaRecorder.isTypeSupported &&
                           MediaRecorder.isTypeSupported(m)) || "";
  } catch (e) { mime = ""; }
  let rec = null;
  try {
    rec = mime ? new MediaRecorder(stream, { mimeType: mime })
               : new MediaRecorder(stream);
  } catch (e) {
    stream.getTracks().forEach(tr => { try { tr.stop(); } catch (e2) {} });
    vsState(t("voice.vs.noSupport",
              "this browser cannot record audio (no MediaRecorder)"), true);
    return;
  }
  vsStream = stream; vsRec = rec; vsMime = mime;
  vsOn = true; vsPhase = "wake"; vsCmdText = "";
  vsUi(); vsLive("");
  vsState(t("voice.vs.wake", "listening for the wake phrase…"), false);
  vsCycle();
}

function vsStop() {
  vsOn = false;
  clearTimeout(vsCmdTimer); vsCmdTimer = null;
  clearTimeout(vsCycleTimer); vsCycleTimer = null;
  if (vsReplyIv) { clearInterval(vsReplyIv); vsReplyIv = null; }
  if (vsRec) {
    vsRec.onstop = null; vsRec.ondataavailable = null;
    try { if (vsRec.state !== "inactive") vsRec.stop(); } catch (e) {}
    vsRec = null;
  }
  if (vsStream) {
    vsStream.getTracks().forEach(tr => { try { tr.stop(); } catch (e) {} });
    vsStream = null;
  }
  vsPhase = "wake"; vsCmdText = "";
  vsUi(); vsLive("");
  vsState(t("voice.vs.idle", "mic off — tap MIC to listen"), false);
}

function vsCycle() {
  if (!vsOn || !vsRec) return;
  const chunks = [];
  vsRec.ondataavailable = e => { if (e.data && e.data.size) chunks.push(e.data); };
  vsRec.onstop = async () => {
    clearTimeout(vsCycleTimer);
    if (!vsOn) return;
    const blob = new Blob(chunks, { type: (vsMime || "audio/webm").split(";")[0] });
    await vsTranscribe(blob);
    if (vsOn) setTimeout(vsCycle, 80);   // next self-contained segment
  };
  try { vsRec.start(); } catch (e) {
    vsState(t("voice.vs.noSupport",
              "this browser cannot record audio (no MediaRecorder)"), true);
    vsStop();
    return;
  }
  vsCycleTimer = setTimeout(() => {
    try { if (vsRec && vsRec.state !== "inactive") vsRec.stop(); }
    catch (e) {}
  }, VS_CYCLE_MS);
}

async function vsTranscribe(blob) {
  if (vsPending) return;          // segments stay strictly sequential
  vsPending = true;
  vsState(t("voice.vs.hearing", "transcribing…"), false);
  try {
    let res = null, body = {};
    try {
      res = await fetch("/api/stt", {
        method: "POST",
        headers: { "Content-Type": (vsMime || blob.type || "audio/webm").split(";")[0] },
        body: blob,
      });
      body = await res.json().catch(() => ({}));
    } catch (e) {
      vsState(t("voice.vs.sttErr", "transcription failed ({reason})")
                .replace("{reason}", "network"), true);
      return;
    }
    if (!vsOn) return;
    if (!res.ok) {
      const reason = String(body.error || res.status).slice(0, 160);
      vsState(t("voice.vs.sttErr", "transcription failed ({reason})")
                .replace("{reason}", reason), true);
      if (res.status === 503 || res.status === 413 ||
          res.status === 415 || res.status === 403) {
        vsStop();       // no transcriber / refused payload: stop honestly
      }
      return;
    }
    vsStep(String(body.text || "").trim());
  } finally {
    vsPending = false;
  }
}

function vsStep(text) {
  if (vsPhase === "wake") {
    if (!text) { vsLive("");
      vsState(t("voice.vs.wake", "listening for the wake phrase…"), false); return; }
    vsLive(text);                    // not awake yet: show only what we heard
    if (!vsWakeHit(text)) {
      vsState(t("voice.vs.wake", "listening for the wake phrase…"), false);
      return;
    }
    vsPhase = "cmd";
    vsCmdText = vsRestAfter(text);
    vsUi();
    beep(880, 0.05);
    vsState(t("voice.vs.wakeHit", "wake phrase detected — go ahead"), false);
    if (vsCmdText) vsLive(vsCmdText);
    clearTimeout(vsCmdTimer);
    vsCmdTimer = setTimeout(() => vsFinishCmd(), VS_CMD_MS);   // 10s cap
    return;
  }
  if (!text) { vsFinishCmd(); return; }   // empty segment = end of command
  vsCmdText += (vsCmdText ? " " : "") + text;
  vsLive(vsCmdText);
  vsState(t("voice.vs.cmd", "capturing command…"), false);
}

function vsFinishCmd() {
  clearTimeout(vsCmdTimer); vsCmdTimer = null;
  const cmd = vsCmdText.trim();
  vsPhase = "wake"; vsCmdText = "";
  vsUi();
  if (!cmd) {
    vsState(t("voice.vs.noCommand",
              "wake phrase heard, but no command followed — try again"), true);
    vsLive("");
    return;
  }
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    vsLive(cmd);
    vsState(t("chat.needToken", "access token required (set it in Settings)"), true);
    return;
  }
  const input = document.getElementById("chatInput");
  if (input) input.value = cmd;
  vsLive(cmd);
  vsState(t("voice.vs.sent", "command sent — waiting for the reply"), false);
  vsAwaitReplyStart();
  chatSend();
}

function vsAwaitReplyStart() {
  if (vsReplyIv) clearInterval(vsReplyIv);
  const before = document.querySelectorAll("#chatLog .chat-row.assistant").length;
  let waited = 0;
  vsReplyIv = setInterval(() => {
    waited += 500;
    const settled = !chatBusy && !chatQPending && !chatQueue.length;
    const grew = document.querySelectorAll("#chatLog .chat-row.assistant").length > before;
    if (waited >= VS_REPLY_MAX_MS) {
      clearInterval(vsReplyIv); vsReplyIv = null;
      vsState(t("voice.vs.replyTimeout", "still working — no voice summary yet"), true);
      return;
    }
    if (!settled || (!grew && waited < 2000)) return;
    clearInterval(vsReplyIv); vsReplyIv = null;
    const rows = document.querySelectorAll("#chatLog .chat-row.assistant .chat-bubble");
    const last = rows.length ? rows[rows.length - 1] : null;
    const text = (last ? last.textContent : "").trim().slice(0, 200);
    if (!grew || !text) {
      vsState(t("voice.vs.noReply", "no reply to speak"), true);
      return;
    }
    vsSpeakReply(text);
  }, 500);
}

async function vsSpeakReply(text) {
  const serverOk = !!(voiceEngine && voiceEngine.available);
  const spoken = await speak(text);   // existing chain: /api/say -> local fallback
  if (spoken && serverOk) {
    vsState(t("voice.vs.spoken", "reply spoken"), false);
  } else if (spoken) {
    vsState(t("voice.vs.spokenLocal", "reply spoken by the browser voice"), false);
  } else {
    vsState(t("voice.vs.speakDown", "reply ready — voice could not be spoken"), true);
  }
}

/* --- image trial panel (car 4c) --- */
function imgState(text, err) {
  const el = document.getElementById("imgState");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("err", !!err);
}

function imgCapHint() {
  const badge = document.getElementById("imgBadge");
  const img = capsData && capsData.media ? capsData.media.image : null;
  if (badge) {
    badge.textContent = !img ? "–"
      : (img.configured ? t("img.badgeReady", "ready")
                        : t("img.badgeOff", "not configured"));
    badge.classList.toggle("live", !!(img && img.configured));
  }
  if (img && img.configured === false && !imgBusy) {
    imgState(img.reason || t("img.notConfigured",
      "image generation not configured — set an endpoint in Settings → Media"), true);
  }
}

async function imgGenerate() {
  const input = document.getElementById("imgPrompt");
  const prompt = (input ? input.value : "").trim();
  if (!prompt) {
    imgState(t("img.needPrompt", "describe what to generate first"), true);
    return;
  }
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    imgState(t("chat.needToken", "access token required (set it in Settings)"), true);
    return;
  }
  imgBusy = true;
  imgState(t("img.working", "generating…"), false);
  try {
    const res = await fetch("/api/image", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ prompt }),
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      imgState(t("chat.badToken", "401 · unauthorized"), true);
      return;
    }
    if (!res.ok) {
      const reason = String(body.error || res.status).slice(0, 160);
      imgState(t("img.fail", "generation failed ({reason})")
                 .replace("{reason}", reason), true);
      if (res.status === 503 && capsData && capsData.media && capsData.media.image) {
        capsData.media.image.configured = false;   // keep the badge honest
        imgCapHint();
      }
      return;
    }
    const imgs = Array.isArray(body.images) ? body.images : [];
    if (!imgs.length) {
      imgState(t("img.none", "no image returned"), true);
      return;
    }
    const grid = document.getElementById("imgGrid");
    if (grid) {
      imgs.forEach(it => {
        if (!it || !it.url) return;
        const a = document.createElement("a");
        a.href = it.url; a.target = "_blank"; a.rel = "noopener";
        const im = document.createElement("img");
        im.src = it.url;
        im.alt = prompt.slice(0, 120);
        im.loading = "lazy";
        a.appendChild(im);
        grid.prepend(a);
      });
      while (grid.children.length > 12) grid.removeChild(grid.lastChild);
    }
    imgState(t("img.ok", "generated ✓"), false);
    beep(880, 0.05);
  } catch (e) {
    imgState(t("img.fail", "generation failed ({reason})")
               .replace("{reason}", String(e).slice(0, 80)), true);
  } finally {
    imgBusy = false;
  }
}

/* ----------------------------------------------------------- exchange --
   Web3 phase-2 W2 approval-gated venue panel. It renders ONLY from the
   `exchange` + `web3` blocks of GET /api/capabilities and from
   GET /api/exchange/plans: when those blocks have not shipped yet the
   panel states so honestly instead of pretending. Public market reads are
   keyless; orders exist only as PLANS the owner decides in this panel —
   the model's execute step carries a plan id and can never self-approve.
   Withdrawals and on-chain transfers do not exist in this product at all.
   Server text (label / summary / reason / fee_note / status / venue /
   quote lines / error strings) is rendered verbatim as DATA and never
   translated; every UI label goes through t(key, fallback) and re-renders
   on a language switch (renderExchange() is called from loadLang and
   refreshCaps).
   The probe is a GET with an optional symbol: an empty input omits the
   parameter so the venue default applies. Writes go through one path only:
   POST /api/exchange/plans/<id> with the Bearer token (same contract as
   evoPost()). */
let exBusy = false;
let exMsg = null;     // probe status: {k,f,a} translated · {raw} verbatim · null
let exMsgErr = false;
let exOut = null;     // last probe payload (string) · null = idle line

function exBlock() { return (capsData && capsData.exchange) ? capsData.exchange : null; }

function exRows() {
  const ex = exBlock();
  return ex && Array.isArray(ex.venues) ? ex.venues : [];
}

function exSetMsg(msg, err) {
  exMsg = msg || null; exMsgErr = !!err;
  const el = document.getElementById("exProbeMsg");
  if (!el) return;
  el.textContent = !msg ? ""
    : (msg.raw !== undefined ? String(msg.raw)
      : t(msg.k, msg.f).replace("{reason}", (msg.a && msg.a.reason) || ""));
  el.classList.toggle("err", !!err);
}

/* one venue's honest state: an unconfigured key slot always shows the
   server's reason string VERBATIM (exact why, never a vague label) */
function exStateHtml(v) {
  if (v && v.configured === false) {
    const why = v.reason ? String(v.reason)
      : t("ex.state.unconfigured", "not configured");
    return `<div class="vs-state err">${escapeHtml(why)}</div>`;
  }
  if (v && (v.auth === "public" || v.kind === "chain")) {
    return `<div class="vs-state">${escapeHtml(t("ex.state.keyless", "ready (keyless)"))}</div>`;
  }
  if (v && v.configured === true) {
    return `<div class="vs-state">${escapeHtml(t("ex.state.configured", "configured"))}</div>`;
  }
  return `<div class="vs-state">${escapeHtml(t("ex.state.unknown", "state unknown"))}</div>`;
}

function exSyncSymbol() {
  const inp = document.getElementById("exSymbol");
  const sel = document.getElementById("exVenueSel");
  if (!inp) return;
  const key = sel ? String(sel.value || "") : "";
  const v = exRows().find(x => String(x.key) === key);
  const sym = v && v.symbol ? String(v.symbol) : "";
  inp.placeholder = sym
    ? t("ex.probe.symbolPh", "default {sym}").replace("{sym}", sym)
    : t("ex.probe.symbolAny", "venue default symbol");
}

function renderExchange() {
  const ex = exBlock();
  const rows = exRows();

  const count = document.getElementById("exVenueCount");
  if (count) count.textContent = rows.length ? `\u00d7${rows.length}` : "";

  const list = document.getElementById("exVenues");
  if (list) {
    if (!rows.length) {
      list.innerHTML = `<div class="gal-empty">${escapeHtml(t(
        "ex.unavailable",
        "exchange capability not available yet — this build has no exchange backend block"))}</div>`;
    } else {
      list.innerHTML = rows.map(v => `
        <div class="gal-card" data-venue="${escapeHtml(String(v.key))}" tabindex="0">
          <span class="gal-tag">${escapeHtml(t("ex.kind." + v.kind, String(v.kind)))}</span>
          <div class="gal-name">${escapeHtml(String(v.label || v.key))}</div>
          <div><span class="head-status"><span class="chat-badge">${escapeHtml(t("ex.auth." + v.auth, String(v.auth)))}</span>${v.orderable ? `<span class="chat-badge">${escapeHtml(t("ex.orderable", "orderable"))}</span>` : ""}</span></div>
          <div class="stat-note">${escapeHtml(t("ex.defaultPair", "default pair {sym}")
            .replace("{sym}", String(v.symbol || "\u2014")))}</div>
          <div class="gal-desc">${escapeHtml(String(v.summary || "\u2014"))}</div>
          ${exStateHtml(v)}
        </div>`).join("");
      list.querySelectorAll(".gal-card").forEach(el => {
        const flip = () => el.classList.toggle("open");
        el.addEventListener("click", flip);
        el.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") {
          e.preventDefault(); flip(); } });
      });
    }
  }

  const toolsEl = document.getElementById("exTools");
  if (toolsEl) {
    toolsEl.textContent = !ex
      ? t("ex.unavailable",
          "exchange capability not available yet — this build has no exchange backend block")
      : (Array.isArray(ex.tools) && ex.tools.length ? ex.tools.join("  \u00b7  ")
        : t("ex.toolsEmpty", "no exchange_* tool registered"));
  }
  const noteEl = document.getElementById("exNote");
  if (noteEl) {
    const note = ex && ex.note ? String(ex.note) : "";
    noteEl.textContent = note;
    noteEl.hidden = !note;
  }

  const sel = document.getElementById("exVenueSel");
  if (sel) {
    const prev = String(sel.value || "");
    if (!rows.length) {
      sel.innerHTML = `<option value="">${escapeHtml(
        t("ex.probe.needVenue", "venue list not loaded"))}</option>`;
      sel.disabled = true;
    } else {
      sel.disabled = false;
      sel.innerHTML = rows.map(v => `<option value="${escapeHtml(String(v.key))}">${
        escapeHtml(String(v.label || v.key))}</option>`).join("");
      if (Array.prototype.some.call(sel.options, o => o.value === prev)) sel.value = prev;
    }
  }
  exSyncSymbol();

  const btn = document.getElementById("exProbeBtn");
  if (btn) btn.disabled = exBusy || !rows.length;

  const out = document.getElementById("exQuoteOut");
  if (out) {
    out.textContent = exOut !== null ? exOut
      : t("ex.probe.idle", "pick a venue, then press FETCH QUOTE");
  }
  if (exMsg) exSetMsg(exMsg, exMsgErr);   // re-translate the live status line

  // W2: the threshold line and the approval queue ride the same render pass
  // (language switch / capability refresh re-translate them too)
  renderExThreshold();
  renderExPlans();
}

async function exProbe() {
  if (exBusy) return;
  const sel = document.getElementById("exVenueSel");
  const venue = sel ? String(sel.value || "") : "";
  if (!venue) {
    exSetMsg({ k: "ex.probe.needVenue", f: "venue list not loaded" }, true);
    return;
  }
  const inp = document.getElementById("exSymbol");
  const sym = (inp ? inp.value : "").trim();
  const btn = document.getElementById("exProbeBtn");
  const out = document.getElementById("exQuoteOut");
  exBusy = true;
  if (btn) btn.disabled = true;
  exSetMsg({ k: "ex.probe.working", f: "fetching\u2026" }, false);
  const url = "/api/exchange/quote?venue=" + encodeURIComponent(venue) +
    (sym ? "&symbol=" + encodeURIComponent(sym) : "");
  try {
    const res = await fetch(url);
    const body = await res.json().catch(() => null);
    const lines = res.ok && body && Array.isArray(body.lines) ? body.lines : null;
    if (lines) {
      exOut = lines.length ? lines.map(String).join("\n")
        : t("ex.probe.empty", "no lines returned");
      exSetMsg({ k: "ex.probe.ok", f: "quote \u2713" }, false);
    } else {
      // honest failure: the server's own error string, verbatim, in the output box
      const reason = body && body.error ? String(body.error) : "HTTP " + res.status;
      exOut = reason;
      exSetMsg({ k: "ex.probe.fail", f: "request failed" }, true);
    }
  } catch (e) {
    exOut = String(e).slice(0, 200);
    exSetMsg({ k: "ex.probe.fail", f: "request failed" }, true);
  } finally {
    exBusy = false;
    if (btn) btn.disabled = !exRows().length;
  }
  renderExchange();
}

/* --------------------------------------------- W2 order approval panel --
   Plans are prepared server-side (exchange_prepare_order) and decided by
   the OWNER here: the model's execute step receives only the plan id, so it
   can never approve its own plan. GET /api/exchange/plans lists them newest
   first; POST /api/exchange/plans/<id> {action: approve|reject|cancel} with
   the Bearer token makes a decision (same write-op contract as evoPost()).
   Every server string (reason / fee_note / status / venue / error text) is
   rendered VERBATIM as data through escapeHtml(); only labels come from the
   locale packs. Nothing is ever invented: an empty queue says so. */
let exPlans = null;        // array of plan objects | null (never loaded)
let exPlansErr = null;     // honest failure string from the list GET (verbatim)
let exPlansBusy = false;   // list fetch in flight
const exDeciding = {};     // plan id -> action while its POST is in flight
let exPlanMsg = null;      // {id, raw} last decision error, shown on that card

function exW3() {
  return (capsData && capsData.web3 && typeof capsData.web3 === "object")
    ? capsData.web3 : null;
}

/* numbers are DATA: fixed-point, trailing zeros trimmed, never localized */
function exNum(v, dp) {
  if (v === null || v === undefined || v === "") return "\u2014";
  const n = Number(v);
  if (!isFinite(n)) return String(v);
  let s = n.toFixed(dp === undefined ? 6 : dp);
  if (s.indexOf(".") >= 0) s = s.replace(/0+$/, "").replace(/\.$/, "");
  return s;
}

function exClock(epoch) {           // epoch SECONDS -> local HH:MM:SS
  const ms = Number(epoch) * 1000;
  if (!isFinite(ms)) return "\u2014";
  const d = new Date(ms);
  const p = x => String(x).padStart(2, "0");
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

function exDur(sec) {
  const s = Math.max(0, Math.floor(sec));
  const p = x => String(x).padStart(2, "0");
  return p(Math.floor(s / 3600)) + ":" + p(Math.floor(s / 60) % 60) +
    ":" + p(s % 60);
}

function exExpiryText(p) {          // "" when the plan carries no expiry
  const exp = p && p.expires_at !== null && p.expires_at !== undefined
    ? Number(p.expires_at) : NaN;
  if (!isFinite(exp) || exp <= 0) return "";
  const clock = exClock(exp);
  const left = exp - Date.now() / 1000;
  return left > 0
    ? t("ex.plan.expires", "expires {time} \u00b7 in {left}")
        .replace("{time}", clock).replace("{left}", exDur(left))
    : t("ex.plan.expired", "expired {time}").replace("{time}", clock);
}

/* the server's own failure string, verbatim — never a vague substitute */
function exErrText(res, body) {
  if (body && typeof body === "object") {
    if (typeof body.error === "string" && body.error) return body.error;
    if (typeof body.message === "string" && body.message) return body.message;
    if (Array.isArray(body.details) && body.details.length) {
      return body.details.map(String).join("; ");
    }
  }
  return "HTTP " + (res && res.status ? res.status : 0);
}

async function exPlansLoad() {
  if (exPlansBusy) return;
  exPlansBusy = true;
  renderExPlans();
  try {
    const res = await fetch("/api/exchange/plans");
    const body = await res.json().catch(() => null);
    if (res.ok && body && Array.isArray(body.plans)) {
      exPlans = body.plans.filter(p => p && typeof p === "object");
      exPlansErr = null;
    } else {
      exPlansErr = exErrText(res, body);   // keep any last good list visible
    }
  } catch (e) {
    exPlansErr = String(e).slice(0, 200);
  } finally {
    exPlansBusy = false;
    renderExPlans();
  }
}

async function exDecide(id, action) {
  id = String(id === null || id === undefined ? "" : id);
  if (!id || exDeciding[id]) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {                 // same honest token rule as every write op
    exPlanMsg = { id: id, raw: t("save.needToken", "token required") };
    renderExPlans();
    return;
  }
  exPlanMsg = null;
  exDeciding[id] = action;
  renderExPlans();               // buttons go disabled while the POST flies
  try {
    const res = await fetch("/api/exchange/plans/" + encodeURIComponent(id), {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: action }) });
    const body = await res.json().catch(() => null);
    if (res.ok && body && body.plan && typeof body.plan === "object") {
      const list = Array.isArray(exPlans) ? exPlans.slice() : [];
      const i = list.findIndex(p => p && String(p.id) === id);
      if (i >= 0) list[i] = body.plan; else list.unshift(body.plan);
      exPlans = list;
      exPlanMsg = null;
      refreshCaps();              // pending/approved/spent counts follow
    } else {
      exPlanMsg = { id: id, raw: exErrText(res, body) };  // 401 et al., verbatim
    }
  } catch (e) {
    exPlanMsg = { id: id, raw: String(e).slice(0, 200) };
  } finally {
    delete exDeciding[id];
    renderExPlans();
  }
}

function exPlanCard(p) {
  const id = String(p.id === null || p.id === undefined ? "" : p.id);
  const rows = exRows();
  const vrow = rows.find(v => String(v.key) === String(p.venue));
  const venue = vrow && vrow.label ? String(vrow.label)
    : (p.venue === null || p.venue === undefined ? "" : String(p.venue));
  const sym = p.symbol === null || p.symbol === undefined ? "" : String(p.symbol);
  const raw = v => (v === null || v === undefined ? "" : String(v));
  const g1 = [raw(p.side), raw(p.order_type)].filter(Boolean).join(" \u00b7 ");
  const status = raw(p.status);
  const g2 = [status, raw(p.approval)].filter(Boolean).join(" \u00b7 ");
  const badges = [g1, g2].filter(Boolean).map(b =>
    `<span class="chat-badge">${escapeHtml(b)}</span>`).join("");

  const price = (p.price === null || p.price === undefined)
    ? (raw(p.order_type) || "\u2014") : exNum(p.price);
  const slip = (p.slippage_bps === null || p.slippage_bps === undefined)
    ? t("ex.plan.slippageNa", "slippage n/a")
    : t("ex.plan.slippage", "slippage {v} bps")
        .replace("{v}", exNum(p.slippage_bps, 2));
  const fee = (p.fee_usd === null || p.fee_usd === undefined)
    ? t("ex.plan.feeNote", "fee {note}")
        .replace("{note}", raw(p.fee_note) || "\u2014")   // note verbatim
    : t("ex.plan.fee", "fee {v} USD").replace("{v}", exNum(p.fee_usd));

  const lines = [
    t("ex.plan.qty", "qty {v}").replace("{v}", exNum(p.qty)) + " \u00b7 " +
      t("ex.plan.price", "price {v}").replace("{v}", price),
    t("ex.plan.notional", "notional {v} USD")
        .replace("{v}", exNum(p.notional_usd)) + " \u00b7 " + slip,
    fee,
  ];
  const exp = exExpiryText(p);
  const expEl = exp ? `<div class="stat-note" data-ex-exp="${escapeHtml(id)}">` +
    `${escapeHtml(exp)}</div>` : "";
  const tail = [];
  if (raw(p.order_id)) tail.push(
    t("ex.plan.orderId", "order {v}").replace("{v}", raw(p.order_id)));
  if (raw(p.reason)) tail.push(
    t("ex.plan.reason", "reason: {v}").replace("{v}", raw(p.reason)));

  const busy = !!exDeciding[id];
  const acts = status === "pending"
    ? [["approve", "ex.plan.approve", "APPROVE", true],
       ["reject", "ex.plan.reject", "REJECT", false]]
    : status === "approved"
      ? [["cancel", "ex.plan.cancel", "CANCEL", false]] : [];
  const buttons = acts.map(a =>
    `<button class="btn${a[3] ? " primary" : ""}" type="button"` +
    ` data-ex-act="${a[0]}" data-plan="${escapeHtml(id)}"${busy ? " disabled" : ""}>` +
    `${escapeHtml(t(a[1], a[2]))}</button>`).join("");
  let msg = "";
  if (busy) {
    msg = `<span class="save-state">${escapeHtml(
      t("ex.plan.working", "working\u2026"))}</span>`;
  } else if (exPlanMsg && exPlanMsg.id === id) {
    msg = `<span class="save-state err">${escapeHtml(String(exPlanMsg.raw))}</span>`;
  }

  return `<div class="mini" data-plan="${escapeHtml(id)}">` +
    `<div class="gal-name">${escapeHtml(venue)}${sym ? " \u00b7 " + escapeHtml(sym) : ""}</div>` +
    (badges ? `<div class="head-status" style="margin-top:6px">${badges}</div>` : "") +
    lines.map(l => `<div class="stat-note">${escapeHtml(l)}</div>`).join("") +
    expEl +
    tail.map(l => `<div class="stat-note">${escapeHtml(l)}</div>`).join("") +
    ((acts.length || msg)
      ? `<div class="ctl-actions">${buttons}${msg}</div>` : "") +
    `</div>`;
}

function renderExThreshold() {
  const line = document.getElementById("exThreshold");
  const hint = document.getElementById("exThresholdHint");
  const w = exW3();
  if (!line) return;
  if (!w) {
    line.innerHTML = `<span class="chip">${escapeHtml(t("ex.thr.needCaps",
      "threshold data unavailable \u2014 this build has no web3 capability block"))}</span>`;
    if (hint) hint.hidden = true;
    return;
  }
  const chips = [
    t("ex.thr.perTx", "per-tx cap {v} USD")
      .replace("{v}", exNum(w.auto_usd_per_tx)),
    t("ex.thr.daily", "daily cap {v} USD")
      .replace("{v}", exNum(w.auto_usd_daily)),
    t("ex.thr.spent", "spent today {v} USD \u00b7 {day}")
      .replace("{v}", exNum(w.spent_today_usd))
      .replace("{day}", w.ledger_day ? String(w.ledger_day) : "\u2014"),
  ];
  line.innerHTML = chips.map(c =>
    `<span class="chip">${escapeHtml(c)}</span>`).join("");
  if (hint) {
    const zero = Number(w.auto_usd_per_tx) === 0 && Number(w.auto_usd_daily) === 0;
    hint.hidden = !zero;
    if (zero) hint.textContent = t("ex.thr.hint",
      "both caps are 0 \u2014 nothing auto-approves; every plan waits for your decision here");
  }
}

function renderExPlans() {
  const w = exW3();
  const cnt = document.getElementById("exPlansCount");
  if (cnt) {
    cnt.textContent = w
      ? t("ex.plans.count", "{p} pending \u00b7 {a} approved")
          .replace("{p}", exNum(w.pending_plans, 0))
          .replace("{a}", exNum(w.approved_plans, 0))
      : "";
  }
  const ex = exBlock();
  const gate = document.getElementById("exGate");
  if (gate) {
    const g = ex && ex.order_gate ? String(ex.order_gate) : "";
    gate.textContent = g
      ? t("ex.gate", "approval gate {v}").replace("{v}", g) : "";
    gate.hidden = !g;
  }

  const list = document.getElementById("exPlans");
  if (!list) return;
  const parts = [];
  if (exPlansErr) {   // the server's own string, verbatim — never silent
    parts.push(`<div class="save-state err">${escapeHtml(exPlansErr)}</div>`);
  }
  if (exPlans === null) {
    if (!exPlansErr) parts.push(`<div class="gal-empty">${escapeHtml(
      t("ex.plans.loading", "loading plans\u2026"))}</div>`);
  } else if (!exPlans.length) {
    parts.push(`<div class="gal-empty">${escapeHtml(t("ex.plans.empty",
      "no plans waiting \u2014 plans are created by exchange_prepare_order; nothing here executes by itself"))}</div>`);
  } else {
    parts.push(exPlans.map(exPlanCard).join(""));
  }
  list.innerHTML = parts.join("");
  list.querySelectorAll("[data-ex-act]").forEach(btn => {
    btn.addEventListener("click", () =>
      exDecide(btn.getAttribute("data-plan"), btn.getAttribute("data-ex-act")));
  });
}

/* per-second expiry line refresh (only the clock/countdown text changes —
   no full re-render, so in-flight buttons keep their state) */
function exTick() {
  if (currentView !== "exchange" || !Array.isArray(exPlans) || !exPlans.length) return;
  document.querySelectorAll("[data-ex-exp]").forEach(el => {
    const id = el.getAttribute("data-ex-exp");
    const p = exPlans.find(x => x && String(x.id) === id);
    if (p) el.textContent = exExpiryText(p);
  });
}

/* ---------------------------------------------------------------- status -- */
function renderStatus() {
  if (!statusData) return;
  const panels = statusData.panels || [];
  const adv = statusData.advisory || { level: "info", reasons: [] };

  // service health summary — the retired ring's information, carried as text
  const up = panels.filter(p => p.state === "up").length;
  document.getElementById("svcSummary").textContent =
    (up === panels.length ? t("svc.all", "All systems nominal")
                          : t("svc.degraded", "System degraded")) +
    " · " + t("svc.sub", "{n}/5 online").replace("{n}", up) +
    " · " + (statusData.version || "") +
    // Car F: active profile name (bare noun, no locale key); absent/empty
    // field = default profile = pre-car-F output, byte for byte.
    ((statusData.profile || "") ? " · " + statusData.profile : "");

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
      boot.style.color = "var(--amber)"; boot.style.borderColor = "rgba(194,116,10,.6)";
      boot.title = t("head.rebootSeen", "reboot detected (see stream)");
    }
  }

  // service cards (Overview)
  document.getElementById("panels").innerHTML = panels.map(p => `
    <div class="mini ${p.state}">
      <span class="m-dot" aria-hidden="true"></span>
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
  const d = dispInfo();
  const unitEl = document.getElementById("costUnit");
  const setUnit = () => {
    unitEl.textContent = (d.code === "CNY" ? t("usage.unitCny", "CNY TODAY")
                                           : t("usage.unit", "{c} TODAY").replace("{c}", d.code));
  };
  if (!today) {
    document.getElementById("costVal").textContent = "—";
    setUnit();
    document.getElementById("usageStats").innerHTML = "";
    document.getElementById("usageNote").textContent = t("usage.off", "usage source unavailable");
    return;
  }
  document.getElementById("costVal").textContent = fmtCost(today.cost);
  setUnit();
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

/* Unknown event codes fall back to the server's own text (raw log line):
   t(key, null) returns the key itself, which would print "event.<code>" in the
   stream instead of the real message. */
function eventText(e) {
  const key = e.code ? "event." + e.code : "";
  if (key && dict[key] !== undefined) {
    return t(key)
      .replace(/\{src\}/g, t("panel." + e.src, e.src))
      .replace("{v}", (statusData && statusData.version) || "…");
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

/* ------------------------------------------------------------ evolution -- */
let evoData = null;

async function refreshEvolution() {
  try {
    evoData = await fetch("/api/evolution").then(r => r.json());
    renderEvolution();
  } catch (e) { /* keep last render */ }
}

function renderEvolution() {
  if (!evoData) return;
  const sc = evoData.schedule || {};
  const badge = document.getElementById("evoBadge");
  badge.textContent = evoData.enabled ? t("common.on", "enabled") : t("common.off", "off");
  badge.className = "evo-badge " + (evoData.enabled ? "on" : "off");
  let line = t("evo.status", "cron daily {d} · weekly {w} · monthly day {m}")
    .replace("{d}", escapeHtml(sc.daily_at || "-"))
    .replace("{w}", escapeHtml(t("wd." + sc.weekly_day, sc.weekly_day || "-")))
    .replace("{m}", escapeHtml(String(sc.monthly_day === undefined ? "-" : sc.monthly_day)));
  if ((evoData.due_now || []).length) {
    line += " · " + t("evo.due", "due now: {p}").replace("{p}",
      escapeHtml(evoData.due_now.join(", ")));
  }
  document.getElementById("evoStatus").innerHTML = line;
  evoCrossCheck();             // B2: settings form vs this active schedule
  const tail = (evoData.log_tail || []).slice(-5).reverse();
  document.getElementById("evoLog").innerHTML = tail.length
    ? tail.map(e => `<li><span class="evo-ts">${escapeHtml(String(e.ts).slice(0, 16).replace("T", " "))}</span>` +
        ` <span class="evo-kind">${escapeHtml(e.kind)}</span>` +
        ` <span class="evo-sum">${escapeHtml(e.summary)}</span></li>`).join("")
    : `<li class="evo-empty">${escapeHtml(t("evo.logEmpty", "no evolution entries yet"))}</li>`;
}

function evoMsg(kind, text) {
  const el = document.getElementById("evoMsg");
  el.className = "evo-msg " + kind;
  el.textContent = text;
}

async function evoPost(url, payload) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) { evoMsg("err", t("save.needToken", "token required")); return null; }
  try {
    const res = await fetch(url, { method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) { evoMsg("err", t("save.badToken", "401 · unauthorized")); return null; }
    if (!res.ok) {
      const details = (body.details || [body.error || "error"]).map(d => t("rule." + d, d));
      evoMsg("err", details.join("; ").slice(0, 90));
      return null;
    }
    return body;
  } catch (e) { evoMsg("err", String(e).slice(0, 60)); return null; }
}

async function evoLearn() {
  const kind = document.getElementById("evoKind").value;
  const content = document.getElementById("evoBody").value;
  const payload = { kind,
    name: document.getElementById("evoName").value,
    description: document.getElementById("evoDesc").value,
    body: content, text: content };
  const body = await evoPost("/api/learn", payload);
  if (!body) return;
  let msg = t("evo.learned", "learned → {p}").replace("{p}", body.path);
  if ((body.prescreen || []).length) msg += " · " + body.prescreen.join("; ");
  evoMsg("ok", msg.slice(0, 120));
  document.getElementById("evoBody").value = "";
  refreshEvolution(); refreshSkills();   // new skill shows up in the armory
}

async function evoReflect() {
  const period = document.getElementById("evoPeriod").value;
  const body = await evoPost("/api/evolution", { action: "reflect", period });
  if (!body) return;
  const key = body.created ? "evo.reflectOk" : "evo.reflectExists";
  const fb = body.created ? "draft → {p}" : "already drafted → {p}";
  evoMsg("ok", t(key, fb).replace("{p}", body.path));
  refreshEvolution();
}

/* --------------------------------------------------------------- chat ---- */
let chatBusy = false;
/* Prompt queue (car 4a-M3, client-only — the server still runs one turn at
   a time): submissions during a streamed turn wait in an editable bar above
   the input; the bar drains FIFO when the current turn finishes. Max 5. */
const CHAT_QUEUE_MAX = 5;
let chatQueue = [];
let chatQPending = false;        // head item in its dispatch flash (pending)
let chatProgEl = null, chatProgTimer = 0;   // MCP progress status line

function chatNote(kind, text) {
  const el = document.getElementById("chatMsg");
  el.className = "chat-msg " + kind;
  el.textContent = text;
}

function chatBadge(live) {
  const el = document.getElementById("chatBadge");
  el.textContent = live ? t("chat.live", "streaming") : t("chat.idle", "idle");
  el.className = "chat-badge" + (live ? " live" : "");
}

function chatBubble(role, text) {
  document.getElementById("chatEmpty")?.remove();
  const row = document.createElement("div");
  row.className = "chat-row " + role;
  const b = document.createElement("div");
  b.className = "chat-bubble";
  b.textContent = text;
  row.appendChild(b);
  const log = document.getElementById("chatLog");
  log.appendChild(row);
  log.scrollTop = log.scrollHeight;
  return b;
}

function chatToolLine(name) {   // tool activity as its own line above the live bubble
  const log = document.getElementById("chatLog");
  const el = document.createElement("div");
  el.className = "chat-tools-row";
  el.textContent = "⚙ " + name;
  log.insertBefore(el, log.lastElementChild);
  log.scrollTop = log.scrollHeight;
}

/* --- prompt queue bar: injected above the form (index.html untouched) --- */
function chatQueueBar() {
  let bar = document.getElementById("chatQueue");
  if (bar) return bar;
  const form = document.querySelector(".chat-form");
  if (!form || !form.parentNode) return null;      // view not rendered
  bar = document.createElement("div");
  bar.id = "chatQueue";                            // new DOM id (M3, additive)
  bar.className = "chat-queue";
  bar.hidden = true;
  bar.style.cssText = "display:flex;flex-direction:column;gap:4px;" +
    "margin:6px 0 0;padding:0;font-size:12px;";
  form.parentNode.insertBefore(bar, form);
  return bar;
}

function chatQueueRender() {
  const bar = chatQueueBar();
  if (!bar) return;
  bar.textContent = "";
  bar.hidden = chatQueue.length === 0;
  chatQueue.forEach((text, i) => {
    const row = document.createElement("div");
    const pending = chatQPending && i === 0;
    row.style.cssText = "display:flex;align-items:center;gap:8px;" +
      "border:1px solid var(--line-solid);border-radius:8px;" +
      "padding:4px 8px;background:" + (pending ? "var(--blue-wash)" : "#fff") + ";" +
      "opacity:" + (pending ? ".75" : "1") + ";";
    const label = document.createElement("span");
    label.style.cssText = "flex:1;min-width:0;overflow:hidden;" +
      "text-overflow:ellipsis;white-space:nowrap;color:var(--muted);";
    label.textContent = (pending ? "⏳ " : (i + 1) + ". ") + text;
    row.appendChild(label);
    const mk = (txt, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = txt;
      b.style.cssText = "border:1px solid var(--line);background:#fff;" +
        "border-radius:6px;padding:2px 8px;font-size:12px;cursor:pointer;" +
        "color:var(--text);";
      b.addEventListener("click", fn);
      return b;
    };
    if (!pending) {
      row.appendChild(mk(t("chat.qEdit", "Edit"), () => {
        const input = document.getElementById("chatInput");
        chatQueue.splice(i, 1);
        chatQueueRender();
        if (input) { input.value = text; input.focus(); }
      }));
      row.appendChild(mk(t("chat.qCancel", "Cancel"), () => {
        chatQueue.splice(i, 1);
        chatQueueRender();
      }));
    }
    bar.appendChild(row);
  });
}

function chatQueueNext(flash) {
  /* Dispatch the head item when idle. `flash` shows its brief pending state
     before the turn starts (queued dispatches); an immediate submission
     (queue was empty, turn idle) goes straight out — zero added latency. */
  if (chatBusy || chatQPending || !chatQueue.length) return;
  const start = () => {
    const text = chatQueue.shift();
    chatQPending = false;
    chatQueueRender();
    chatTurn(text);
  };
  if (flash) {
    chatQPending = true;
    chatQueueRender();
    setTimeout(start, 150);
  } else {
    start();
  }
}

/* --- MCP progress status line (car 4a-M3): transient, self-cleaning --- */
function chatProgressClear() {
  clearTimeout(chatProgTimer);
  if (chatProgEl) { chatProgEl.remove(); chatProgEl = null; }
}

function chatProgress(data) {
  const log = document.getElementById("chatLog");
  if (!log) return;
  if (!chatProgEl || !chatProgEl.isConnected) {
    document.getElementById("chatEmpty")?.remove();
    chatProgEl = document.createElement("div");
    chatProgEl.className = "chat-progress";
    chatProgEl.style.cssText = "font-size:12px;color:var(--muted);" +
      "font-family:Consolas,monospace;padding:2px 4px;" +
      "transition:opacity .35s ease;";
    log.insertBefore(chatProgEl, log.lastElementChild);
  }
  const who = [data.server, data.tool].filter(Boolean).join("/");
  const msg = data.message ? " · " + data.message : "";
  const num = typeof data.progress === "number" ? " · " + data.progress : "";
  chatProgEl.textContent = "◌ " + who + msg + num;   // bounded server fields only
  log.scrollTop = log.scrollHeight;
  clearTimeout(chatProgTimer);
  chatProgTimer = setTimeout(() => {        // fade then drop after 2s idle
    if (chatProgEl) chatProgEl.style.opacity = "0";
    chatProgTimer = setTimeout(chatProgressClear, 380);
  }, 2000);
}

async function loadChatHistory() {
  try {
    const h = await fetch("/api/chat/history").then(r => r.json());
    const msgs = (h.messages || []).slice(-40);
    if (!msgs.length) return;
    document.getElementById("chatEmpty")?.remove();
    for (const m of msgs) chatBubble(m.role, m.content);
  } catch (e) { /* empty state stays */ }
}

function chatSend() {
  /* Input stays ENABLED while streaming (car 4a-M3): submissions made
     during a turn join the editable queue bar; a free submission dispatches
     immediately. Queue order == submission order (the server is serial). */
  const input = document.getElementById("chatInput");
  const text = input.value.trim();
  if (!text) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) { chatNote("err", t("chat.needToken", "access token required (set it in Settings)")); return; }
  chatNote("", "");
  input.value = "";
  if (chatBusy) {
    if (chatQueue.length >= CHAT_QUEUE_MAX) {
      chatNote("err", t("chat.qFull", "queue is full (5) — wait for the current turn"));
      input.value = text;             // not accepted: hand the text back
      return;
    }
    chatQueue.push(text);
    chatQueueRender();
    return;
  }
  chatQueue.push(text);
  chatQueueNext(false);
}

async function chatTurn(text) {
  const input = document.getElementById("chatInput");
  chatBusy = true;
  chatBadge(true);
  chatBubble("user", text);
  const bubble = chatBubble("assistant", "");
  bubble.classList.add("pending");
  let acc = "";
  let meta = null;
  const handle = (ev, data) => {
    if (ev === "delta") {
      acc += data.text || "";
      bubble.textContent = acc;
      const log = document.getElementById("chatLog");
      log.scrollTop = log.scrollHeight;
    } else if (ev === "tool") {
      chatToolLine(data.name || "?");
    } else if (ev === "progress") {
      chatProgress(data);
    } else if (ev === "done") {
      chatProgressClear();
      meta = data;
    } else if (ev === "error") {
      chatNote("err", t("chat.err", "stream failed — retry"));
    }
  };
  const token = localStorage.getItem(TOKEN_KEY) || "";
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ text }) });
    if (!res.ok) {                       // 401/400 answered before the stream
      const body = await res.json().catch(() => ({}));
      chatNote("err", res.status === 401
        ? t("chat.badToken", "401 · unauthorized")
        : ((body.details || [body.error || "error"])[0] + ""));
      bubble.remove();
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i); buf = buf.slice(i + 2);
        let ev = "message", data = "";
        for (const ln of frame.split("\n")) {
          if (ln.startsWith("event:")) ev = ln.slice(6).trim();
          else if (ln.startsWith("data:")) data += ln.slice(5).trim();
        }
        try { handle(ev, JSON.parse(data)); } catch (e) { /* skip frame */ }
      }
    }
    if (meta && typeof meta.text === "string") {
      bubble.textContent = meta.text;          // authoritative replace
      document.getElementById("chatMeta").textContent =
        t("chat.meta", "{i} iterations · {t} tool calls")
          .replace("{i}", meta.iterations).replace("{t}", meta.tool_calls);
      if (!meta.text.trim()) bubble.textContent = t("chat.noOutput", "(no output)");
    } else if (!acc) {
      bubble.textContent = t("chat.noOutput", "(no output)");
    }
  } catch (e) {
    chatNote("err", t("chat.err", "stream failed — retry"));
    if (!acc) bubble.remove();
  } finally {
    bubble.classList.remove("pending");
    chatProgressClear();
    chatBusy = false;
    input.focus();
    chatBadge(false);
    chatQueueRender();
    if (chatQueue.length) chatQueueNext(true);   // drain FIFO with a pending flash
  }
}

async function chatClear() {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) { chatNote("err", t("chat.needToken", "access token required (set it in Settings)")); return; }
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "clear" }) });
    if (res.status === 401) {
      chatNote("err", t("chat.badToken", "401 · unauthorized")); return;
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      chatNote("err", t("chat.err", "stream failed — retry")); return;
    }
    document.getElementById("chatLog").innerHTML =
      `<div class="chat-empty" id="chatEmpty">${escapeHtml(t("chat.empty", "no messages yet"))}</div>`;
    document.getElementById("chatMeta").textContent = "";
    chatNote("ok", body.archived ? t("chat.cleared", "session archived")
                                 : t("chat.empty", "no messages yet"));
  } catch (e) {
    chatNote("err", t("chat.err", "stream failed — retry"));
  }
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
let updApplying = false, updToastTimer = null;
function renderVersion() {
  const banner = document.getElementById("updateBanner");
  const applyBtn = document.getElementById("updateApplyBtn");
  // the APPLY control exists ONLY while an update is genuinely available
  // (and never mid-apply); a null/failed check shows neither button state —
  // that outcome is reported by the check toast instead.
  if (applyBtn) {
    applyBtn.hidden = updApplying || !(versionData &&
      versionData.update_available === true);
  }
  if (!versionData) { banner.hidden = true; return; }
  if (versionData.update_available &&
      sessionStorage.getItem("aidog_upd_dismiss") !== versionData.latest) {
    banner.hidden = false;
    document.getElementById("updateText").textContent =
      t("upd.text", "UPDATE v{v} AVAILABLE").replace("{v}", versionData.latest) +
      (versionData.notes ? " — " + versionData.notes.slice(0, 90) : "");
  } else banner.hidden = true;
}

/* ------------------------------------------- update check / apply (0.5.40) --
   Three honest check outcomes: latest / update available / check failed
   (offline or manifest error, reported verbatim). Apply runs detached on the
   server — the console WILL restart mid-apply, so poll failures during that
   window mean "still applying, keep waiting", and the 120s timeout says so
   instead of pretending success. */
const actSleep = ms => new Promise(r => setTimeout(r, ms));

function updToast(text, isErr) {
  const el = document.getElementById("updToast");
  if (!el) return;
  el.textContent = text;
  el.style.borderLeftColor = isErr ? "var(--red)" : "var(--blue)";
  el.style.color = isErr ? "var(--red)" : "var(--navy)";
  el.hidden = false;
  clearTimeout(updToastTimer);
  updToastTimer = setTimeout(() => { el.hidden = true; }, 8000);
}

async function updateCheck() {
  const btn = document.getElementById("updateCheckBtn");
  if (btn) btn.disabled = true;
  try {
    const res = await fetch("/api/version?fresh=1");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    if (!d || typeof d !== "object") throw new Error("bad payload");
    versionData = d;
    renderVersion();
    if (versionData.update_available === true) {
      updToast(t("upd.text", "UPDATE v{v} AVAILABLE")
        .replace("{v}", String(versionData.latest || "")), false);
    } else if (versionData.update_available === false) {
      updToast(t("upd.latest", "checked — you are up to date (v{v})")
        .replace("{v}", String(versionData.installed || "")), false);
    } else {
      updToast(t("upd.checkFail", "update check failed: {e}")
        .replace("{e}", String(versionData.error || "unknown error")), true);
    }
  } catch (e) {
    updToast(t("upd.checkFail", "update check failed: {e}")
      .replace("{e}", String(e).slice(0, 120)), true);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function updateApply() {
  if (updApplying) return;
  if (!versionData || versionData.update_available !== true) return;
  if (!window.confirm(t("upd.confirm",
      "Apply the update now? Services restart during the upgrade."))) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    updToast(t("save.needToken", "token required"), true);
    return;
  }
  const btn = document.getElementById("updateApplyBtn");
  updApplying = true;
  renderVersion();            // APPLY hides itself while the apply runs
  updToast(t("upd.applying",
    "applying update — the console may restart while it runs; keep this page open"), false);
  const done = () => {
    updApplying = false;
    if (btn) btn.disabled = false;
    renderVersion();
  };
  try {
    const res = await fetch("/api/update/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({}),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      updToast(t("save.badToken", "401 · unauthorized"), true);
      done(); return;
    }
    if (!res.ok) {
      const err = (body && (body.error || (Array.isArray(body.details) &&
        body.details.join("; ")))) || ("HTTP " + res.status);
      updToast(t("upd.applyFail", "apply failed: {e}")
        .replace("{e}", String(err)), true);
      done(); return;
    }
  } catch (e) {
    updToast(t("upd.applyFail", "apply failed: {e}")
      .replace("{e}", String(e).slice(0, 120)), true);
    done(); return;
  }
  // queued: poll a fresh version read every 3s for up to 120s. The console
  // dying mid-apply is the designed behaviour — every failed fetch here is
  // "still applying", never an error.
  const from = String((versionData && versionData.installed) || "");
  const deadline = Date.now() + 120000;
  while (Date.now() < deadline) {
    await actSleep(3000);
    let d = null;
    try {
      const r = await fetch("/api/version?fresh=1");
      if (r.ok) d = await r.json();
    } catch (e) { d = null; }      // console restarting — keep waiting
    if (d && typeof d === "object" &&
        (String(d.installed || "") || "") !== "" &&
        (from ? String(d.installed) !== from : d.update_available === false)) {
      versionData = d;
      updToast(t("upd.applied", "update applied — v{v} is running")
        .replace("{v}", String(d.installed)), false);
      done(); return;
    }
  }
  updToast(t("upd.applyUnknown",
    "the update never reported back — check data/update_apply.log or the terminal"), true);
  done();
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
    const med = settings.media || {};
    const sttSel = document.getElementById("sttModel");
    if (sttSel) sttSel.value = med.stt_model || "base";
    const engSel = document.getElementById("ttsEngine");
    if (engSel) engSel.value = med.tts_engine || "edge";
    const setField = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.value = val == null ? "" : String(val);
    };
    setField("imgBaseUrl", med.image_base_url);
    setField("imgModelName", med.image_model);
    setField("ttsBaseUrl", med.tts_base_url);
    setField("ttsModelName", med.tts_model);
    setField("wakePhrases", Array.isArray(med.wake_phrases)
      ? med.wake_phrases.join(", ") : "");
    document.getElementById("alertSelect").value = settings.alert_preview || "off";
    document.getElementById("refreshSelect").value = String(settings.refresh_sec || 10);
    const stored = localStorage.getItem(LANG_KEY);
    // An explicit ?lang= view override wins over the saved setting for this
    // page view — otherwise the settings fetch races loadLang() and can flip
    // the UI back to the saved language after the pack already applied.
    if (!stored && !QS_LANG && settings.language && settings.language !== document.documentElement.lang) {
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
  // any non-success outcome below must leave nothing repaintable behind
  delete state.dataset.saveKind;
  delete state.dataset.restartHint;
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
    media: {
      stt_model: (document.getElementById("sttModel") || {}).value || "base",
      tts_engine: (document.getElementById("ttsEngine") || {}).value || "edge",
      image_base_url: ((document.getElementById("imgBaseUrl") || {}).value || "").trim(),
      image_model: ((document.getElementById("imgModelName") || {}).value || "").trim(),
      tts_base_url: ((document.getElementById("ttsBaseUrl") || {}).value || "").trim(),
      tts_model: ((document.getElementById("ttsModelName") || {}).value || "").trim(),
      wake_phrases: ((document.getElementById("wakePhrases") || {}).value || "")
        .split(",").map(s => s.trim()).filter(s => s.length > 0).slice(0, 3),
    },
    alert_preview: document.getElementById("alertSelect").value,
  };
  // --- car B1: blocks the operator actually edited join the payload here.
  //     Nothing is ever added for a clean block, so the legacy 16-key
  //     payload above keeps its exact behavior. Credential values never
  //     enter this document. ---
  if (usageDirty) {
    payload.usage = {
      metrics_url: ((document.getElementById("usageMetricsUrl") || {}).value || "").trim(),
      pricing_config: ((document.getElementById("usagePricingCfg") || {}).value || "").trim(),
      log_dir: ((document.getElementById("usageLogDir") || {}).value || "").trim(),
    };
  }
  const mycExtra = mycoreExtraPayload();
  if (mycExtra) Object.assign(payload.mycore, mycExtra);
  // keys the chat service reads ONCE at start: editing them needs a restart
  // (a models/budget edit is live and never raises mycRestartDirty)
  const needSvcRestart = !!(mycRestartDirty ||
    (toolsDirty && Object.prototype.hasOwnProperty.call(payload.mycore, "tools_off")));
  if (chanDirty) payload.channels = channelsPayload();
  const hookPatch = hooksPayload();
  if (hookPatch) payload.hooks = hookPatch;
  const hookMsg = document.getElementById("hookMsg");
  const chanMsg = document.getElementById("chanMsg");
  if (hookMsg) { hookMsg.className = "save-state"; hookMsg.textContent = ""; }
  if (chanMsg) { chanMsg.className = "save-state"; chanMsg.textContent = ""; }
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
      // the whole write is all-or-nothing: show EVERY server detail verbatim
      // (they name keys / slots / grammar reasons — never a value), and mirror
      // the block-owned details into their own status line.
      const details = Array.isArray(body.details) ? body.details : [];
      state.className = "save-state err";
      state.textContent = details.length ? details.join("; ")
        : (body.error || "error");
      const hookErrs = details.filter(d => String(d).includes("hooks"));
      const chanErrs = details.filter(d => String(d).includes("channels"));
      if (hookMsg && hookErrs.length) {
        hookMsg.className = "save-state err";
        hookMsg.textContent = hookErrs.join("; ");
      }
      if (chanMsg && chanErrs.length) {
        chanMsg.className = "save-state err";
        chanMsg.textContent = chanErrs.join("; ");
      }
      return;
    }
    settings = Object.assign(settings, body.settings || payload);
    // restart-to-apply semantics (activation car): a save that touched the
    // registry-at-start mycore keys says so in the save row — the button
    // lives in the Overview activation card.
    let okText = body.changed ? t("save.ok", "saved ✓") : t("save.same", "no change ✓");
    if (needSvcRestart) {
      okText += " · " + t("ctl.restartSvcHint",
        "restart the chat service to apply — button in Overview");
    }
    state.className = "save-state";
    state.textContent = okText;
    // remembered so a language switch can repaint this row (settingsRelang)
    state.dataset.saveKind = body.changed ? "ok" : "same";
    if (needSvcRestart) state.dataset.restartHint = "1";
    else delete state.dataset.restartHint;
    input.value = "";
    // accepted -> the form is the source of truth again: clear the dirty
    // marks and re-read the rows the server normalized (hooks/channels/tools).
    chanDirty = false; usageDirty = false; mycDirty = false;
    mycRestartDirty = false;
    if (hookPatch) { hookDirty.clear(); renderHooks(); }
    if (payload.channels) { renderChannels(); loadChanStatus(); }
    if (toolsDirty) { toolsDirty = false; renderToolsOff(); }
    beep(880, 0.06);
    scheduleRefresh();
    refreshUsage();            // currency/fx may have changed (server cache cleared)
    refreshCaps();             // tools_off / shell / env flags may have changed
  } catch (e) {
    state.className = "save-state err";
    state.textContent = String(e).slice(0, 50);
  }
}

/* ------------------------------------------- settings panels (car B1) --
   Frontend coverage car, lane B1: the console faces for the remaining
   settings blocks (usage paths, mycore extras, channel adapters, event
   hooks) plus the runtime credential-slot editor.
   Hard rules carried in this section:
     - a credential VALUE never leaves its password input: it is never
       copied into another node, attribute, log line or payload — the
       status line shows slot name + length only, and SET clears the
       input immediately;
     - every panel is fail-soft: an endpoint this build does not have
       (404 / network / shape) degrades to an honest "unavailable" note,
       never to a fake state;
     - only blocks the operator actually edited join the save payload;
       the legacy 16-key payload above is built exactly as before. */
const HOOK_EVENTS = ["turn_start", "turn_end", "tool_call", "compact",
                     "cron_fire", "error"];
/* JS mirror of web/server.py::_CHANNEL_SCHEMA (9 routes): [field, kind]
   kinds: bool = checkbox · list = comma-separated strings ·
          port = integer 1..65535 · str = single-line string */
const CHAN_SCHEMA = {
  discord: [["enabled", "bool"], ["allow", "list"], ["channel_ids", "list"]],
  slack: [["enabled", "bool"], ["allow", "list"], ["channel_ids", "list"]],
  email: [["enabled", "bool"], ["allow", "list"], ["to", "str"],
          ["from", "str"], ["smtp_host", "str"], ["smtp_port", "port"],
          ["smtp_ssl", "bool"], ["imap_host", "str"], ["imap_port", "port"]],
  webhook: [["enabled", "bool"], ["url", "str"]],
  dingtalk: [["enabled", "bool"]],
  feishu: [["enabled", "bool"], ["allow", "list"], ["chat_ids", "list"]],
  wechat: [["enabled", "bool"]],
  whatsapp: [["enabled", "bool"], ["phone_number_id", "str"]],
  signal: [["enabled", "bool"], ["url", "str"], ["allow", "list"]],
};
const CHAN_FIELD_KEYS = {
  enabled: "chan.enabled", allow: "chan.allow", channel_ids: "chan.channelIds",
  chat_ids: "chan.chatIds", to: "chan.to", from: "chan.from",
  smtp_host: "chan.smtpHost", smtp_port: "chan.smtpPort",
  smtp_ssl: "chan.smtpSsl", imap_host: "chan.imapHost",
  imap_port: "chan.imapPort", url: "chan.url",
  phone_number_id: "chan.phoneId",
};
const CRED_GROUPS = ["core", "gateway", "web3", "channels", "media",
                     "connectors"];
const CRED_HINTS = {
  unavailable: ["cred.unavailable",
    "credential slots unavailable — this build has no GET /api/secrets"],
  readOnly: ["cred.readOnly",
    "read-only: no WEB_TOKEN slot on this machine — rotate it by hand"],
  needToken: ["cred.needToken",
    "access token required — set it in Access"],
};

let credsData = null;      // last GET /api/secrets payload (names/lengths only)
let credHintReason = "";   // "" | unavailable | readOnly | needToken
let chanStatusRows = [];   // GET /api/channels rows (name/enabled/configured/note)
let chanDirty = false, usageDirty = false, mycDirty = false, toolsDirty = false;
// activation car: only the keys the chat service reads AT START raise this
// flag (tools_off / mcp / shell / context_dirs) — a models/budget edit must
// never nag for a restart it does not need.
let mycRestartDirty = false;
const hookDirty = new Set();

/* ---------------------------------------------------------- credentials -- */
function credStatusText(slot) {
  const s = slot && typeof slot === "object" ? slot : {};
  const name = String(s.name || "");
  if (s.set && typeof s.length === "number") {
    return name + " · " + t("cred.st.chars", "{n} chars").replace("{n}", String(s.length));
  }
  if (s.set) return name + " · " + t("cred.st.set", "set");
  return name + " · " + t("cred.st.empty", "empty");
}

function paintCredHint() {
  const hint = document.getElementById("credTokenHint");
  if (!hint) return;
  if (!credHintReason) { hint.hidden = true; hint.textContent = ""; return; }
  const spec = CRED_HINTS[credHintReason] || CRED_HINTS.needToken;
  hint.textContent = t(spec[0], spec[1]);
  hint.hidden = false;
}

/* Re-render the pure-data status lines ("NAME · set / empty / N chars")
   after a language switch — rows are built once, so their pack-driven words
   need an explicit repaint. Rows carrying an error keep their detail. Both
   credential containers are covered (Credentials card + the Telegram card). */
function repaintCredStatuses() {
  if (!credsData || !Array.isArray(credsData.slots)) return;
  credsData.slots.forEach(slot => {
    if (!slot || !slot.name) return;
    document.querySelectorAll(`[data-slot="${slot.name}"]`).forEach(row => {
      const st = row.querySelector(".cred-status");
      if (!st || st.dataset.errDetail) return;
      st.textContent = credStatusText(slot) +
        (st.dataset.restartHint === "1"
          ? " · " + t("cred.restartHint", "restart the chat service to apply")
          : "");
    });
  });
}

function credErrDetail(st, detail) {
  st.dataset.errDetail = String(detail);
  st.className = "cred-status save-state err";
  st.textContent = t("cred.err", "error: ") + String(detail);
}

function credRow(slot, writable, labelKey) {
  const name = String(slot && slot.name ? slot.name : "");
  const wrap = document.createElement("div");
  wrap.dataset.slot = name;
  const row = document.createElement("div");
  row.className = "ctl-row token-row";
  row.style.marginTop = "6px";
  const label = document.createElement("label");
  if (labelKey) {                 // friendly label (Telegram card rows)
    label.dataset.i18n = labelKey;
    label.textContent = t(labelKey, name);
  } else {
    label.textContent = name;
    label.style.fontFamily = "var(--mono)";
  }
  row.appendChild(label);
  const st = document.createElement("div");
  st.className = "cred-status stat-note";
  st.textContent = credStatusText(slot);
  if (name === "WEB_TOKEN") {
    // presence-only: the console never rewrites its own gate
    st.style.marginLeft = "auto";
    row.appendChild(st);
    wrap.appendChild(row);
    const note = document.createElement("div");
    note.className = "stat-note";
    note.dataset.i18n = "cred.rotateHint";
    note.textContent = t("cred.rotateHint",
      "WEB_TOKEN is presence-only here: rotate it by hand in secrets.env");
    wrap.appendChild(note);
    return wrap;
  }
  const input = document.createElement("input");
  input.type = "password";
  input.className = "cred-input";
  input.autocomplete = "new-password";
  input.maxLength = 4096;
  input.dataset.i18nPh = "cred.ph";
  input.placeholder = t("cred.ph", "value (never displayed back)");
  if (!writable) input.disabled = true;
  const mk = (cls, key, fb) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn " + cls;
    b.dataset.i18n = key;
    b.textContent = t(key, fb);
    if (!writable) b.disabled = true;
    return b;
  };
  row.appendChild(input);
  row.appendChild(mk("cred-set", "cred.set", "SET"));
  row.appendChild(mk("cred-clear", "cred.clear", "CLEAR"));
  wrap.appendChild(row);
  wrap.appendChild(st);
  return wrap;
}

function renderCreds(body) {
  const list = document.getElementById("credList");
  if (!list) return;
  const slots = Array.isArray(body.slots) ? body.slots : [];
  const writable = body.writable !== false;
  list.textContent = "";
  const buckets = CRED_GROUPS.map(g => [g, []]);
  slots.forEach(s => {
    if (!s || typeof s !== "object" || !s.name) return;
    const g = typeof s.group === "string" && s.group ? s.group : "core";
    const bucket = buckets.find(b => b[0] === g);
    if (bucket) bucket[1].push(s); else buckets.push([g, [s]]);
  });
  buckets.filter(b => b[1].length).forEach(([g, items]) => {
    const head = document.createElement("div");
    head.style.cssText =
      "font-size:12px;letter-spacing:.14em;color:var(--blue-ink);" +
      "margin-top:14px;text-transform:uppercase;";
    if (CRED_GROUPS.includes(g)) {
      head.dataset.i18n = "cred.group." + g;
      head.textContent = t("cred.group." + g, g.toUpperCase());
    } else {
      head.textContent = g;          // unknown group: raw name, no invented label
    }
    list.appendChild(head);
    items.forEach(s => list.appendChild(credRow(s, writable)));
  });
}

/* -------------------------------------------------- Telegram card (0.5.43) --
   The two TG slots rendered into #tgCredList by the SAME presence-only
   mechanism as the Credentials card (credRow / credWrite / wireCredList):
   status line, inline SET/CLEAR, zero value echo. Rows only render for
   slots the server actually reported — an absent row is an honest absence,
   never a guessed state. */
const TG_CARD_SLOTS = [
  ["MYCORE_TG_TOKEN", "act.item.tg_token", "act.hint.tg_token",
   "the bot's login token — set it under Credentials"],
  ["AIDOG_TG_ALLOWED_USERS", "act.item.tg_allowlist", "act.hint.tg_allowlist",
   "numeric Telegram user ids, comma separated — who may talk to the bot"],
];

function renderTgCreds(body) {
  const list = document.getElementById("tgCredList");
  if (!list) return;
  const slots = Array.isArray(body.slots) ? body.slots : [];
  const writable = body.writable !== false;
  list.textContent = "";
  let shown = 0;
  TG_CARD_SLOTS.forEach(([name, labelKey, hintKey, hintFb]) => {
    const slot = slots.find(s => s && s.name === name);
    if (!slot) return;            // not in the payload: hide, never invent
    list.appendChild(credRow(slot, writable, labelKey));
    const hint = document.createElement("div");
    hint.className = "stat-note";
    hint.style.marginTop = "2px";
    hint.dataset.i18n = hintKey;
    hint.textContent = t(hintKey, hintFb);
    list.appendChild(hint);
    shown++;
  });
  list.hidden = shown === 0;
}

async function loadCreds() {
  const list = document.getElementById("credList");
  const tgList = document.getElementById("tgCredList");
  if (!list) return;
  try {
    const res = await fetch("/api/secrets");
    if (!res.ok) throw new Error("http " + res.status);
    const body = await res.json();
    if (!body || typeof body !== "object" || !Array.isArray(body.slots)) {
      throw new Error("bad shape");
    }
    credsData = body;
    renderCreds(body);
    renderTgCreds(body);
    list.hidden = false;
    credHintReason = body.writable === false ? "readOnly"
      : ((localStorage.getItem(TOKEN_KEY) || "") ? "" : "needToken");
  } catch (e) {                       // 404 / network: honest unavailable
    credsData = null;
    list.hidden = true;
    if (tgList) { tgList.textContent = ""; tgList.hidden = true; }
    credHintReason = "unavailable";
  }
  paintCredHint();
  renderOrch();                 // Lane I: slot count rides this fetch
}

async function credWrite(name, value, wrap, input) {
  const st = wrap.querySelector(".cred-status");
  if (!st) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    credHintReason = (credsData && credsData.writable === false) ? "readOnly" : "needToken";
    paintCredHint();
    st.className = "cred-status save-state err";
    st.textContent = t("cred.needToken", "access token required — set it in Access");
    return;
  }
  st.className = "cred-status save-state";
  st.textContent = t("cred.working", "saving…");
  try {
    const res = await fetch("/api/secrets", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ name: name, value: value }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      credErrDetail(st, details[0] || body.error || ("HTTP " + res.status));
      return;
    }
    if (input) input.value = "";      // the value never lingers anywhere
    const slot = Object.assign({ name: name, set: value !== "" },
                               body.slot && typeof body.slot === "object" ? body.slot : {});
    if (credsData && Array.isArray(credsData.slots)) {
      const i = credsData.slots.findIndex(s => s && s.name === name);
      if (i >= 0) credsData.slots[i] = Object.assign({}, credsData.slots[i], slot);
      else credsData.slots.push(slot);
    }
    st.className = "cred-status stat-note";
    delete st.dataset.errDetail;
    // bot credentials are read from the service process environment once at
    // start: a successful SET/CLEAR says the restart is still pending here
    if (name === "MYCORE_TG_TOKEN" || name === "AIDOG_TG_ALLOWED_USERS") {
      st.dataset.restartHint = "1";
    }
    st.textContent = credStatusText(slot) +
      (st.dataset.restartHint === "1"
        ? " · " + t("cred.restartHint", "restart the chat service to apply")
        : "");
    // the same slot may be rendered twice (Credentials + Telegram card):
    // keep every row for this name in sync with the fresh server truth
    document.querySelectorAll(`[data-slot="${name}"]`).forEach(w => {
      if (w === wrap) return;
      const st2 = w.querySelector(".cred-status");
      if (!st2 || st2.dataset.errDetail) return;
      if (st.dataset.restartHint === "1") st2.dataset.restartHint = "1";
      st2.textContent = credStatusText(slot) +
        (st2.dataset.restartHint === "1"
          ? " · " + t("cred.restartHint", "restart the chat service to apply")
          : "");
    });
    // setup probes (slot state chips in both cards) re-read the server truth
    refreshSetup();
    beep(880, 0.06);
  } catch (e) {
    credErrDetail(st, String(e).slice(0, 80));
  }
}

function wireCredList() {
  const handler = e => {
    const btn = e.target.closest(".cred-set, .cred-clear");
    if (!btn) return;
    const wrap = e.target.closest("[data-slot]");
    if (!wrap) return;
    const name = wrap.dataset.slot;
    const input = wrap.querySelector(".cred-input");
    const clear = btn.classList.contains("cred-clear");
    const st = wrap.querySelector(".cred-status");
    if (clear && !window.confirm(t("cred.confirmClear", "Clear this slot in secrets.env?"))) {
      return;
    }
    if (!clear) {
      if (!input || !input.value) {   // SET with an empty box must never clear a slot
        if (st) credErrDetail(st, t("cred.needValue", "type a value first"));
        return;
      }
      credWrite(name, input.value, wrap, input);
    } else {
      credWrite(name, "", wrap, input);
    }
  };
  ["credList", "tgCredList"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", handler);
  });
}

/* -------------------------------------------------------------- channels -- */
function chanField(key, kind, val) {
  const row = document.createElement("div");
  row.className = "ctl-row";
  const label = document.createElement("label");
  const lk = CHAN_FIELD_KEYS[key] || ("chan." + key);
  label.dataset.i18n = lk;
  label.textContent = t(lk, key);
  row.appendChild(label);
  let input;
  if (kind === "bool") {
    input = document.createElement("input");
    input.type = "checkbox";
    input.checked = !!val;
    input.style.cssText = "width:auto;min-height:auto;accent-color:var(--blue);";
  } else if (kind === "port") {
    input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.max = "65535";
    input.value = val == null ? "" : String(val);
    input.style.width = "110px";
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.maxLength = 2048;
    input.autocomplete = "off";
    input.spellCheck = false;
    input.value = kind === "list" && Array.isArray(val)
      ? val.join(", ") : (val == null ? "" : String(val));
  }
  input.dataset.chanField = key;
  if (key === "enabled") input.classList.add("chan-enabled");
  row.appendChild(input);
  return row;
}

function renderChannels() {
  const list = document.getElementById("chanList");
  if (!list) return;
  const base = (settings.channels && typeof settings.channels === "object")
    ? settings.channels : {};
  list.textContent = "";
  Object.keys(CHAN_SCHEMA).forEach(route => {
    const sub = (base[route] && typeof base[route] === "object") ? base[route] : {};
    const blk = document.createElement("div");
    blk.dataset.chanRoute = route;
    blk.style.cssText =
      "margin-top:10px;padding-top:8px;border-top:1px dashed var(--line);";
    const head = document.createElement("div");
    head.className = "ctl-row token-row";
    const nm = document.createElement("span");
    nm.textContent = route;           // server route id: data, never translated
    nm.style.cssText =
      "font-family:var(--mono);font-size:12.5px;letter-spacing:.06em;color:var(--navy);";
    const chip = document.createElement("span");
    chip.className = "chat-badge";
    chip.dataset.chanChip = route;
    head.appendChild(nm);
    head.appendChild(chip);
    blk.appendChild(head);
    CHAN_SCHEMA[route].forEach(([key, kind]) =>
      blk.appendChild(chanField(key, kind, sub[key])));
    list.appendChild(blk);
  });
  paintChanChips();
}

function paintChanChips() {
  document.querySelectorAll("#chanList [data-chan-chip]").forEach(chip => {
    const route = chip.dataset.chanChip;
    const row = chanStatusRows.find(r => r && String(r.name) === route);
    if (!row) {
      chip.textContent = t("chan.st.unknown", "status unavailable");
      chip.title = "";
      return;
    }
    chip.textContent =
      (row.enabled ? t("chan.st.enabled", "enabled")
                   : t("chan.st.disabled", "disabled")) + " · " +
      (row.configured ? t("chan.st.configured", "configured")
                      : t("chan.st.unconfigured", "not configured"));
    chip.title = row.note ? String(row.note) : "";   // server note, verbatim
  });
}

async function loadChanStatus() {
  try {
    const body = await fetch("/api/channels").then(r => r.json());
    chanStatusRows = (body && Array.isArray(body.channels)) ? body.channels : [];
  } catch (e) { chanStatusRows = []; }
  paintChanChips();
  renderOrch();                 // Lane I: channel count rides this fetch
}

/* The channels block joins the payload ALL-OR-NOTHING on the server, so the
   form always sends the full 9-route tree — but only when a field really
   changed, keeping the blast radius of one edit minimal. */
function channelsPayload() {
  const out = {};
  const base = (settings.channels && typeof settings.channels === "object")
    ? settings.channels : {};
  Object.keys(CHAN_SCHEMA).forEach(route => {
    const blk = document.querySelector(`#chanList [data-chan-route="${route}"]`);
    if (!blk) return;
    const sub = {};
    const stored = (base[route] && typeof base[route] === "object") ? base[route] : {};
    CHAN_SCHEMA[route].forEach(([key, kind]) => {
      const input = blk.querySelector(`[data-chan-field="${key}"]`);
      if (!input) return;
      if (kind === "bool") {
        sub[key] = !!input.checked;
      } else if (kind === "list") {
        sub[key] = String(input.value || "").split(",")
          .map(s => s.trim()).filter(Boolean).slice(0, 256);
      } else if (kind === "port") {
        const n = parseInt(input.value, 10);
        const fallback = parseInt(stored[key], 10);
        const dflt = key === "smtp_port" ? 587 : 993;
        sub[key] = Number.isFinite(n) ? n
          : (Number.isFinite(fallback) ? fallback : dflt);
      } else {
        sub[key] = String(input.value || "").trim();
      }
    });
    out[route] = sub;
  });
  return out;
}

async function chanSendTest() {
  const msg = document.getElementById("chanTestMsg");
  const btn = document.getElementById("chanTestBtn");
  if (!msg) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken", "access token required — set it in Access");
    return;
  }
  const route = (document.getElementById("chanTestRoute") || {}).value || "";
  const target = ((document.getElementById("chanTestTarget") || {}).value || "").trim();
  const text = ((document.getElementById("chanTestText") || {}).value || "").trim();
  if (btn) btn.disabled = true;
  msg.className = "save-state";
  msg.textContent = t("chan.testWorking", "sending…");
  try {
    const res = await fetch("/api/channels/send", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ route: route, target: target, text: text }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.ok && body.ok === true) {
      msg.className = "save-state";
      msg.textContent = t("chan.testOk", "sent ✓");
      return;
    }
    const details = Array.isArray(body.details) ? body.details : [];
    const detail = details.length ? details.join("; ")
      : (body.error || ("HTTP " + res.status));
    msg.className = "save-state err";
    msg.textContent = res.status + " · " + detail;   // 409/400/502 verbatim
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = String(e).slice(0, 80);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---------------------------------------------------------- event hooks -- */
function hookRowEl(cmd, timeout) {
  const row = document.createElement("div");
  row.className = "ctl-row token-row";
  row.dataset.hookRow = "1";
  row.style.marginTop = "6px";
  const ta = document.createElement("textarea");
  ta.className = "hook-cmd evo-input evo-body";
  ta.rows = 2;
  ta.style.cssText = "flex:1 1 220px;min-width:0;min-height:44px;";
  ta.dataset.i18nPh = "hook.cmdPh";
  ta.placeholder = t("hook.cmdPh",
    "one argv element per line — executed directly, never through a shell");
  ta.value = Array.isArray(cmd) ? cmd.join("\n") : "";
  const lab = document.createElement("label");
  lab.dataset.i18n = "hook.timeout";
  lab.textContent = t("hook.timeout", "Timeout (s)");
  const to = document.createElement("input");
  to.type = "number";
  to.className = "hook-timeout";
  to.min = "1";
  to.max = "30";
  to.placeholder = "5";
  to.value = (typeof timeout === "number" && timeout >= 1 && timeout <= 30)
    ? String(timeout) : "";
  to.style.width = "84px";
  row.appendChild(ta);
  row.appendChild(lab);
  row.appendChild(to);
  return row;
}

function hookCountOf(blk) {
  let n = 0;
  blk.querySelectorAll(".hook-cmd").forEach(ta => {
    if (String(ta.value || "").split("\n").some(line => line.trim())) n += 1;
  });
  return n;
}

function paintHookCount(chip) {
  chip.textContent = t("hook.count", "{n} hook(s)")
    .replace("{n}", chip.dataset.n || "0");
}

function updateHookCount(blk) {
  const chip = blk.querySelector("[data-hook-count]");
  if (!chip) return;
  chip.dataset.n = String(hookCountOf(blk));
  paintHookCount(chip);
}

function updateHookCounts() {
  document.querySelectorAll("#hookList [data-hook-event]").forEach(updateHookCount);
}

function renderHooks() {
  const list = document.getElementById("hookList");
  if (!list) return;
  const stored = (settings.hooks && typeof settings.hooks === "object")
    ? settings.hooks : {};
  list.textContent = "";
  HOOK_EVENTS.forEach(ev => {
    const entries = Array.isArray(stored[ev]) ? stored[ev] : [];
    const blk = document.createElement("div");
    blk.dataset.hookEvent = ev;
    blk.style.cssText =
      "margin-top:10px;padding-top:8px;border-top:1px dashed var(--line);";
    const head = document.createElement("div");
    head.className = "ctl-row token-row";
    const nm = document.createElement("span");
    nm.textContent = ev;              // server event id: data, never translated
    nm.style.cssText =
      "font-family:var(--mono);font-size:12.5px;letter-spacing:.06em;color:var(--navy);";
    const chip = document.createElement("span");
    chip.className = "chat-badge";
    chip.dataset.hookCount = ev;
    const add = document.createElement("button");
    add.type = "button";
    add.className = "btn";
    add.dataset.i18n = "hook.add";
    add.textContent = t("hook.add", "+ ADD");
    add.addEventListener("click", () => {
      blk.appendChild(hookRowEl([], null));
      hookDirty.add(ev);
      updateHookCount(blk);
    });
    head.appendChild(nm);
    head.appendChild(chip);
    head.appendChild(add);
    blk.appendChild(head);
    const rows = entries.length ? entries : [null];
    rows.forEach(en => {
      const cmd = en && Array.isArray(en.cmd) ? en.cmd : [];
      const to = en && typeof en.timeout === "number" ? en.timeout : null;
      blk.appendChild(hookRowEl(cmd, to));
    });
    list.appendChild(blk);
  });
  updateHookCounts();
}

/* Only the events the operator edited enter the payload; an event whose
   rows are all empty saves [] (that clears the event on the server). */
function hooksPayload() {
  if (!hookDirty.size) return null;
  const out = {};
  hookDirty.forEach(ev => {
    const blk = document.querySelector(`#hookList [data-hook-event="${ev}"]`);
    if (!blk) return;
    const entries = [];
    blk.querySelectorAll("[data-hook-row]").forEach(w => {
      const ta = w.querySelector(".hook-cmd");
      const cmd = String((ta && ta.value) || "").split("\n")
        .map(s => s.trim()).filter(Boolean).slice(0, 32);
      if (!cmd.length) return;
      const to = parseInt((w.querySelector(".hook-timeout") || {}).value, 10);
      entries.push({ cmd: cmd, timeout: Number.isFinite(to) ? to : 5 });
    });
    out[ev] = entries;
  });
  return Object.keys(out).length ? out : null;
}

/* -------------------------------------------- usage / mycore extras ---- */
function renderToolsOff() {
  const grid = document.getElementById("toolsOffList");
  const labRow = document.getElementById("toolsOffRow");
  if (!grid) return;
  const mc = (capsData && capsData.mycore && typeof capsData.mycore === "object")
    ? capsData.mycore : null;
  const known = (mc && Array.isArray(mc.known_tools) && mc.known_tools.length)
    ? mc.known_tools.filter(n => typeof n === "string").slice().sort() : null;
  if (!known) {            // field absent in this build -> hide the grid, honestly
    grid.hidden = true;
    grid.style.cssText = "";   // no inline display may beat the [hidden] rule
    if (labRow) { labRow.hidden = true; labRow.style.display = "none"; }
    return;
  }
  // .ctl-row carries an author `display:flex`, so [hidden] alone would not
  // hide the label row — clear it with an inline display instead.
  if (labRow) { labRow.hidden = false; labRow.style.display = ""; }
  grid.hidden = false;
  const myc = (settings.mycore && typeof settings.mycore === "object")
    ? settings.mycore : {};
  const off = new Set(Array.isArray(myc.tools_off) ? myc.tools_off : []);
  grid.textContent = "";
  grid.style.cssText = "display:grid;" +
    "grid-template-columns:repeat(auto-fill,minmax(150px,1fr));" +
    "gap:6px 12px;margin-bottom:12px;";
  known.forEach(name => {
    const lab = document.createElement("label");
    lab.style.cssText =
      "display:flex;gap:6px;align-items:center;font-family:var(--mono);font-size:12.5px;";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "tool-off";
    cb.value = name;
    cb.checked = off.has(name);       // checked = the tool is disabled
    lab.appendChild(cb);
    lab.appendChild(document.createTextNode(name));
    grid.appendChild(lab);
  });
}

function toolsOffValue() {
  const grid = document.getElementById("toolsOffList");
  if (!grid || grid.hidden) return null;
  const out = [];
  grid.querySelectorAll("input.tool-off").forEach(cb => {
    if (cb.checked) out.push(cb.value);
  });
  return out.slice(0, 32);
}

function renderEnvOverride() {
  const el = document.getElementById("envOverrideChip");
  if (!el) return;
  const names = (capsData && Array.isArray(capsData.env_overrides))
    ? capsData.env_overrides.filter(n => typeof n === "string") : [];
  if (!names.length) {      // field absent or empty -> the note stays hidden
    el.hidden = true;
    el.textContent = "";
    return;
  }
  el.hidden = false;
  el.textContent = t("ctl.envOverride",
    "Environment overrides: {names} — the console value may be ignored")
    .replace("{names}", names.join(", "));
}

function mycoreExtraPayload() {
  const out = {};
  if (mycDirty) {
    const g = id => ((document.getElementById(id) || {}).value || "");
    out.context_dirs = g("mycContextDirs").trim();
    out.upstreams = g("mycUpstreams").trim();
    out.mcp = g("mycMcp").trim();
    out.shell = g("mycShell").trim();
    // mycore.models / mycore.budget are first-class schema keys now: they
    // join the payload only with a value, so an untouched form on a build
    // without them can never fail the whole save with "unknown key".
    const stored = (settings.mycore && typeof settings.mycore === "object")
      ? settings.mycore : {};
    const mEl = document.getElementById("mycModels");
    if (mEl) {
      const list = String(mEl.value || "").split(",").map(s => s.trim())
        .filter(Boolean).slice(0, 32);
      const had = Array.isArray(stored.models) && stored.models.length;
      if (list.length || had) out.models = list;
    }
    const bEl = document.getElementById("mycBudget");
    if (bEl && String(bEl.value || "").trim() !== "") {
      const n = parseInt(bEl.value, 10);
      if (Number.isFinite(n)) out.budget = n;   // server enforces 4000..1000000
    }
  }
  if (toolsDirty) {
    const off = toolsOffValue() || [];
    const myc = (settings.mycore && typeof settings.mycore === "object")
      ? settings.mycore : {};
    const stored = Array.isArray(myc.tools_off) ? myc.tools_off : [];
    // an empty list is sent only when it really clears a stored list — the
    // server refuses tools_off: [] (1..32) and says so honestly
    if (off.length || stored.length) out.tools_off = off;
  }
  return Object.keys(out).length ? out : null;
}

/* Builds the settings values of the panels once at boot (after
   GET /api/settings resolved). Programmatic value writes fire no input
   events, so no dirty flag is raised here. */
function initSettingsPanels() {
  const u = (settings.usage && typeof settings.usage === "object")
    ? settings.usage : {};
  const myc = (settings.mycore && typeof settings.mycore === "object")
    ? settings.mycore : {};
  const setField = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val == null ? "" : String(val);
  };
  setField("usageMetricsUrl", u.metrics_url);
  setField("usagePricingCfg", u.pricing_config);
  setField("usageLogDir", u.log_dir);
  setField("mycContextDirs", myc.context_dirs);
  setField("mycUpstreams", myc.upstreams);
  setField("mycMcp", myc.mcp);
  setField("mycShell", myc.shell);
  // activation car: mycore.models / mycore.budget are console-managed now
  setField("mycModels", Array.isArray(myc.models) ? myc.models.join(", ") : "");
  setField("mycBudget", myc.budget);
  chanDirty = false; usageDirty = false; mycDirty = false; toolsDirty = false;
  mycRestartDirty = false;
  hookDirty.clear();
  renderToolsOff();
  renderEnvOverride();
  renderHooks();
  renderChannels();
  loadChanStatus();
  loadCreds();
  evoPrefill();                // B2: evolution schedule form <- settings
  exCapPrefill();              // B2: exchange caps form <- settings.web3
  missionPrefillCfg();         // car 3: quiet hours + min_interval <- settings
}

/* repainted after a language switch: the save row may carry a restart hint
   written in the previous pack (only ever the two success templates — an
   error detail stays verbatim as it was shown) */
function repaintSaveState() {
  const state = document.getElementById("saveState");
  if (!state || !state.dataset.saveKind || state.classList.contains("err")) return;
  let text = state.dataset.saveKind === "same"
    ? t("save.same", "no change ✓") : t("save.ok", "saved ✓");
  if (state.dataset.restartHint === "1") {
    text += " · " + t("ctl.restartSvcHint",
      "restart the chat service to apply — button in Overview");
  }
  state.textContent = text;
}

/* Pack-driven texts that live inside JS-built rows: re-applied on a
   language switch (data-i18n nodes are already handled by loadLang itself). */
function settingsRelang() {
  paintCredHint();
  repaintCredStatuses();   // "set / empty / {n} chars" follows the language too
  repaintSaveState();      // activation car: saved row + restart hint follow
  paintChanChips();
  updateHookCounts();
  document.querySelectorAll(".cred-status[data-err-detail]").forEach(el => {
    el.textContent = t("cred.err", "error: ") + el.dataset.errDetail;
  });
  renderEnvOverride();
  renderCapGrid();             // B2: system card + cron list follow the pack
  renderCronList();
  renderSetup();               // activation car: checklist rows re-translate
  renderMission();             // mission card: dynamic lines follow the pack
  renderOrch();                // Lane I: orchestration rows follow the pack
  evoCrossCheck();
}

/* ------------------------------ car B2: evolution / exchange / overview --
   Lane B2 of the frontend coverage car. Three console faces, all fail-soft:
     - evolution schedule editor -> POST /api/settings {evolution:{...}}
       (Bearer; all four keys), prefilled from GET /api/settings and
       cross-checked against GET /api/evolution (the ACTIVE schedule);
     - exchange cap editor -> POST /api/settings {web3:{...}} with BOTH keys
       always (the server is all-or-nothing), then chips + capabilities
       refresh so the read-only summary above shows the stored numbers;
     - Overview system card -> GET /api/capabilities (names and counts only)
       plus GET /api/cron (read route); the 0.5.40 activation car adds the
       doctor button and the per-row cron delete action next to them.
   Hard rules: a server 400 detail is shown VERBATIM (it names keys, bounds
   and grammar — never a value); a field the server does not send degrades
   to t("cap.unavailable") or a hidden row, never to an invented number. */
const EVO_DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"];

function evoSettingsBlock() {
  return (settings.evolution && typeof settings.evolution === "object")
    ? settings.evolution : {};
}

/* the form <- GET /api/settings evolution partition (always present after
   lane A-1.5; the fallback below mirrors mycore.evolution.DEFAULT_SETTINGS
   so an absent block renders the same defaults the server would apply) */
function evoPrefill() {
  const ev = evoSettingsBlock();
  const en = document.getElementById("evoEnabled");
  if (en) en.setAttribute("aria-pressed", String(ev.enabled === true));
  const da = document.getElementById("evoDailyAt");
  if (da) da.value = ev.daily_at || "09:10";
  const wd = document.getElementById("evoWeeklyDay");
  if (wd) wd.value = EVO_DAYS.indexOf(ev.weekly_day) >= 0 ? ev.weekly_day : "sun";
  const md = document.getElementById("evoMonthlyDay");
  if (md) {
    const d = Number(ev.monthly_day);
    md.value = String(Number.isInteger(d) && d >= 1 && d <= 28 ? d : 1);
  }
}

/* display cross-check: the FORM (operator edits) vs the ACTIVE schedule
   served by GET /api/evolution. Equal -> the note stays hidden; different
   (unsaved edit, or settings stored elsewhere) -> one honest line saying
   the form is not applied yet. Never a silent guess, never a rewrite. */
function evoCrossCheck() {
  const el = document.getElementById("evoCrossNote");
  if (!el) return;
  const sc = (evoData && typeof evoData === "object") ? evoData : null;
  const da = document.getElementById("evoDailyAt");
  const wd = document.getElementById("evoWeeklyDay");
  const md = document.getElementById("evoMonthlyDay");
  const en = document.getElementById("evoEnabled");
  // before either side is loaded there is nothing honest to say
  if (!sc || !sc.schedule || sc.schedule.daily_at === undefined ||
      !da || !da.value || !wd || !md || !en) {
    el.hidden = true; el.textContent = ""; return;
  }
  const s = sc.schedule;
  const same = da.value === String(s.daily_at)
    && wd.value === String(s.weekly_day)
    && Number(md.value) === Number(s.monthly_day)
    && (en.getAttribute("aria-pressed") === "true") === Boolean(sc.enabled);
  el.hidden = same;
  if (!same) el.textContent = t("evo.diff",
    "form and the active schedule differ — press SAVE to apply");
}

async function evoSave() {
  const state = document.getElementById("evoSaveState");
  if (!state) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    state.className = "save-state err";
    state.textContent = t("save.needToken", "token required");
    return;
  }
  const wd = document.getElementById("evoWeeklyDay");
  const md = document.getElementById("evoMonthlyDay");
  const patch = { evolution: {
    enabled: document.getElementById("evoEnabled").getAttribute("aria-pressed") === "true",
    daily_at: ((document.getElementById("evoDailyAt") || {}).value || ""),
    weekly_day: wd ? wd.value : "sun",
    // an empty input yields NaN -> JSON null: the SERVER then answers with
    // its own 1..28 detail, shown verbatim below (nothing invented here)
    monthly_day: md ? parseInt(md.value, 10) : 1,
  } };
  state.className = "save-state";
  state.textContent = t("evo.working", "saving…");
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(patch),
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      state.className = "save-state err";
      state.textContent = t("save.badToken", "401 · unauthorized");
      return;
    }
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      state.className = "save-state err";
      state.textContent = details.length ? details.join("; ")
        : (body.error || ("HTTP " + res.status));
      return;
    }
    settings = Object.assign(settings, body.settings || patch);
    evoPrefill();              // re-read what the server actually stored
    state.className = "save-state";
    state.textContent = body.changed === false
      ? t("save.same", "no change ✓") : t("save.ok", "saved ✓");
    beep(880, 0.06);
    refreshEvolution();        // status line + badge + cross-check follow
  } catch (e) {
    state.className = "save-state err";
    state.textContent = String(e).slice(0, 60);
  }
}

/* exchange caps: prefilled from settings.web3 (the stored value), falling
   back to the capability block — both carry the same two numbers. */
function exCapPrefill() {
  const src = (settings.web3 && typeof settings.web3 === "object")
    ? settings.web3
    : ((capsData && typeof capsData.web3 === "object") ? capsData.web3 : null);
  if (!src) return;            // honest: no source -> the inputs stay empty
  const num = v => { const n = Number(v); return isFinite(n) ? String(n) : ""; };
  const pt = document.getElementById("exCapPerTx");
  const dc = document.getElementById("exCapDaily");
  if (pt) pt.value = num(src.auto_usd_per_tx);
  if (dc) dc.value = num(src.auto_usd_daily);
}

async function exCapSave() {
  const state = document.getElementById("exCapState");
  if (!state) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    state.className = "save-state err";
    state.textContent = t("save.needToken", "token required");
    return;
  }
  const val = id => {
    const el = document.getElementById(id);
    return el ? parseFloat(el.value) : NaN;   // empty -> NaN -> JSON null -> 400
  };
  const patch = { web3: { auto_usd_per_tx: val("exCapPerTx"),
                          auto_usd_daily: val("exCapDaily") } };
  state.className = "save-state";
  state.textContent = t("ex.capWorking", "saving…");
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(patch),
    });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      state.className = "save-state err";
      state.textContent = t("save.badToken", "401 · unauthorized");
      return;
    }
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      state.className = "save-state err";
      state.textContent = details.length ? details.join("; ")
        : (body.error || ("HTTP " + res.status));
      return;
    }
    settings = Object.assign(settings, body.settings || patch);
    state.className = "save-state";
    state.textContent = body.changed === false
      ? t("save.same", "no change ✓") : t("save.ok", "saved ✓");
    beep(880, 0.06);
    exCapPrefill();
    renderExThreshold();       // chips repaint at once …
    refreshCaps();             // … then the fresh catalog repaints them
  } catch (e) {
    state.className = "save-state err";
    state.textContent = String(e).slice(0, 60);
  }
}

/* ------------------------------------------ Overview system card (B2) --
   One kv row per capability block; a block the server did not send shows
   t("cap.unavailable") instead of a number. Names and counts only — this is
   the same no-auth catalog the smoke test scrubs for value substrings. */
function capRowsOf(c) {
  const rows = [];
  const NA = t("cap.unavailable", "unavailable");
  const push = (key, fb, val) => rows.push(
    [t(key, fb), (val === null || val === undefined) ? NA : String(val)]);
  const none = t("cap.none", "none");

  const tools = Array.isArray(c.tools) ? c.tools : null;
  push("cap.tools", "Registered tools", tools ? tools.length : null);
  if (tools) {
    const exT = tools
      .map(x => (x && typeof x === "object" ? String(x.name || "") : ""))
      .filter(n => n.indexOf("exchange_") === 0);
    if (exT.length) push("cap.exTools", "Exchange tools", exT.join(", "));
  }
  push("cap.skills", "Skills",
    Array.isArray(c.skills) ? c.skills.length : null);

  const ch = (c.channels && typeof c.channels === "object") ? c.channels : null;
  push("cap.channels", "Channels",
    (ch && Array.isArray(ch.enabled) && Array.isArray(ch.configured))
      ? t("cap.chanCounts", "{e} enabled · {c} configured")
          .replace("{e}", String(ch.enabled.length))
          .replace("{c}", String(ch.configured.length))
      : null);

  const hk = (c.hooks && typeof c.hooks === "object") ? c.hooks : null;
  push("cap.hooks", "Event hooks",
    (hk && Array.isArray(hk.events))
      ? (hk.events.length ? hk.events.join(", ") : none) : null);

  const cr = (c.cron && typeof c.cron === "object") ? c.cron : null;
  push("cap.cron", "Cron jobs",
    (cr && typeof cr.jobs === "number") ? cr.jobs : null);

  const br = (c.browser && typeof c.browser === "object") ? c.browser : null;
  push("cap.browser", "Browser automation",
    (br && typeof br.available === "boolean")
      ? (br.available ? t("cap.avail", "available")
                      : t("cap.noAvail", "not available"))
      : null);

  const tm = (c.terminal && typeof c.terminal === "object") ? c.terminal : null;
  push("cap.terminal", "Terminal backend",
    (tm && tm.backend !== undefined)
      ? (tm.backend ? String(tm.backend)
                    : (tm.note ? String(tm.note) : t("cap.terminalOff", "not set")))
      : null);

  const lm = (c.local_models && typeof c.local_models === "object")
    ? c.local_models : null;
  push("cap.localModels", "Local models",
    (lm && typeof lm.count === "number") ? lm.count : null);

  const myc = (c.mycore && typeof c.mycore === "object") ? c.mycore : null;
  const mcp = (myc && myc.mcp_servers && typeof myc.mcp_servers === "object")
    ? myc.mcp_servers : null;
  push("cap.mcp", "MCP servers",
    (mcp && Array.isArray(mcp.names))
      ? (mcp.names.length ? mcp.names.join(", ") : none) : null);

  const exb = (c.exchange && typeof c.exchange === "object") ? c.exchange : null;
  push("cap.venues", "Exchange venues",
    (exb && Array.isArray(exb.venues)) ? exb.venues.length : null);

  const w3 = (c.web3 && typeof c.web3 === "object") ? c.web3 : null;
  push("cap.web3", "Web3 auto caps",
    (w3 && w3.auto_usd_per_tx !== undefined && w3.auto_usd_daily !== undefined)
      ? t("ex.thr.perTx", "per-tx cap {v} USD")
          .replace("{v}", exNum(w3.auto_usd_per_tx)) + " · " +
        t("ex.thr.daily", "daily cap {v} USD")
          .replace("{v}", exNum(w3.auto_usd_daily))
      : null);

  push("cap.env", "Environment overrides",
    Array.isArray(c.env_overrides)
      ? (c.env_overrides.length ? c.env_overrides.join(", ") : none) : null);

  return rows;
}

function renderCapGrid() {
  const grid = document.getElementById("capGrid");
  if (!grid) return;
  const c = (capsData && typeof capsData === "object") ? capsData : null;
  if (!c) {
    // before the FIRST fetch settles the table simply stays empty (nothing
    // is claimed yet); a failed/shapeless fetch says "unavailable" honestly
    if (!capsSeen) { grid.textContent = ""; return; }
    grid.innerHTML = `<tr><td colspan="2">${escapeHtml(
      t("cap.unavailable", "capability catalog unavailable"))}</td></tr>`;
    return;
  }
  grid.innerHTML = capRowsOf(c).map(([k, v]) =>
    `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(v == null ? "—" : String(v))}</td></tr>`)
    .join("");
}

/* ---------------------------------------------------- cron list (B2) --
   GET /api/cron stays a READ route (the 0.5.40 car adds a DELETE action via
   POST {action:"del"}, creation stays natural language / CLI / Telegram).
   Absent endpoint or shape -> honest note. */
let cronData = null;
let cronSeen = false;        // false until the FIRST cron fetch settles

async function refreshCron() {
  try {
    const d = await fetch("/api/cron").then(r => r.json());
    cronData = (d && typeof d === "object" && Array.isArray(d.jobs)) ? d : null;
  } catch (e) { cronData = null; }
  cronSeen = true;
  renderCronList();
}

function renderCronList() {
  const el = document.getElementById("cronList");
  if (!el) return;
  if (!cronData) {
    if (!cronSeen) { el.textContent = ""; return; }   // not fetched yet
    el.innerHTML = `<div class="stat-note">${escapeHtml(
      t("cron.unavailable", "cron list unavailable"))}</div>`;
    return;
  }
  if (!cronData.jobs.length) {
    el.innerHTML = `<div class="stat-note">${escapeHtml(t("cron.empty",
      "no cron jobs yet — ask in chat, the CLI or Telegram to create one"))}</div>`;
    return;
  }
  el.innerHTML = cronData.jobs.map(j => {
    const job = (j && typeof j === "object") ? j : {};
    const spec = String(job.spec || "");
    const title = String(job.text || spec);
    const meta = [];
    if (spec && job.text) meta.push(spec);
    if (job.origin !== null && job.origin !== undefined && job.origin !== "") {
      meta.push(t("cron.origin", "origin {v}").replace("{v}", String(job.origin)));
    }
    if (job.next) {
      meta.push(t("cron.next", "next {v}").replace("{v}", String(job.next)));
    }
    if (job.due) meta.push(t("cron.due", "due now"));
    const jid = String(job.id || "");
    const del = jid
      ? `<button class="btn" id="cronDelBtn" type="button" data-job="${escapeHtml(jid)}" ` +
        `title="${escapeHtml(t("cron.delConfirm", "Delete cron job {id}?"))
          .replace("{id}", escapeHtml(jid))}" ` +
        `style="padding:2px 9px;font-size:12px;" aria-label="delete">×</button>`
      : "";
    return `<div class="ctl-row" data-cron="${escapeHtml(jid)}">` +
      `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">${escapeHtml(title)}</label>` +
      `<span class="stat-note" style="margin-top:0;">${escapeHtml(meta.join(" · "))}</span>` +
      del +
      `</div>`;
  }).join("");
}

/* ------------------------------------ activation & operations (0.5.40) --
   Lane B of the activation & operations car. Four honest console faces:
     - ACTIVATION card (Overview, FIRST card): GET /api/setup — required
       slot / endpoint / service rows, each with a fix deep-link into the
       Settings control that repairs it, a fresh-run welcome line (or run
       `aidog setup` in a terminal) and #actRecheck. A build without the
       endpoint keeps the whole card hidden — an honest absence, never a
       guessed state. Credential values never appear: presence/format only.
     - Bot block: token + allowlist presence/format chips, service state and
       #svcRestartBtn -> confirm -> POST /api/service/restart {target:
       "service"} -> poll GET /api/status up to 20s for state "running";
       409 / failure / timeout each render their own honest line with the
       server error verbatim.
     - Doctor: POST /api/doctor/run -> summary counts + up to 80 verbatim
       output lines in a scrollable monospace block (button disabled while
       running; non-zero FAIL is output, not an error).
     - cron delete: per-row #cronDelBtn[data-job] -> confirm -> POST /api/cron
       {action:"del", id} -> list refresh; creation stays natural-language. */
let setupData = null, setupSeen = false, setupBusy = false;

/* item id -> the Settings control that fixes it (deep-link target) */
const ACT_FIX = {
  web_token: { slot: "WEB_TOKEN" },
  model_key: { slot: "MODEL_API_KEY" },
  tg_token: { slot: "MYCORE_TG_TOKEN" },
  tg_allowlist: { slot: "AIDOG_TG_ALLOWED_USERS" },
  model_endpoint: { field: "mycUrl" },
  service: { restart: true },
};

function actChip(text, tone) {
  const col = tone === "ok" ? "var(--ok)"
    : tone === "bad" ? "var(--red)"
    : tone === "warn" ? "var(--amber)" : "var(--muted)";
  return `<span class="chat-badge" style="color:${col};border-color:${col};">` +
    `${escapeHtml(text)}</span>`;
}

function actTone(state) {
  const s = String(state || "");
  if (s === "set" || s === "reachable" || s === "running") return "ok";
  if (s === "empty" || s === "unreachable" || s === "stopped") return "bad";
  if (s === "invalid" || s.indexOf("pending") === 0) return "warn";
  return "";                       // unknown state: raw text, neutral chip
}

function actChipText(state) {
  const s = String(state || "");
  const key = s.indexOf("pending") === 0 ? "pending" : s;
  const map = {
    set: ["act.st.set", "set ✓"],
    empty: ["act.st.empty", "empty ✗"],
    invalid: ["act.st.invalid", "invalid ⚠"],
    reachable: ["act.st.reachable", "reachable ✓"],
    unreachable: ["act.st.unreachable", "unreachable ✗"],
    running: ["act.st.running", "running ✓"],
    stopped: ["act.st.stopped", "stopped"],
    pending: ["act.st.pending", "pending (fail-closed)"],
  };
  if (map[key]) return t(map[key][0], map[key][1]);
  return s || "—";                 // server state verbatim, never invented
}

function renderSetup() {
  const card = document.getElementById("actCard");
  if (!card) return;
  if (!setupData) {                // no endpoint / bad shape: hide, honestly
    card.hidden = true;
    card.style.display = "none";
    const tgBotHide = document.getElementById("tgBotState");
    if (tgBotHide) { tgBotHide.hidden = true; tgBotHide.textContent = ""; }
    return;
  }
  card.hidden = false;
  card.style.display = "";
  const ready = setupData.ready === true;
  const badge = document.getElementById("actBadge");
  if (badge) {
    badge.textContent = ready ? t("act.ready", "READY ✓")
      : t("act.notReady", "NOT READY");
    badge.style.color = ready ? "var(--ok)" : "var(--amber)";
    badge.style.borderColor = ready ? "var(--ok)" : "var(--amber)";
  }
  // fresh-run welcome: this IS the first-boot onboarding (3 steps + the CLI)
  const start = document.getElementById("actStart");
  if (start) {
    start.hidden = ready;
    if (!ready) {
      start.innerHTML = escapeHtml(t("act.start",
        "Welcome — 3 steps to activate: 1) set the console access token, " +
        "2) set the bot token and the owner allowlist, 3) point the model " +
        "endpoint. Fix each row below, or run in a terminal:")) + " " +
        `<code style="font-family:Consolas,monospace;font-size:12.5px;` +
        `color:var(--blue-ink);border:1px dashed var(--line);` +
        `padding:1px 6px;border-radius:6px;">aidog setup</code>`;
    }
  }
  const items = Array.isArray(setupData.items) ? setupData.items : [];
  const list = document.getElementById("actList");
  if (list) {
    list.innerHTML = items.map(it => {
      const o = (it && typeof it === "object") ? it : {};
      const id = String(o.id || "");
      if (!id) return "";
      const label = t("act.item." + id, id);
      const opt = o.required === false
        ? ` <span class="stat-note" style="margin-top:0;display:inline;">` +
          `${escapeHtml(t("act.optional", "optional"))}</span>` : "";
      const fix = ACT_FIX[id]
        ? `<button class="btn" type="button" data-act-fix="${escapeHtml(id)}" ` +
          `style="padding:3px 10px;">${escapeHtml(t("act.fix", "FIX"))}</button>`
        : "";
      const hint = o.hint
        ? `<div class="stat-note" style="margin-top:0;">` +
          `${escapeHtml(t(String(o.hint), String(o.hint_en || "")))}</div>`
        : "";
      return `<div data-act-item="${escapeHtml(id)}">` +
        `<div class="ctl-row">` +
        `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
        `${escapeHtml(label)}</label>${opt}` +
        actChip(actChipText(o.state), actTone(o.state)) + fix +
        `</div>${hint}</div>`;
    }).join("");
  }
  // bot block: token / allowlist presence + format, service state — painted
  // into BOTH faces (Overview #actBotState + Settings Telegram card
  // #tgBotState) from the same honest setup payload; a missing payload
  // leaves the Settings chip hidden (renderSetup already returned above).
  const find = id => items.find(x => x && x.id === id) || null;
  const parts = [];
  ["tg_token", "tg_allowlist"].forEach(id => {
    const o = find(id);
    if (!o) return;
    parts.push(escapeHtml(t("act.item." + id, id)) + " " +
      actChip(actChipText(o.state), actTone(o.state)));
  });
  const svc = String(setupData.service || "");
  if (svc) {
    parts.push(escapeHtml(t("act.item.service", "Chat service")) + " " +
      actChip(actChipText(svc), actTone(svc)));
  }
  const botHtml = parts.length ? parts.join(" · ") : "—";
  const bot = document.getElementById("actBotState");
  if (bot) bot.innerHTML = botHtml;
  const tgBot = document.getElementById("tgBotState");
  if (tgBot) { tgBot.hidden = false; tgBot.innerHTML = botHtml; }
}

async function refreshSetup() {
  const msg = document.getElementById("actMsg");
  try {
    const res = await fetch("/api/setup");
    if (!res.ok) throw new Error("HTTP " + res.status);
    const d = await res.json();
    if (!d || typeof d !== "object" || !Array.isArray(d.items)) {
      throw new Error("bad payload");
    }
    setupData = d;
    if (msg) { msg.className = "save-state"; msg.textContent = ""; }
  } catch (e) {
    if (setupSeen && setupData) {
      // keep the last honest snapshot and report this probe failure verbatim
      if (msg) {
        msg.className = "save-state err";
        msg.textContent = t("act.checkFail", "setup check failed: {e}")
          .replace("{e}", String(e).slice(0, 120));
      }
    } else {
      setupData = null;           // 404 (lane not landed) / bad shape
    }
  }
  setupSeen = true;
  renderSetup();
}

function actFocusEl(el) {
  if (!el) return;
  try { el.scrollIntoView({ block: "center", behavior: "smooth" }); }
  catch (e) { el.scrollIntoView(); }
  const input = (el.querySelector && el.querySelector("input, textarea")) || el;
  if (input && typeof input.focus === "function") {
    try { input.focus({ preventScroll: true }); } catch (e) { input.focus(); }
  }
}

/* fix path: deep-link to the control that repairs this item */
function actFix(id) {
  const fix = ACT_FIX[id];
  if (!fix) return;
  if (fix.restart) {
    const b = document.getElementById("svcRestartBtn");
    actFocusEl(b);
    return;
  }
  setView("settings", true);
  requestAnimationFrame(() => {
    if (fix.slot) {
      const sel = `#credList [data-slot="${fix.slot}"]`;
      const row = document.querySelector(sel);
      if (row) { actFocusEl(row); return; }
      loadCreds().then(() => actFocusEl(document.querySelector(sel)));
      return;
    }
    if (fix.field) actFocusEl(document.getElementById(fix.field));
  });
}

/* shared chat-service restart (Overview #svcRestartBtn + Settings Telegram
   card #tgRestartBtn): confirm -> POST /api/service/restart {target:
   "service"} -> poll /api/status up to 20s. Behavior for the Overview face
   is byte-for-byte the pre-existing one (activation E2E U2 depends on it). */
async function svcRestartWith(btn, msg) {
  if (!btn || !msg) return;
  if (!window.confirm(t("act.restartConfirm",
      "Restart the chat service now? Bot token / allowlist / tool changes " +
      "take effect after a restart."))) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("save.needToken", "token required");
    return;
  }
  btn.disabled = true;
  msg.className = "save-state";
  msg.textContent = t("act.restarting", "restarting… waiting for the service");
  try {
    const res = await fetch("/api/service/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ target: "service" }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      msg.className = "save-state err";
      msg.textContent = t("save.badToken", "401 · unauthorized");
      btn.disabled = false;
      return;
    }
    if (!res.ok) {
      const err = (body && (body.error || (Array.isArray(body.details) &&
        body.details.length && body.details.join("; ")))) || ("HTTP " + res.status);
      msg.className = "save-state err";
      msg.textContent = (res.status === 409
        ? t("act.restartNoTmux", "no service session to restart (tmux) — start it first")
        : t("act.restartFail", "restart failed")) + " · " + String(err);
      btn.disabled = false;
      refreshSetup();
      return;
    }
    // restart accepted: poll /api/status up to 20s for state "running"
    const deadline = Date.now() + 20000;
    let running = false;
    while (Date.now() < deadline) {
      await actSleep(1500);
      try {
        const st = await fetch("/api/status").then(r => r.json());
        const s = st && st.mycore && st.mycore.service && st.mycore.service.state;
        if (st && typeof st === "object") { statusData = st; renderStatus(); }
        if (s === "running") { running = true; break; }
      } catch (e) { /* transient while the service restarts — keep waiting */ }
    }
    if (running) {
      msg.className = "save-state";
      msg.textContent = t("act.restartOk", "service restarted ✓");
      beep(880, 0.06);
    } else {
      msg.className = "save-state err";
      msg.textContent = t("act.restartTimeout",
        "the service did not report running within 20s — check the Overview panels");
    }
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("act.restartFail", "restart failed") + " · " +
      String(e).slice(0, 120);
  }
  btn.disabled = false;
  refreshSetup();
}

/* two faces of the same flow (element ids resolved at call time) */
function svcRestart() {
  return svcRestartWith(
    document.getElementById("svcRestartBtn"),
    document.getElementById("svcRestartMsg"));
}
function tgRestart() {
  return svcRestartWith(
    document.getElementById("tgRestartBtn"),
    document.getElementById("tgRestartMsg"));
}

/* ------------------------------------------------------ doctor (0.5.40) -- */
async function doctorRun() {
  const btn = document.getElementById("doctorRunBtn");
  const out = document.getElementById("doctorOut");
  const msg = document.getElementById("doctorMsg");
  if (!btn || !out || !msg) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("save.needToken", "token required");
    return;
  }
  btn.disabled = true;
  msg.className = "save-state";
  msg.textContent = t("cap.doctorRunning", "running doctor…");
  out.hidden = true;
  out.textContent = "";
  try {
    const res = await fetch("/api/doctor/run", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({}),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      msg.className = "save-state err";
      msg.textContent = t("save.badToken", "401 · unauthorized");
      return;
    }
    if (!res.ok) {
      const err = (body && (body.error || (Array.isArray(body.details) &&
        body.details.length && body.details.join("; ")))) || ("HTTP " + res.status);
      msg.className = "save-state err";
      msg.textContent = t("cap.doctorErr", "doctor run failed: {e}")
        .replace("{e}", String(err));
      out.hidden = false;
      out.textContent = msg.textContent;
      return;
    }
    const lines = Array.isArray(body.lines) ? body.lines.map(String) : [];
    // the doctor's own summary line passes through verbatim when it parsed;
    // without it the counts are composed from the parsed numbers instead
    const counts = t("cap.doctorCounts", "FAIL {f} · WARN {w}")
      .replace("{f}", String(body.fail == null ? 0 : body.fail))
      .replace("{w}", String(body.warn == null ? 0 : body.warn));
    const summary = (typeof body.summary === "string" && body.summary)
      ? body.summary : counts;
    out.textContent = lines.length ? lines.slice(0, 80).join("\n") : summary;
    out.hidden = false;
    msg.className = "save-state" + ((Number(body.fail) || 0) > 0 ? " err" : "");
    msg.textContent = summary;
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("cap.doctorErr", "doctor run failed: {e}")
      .replace("{e}", String(e).slice(0, 120));
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------------- cron delete (0.5.40) -- */
async function cronDel(id) {
  const msg = document.getElementById("cronMsg");
  if (!id) return;
  if (!window.confirm(t("cron.delConfirm", "Delete cron job {id}?")
      .replace("{id}", id))) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = t("save.needToken", "token required"); }
    return;
  }
  if (msg) { msg.className = "save-state"; msg.textContent = "…"; }
  try {
    const res = await fetch("/api/cron", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "del", id: id }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (!res.ok) {
      const err = (body && (body.error || (Array.isArray(body.details) &&
        body.details.length && body.details.join("; ")))) || ("HTTP " + res.status);
      if (msg) {
        msg.className = "save-state err";
        msg.textContent = t("cron.delErr", "delete failed: {e}")
          .replace("{e}", String(err));
      }
      return;
    }
    if (msg) { msg.className = "save-state"; msg.textContent = ""; }
    refreshCron();               // the row disappearing is the confirmation
  } catch (e) {
    if (msg) {
      msg.className = "save-state err";
      msg.textContent = t("cron.delErr", "delete failed: {e}")
        .replace("{e}", String(e).slice(0, 120));
    }
  }
}

/* ------------------------------------------------ mission board (0.5.44) --
   Settings MISSIONS card (autonomous-ops car, Lane F): reads
   GET /api/mission -> {reporting, active|null, recent[≤10], count} and
   drives POST /api/mission {action: start|pause|resume|stop} with the
   Bearer token. The reporting tier select writes its own settings partition
   (POST /api/settings {mission:{reporting}} — the same validated merge every
   other block uses) AND rides along with start, so the displayed tier is
   never a guess: it is what GET /api/mission reports. Honest states only:
   no active mission renders `mission.idle`, an unreachable board renders
   `mission.unavail`, a server 400/409 detail passes through VERBATIM (it
   names the state rule — never a value). `goal` is user input: escaped here,
   never a credential, and capped at the server's 4000-char limit. */
let missionData = null, missionSeen = false, missionBusy = false;
/* car 2 task board (0.5.45 Lane I): the selected mission's DETAIL pane.
   `missionDetailId` is the selection (null = list view, unchanged),
   `missionDetail` the last honest GET /api/mission?id= payload, and
   `missionDetailErr` the verbatim failure (404/network) — never a guess. */
let missionDetailId = null, missionDetail = null, missionDetailErr = null;

function missionTrunc(s, n) {
  const str = String(s == null ? "" : s);
  return str.length > n ? str.slice(0, n) + "…" : str;
}

function missionStateLabel(st) {
  return t("mission.st." + st, String(st == null ? "" : st));
}

async function refreshMission() {
  try {
    const d = await fetch("/api/mission").then(r => r.json());
    if (d && typeof d === "object") { missionData = d; missionSeen = true; }
    else throw new Error("bad payload");
  } catch (e) {
    // keep the last honest snapshot after the first success (setup parity)
    if (!missionSeen) missionData = null;
  }
  renderMission();
  // an OPEN detail rides the same poll cadence — refreshed in place (the
  // loading state only ever shows before the first successful fetch)
  if (missionDetailId) await missionFetchDetail();
}

/* one GET /api/mission?id= round trip for the current selection; a failure
   replaces the pane content with the server's own words (404 detail /
   error / HTTP code / exception text) — the board never invents rows. */
async function missionFetchDetail() {
  const mid = missionDetailId;
  if (!mid) return;
  try {
    const res = await fetch("/api/mission?id=" + encodeURIComponent(mid));
    const body = await res.json().catch(() => null);
    if (missionDetailId !== mid) return;          // the user switched away
    const m = (body && typeof body === "object" && body.mission
      && typeof body.mission === "object") ? body.mission : null;
    if (!res.ok || !m) {
      const err = (body && ((Array.isArray(body.details) && body.details.length
        && body.details.join("; ")) || body.error)) || ("HTTP " + res.status);
      missionDetailErr = String(err);
      missionDetail = null;
    } else {
      missionDetail = m;
      missionDetailErr = null;
    }
  } catch (e) {
    missionDetailErr = String(e).slice(0, 140);
    missionDetail = null;
  }
  renderMission();
}

async function missionOpen(mid) {
  missionDetailId = String(mid || "");
  missionDetail = null;
  missionDetailErr = null;
  renderMission();                            // loading state, no claim yet
  await missionFetchDetail();
}

function missionBack() {
  missionDetailId = null;
  missionDetail = null;
  missionDetailErr = null;
  renderMission();
}

/* localized relative time for `last_report_at` (epoch seconds): compact
   units (m/h/d) travel inside the template so every locale reads "N unit
   ago" naturally. */
function missionAgo(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n) || n <= 0) return t("mission.never", "never");
  const s = Math.max(0, Date.now() / 1000 - n);
  if (s < 90) return t("mission.agoNow", "just now");
  const v = s < 3600 ? Math.round(s / 60) + "m"
    : s < 86400 ? Math.round(s / 3600) + "h"
    : Math.round(s / 86400) + "d";
  return t("mission.ago", "{v} ago").replace("{v}", v);
}

/* milestone-level status is NOT in the API payload (Lane H returns
   id/title/subtasks only) — derive it deterministically from the subtask
   rows so the badge never claims more than the server said: a gap blocks
   the read, then any blocked, then any running, then all-done. */
function missionMsStatus(m) {
  const subs = (m && Array.isArray(m.subtasks)) ? m.subtasks : [];
  if (!subs.length) return "pending";
  if (subs.some(s => s && s.status === "degraded")) return "degraded";
  if (subs.some(s => s && s.status === "blocked")) return "blocked";
  if (subs.some(s => s && s.status === "running")) return "running";
  if (subs.every(s => s && s.status === "done")) return "done";
  return "pending";
}

const MISSION_SYM = { done: "✓", running: "▸", degraded: "⊘",
                      blocked: "⛔", pending: "○" };

function missionStateChip(st) {
  const tone = (st === "running" || st === "done") ? "ok"
    : (st === "degraded") ? "warn"
    : (st === "sealed" || st === "paused_approve") ? "bad" : "";
  return actChip(missionStateLabel(st), tone);
}

/* one budget segment: used/max with a warning colour at >=80% and red at
   the limit — numbers are the server's own (budget vs budget_used). */
function missionBudSeg(label, used, max, fmt) {
  const u = Number(used), m = Number(max);
  const uu = Number.isFinite(u) ? u : 0;
  const mm = Number.isFinite(m) ? m : 0;
  const pct = mm > 0 ? uu / mm : 0;
  const cls = pct >= 1 ? " over" : pct >= 0.8 ? " warn" : "";
  const txt = t(label.k, label.f)
    .replace("{u}", fmt(uu)).replace("{m}", fmt(mm));
  return `<span class="md-seg${cls}">${escapeHtml(txt)}</span>`;
}

/* ------------------- car 3 (0.5.46 Lane K2): constraints + approvals ---
   The three A-face start constraints travel in GET ?id= detail rows as
   `deadline` / `redlines` / `deliverable` (null = unset, never invented).
   The deadline renders as a RELATIVE time with a red past-deadline line —
   the server validated "future" at start, time keeps running. The
   approvals block is badge + deep links ONLY: approval happens in the
   EXISTING Web3 panel behind the Bearer gate, never here. */
function missionSpan(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.round(s / 60) + "m";
  if (s < 86400) {
    const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
    return m ? h + "h " + m + "m" : h + "h";
  }
  const d = Math.floor(s / 86400), h2 = Math.round((s % 86400) / 3600);
  return h2 ? d + "d " + h2 + "h" : d + "d";
}

function missionConstraintsHtml(m) {
  const parts = [];
  if (m.deadline) {
    const raw = String(m.deadline);
    const ts = Date.parse(raw);
    let inner, over = false;
    if (Number.isNaN(ts)) {           // an odd stored value shows verbatim
      inner = t("mission.deadlineLbl", "DEADLINE") + ": " + raw;
    } else if (ts <= Date.now()) {
      over = true;
      inner = "\u23f0 " + t("mission.deadlineOver", "deadline {ago}")
        .replace("{ago}", missionAgo(ts / 1000));
    } else {
      inner = "\u23f0 " + t("mission.deadlineIn", "deadline in {v}")
        .replace("{v}", missionSpan((ts - Date.now()) / 1000));
    }
    parts.push(`<div class="md-cs${over ? " over" : ""}">${escapeHtml(inner)}</div>`);
  }
  if (m.redlines) {
    parts.push(`<div class="md-cs"><span class="md-cs-l">` +
      `${escapeHtml(t("mission.redlinesLbl", "RED LINES"))}</span>` +
      `${escapeHtml(String(m.redlines))}</div>`);
  }
  if (m.deliverable) {
    parts.push(`<div class="md-cs"><span class="md-cs-l">` +
      `${escapeHtml(t("mission.deliverableLbl", "DELIVERABLE"))}</span>` +
      `${escapeHtml(String(m.deliverable))}</div>`);
  }
  return parts.join("");
}

function missionApprovalsHtml(m) {
  const appr = Array.isArray(m.approvals) ? m.approvals : [];
  if (m.state !== "paused_approve" && !appr.length) return "";
  let rows;
  if (appr.length) {
    rows = appr.map(a => {
      const ids = (a && Array.isArray(a.plans)) ? a.plans : [];
      const links = ids.map(pid =>
        `<a class="md-planlink" href="#" data-plan-link="` +
        `${escapeHtml(String(pid))}" title="` +
        `${escapeHtml(t("mission.apprOpen", "open in the Web3 panel"))}">` +
        `${escapeHtml(String(pid))}</a>`).join(" ");
      const lab = t("mission.apprTask", "task {t} · {n} plan(s) waiting")
        .replace("{t}", String((a && a.task) || "?"))
        .replace("{n}", String(ids.length));
      return `<div class="md-appr"><span class="md-appr-t">` +
        `${escapeHtml(lab)}</span> ${links}</div>`;
    }).join("");
  } else {
    rows = `<div class="stat-note">${escapeHtml(
      t("mission.apprEmpty", "approval queue empty"))}</div>`;
  }
  return `<div class="md-sec">${escapeHtml(
      t("mission.approvals", "APPROVALS"))}</div>` + rows +
    `<div class="stat-note">${escapeHtml(t("mission.approveHint",
      "approve in the Web3 panel — the mission resumes on its own, " +
      "nothing to do here"))}</div>`;
}

/* deep link into the EXISTING Web3 panel: switch view (the actFix/orch
   posture), let the approval queue load, then scroll + pulse the plan card
   that carries this id. No second decision surface, no fetch of our own. */
function missionGoPlan(pid) {
  const id = String(pid || "").replace(/["\\]/g, "");
  if (!id) return;
  setView("exchange", true);
  const focus = () => {
    const el = document.querySelector('#exPlans [data-plan="' + id + '"]');
    if (!el || typeof el.scrollIntoView !== "function") return false;
    el.scrollIntoView({ block: "center" });
    el.classList.add("plan-focus");
    setTimeout(() => el.classList.remove("plan-focus"), 2400);
    return true;
  };
  if (focus()) return;
  let tries = 0;                       // the list may still be loading
  const iv = setInterval(() => {
    tries += 1;
    if (focus() || tries >= 24) clearInterval(iv);
  }, 150);
}

function renderMissionDetail() {
  const box = document.getElementById("missionDetail");
  if (!box) return;
  if (!missionDetailId) { box.hidden = true; box.innerHTML = ""; return; }
  box.hidden = false;
  const back = `<span class="btn md-back" id="missionDetailBack" role="button"` +
    ` tabindex="0">${escapeHtml(t("mission.back", "← back to list"))}</span>`;
  if (missionDetailErr) {
    box.innerHTML = `<div class="md-head">${back}</div>` +
      `<div class="stat-note" role="alert">${escapeHtml(
        t("mission.err", "mission request failed: {e}")
          .replace("{e}", missionDetailErr))}</div>`;
    return;
  }
  if (!missionDetail) {                 // first fetch in flight: no claim
    box.innerHTML = `<div class="md-head">${back}</div>` +
      `<div class="stat-note">…</div>`;
    return;
  }
  const m = missionDetail;
  const prog = (m.progress && typeof m.progress === "object") ? m.progress : {};
  const meta = [String(m.id || ""), String(m.reporting || ""),
    t("mission.tasks", "tasks {d}/{t}")
      .replace("{d}", String(prog.done == null ? 0 : prog.done))
      .replace("{t}", String(prog.total == null ? 0 : prog.total))]
    .map(escapeHtml).join(" · ");
  const deg = Number(prog.degraded || 0);
  const degChip = deg > 0
    ? ` ${actChip(t("mission.st.degraded", "degraded") + " " + deg, "warn")}` : "";
  const lastRep = m.last_report_at
    ? t("mission.lastReport", "last report {ago}")
        .replace("{ago}", missionAgo(m.last_report_at))
    : t("mission.lastReport", "last report {ago}")
        .replace("{ago}", t("mission.never", "never"));

  /* car 3: the three constraints + the approval queue (badge + deep links) */
  const csHtml = missionConstraintsHtml(m);
  const apprHtml = missionApprovalsHtml(m);

  /* budget + usage: the resolved guard values (frozen at start) vs honest
     counters — >=80% amber, >=100% red (missionBudSeg paints it). */
  const b = (m.budget && typeof m.budget === "object") ? m.budget : {};
  const bu = (m.budget_used && typeof m.budget_used === "object")
    ? m.budget_used : {};
  const oneDec = v => String(Math.round(Number(v) * 10) / 10);
  const budHtml = `<div class="md-bud">` +
    missionBudSeg({ k: "mission.buSlices", f: "slices {u}/{m}" },
      bu.slices, b.max_slices, v => String(Math.round(v))) +
    missionBudSeg({ k: "mission.buWall", f: "wall {u}/{m}h" },
      bu.wall_hours, b.max_wall_hours, oneDec) +
    missionBudSeg({ k: "mission.buCalls", f: "calls {u}/{m}" },
      bu.daily_calls, b.max_daily_model_calls, v => String(Math.round(v))) +
    `</div>`;

  /* milestones + subtask check-states: symbols only, statuses projected
     exactly as the server sent them (in_progress -> running upstream). */
  const plan = (m.plan && typeof m.plan === "object") ? m.plan : null;
  let planHtml;
  if (!plan || !Array.isArray(plan.milestones) || !plan.milestones.length) {
    planHtml = `<div class="stat-note">${escapeHtml(
      t("mission.noPlan", "no plan yet — this mission has not been planned"))}</div>`;
  } else {
    const items = plan.milestones.map(ms => {
      const title = String((ms && (ms.title || ms.text)) || "");
      const stt = missionMsStatus(ms);
      const subs = (ms && Array.isArray(ms.subtasks)) ? ms.subtasks : [];
      const subHtml = subs.map(s => {
        const ss = String((s && s.status) || "pending");
        const sym = MISSION_SYM[ss] || MISSION_SYM.pending;
        return `<div class="md-sub s-${escapeHtml(ss)}">` +
          `<span class="md-sym" aria-hidden="true">${sym}</span>` +
          `<span>${escapeHtml(String((s && s.text) || ""))}</span>` +
          `<span class="md-sub-st">${escapeHtml(
            t("mission.st." + ss, ss))}</span></div>`;
      }).join("");
      return `<div class="md-ms">` +
        `<div class="md-ms-head">${missionStateChip(stt)}` +
        `<span class="md-ms-title">${escapeHtml(title)}</span></div>` +
        subHtml + `</div>`;
    }).join("");
    planHtml = `<div class="md-sec">${escapeHtml(
      t("mission.milestones", "MILESTONES"))}</div>${items}`;
  }

  /* method-exhaustion summary: marked/total in a native <details>, blockers
     listed verbatim when present (both scrubbed server-side). */
  const methods = (plan && Array.isArray(plan.methods)) ? plan.methods : [];
  const blockers = (plan && Array.isArray(plan.blockers)) ? plan.blockers : [];
  let methHtml = "";
  if (methods.length) {
    const mk = methods.filter(x => x && x.marked).length;
    const rowsM = methods.map(x =>
      `<div class="md-meth${x && x.marked ? " on" : ""}">` +
      `<span aria-hidden="true">${x && x.marked ? "✓" : "○"}</span> ` +
      `${escapeHtml(missionTrunc(String((x && x.text) || ""), 160))}</div>`).join("");
    methHtml = `<details class="md-methods"><summary>${escapeHtml(
      t("mission.methods", "methods {m}/{t} marked")
        .replace("{m}", String(mk)).replace("{t}", String(methods.length)))}` +
      `</summary>${rowsM}`;
    if (blockers.length) {
      methHtml += `<div class="md-sec">${escapeHtml(
        t("mission.blockers", "blockers"))}</div>` +
        blockers.map(x => `<div class="md-meth">${escapeHtml(
          missionTrunc(String(x), 160))}</div>`).join("");
    }
    methHtml += `</details>`;
  }

  /* event tail (≤20, scrubbed upstream): kind + text, escaped, scrollable */
  const events = Array.isArray(m.events) ? m.events : [];
  const evHtml = events.length
    ? `<div class="md-events">` + events.map(ev => {
        const e = (ev && typeof ev === "object") ? ev : {};
        return `<div class="md-ev"><span class="md-ev-kind">${escapeHtml(
          String(e.kind || ""))}</span>${escapeHtml(String(e.text || ""))}</div>`;
      }).join("") + `</div>`
    : `<div class="stat-note">${escapeHtml(
        t("mission.noEvents", "no events yet"))}</div>`;

  box.innerHTML =
    `<div class="md-head">${back}${missionStateChip(String(m.state || ""))}` +
    `<span class="stat-note md-meta">${meta}${degChip}</span></div>` +
    `<div class="md-goal">${escapeHtml(missionTrunc(m.goal, 400))}</div>` +
    `<div class="stat-note">${escapeHtml(lastRep)}</div>` +
    csHtml + apprHtml +
    budHtml + planHtml + methHtml +
    `<div class="md-sec">${escapeHtml(t("mission.events", "EVENT TAIL"))}</div>` +
    evHtml;
}

function renderMission() {
  renderMissionDetail();               // the pane follows its own state
  const stateEl = document.getElementById("missionState");
  const recentEl = document.getElementById("missionRecent");
  const sel = document.getElementById("missionReporting");
  const startBtn = document.getElementById("missionStartBtn");
  const pauseBtn = document.getElementById("missionPauseBtn");
  const resumeBtn = document.getElementById("missionResumeBtn");
  const stopBtn = document.getElementById("missionStopBtn");
  if (!stateEl) return;

  if (!missionData) {
    stateEl.hidden = !missionSeen;          // before the first probe: no claim
    if (missionSeen) {
      stateEl.textContent = t("mission.unavail", "mission board unavailable");
    }
    if (recentEl) { recentEl.hidden = true; recentEl.innerHTML = ""; }
    if (startBtn) startBtn.disabled = missionBusy;
    [pauseBtn, resumeBtn, stopBtn].forEach(b => { if (b) b.disabled = true; });
    return;
  }

  const active = missionData.active || null;
  // the select shows the STORED tier — never a local guess
  if (sel && typeof missionData.reporting === "string" && sel.value !== missionData.reporting
      && document.activeElement !== sel) {
    sel.value = missionData.reporting;
  }

  if (!active) {
    stateEl.hidden = false;
    stateEl.textContent = t("mission.idle", "no active mission — idle");
  } else {
    const prog = (active.progress && typeof active.progress === "object")
      ? active.progress : {};
    const goal = missionTrunc(active.goal, 120);
    const meta = [
      String(active.id || ""),
      missionStateLabel(active.state),
      t("mission.tasks", "tasks {d}/{t}")
        .replace("{d}", String(prog.done == null ? 0 : prog.done))
        .replace("{t}", String(prog.total == null ? 0 : prog.total)),
      String(active.reporting || missionData.reporting || ""),
    ].map(escapeHtml).join(" · ");
    stateEl.hidden = false;
    // the ACTIVE line opens its own detail too (the board's primary target);
    // car 3: a paused_approve row also carries the red "approval pending"
    // badge — the detail pane holds the plan deep links behind it
    const actBadge = active.state === "paused_approve"
      ? " " + actChip(t("mission.awaitBadge", "approval pending"), "bad")
      : "";
    stateEl.innerHTML = `<div class="md-open" role="button" tabindex="0"` +
      ` data-mid="${escapeHtml(String(active.id || ""))}">` +
      `<div>${meta}${actBadge}</div>` +
      `<div style="opacity:.85;">${escapeHtml(goal)}</div></div>`;
  }

  // recent tail: a compact honest table, never invented rows
  if (recentEl) {
    const rows = Array.isArray(missionData.recent) ? missionData.recent : [];
    if (!rows.length) {
      recentEl.hidden = true;
      recentEl.innerHTML = "";
    } else {
      recentEl.hidden = false;
      recentEl.innerHTML = rows.map(r => {
        const row = (r && typeof r === "object") ? r : {};
        const mid = String(row.id || "");
        const meta = [mid, missionStateLabel(row.state)]
          .map(escapeHtml).join(" · ");
        const rowBadge = row.state === "paused_approve"
          ? " " + actChip(t("mission.awaitBadge", "approval pending"), "bad")
          : "";
        const sel = (mid && mid === missionDetailId) ? " is-sel" : "";
        return `<div class="ctl-row mission-row${sel}" role="button" tabindex="0"` +
          ` data-mid="${escapeHtml(mid)}">` +
          `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
          `${escapeHtml(missionTrunc(row.goal, 90))}</label>` +
          `<span class="stat-note" style="margin-top:0;">${meta}${rowBadge}</span></div>`;
      }).join("");
    }
  }

  if (startBtn) startBtn.disabled = missionBusy || !!active;
  // car 2: pause is legal from running AND degraded (Lane H edge)
  if (pauseBtn) pauseBtn.disabled = missionBusy || !active ||
    (active.state !== "running" && active.state !== "degraded");
  if (resumeBtn) resumeBtn.disabled = missionBusy || !active || active.state !== "paused_user";
  if (stopBtn) stopBtn.disabled = missionBusy || !active;
}

async function missionAction(action) {
  const msg = document.getElementById("missionMsg");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = t("save.needToken", "token required"); }
    return;
  }
  const payload = { action: action };
  if (action === "start") {
    const goalEl = document.getElementById("missionGoal");
    const goal = goalEl ? goalEl.value.trim() : "";
    if (!goal) {
      if (msg) { msg.className = "save-state err";
        msg.textContent = t("mission.needGoal",
          "goal required — describe what to accomplish"); }
      return;
    }
    payload.goal = goal;
    const sel = document.getElementById("missionReporting");
    if (sel) payload.reporting = sel.value;
    // car 2 budget override: only the FILLED fields travel — blank means
    // "use the settings defaults" (the server merges over settings anyway).
    // A non-numeric entry in a number input yields "" (skipped); an
    // out-of-range NUMBER is sent and the SERVER refuses it 400 — its
    // detail renders verbatim below, never a local guess.
    const bget = id => {
      const el = document.getElementById(id);
      const v = el ? String(el.value || "").trim() : "";
      if (v === "") return null;
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    };
    const budget = {};
    const bs = bget("missionBudgetSlices");
    if (bs !== null) budget.max_slices = bs;
    const bw = bget("missionBudgetWall");
    if (bw !== null) budget.max_wall_hours = bw;
    const bc = bget("missionBudgetCalls");
    if (bc !== null) budget.max_daily_model_calls = bc;
    if (Object.keys(budget).length) payload.budget = budget;
    // car 3 constraints: only FILLED fields travel (blank = unset); the
    // datetime-local value becomes an ISO8601 instant — the server owns
    // every rule (future / <=500 / <=200) and its 400 detail renders
    // verbatim below, never a local guess
    const dlEl = document.getElementById("missionDeadline");
    if (dlEl && dlEl.value) {
      const when = new Date(dlEl.value);
      if (!Number.isNaN(when.getTime())) payload.deadline = when.toISOString();
    }
    const rlEl = document.getElementById("missionRedlines");
    const rl = rlEl ? String(rlEl.value || "").trim() : "";
    if (rl) payload.redlines = rl;
    const dvEl = document.getElementById("missionDeliverable");
    const dv = dvEl ? String(dvEl.value || "").trim() : "";
    if (dv) payload.deliverable = dv;
  } else {
    const active = missionData && missionData.active;
    if (!active || !active.id) return;
    payload.id = String(active.id);
    if (action === "stop" && !window.confirm(t("mission.stopConfirm",
        "Stop mission {id}? It will be sealed with evidence.")
        .replace("{id}", payload.id))) return;
  }
  missionBusy = true;
  renderMission();
  if (msg) { msg.className = "save-state"; msg.textContent = "…"; }
  try {
    const res = await fetch("/api/mission", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(payload),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      if (msg) { msg.className = "save-state err";
        msg.textContent = t("save.badToken", "401 · unauthorized"); }
      return;
    }
    if (!res.ok) {
      const det = (Array.isArray(body.details) && body.details.length)
        ? body.details.join("; ") : null;
      const err = det || body.error || ("HTTP " + res.status);
      if (msg) { msg.className = "save-state err";
        msg.textContent = t("mission.err", "mission request failed: {e}")
          .replace("{e}", String(err)); }
      return;
    }
    if (msg) {
      msg.className = "save-state";
      msg.textContent = action === "start"
        ? t("mission.started", "mission started ✓")
        : t("save.ok", "saved ✓");
    }
    beep(880, 0.06);
  } catch (e) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = t("mission.err", "mission request failed: {e}")
        .replace("{e}", String(e).slice(0, 120)); }
  }
  missionBusy = false;
  await refreshMission();
}

/* tier select -> its own settings partition (live, no SAVE button) */
async function missionTierWrite(value) {
  const msg = document.getElementById("missionMsg");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = t("save.needToken", "token required"); }
    return;
  }
  if (msg) { msg.className = "save-state"; msg.textContent = "…"; }
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ mission: { reporting: value } }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      if (msg) { msg.className = "save-state err";
        msg.textContent = t("save.badToken", "401 · unauthorized"); }
    } else if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      if (msg) { msg.className = "save-state err";
        msg.textContent = details.length ? details.join("; ")
          : (body.error || "error"); }
    } else {
      if (msg) { msg.className = "save-state";
        msg.textContent = t("save.ok", "saved ✓"); }
    }
  } catch (e) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = String(e).slice(0, 120); }
  }
  await refreshMission();
}

/* car 3 (0.5.46 Lane K2): the reporter settings next to the tier select —
   quiet window ("" or HH:MM-HH:MM, may cross midnight) + min_interval
   (0..86400, 0 = tier default). Same write posture as missionTierWrite:
   Bearer POST /api/settings {mission:{...}} on `change`, server 400 detail
   VERBATIM in #missionMsg (it names the grammar, never a value), and the
   stored values re-prefill from the write response so the form can never
   drift from what the server actually holds. */
async function missionCfgWrite() {
  const msg = document.getElementById("missionMsg");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = t("save.needToken", "token required"); }
    return;
  }
  const qEl = document.getElementById("missionQuietHours");
  const iEl = document.getElementById("missionMinInterval");
  const quiet = qEl ? String(qEl.value || "").trim() : "";
  const rawI = iEl ? String(iEl.value || "").trim() : "";
  let mi = 0;
  if (rawI !== "") {
    const n = Number(rawI);
    mi = Number.isFinite(n) ? n : rawI;   // a non-number still travels: the
  }                                       // server refuses it 400, honestly
  if (msg) { msg.className = "save-state"; msg.textContent = "…"; }
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ mission: { quiet_hours: quiet,
                                        min_interval: mi } }),
    });
    let body = {};
    try { body = await res.json(); } catch (e) { body = {}; }
    if (res.status === 401) {
      if (msg) { msg.className = "save-state err";
        msg.textContent = t("save.badToken", "401 · unauthorized"); }
      return;
    }
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      if (msg) { msg.className = "save-state err";
        msg.textContent = details.length ? details.join("; ")
          : (body.error || "error"); }
      return;
    }
    if (body.settings && typeof body.settings === "object") {
      settings.mission = body.settings.mission || settings.mission;
      missionPrefillCfg();
    }
    if (msg) { msg.className = "save-state";
      msg.textContent = t("save.ok", "saved ✓"); }
    beep(880, 0.06);
  } catch (e) {
    if (msg) { msg.className = "save-state err";
      msg.textContent = String(e).slice(0, 120); }
  }
}

/* form <- GET /api/settings mission partition (always present: the server
   keeps DEFAULT_SETTINGS resident). Never fight the caret: an element the
   user is editing keeps its local value. */
function missionPrefillCfg() {
  const m = (settings && settings.mission
    && typeof settings.mission === "object") ? settings.mission : {};
  const q = document.getElementById("missionQuietHours");
  if (q && document.activeElement !== q) {
    q.value = m.quiet_hours == null ? "" : String(m.quiet_hours);
  }
  const i = document.getElementById("missionMinInterval");
  if (i && document.activeElement !== i) {
    const v = Number(m.min_interval);
    i.value = Number.isFinite(v) ? String(v) : "0";
  }
}

/* ------------------------------------------- orchestration card (0.5.45) --
   READ-ONLY summary of what a mission can draw on (Lane I / design C):
   tools / enabled channels / web3 surface / credential slots / skills.
   Every value comes from an existing catalog fetch (refreshCaps /
   loadChanStatus / loadCreds) — NO second switchboard is built here; each
   row is a DEEP LINK to the control that already owns that setting.
   Shape discipline (actChip posture): a source whose payload shape does
   not match simply omits its row — never an invented value. */
function orchLink(kind, label, value) {
  return `<button type="button" class="ctl-row orch-row" data-orch="${escapeHtml(kind)}"` +
    ` title="${escapeHtml(t("orch.open", "open the control that owns this"))}">` +
    `<label>${escapeHtml(label)}</label>` +
    `<span class="stat-note orch-val">${escapeHtml(value)}</span></button>`;
}

function renderOrch() {
  const list = document.getElementById("orchList");
  if (!list) return;
  const c = (capsData && typeof capsData === "object") ? capsData : null;
  if (!c) {
    // before the first catalog fetch: no claim; after it failed: honest note
    list.innerHTML = capsSeen ? `<div class="stat-note">${escapeHtml(
      t("orch.unavail", "orchestration data unavailable"))}</div>` : "";
    return;
  }
  const rows = [];
  // tools: the live registry count (tools_off already applied server-side)
  if (Array.isArray(c.tools)) {
    rows.push(orchLink("tools", t("orch.tools", "available tools"),
      String(c.tools.length)));
  }
  // channels: enabled count from the capabilities block; the /api/channels
  // rows (when loaded) are the fallback source for the same number
  const capChan = (c.channels && typeof c.channels === "object") ? c.channels : null;
  let enabledN = null;
  if (capChan && Array.isArray(capChan.enabled)) enabledN = capChan.enabled.length;
  else if (Array.isArray(chanStatusRows) && chanStatusRows.length) {
    enabledN = chanStatusRows.filter(r => r && r.enabled).length;
  }
  if (enabledN !== null) {
    rows.push(orchLink("chan", t("orch.chan", "channels enabled"),
      String(enabledN)));
  }
  // web3 surface: read-only vs orderable-behind-the-approval-gate — from
  // the exchange block of the capability catalog (W2 truth)
  const ex = (c.exchange && typeof c.exchange === "object") ? c.exchange : null;
  if (ex && typeof ex.read_only === "boolean") {
    const orderable = Array.isArray(ex.orderable) ? ex.orderable.length : 0;
    const val = (orderable > 0)
      ? t("orch.w3Order", "orderable ({n}) · approval gate")
          .replace("{n}", String(orderable))
      : t("orch.w3ro", "read-only");
    rows.push(orchLink("web3", t("orch.web3", "web3 surface"), val));
  }
  // credential slots: set count over the writable catalog (32) — the
  // presence-only rows from GET /api/secrets (never a value)
  const cr = (credsData && Array.isArray(credsData.slots)) ? credsData : null;
  if (cr) {
    const writable = cr.slots.filter(s => s && s.writable !== false);
    const setN = writable.filter(s => s.set).length;
    rows.push(orchLink("slots", t("orch.slots", "credential slots set"),
      `${setN}/${writable.length}`));
  }
  // skills: the capabilities field decides — absent field = row hidden
  if (Array.isArray(c.skills)) {
    rows.push(orchLink("skills", t("orch.skills", "skills installed"),
      String(c.skills.length)));
  }
  list.innerHTML = rows.join("");
}

/* deep link: existing navigation only (setView + actFocusEl, the actFix
   posture) — never a second switchboard. Unknown kinds are a no-op. */
function orchGo(kind) {
  const focusSettings = sel => {
    setView("settings", true);
    requestAnimationFrame(() => {
      const el = document.querySelector(sel);
      if (el) actFocusEl(el);
    });
  };
  if (kind === "tools") focusSettings("#toolsOffRow");
  else if (kind === "chan") focusSettings("#chanList");
  else if (kind === "slots") focusSettings("#credList");
  else if (kind === "web3") setView("exchange", true);
  else if (kind === "skills") setView("skills", true);
}

/* --------------------------------------------------------------- polling -- */
let statusTimer = null, usageTimer = null, missionTimer = null;

async function refreshStatus() {
  try {
    statusData = await fetch("/api/status").then(r => r.json());
    renderStatus();
    document.getElementById("ts").textContent = statusData.time_utc || "";
    // stream lines rendered before status arrived still carry placeholders
    // (e.g. the boot event's {v}) — repaint them now that version is known
    refreshEventTexts();
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
  // mission board rides the SAME user-configured cadence as the status panel
  clearInterval(missionTimer);
  missionTimer = setInterval(refreshMission, ms);
}

function tickClock() {
  document.getElementById("clock").textContent =
    new Date().toLocaleTimeString("en-GB");
  refreshTools();
  exTick();            // W2: plan expiry countdowns tick with the clock
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

/* ---------------------------------------------------------------- views -- */
/* All seven views live in the same document; the inactive ones are hidden with
   CSS, so every poller keeps writing into real DOM and ids stay stable.
   ?view=<name> deep-links the active view (default: overview). */
const VIEWS = ["overview", "chat", "skills", "exchange", "evolution", "stream", "settings"];
const QS_VIEW = (params.get("view") || "").toLowerCase();
let currentView = VIEWS.includes(QS_VIEW) ? QS_VIEW : "overview";

function setView(name, fromUser) {
  if (!VIEWS.includes(name)) name = "overview";
  currentView = name;
  document.querySelectorAll(".view").forEach(v =>
    v.classList.toggle("is-active", v.dataset.view === name));
  document.querySelectorAll(".nav-item").forEach(b => {
    const on = b.dataset.view === name;
    b.classList.toggle("is-active", on);
    if (on) b.setAttribute("aria-current", "page");
    else b.removeAttribute("aria-current");
  });
  const head = document.querySelector("#view-" + name + " .view-head h2");
  if (head) document.title = "I'm AiDog — " + head.textContent;
  if (fromUser) {
    const p = new URLSearchParams(location.search);
    p.set("view", name);
    history.replaceState(null, "", location.pathname + "?" + p.toString());
    window.scrollTo(0, 0);
  }
  // W2: the approval queue loads when the exchange view opens — on a nav
  // click (fresh data after a decision elsewhere) and on the first paint
  // (deep link / boot). A language switch only re-renders, it never refetches.
  if (name === "exchange" && (fromUser || exPlans === null)) exPlansLoad();
}

/* --------------------------------------------------------- topbar height -- */
/* the sticky rail starts right under the sticky topbar: measure it once and
   on every resize so wrapped chips never overlap the nav */
const topbarEl = document.querySelector(".topbar");
function syncTopbarHeight() {
  if (!topbarEl) return;
  document.documentElement.style.setProperty("--topbar-h", topbarEl.offsetHeight + "px");
}
window.addEventListener("resize", syncTopbarHeight);
syncTopbarHeight();

/* ------------------------------------------------------------------ wire -- */
document.getElementById("langBtn").addEventListener("click", () => {
  const next = document.documentElement.lang === "en" ? "zh" : "en";
  localStorage.setItem(LANG_KEY, next);
  loadLang(next);
});
document.querySelectorAll(".nav-item").forEach(btn => {
  btn.addEventListener("click", () => setView(btn.dataset.view, true));
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
/* --- car B2: schedule editor + cap editor + system card wiring (all
       guarded: an absent element is a no-op, never a page error) --- */
const evoEnabledEl = document.getElementById("evoEnabled");
if (evoEnabledEl) evoEnabledEl.addEventListener("click", e => {
  const b = e.currentTarget;
  b.setAttribute("aria-pressed", b.getAttribute("aria-pressed") !== "true");
});
const evoSaveBtnEl = document.getElementById("evoSaveBtn");
if (evoSaveBtnEl) evoSaveBtnEl.addEventListener("click", evoSave);
["evoEnabled", "evoDailyAt", "evoWeeklyDay", "evoMonthlyDay"].forEach(id => {
  const el = document.getElementById(id);
  if (el) {
    el.addEventListener("input", evoCrossCheck);
    el.addEventListener("change", evoCrossCheck);
  }
});
const exCapSaveBtnEl = document.getElementById("exCapSaveBtn");
if (exCapSaveBtnEl) exCapSaveBtnEl.addEventListener("click", exCapSave);
/* --- car B1: dirty tracking for the new blocks (delegated on the containers,
       so rows rendered later are covered too). Programmatic fills at boot
       fire no events, so only a real edit raises a flag. --- */
["usageMetricsUrl", "usagePricingCfg", "usageLogDir"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => { usageDirty = true; });
});
["mycContextDirs", "mycMcp", "mycShell"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => {
    mycDirty = true; mycRestartDirty = true;
  });
});
["mycUpstreams", "mycModels", "mycBudget"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => { mycDirty = true; });
});
const toolsOffEl = document.getElementById("toolsOffList");
if (toolsOffEl) toolsOffEl.addEventListener("change", () => { toolsDirty = true; });
const chanListEl = document.getElementById("chanList");
if (chanListEl) {
  chanListEl.addEventListener("input", () => { chanDirty = true; });
  chanListEl.addEventListener("change", () => { chanDirty = true; });
}
const hookListEl = document.getElementById("hookList");
if (hookListEl) hookListEl.addEventListener("input", e => {
  const blk = e.target && e.target.closest ? e.target.closest("[data-hook-event]") : null;
  if (blk) { hookDirty.add(blk.dataset.hookEvent); updateHookCount(blk); }
});
wireCredList();
/* --- activation & operations car (0.5.40): every listener is guarded so an
       absent element is a no-op, never a page error --- */
const actRecheckEl = document.getElementById("actRecheck");
if (actRecheckEl) actRecheckEl.addEventListener("click", async () => {
  const msg = document.getElementById("actMsg");
  actRecheckEl.disabled = true;
  if (msg) { msg.className = "save-state";
    msg.textContent = t("act.checking", "checking…"); }
  await refreshSetup();
  actRecheckEl.disabled = false;
});
const actListEl = document.getElementById("actList");
if (actListEl) actListEl.addEventListener("click", e => {
  const btn = e.target.closest("[data-act-fix]");
  if (btn) actFix(btn.dataset.actFix);
});
const svcRestartBtnEl = document.getElementById("svcRestartBtn");
if (svcRestartBtnEl) svcRestartBtnEl.addEventListener("click", svcRestart);
const tgRestartBtnEl = document.getElementById("tgRestartBtn");
if (tgRestartBtnEl) tgRestartBtnEl.addEventListener("click", tgRestart);
const updCheckEl = document.getElementById("updateCheckBtn");
if (updCheckEl) updCheckEl.addEventListener("click", updateCheck);
const updApplyEl = document.getElementById("updateApplyBtn");
if (updApplyEl) updApplyEl.addEventListener("click", updateApply);
const doctorRunEl = document.getElementById("doctorRunBtn");
if (doctorRunEl) doctorRunEl.addEventListener("click", doctorRun);
const cronListDelEl = document.getElementById("cronList");
if (cronListDelEl) cronListDelEl.addEventListener("click", e => {
  const btn = e.target.closest("#cronDelBtn, [data-job]");
  if (btn && btn.dataset.job) cronDel(btn.dataset.job);
});
/* mission card (0.5.44): four actions + the tier select, all guarded */
[["missionStartBtn", "start"], ["missionPauseBtn", "pause"],
 ["missionResumeBtn", "resume"], ["missionStopBtn", "stop"]].forEach(pair => {
  const el = document.getElementById(pair[0]);
  if (el) el.addEventListener("click", () => missionAction(pair[1]));
});
const missionReportingEl = document.getElementById("missionReporting");
if (missionReportingEl) missionReportingEl.addEventListener("change",
  e => missionTierWrite(e.target.value));
/* car 3 reporter settings: both inputs write on `change` (commit/blur) —
   never per keystroke, so a half-typed window never fires a 400 */
["missionQuietHours", "missionMinInterval"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("change", () => missionCfgWrite());
});
/* car 3 redlines: honest live character count (the server cap is 500) */
const missionRlEl = document.getElementById("missionRedlines");
if (missionRlEl) missionRlEl.addEventListener("input", () => {
  const c = document.getElementById("missionRedlinesCount");
  if (!c) return;
  const n = missionRlEl.value.length;
  c.textContent = n + "/500";
  c.classList.toggle("at-max", n >= 500);
});
/* car 2 task board (Lane I): row -> detail wiring. Delegated on the three
   containers (their innerHTML is rebuilt on every poll), so rows rendered
   later stay live; Enter/Space on a focused row mirrors the click. */
function missionRowPick(root, e) {
  const row = e.target && e.target.closest
    ? e.target.closest("[data-mid]") : null;
  if (row && root.contains(row)) missionOpen(row.dataset.mid);
}
[["missionState", null], ["missionRecent", null]].forEach(pair => {
  const el = document.getElementById(pair[0]);
  if (!el) return;
  el.addEventListener("click", e => missionRowPick(el, e));
  el.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      missionRowPick(el, e);
    }
  });
});
const missionDetailEl = document.getElementById("missionDetail");
if (missionDetailEl) {
  const backAct = e => {
    const b = e.target && e.target.closest
      ? e.target.closest("#missionDetailBack") : null;
    if (b) missionBack();
  };
  /* car 3: an approval plan id is a deep link into the EXISTING Web3
     panel (view switch + scroll/pulse) — approving stays over there */
  const detailAct = e => {
    const pl = e.target && e.target.closest
      ? e.target.closest("[data-plan-link]") : null;
    if (pl) {
      e.preventDefault();
      missionGoPlan(pl.getAttribute("data-plan-link"));
      return;
    }
    backAct(e);
  };
  missionDetailEl.addEventListener("click", detailAct);
  missionDetailEl.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      detailAct(e);
    }
  });
}
/* orchestration card: row -> existing control (deep link only) */
const orchListEl = document.getElementById("orchList");
if (orchListEl) orchListEl.addEventListener("click", e => {
  const row = e.target && e.target.closest
    ? e.target.closest("[data-orch]") : null;
  if (row) orchGo(row.dataset.orch);
});
const chanTestBtnEl = document.getElementById("chanTestBtn");
if (chanTestBtnEl) chanTestBtnEl.addEventListener("click", chanSendTest);
const micBtnEl = document.getElementById("micBtn");
if (micBtnEl) micBtnEl.addEventListener("click", () => { vsToggle(); });
const imgGoEl = document.getElementById("imgGo");
if (imgGoEl) imgGoEl.addEventListener("click", imgGenerate);
const imgPromptEl = document.getElementById("imgPrompt");
if (imgPromptEl) imgPromptEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); imgGenerate(); }
});
const exVenueSelEl = document.getElementById("exVenueSel");
if (exVenueSelEl) exVenueSelEl.addEventListener("change", exSyncSymbol);
const exProbeBtnEl = document.getElementById("exProbeBtn");
if (exProbeBtnEl) exProbeBtnEl.addEventListener("click", exProbe);
const exSymbolEl = document.getElementById("exSymbol");
if (exSymbolEl) exSymbolEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); exProbe(); }
});
document.getElementById("chatSend").addEventListener("click", chatSend);
document.getElementById("chatClear").addEventListener("click", chatClear);
document.getElementById("chatInput").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); chatSend(); }
});
document.getElementById("evoLearnBtn").addEventListener("click", evoLearn);
document.getElementById("evoReflectBtn").addEventListener("click", evoReflect);
document.getElementById("evoKind").addEventListener("change", e => {
  const mem = e.target.value === "memory";
  document.getElementById("evoName").style.display = mem ? "none" : "";
  document.getElementById("evoDesc").style.display = mem ? "none" : "";
});
document.getElementById("refreshSelect").addEventListener("change", scheduleRefresh);
document.getElementById("updateDismiss").addEventListener("click", () => {
  if (versionData && versionData.latest) sessionStorage.setItem("aidog_upd_dismiss", versionData.latest);
  document.getElementById("updateBanner").hidden = true;
});
document.getElementById("alertSelect").addEventListener("change", e => {
  settings.alert_preview = e.target.value; dismissedLevel = null;
  if (statusData) applyAlert(statusData.advisory);
});

const startLang = QS_LANG || localStorage.getItem(LANG_KEY) || "en";
runBoot();
setView(currentView, false);      // deep link applied before the first paint
loadLang(startLang);
loadSettings().then(() => { scheduleRefresh(); renderVoice(); initSettingsPanels(); });
refreshStatus(); refreshUsage(); refreshEvents(); refreshVersion(); refreshSkills();
refreshEvolution();
refreshCron();           // cron list for the Overview card (+ delete row)
refreshMission();        // mission board: honest idle until the first probe
refreshSetup();          // activation car: Overview checklist (hidden if 404)
loadChatHistory();   // console session restored at boot (read-only route)
refreshVoiceCap();   // engine status visible before the first orb tap (#4)
refreshCaps();       // media face: stt/image presence for the chat cards
renderExchange();    // exchange panel paints its honest fallback at once
setInterval(tickClock, 1000); tickClock();
setInterval(refreshUsage, 60000);
setInterval(refreshEvolution, 60000);
setInterval(refreshEvents, 10000);
setInterval(refreshVersion, 300000);

/* ============================================================= *
 *  Draft E — Counsel authority card (appended; skin layer).     *
 *  Hold-to-seal preview gesture + four guilloche medallions.    *
 *  Defensive by contract: every path guards missing elements,   *
 *  nothing throws, and nothing ever claims a live service or an *
 *  executed action. All visible strings come from the locale    *
 *  packs (auth.* keys). English only in this file per the repo  *
 *  source-language lint. Existing logic above is untouched.     *
 * ============================================================= */
(function () {
  "use strict";

  var HOLD_MS = 1200;                      // Draft E: 1.2s hold to seal
  var SVGNS = "http://www.w3.org/2000/svg";
  var MEDS = ["situation", "memory", "constitution", "authority"];
  var RING_R = 44, RING_LEN = 2 * Math.PI * RING_R;

  var card = document.getElementById("authCard");
  var btn = document.getElementById("authSeal");
  if (!card || !btn) return;               // skin layer: absent card = no-op

  var svg = document.getElementById("authSealSvg");
  var label = document.getElementById("authSealLabel");
  var sub = document.getElementById("authSealSub");
  var hint = document.getElementById("authHint");
  var result = document.getElementById("authResult");
  var toastEl = document.getElementById("authToast");
  var meds = document.getElementById("authMeds");
  var roLbl = document.getElementById("authReadoutLbl");
  var roBody = document.getElementById("authReadoutBody");

  /* locale lookup: same precedence as t(), fail-soft to the English fallback */
  function L(key, fb) {
    try {
      if (typeof dict !== "undefined" && dict && dict[key] !== undefined) {
        return dict[key];
      }
    } catch (e) { /* dict unavailable: fall through */ }
    return fb || key;
  }
  function set(el, key, fb) { if (el) el.textContent = L(key, fb); }

  var state = {
    holding: false,
    sealed: false,
    startT: 0,
    raf: 0,
    p: 0,
    labelKey: "auth.holdLabel",
    subKey: "auth.holdSub",
    hintKey: "auth.hintIdle",
    toastTimer: 0
  };

  /* ---------------------------------------------------------- guilloche --
     Hypotrochoid rosette n,R,d -> SVG path (fill none, stroke currentColor,
     non-scaling-stroke), the Draft E signature engraving. */
  function ros(n, R, d) {
    var r = R / (n + 1), g = R - r, pts = [], i, tt, x, y;
    for (i = 0; i <= 1400; i++) {
      tt = i / 1400 * Math.PI * 2;
      x = g * Math.cos(tt) + d * Math.cos(n * tt);
      y = g * Math.sin(tt) - d * Math.sin(n * tt);
      pts.push(x.toFixed(2) + "," + y.toFixed(2));
    }
    return "M" + pts.join("L");
  }
  function addPath(parent, d, sw) {
    if (!parent) return null;
    var p = document.createElementNS(SVGNS, "path");
    p.setAttribute("d", d);
    p.setAttribute("fill", "none");
    p.setAttribute("stroke", "currentColor");
    p.setAttribute("stroke-width", String(sw || 0.7));
    p.setAttribute("vector-effect", "non-scaling-stroke");
    p.setAttribute("class", "auth-guil");
    parent.appendChild(p);
    return p;
  }
  function addCircle(parent, r, sw, stroke, attrs) {
    if (!parent) return null;
    var c = document.createElementNS(SVGNS, "circle");
    c.setAttribute("r", String(r));
    c.setAttribute("fill", "none");
    c.setAttribute("stroke", stroke);
    c.setAttribute("stroke-width", String(sw));
    var k;
    for (k in attrs) {
      if (Object.prototype.hasOwnProperty.call(attrs, k)) c.setAttribute(k, attrs[k]);
    }
    parent.appendChild(c);
    return c;
  }

  /* medallion rosettes: one lace pattern per posture (Draft E params) */
  if (meds) {
    var svgs = meds.querySelectorAll("svg");
    for (var mi = 0; mi < svgs.length; mi++) {
      (function (s, k) {
        addPath(s, ros(15 - k, 88, 26 - k * 3), 0.8);
        addPath(s, ros(9 + k, 56, 18), 0.7);
        addCircle(s, 22, 1, "currentColor", { class: "auth-med-core" });
      })(svgs[mi], mi);
    }
  }

  /* seal art: engraved rosettes + base ring + coral hold-progress ring */
  var prog = null;
  if (svg) {
    addPath(svg, ros(17, 100, 30), 0.7);
    addPath(svg, ros(11, 66, 20), 0.7);
    addCircle(svg, RING_R, 3, "currentColor", { opacity: ".18" });
    prog = addCircle(svg, RING_R, 3, "#e98667", {
      class: "auth-prog", opacity: "1",
      "stroke-linecap": "round", transform: "rotate(-90)"
    });
    if (prog) {
      prog.style.strokeDasharray = String(RING_LEN);
      prog.style.strokeDashoffset = String(RING_LEN);
    }
  }

  /* ------------------------------------------------------------- toast --- */
  function showToast(key) {
    if (!toastEl || !key) return;
    toastEl.textContent = L(key, "");
    toastEl.classList.add("show");
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(function () {
      toastEl.classList.remove("show");
    }, 2600);
  }

  /* ------------------------------------------------------------- state --- */
  function render() {
    set(label, state.labelKey, "Hold to seal");
    set(sub, state.subKey, "1.2s - read-only preview");
    set(hint, state.hintKey, "Press and hold - release early to cancel");
  }

  function frame(ts) {
    if (!state.holding) return;
    if (!state.startT) state.startT = ts;
    var p = Math.min(1, (ts - state.startT) / HOLD_MS);
    state.p = p;
    if (prog) prog.style.strokeDashoffset = String(RING_LEN * (1 - p));
    if (p < 1) state.raf = requestAnimationFrame(frame);
    else finish();
  }

  function begin(e) {
    if (state.sealed || state.holding) return;
    if (e && e.button !== undefined && e.button !== 0) return;  // primary only
    if (e && e.preventDefault) e.preventDefault();
    state.holding = true;
    state.startT = 0;
    state.p = 0;
    state.labelKey = "auth.sealingLabel";
    state.hintKey = "auth.hintHolding";
    btn.classList.add("holding");
    render();
    cancelAnimationFrame(state.raf);
    state.raf = requestAnimationFrame(frame);
  }

  function cancel() {
    if (!state.holding) return;
    state.holding = false;
    cancelAnimationFrame(state.raf);
    btn.classList.remove("holding");
    var moved = state.p > 0;
    state.p = 0;
    if (prog) prog.style.strokeDashoffset = String(RING_LEN);
    state.startT = 0;
    state.labelKey = "auth.holdLabel";
    state.hintKey = moved ? "auth.hintCancelled" : "auth.hintIdle";
    render();
    if (moved) showToast("auth.toastCancelled");
  }

  function finish() {
    state.holding = false;
    state.sealed = true;
    state.p = 1;
    cancelAnimationFrame(state.raf);
    btn.classList.remove("holding");
    btn.classList.add("sealed");
    if (prog) prog.style.strokeDashoffset = "0";
    state.labelKey = "auth.sealedLabel";
    state.subKey = "auth.sealedSub";
    state.hintKey = "auth.hintSealed";
    if (result) result.classList.add("show");
    render();
    showToast("auth.toastSealed");
  }

  btn.addEventListener("pointerdown", begin);
  btn.addEventListener("pointerup", cancel);
  btn.addEventListener("pointerleave", cancel);
  btn.addEventListener("pointercancel", cancel);
  window.addEventListener("pointerup", cancel);   // release outside = cancel
  window.addEventListener("pointercancel", cancel);
  btn.addEventListener("keydown", function (e) {
    if (e.key !== " " && e.key !== "Enter" && e.key !== "Spacebar") return;
    if (e.repeat) return;
    e.preventDefault();
    begin(e);
  });
  btn.addEventListener("keyup", function (e) {
    if (e.key !== " " && e.key !== "Enter" && e.key !== "Spacebar") return;
    e.preventDefault();
    cancel();
  });

  /* ------------------------------------------------- medallion readout --- */
  function setReadout(key) {
    if (MEDS.indexOf(key) < 0) key = "situation";
    var lk = "auth.med." + key + ".name";
    var bk = "auth.med." + key + ".body";
    if (roLbl) {
      roLbl.setAttribute("data-i18n", lk);
      set(roLbl, lk, "");
    }
    if (roBody) {
      roBody.setAttribute("data-i18n", bk);
      set(roBody, bk, "");
    }
  }

  if (meds) {
    meds.addEventListener("click", function (e) {
      var target = e && e.target ? e.target : null;
      var m = target && target.closest ? target.closest(".med") : null;
      if (!m || !meds.contains(m)) return;
      var all = meds.querySelectorAll(".med");
      for (var i = 0; i < all.length; i++) all[i].classList.remove("on");
      m.classList.add("on");
      setReadout(m.getAttribute("data-med") || "situation");
    });
  }

  /* -------------------------------------------------- language follow -----
     loadLang() re-translates static data-i18n nodes; this re-renders the
     state-dependent labels (hint / seal caption) afterwards, so a language
     swap mid-preview never leaves stale English strings behind. */
  function applyLang() {
    if (!card) return;
    render();
    var active = meds ? meds.querySelector(".med.on") : null;
    setReadout(active ? (active.getAttribute("data-med") || "situation")
                      : "situation");
  }

  try {
    var origLoadLang = window.loadLang;
    if (typeof origLoadLang === "function" && !window.__authLangHooked) {
      window.__authLangHooked = true;
      window.loadLang = function () {
        var out = origLoadLang.apply(this, arguments);
        if (out && typeof out.then === "function") {
          return out.then(function (v) {
            try { applyLang(); } catch (e) { /* never break i18n */ }
            return v;
          });
        }
        try { applyLang(); } catch (e) { /* never break i18n */ }
        return out;
      };
    }
  } catch (e) { /* hooking is best-effort */ }

  /* first paint: the initial loadLang() call ran before this block existed,
     so wait for the pack to land, then render once (bounded, no polling leak) */
  var waited = 0;
  var iv = setInterval(function () {
    var ready = false;
    try { ready = !!(typeof dict !== "undefined" && dict && dict["auth.title"]); }
    catch (e) { ready = false; }
    if (ready || waited >= 8000) {
      clearInterval(iv);
      if (ready) applyLang();
    }
    waited += 120;
  }, 120);
}());
