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
let vsOn = false, vsStream = null, vsRec = null, vsMime = "";
let vsPhase = "wake", vsCmdText = "", vsPending = false;
let vsCycleTimer = null, vsCmdTimer = null, vsReplyIv = null;
let imgBusy = false;

async function refreshCaps() {
  try { capsData = await fetch("/api/capabilities").then(r => r.json()); }
  catch (e) { capsData = null; }
  imgCapHint();
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
        "border-radius:6px;padding:2px 8px;font-size:11px;cursor:pointer;" +
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
    chatProgEl.style.cssText = "font-size:11px;color:var(--muted);" +
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

/* ---------------------------------------------------------------- views -- */
/* All six views live in the same document; the inactive ones are hidden with
   CSS, so every poller keeps writing into real DOM and ids stay stable.
   ?view=<name> deep-links the active view (default: overview). */
const VIEWS = ["overview", "chat", "skills", "evolution", "stream", "settings"];
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
const micBtnEl = document.getElementById("micBtn");
if (micBtnEl) micBtnEl.addEventListener("click", () => { vsToggle(); });
const imgGoEl = document.getElementById("imgGo");
if (imgGoEl) imgGoEl.addEventListener("click", imgGenerate);
const imgPromptEl = document.getElementById("imgPrompt");
if (imgPromptEl) imgPromptEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); imgGenerate(); }
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
loadSettings().then(() => { scheduleRefresh(); renderVoice(); });
refreshStatus(); refreshUsage(); refreshEvents(); refreshVersion(); refreshSkills();
refreshEvolution();
loadChatHistory();   // console session restored at boot (read-only route)
refreshVoiceCap();   // engine status visible before the first orb tap (#4)
refreshCaps();       // media face: stt/image presence for the chat cards
setInterval(tickClock, 1000); tickClock();
setInterval(refreshUsage, 60000);
setInterval(refreshEvolution, 60000);
setInterval(refreshEvents, 10000);
setInterval(refreshVersion, 300000);
