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
  renderStatus(); renderUsage(); renderSelf(selfData); renderVersion(); renderVoice();
  mediaLangRefresh();          // voice session + image badge texts follow too
  settingsRelang();            // rows built by JS keep their pack strings too
  renderExchange();            // venue rows / probe labels follow the language too
  renderCommunity();           // delta4: reason note / filter / rows re-translate
  chatBadge(chatBusy);            // badge label follows the active language
  renderCtx(lastCtx);             // X14: context line label follows too
  refreshEventTexts();
  // Round C (owner report 2026-10-31): every pane whose chrome is baked with
  // t() at PAINT time has no data-i18n node for the sweep above to rewrite,
  // so a zh -> en switch used to leave it behind: tail chip + guardrail
  // numbers, decision row, profiles, checkpoint note, skills tags,
  // evolution badge/status/log and the bridge status line. Same repaints the
  // boot sequence performs; the async ones are loopback GETs.
  renderControl();             // tail chip + guardrail stats (car 7)
  paintDecisionStatus();       // decision status row (car 4)
  renderProfiles();            // profile rows (X11)
  refreshCheckpoints();        // checkpoint note (X10)
  refreshSkills();             // armory tags + descriptions (repaints ORCH too)
  refreshEvolution();          // evolution badge / status / log
  loadBridgeStatus();          // bridge status line (car 6)
  renderOvState();             // round C (P4): status sentence follows the pack
  renderOvTech();              // round C (P4): technical rows follow the pack
  paintOvTechBtn();            // round C (P4): disclosure label follows the pack
  paintSaveBar();              // round C (P6): bar state follows the pack
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
  applyExNav();                // 0.5.56: auto mode follows the fresh catalog
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
    if (artData !== null) refreshArtifacts();   // X17: an open gallery follows
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

  // Phase B: the quote readout rides the same render pass (a language switch
  // re-translates the idle/working line and keeps the last honest result)
  renderExtQuotes();

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

/* -------------------------------------- Extensions tabs (Phase B) -------
   The tab STRIP is a DOM registry: every .ext-tab[data-tab] button inside
   #view-exchange owns exactly one [data-tabpanel="<name>"] panel. Adding a
   category later = one button + one panel in index.html and nothing here.
   Finance is the default and carries every exchange card (thresholds,
   plans, venues, tools, probe + the new quote and external-link cards);
   Home/Vehicle are thin launchers onto the SENSE board — they call the very
   same setView() the sidebar uses and never duplicate SNIFF collection. */
function setExtTab(name) {
  const tabs = Array.prototype.slice.call(
    document.querySelectorAll("#view-exchange .ext-tab"));
  if (!tabs.length) return;
  if (!tabs.some(b => b.dataset.tab === name)) name = tabs[0].dataset.tab;
  tabs.forEach(b => {
    const on = b.dataset.tab === name;
    b.classList.toggle("is-active", on);
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;          // roving tabindex (WAI-ARIA tabs)
  });
  document.querySelectorAll("#view-exchange [data-tabpanel]").forEach(p => {
    p.hidden = p.dataset.tabpanel !== name;
  });
}

function initExtTabs() {
  const tabs = Array.prototype.slice.call(
    document.querySelectorAll("#view-exchange .ext-tab"));
  tabs.forEach((b, i) => {
    b.addEventListener("click", () => setExtTab(b.dataset.tab));
    b.addEventListener("keydown", e => {
      const step = e.key === "ArrowRight" ? 1 : (e.key === "ArrowLeft" ? -1 : 0);
      if (!step) return;
      e.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      next.focus();
      setExtTab(next.dataset.tab);
    });
  });
}
initExtTabs();
setExtTab("finance");                  // default tab, whatever the markup says

/* --------------------------- keyless quotes (GET /api/quotes) ----------
   READ-ONLY public market data behind one route: market=cn (Sina wire,
   GBK + its mandatory Referer) or market=us (Yahoo chart JSON), symbol as
   typed. The SERVER is the only source of numbers — a failure renders its
   honest error verbatim (never a guess, never a stale figure), exactly like
   the exchange probe above. The endpoint carries its own 10/60s budget and
   an offline seam (AIDOG_QUOTES_URL) for the tests. */
let extQuoteMsg = null;     // {k, f, err} — re-translated on every render
let extQuoteData = null;    // last 200 payload (rendered verbatim)
let extQuoteErr = null;     // honest server error string, or null
let extQuoteBusy = false;

function extQuoteDefault(market) { return market === "us" ? "AAPL" : "sh600519"; }

function extQuoteNum(v) {
  return typeof v === "number" && isFinite(v) ? Math.round(v * 10000) / 10000 : null;
}

function extQuoteLine(d) {
  const price = extQuoteNum(d.price);
  const chg = extQuoteNum(d.change);
  const pct = extQuoteNum(d.pct);
  let line = String(d.name || d.symbol || "") + " (" + String(d.symbol || "") + ")";
  if (price !== null) line += "  " + price + (d.currency ? " " + d.currency : "");
  if (chg !== null && pct !== null) {
    line += "  " + (chg >= 0 ? "+" : "") + chg + " (" +
      (pct >= 0 ? "+" : "") + pct.toFixed(2) + "%)";
  }
  if (d.ts) line += "  · " + new Date(Number(d.ts) * 1000).toLocaleString();
  return line;
}

function renderExtQuotes() {
  const msg = document.getElementById("extQuoteMsg");
  if (msg) {
    msg.textContent = extQuoteMsg ? t(extQuoteMsg.k, extQuoteMsg.f) : "";
    msg.classList.toggle("err", !!(extQuoteMsg && extQuoteMsg.err));
  }
  const out = document.getElementById("extQuoteResult");
  if (out) {
    out.textContent = extQuoteErr ? extQuoteErr
      : (extQuoteData ? extQuoteLine(extQuoteData)
        : t("ext.quotes.idle", "pick a market, type a symbol, then press FETCH"));
    out.classList.toggle("is-err", !!extQuoteErr);
  }
  const btn = document.getElementById("extQuoteBtn");
  if (btn) btn.disabled = extQuoteBusy;
}

async function extQuoteFetch() {
  if (extQuoteBusy) return;
  const sel = document.getElementById("extQuoteMarket");
  const market = sel ? String(sel.value || "cn") : "cn";
  const inp = document.getElementById("extQuoteSymbol");
  const symbol = inp ? String(inp.value || "").trim() : "";
  extQuoteBusy = true;
  extQuoteErr = null;
  extQuoteMsg = { k: "ext.quotes.working", f: "fetching…" };
  renderExtQuotes();
  try {
    const res = await fetch("/api/quotes?market=" + encodeURIComponent(market) +
      "&symbol=" + encodeURIComponent(symbol));
    const body = await res.json().catch(() => null);
    if (res.ok && body && body.ok === true) {
      extQuoteData = body;
      extQuoteMsg = { k: "ext.quotes.ok", f: "quote ✓" };
    } else {
      extQuoteData = null;
      extQuoteErr = body && body.error ? String(body.error) : ("HTTP " + res.status);
      extQuoteMsg = { k: "ext.quotes.failed", f: "quote fetch failed", err: true };
    }
  } catch (e) {
    extQuoteData = null;
    extQuoteErr = String(e).slice(0, 200);
    extQuoteMsg = { k: "ext.quotes.failed", f: "quote fetch failed", err: true };
  } finally {
    extQuoteBusy = false;
    renderExtQuotes();
  }
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

  // Wave 3 (X3): the raw status.mycore block — the panels only summarize it.
  renderMycoreFacts();
  // Wave 4 (X6): failover health rows in Settings -> Model (shape-hides)
  renderModeltier();

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

/* Wave 3 (X3): status.mycore{service,watchdog} rendered as its own readout
   under the Services panels — service pid / last event, watchdog liveness /
   state-file age / per-target verdicts / last sanitized line.
   Labels use reserved keys whose fallback is the English metric name: the
   0.5.53 i18n completion lane added both pack entries (795 keys per pack),
   so t() resolves in each locale and still falls back honestly if a key is
   ever missing.
   Fail-soft by shape: no mycore object, or neither sub-object with a state,
   hides the whole block — never a guessed state. */
function renderMycoreFacts() {
  const box = document.getElementById("mycoreFacts");
  if (!box) return;
  const m = statusData && statusData.mycore;
  const svc = (m && typeof m.service === "object" && m.service) ? m.service : null;
  const wdf = (m && typeof m.watchdog === "object" && m.watchdog) ? m.watchdog : null;
  const cell = (key, label, value) =>
    `<div><div class="k">${escapeHtml(t(key, label))}</div>` +
    `<div class="v">${escapeHtml(value)}</div></div>`;
  const out = [];
  if (svc && svc.state) {
    const bits = [String(svc.state)];
    if (svc.pid) bits.push("pid " + svc.pid);
    if (svc.last_event) {
      bits.push("last " + String(svc.last_event) +
        (svc.last_event_at ? " · " + String(svc.last_event_at) : ""));
    }
    out.push(cell("myc.svc", "MYCORE SERVICE", bits.join(" · ")));
  }
  if (wdf && wdf.state) {
    const bits = [String(wdf.state), wdf.process ? "process on" : "process off"];
    if (typeof wdf.age_sec === "number") bits.push("age " + wdf.age_sec + "s");
    const tg = (wdf.targets && typeof wdf.targets === "object")
      ? Object.entries(wdf.targets) : [];
    if (tg.length) bits.push("targets " + tg.map(([k, v]) => k + ":" + v).join(", "));
    if (wdf.last) bits.push("last " + String(wdf.last).slice(0, 140));
    out.push(cell("myc.wd", "MYCORE WATCHDOG", bits.join(" · ").slice(0, 400)));
  }
  box.innerHTML = out.join("");
  box.hidden = out.length === 0;
}

/* ---------------------------------------- Wave 4 lane gamma: X5 + X6 -----
   Settings -> Model card. X5 = one connectivity probe of the EFFECTIVE
   failover chain; X6 = the modeltier health/cooling readout from /api/status.
   Red line: only the server's sanitized `name` and an error CLASS are ever
   rendered — no URL rewriting, no key material, no response bodies. */
async function upstreamTest() {
  const btn = document.getElementById("upTestBtn");
  const msg = document.getElementById("upTestMsg");
  const out = document.getElementById("upTestOut");
  if (!btn || !msg || !out) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken", "access token required — set it in Access");
    return;
  }
  btn.disabled = true;                       // loading state until answered
  msg.className = "save-state";
  msg.textContent = t("chan.testWorking", "sending…");
  out.hidden = false;
  out.textContent = "";
  try {
    const res = await fetch("/api/mycore/upstreams/test", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: "{}" });
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) {
      const details = (body && Array.isArray(body.details)) ? body.details : [];
      msg.className = "save-state err";
      msg.textContent = res.status + " · " +
        (details.length ? details[0]
          : ((body && body.error) || t("myc.testFail", "test failed")));
      return;
    }
    if (!Array.isArray(body)) {
      msg.className = "save-state err";
      msg.textContent = t("myc.testBadBody", "unexpected answer — honest failure");
      return;
    }
    msg.className = "save-state";
    msg.textContent = "";
    if (!body.length) {
      const note = document.createElement("div");
      note.className = "stat-note test-note";
      note.textContent = t("myc.noChain",
        "no active failover chain — add endpoints in Failover upstreams above");
      out.appendChild(note);
      return;
    }
    body.forEach(r => {
      const row = (r && typeof r === "object") ? r : {};
      const ok = row.ok === true;
      const name = String(row.name || "endpoint");
      const tail = ok
        ? (Number.isFinite(Number(row.latency_ms))
            ? Math.round(Number(row.latency_ms)) + "ms" : "—")
        : String(row.error_class || "failed");
      const el = document.createElement("div");
      el.className = "test-row " + (ok ? "ok" : "bad");
      el.textContent = (ok ? "✓ " : "✗ ") + name + " · " + tail;
      out.appendChild(el);
    });
  } catch (e) {
    // network failure stays an honest error line, never a guessed result
    msg.className = "save-state err";
    msg.textContent = t("myc.testNet", "request failed — ") +
      String((e && e.message) || e).slice(0, 80);
  } finally {
    btn.disabled = false;
  }
}

/* ------------------------------------------- D1: model discovery (round D) --
   Settings -> Model card. ONE read-only GET {base}/models through
   POST /api/models/discover (Bearer gate on the server): a directory read,
   so discovery spends ZERO tokens and never reaches a completion.
   Hard rules, mirroring the X5/X6 block above:
     - #discKey is read into one request body and nowhere else — never
       stored, never rendered, never copied into another node, and it is not
       a settings field (SAVE_BAR_SKIP), so it can never promise a save;
     - only the SERVER's reason word is rendered (an HTTP status or an
       exception class), never a URL, a body line or a key;
     - ADD only edits the existing #mycModels field and dispatches one
       `input` event: the dirty flag, the sticky bar and the existing
       saveSettings() stay the single writer — discovery adds NO write route.
   The chips under the field mirror it both ways: they render from its value
   and write back through the same input event, so the text field keeps being
   the source of truth the activation car's U6 leg fills. */
const DISCOVER_MAX = 32;              // mycore.models grammar: at most 32 ids
let discState = { models: [], total: 0, ready: false };
let discMsgState = null;              // {kind, a, b, reason, raw} — relang repaint

function modelIdList() {
  const el = document.getElementById("mycModels");
  if (!el) return [];
  return String(el.value || "").split(",").map(s => s.trim()).filter(Boolean);
}

/* chips = a mirror of #mycModels. Every write goes back through ONE input
   event on the field, so dirty tracking, the sticky save bar and
   mycoreExtraPayload() see it exactly like a typed edit. */
function renderModelChips() {
  const box = document.getElementById("mycModelChips");
  if (!box) return;
  const ids = modelIdList();
  box.innerHTML = "";
  box.hidden = ids.length === 0;
  ids.forEach(id => {
    const chip = document.createElement("span");
    chip.className = "chip";
    const label = document.createElement("span");
    label.textContent = id;
    const x = document.createElement("button");
    x.type = "button";
    x.className = "model-chip-x";
    x.textContent = "\u00d7";
    x.setAttribute("aria-label",
      t("myc.chipDel", "remove {v}").replace("{v}", id));
    x.addEventListener("click", () => {
      const el = document.getElementById("mycModels");
      if (!el) return;
      el.value = modelIdList().filter(m => m !== id).join(", ");
      el.dispatchEvent(new Event("input", { bubbles: true }));
    });
    chip.appendChild(label);
    chip.appendChild(x);
    box.appendChild(chip);
  });
}

function discSelected() {
  return Array.prototype.map.call(
    document.querySelectorAll("#discOut input.disc-chk:checked"),
    el => String(el.value || ""));
}

function discMsg(state) { discMsgState = state || null; discPaintMsg(); }

/* ONE painter for the status line, re-run on a language switch (same idea as
   repaintSaveState): a server detail stays verbatim, our own sentences come
   from the current pack every time. */
function discPaintMsg() {
  const el = document.getElementById("discMsg");
  if (!el) return;
  const s = discMsgState;
  if (!s) { el.className = "save-state"; el.textContent = ""; return; }
  const own = s.kind === "run" || s.kind === "none" || s.kind === "found" ||
              s.kind === "full" || s.kind === "fail";
  el.className = "save-state" + (own ? "" : " err");
  if (s.kind === "run") {
    el.textContent = t("myc.discoverRun", "reading the model directory…");
  } else if (s.kind === "none") {
    el.textContent = t("myc.discoverNone",
      "the endpoint answered, but listed no models");
  } else if (s.kind === "found") {
    el.textContent = t("myc.discoverFound",
      "{a} of {b} models listed — tick the ones to add")
      .replace("{a}", String(s.a)).replace("{b}", String(s.b));
  } else if (s.kind === "full") {
    el.textContent = t("myc.discoverFull",
      "only {n} added — the whitelist holds at most 32 entries")
      .replace("{n}", String(s.a));
  } else if (s.kind === "fail") {
    el.textContent = t("myc.discoverFail", "discovery failed — {reason}")
      .replace("{reason}", String(s.reason || "?"));
  } else if (s.kind === "net") {
    el.textContent = t("myc.testNet", "request failed — ") + String(s.raw || "");
  } else {
    el.textContent = String(s.raw || "");   // a server detail: verbatim only
  }
}

function discPaintAdd() {
  const btn = document.getElementById("discAddBtn");
  if (!btn) return;
  const n = discState.ready ? discSelected().length : 0;
  btn.textContent = t("myc.discoverAdd", "ADD {n} SELECTED")
    .replace("{n}", String(n));
}

function renderDiscResults() {
  const out = document.getElementById("discOut");
  const add = document.getElementById("discAddBtn");
  if (!out) return;
  out.innerHTML = "";
  out.hidden = !discState.ready || discState.models.length === 0;
  if (add) add.hidden = out.hidden;
  if (out.hidden) { discPaintAdd(); return; }
  const all = document.createElement("label");
  all.className = "disc-all";
  const allBox = document.createElement("input");
  allBox.type = "checkbox";
  allBox.id = "discAll";
  allBox.addEventListener("change", () => {
    out.querySelectorAll("input.disc-chk").forEach(x => {
      x.checked = allBox.checked;
    });
    discPaintAdd();
  });
  const allText = document.createElement("span");
  allText.setAttribute("data-i18n", "myc.discoverAll");
  allText.textContent = t("myc.discoverAll", "select all");
  all.appendChild(allBox);
  all.appendChild(allText);
  out.appendChild(all);
  discState.models.forEach(id => {
    const row = document.createElement("label");
    row.className = "disc-row";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.className = "disc-chk";
    box.value = id;
    box.addEventListener("change", discPaintAdd);
    const text = document.createElement("span");
    text.textContent = id;
    row.appendChild(box);
    row.appendChild(text);
    out.appendChild(row);
  });
  discPaintAdd();
}

/* ADD writes only into #mycModels — and only up to the 32 entries the server
   grammar accepts. Anything that does not fit says so; nothing is dropped
   silently. */
function discAdd() {
  const el = document.getElementById("mycModels");
  if (!el) return;
  const picked = discSelected();
  if (!picked.length) return;
  const have = modelIdList();
  const fresh = picked.filter(m => have.indexOf(m) < 0);
  const free = Math.max(0, DISCOVER_MAX - have.length);
  const add = fresh.slice(0, free);
  if (add.length) {
    el.value = have.concat(add).join(", ");
    el.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (fresh.length > add.length) discMsg({ kind: "full", a: add.length });
}

async function runDiscover() {
  const btn = document.getElementById("discBtn");
  const urlEl = document.getElementById("mycUrl");
  const keyEl = document.getElementById("discKey");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    discMsg({ kind: "raw",
      raw: t("cred.needToken", "access token required — set it in Access") });
    return;
  }
  if (btn) btn.disabled = true;
  discState = { models: [], total: 0, ready: false };
  renderDiscResults();
  discMsg({ kind: "run" });
  try {
    const res = await fetch("/api/models/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({
        base_url: ((urlEl && urlEl.value) || "").trim(),
        api_key: ((keyEl && keyEl.value) || "").trim()
      })
    });
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok) {
      const details = (body && Array.isArray(body.details)) ? body.details : [];
      discMsg({ kind: "raw", raw: res.status + " · " +
        (details.length ? details[0]
          : ((body && body.error) || t("myc.testFail", "test failed"))) });
      return;
    }
    if (!body || body.ok !== true) {
      discMsg({ kind: "fail", reason: String((body && body.reason) || "?") });
      return;
    }
    const models = (Array.isArray(body.models) ? body.models : [])
      .map(m => (m && typeof m === "object") ? String(m.id || "") : "")
      .filter(Boolean);
    const total = Number.isFinite(Number(body.total))
      ? Number(body.total) : models.length;
    discState = { models: models, total: total, ready: true };
    renderDiscResults();
    discMsg(models.length
      ? { kind: "found", a: models.length, b: total }
      : { kind: "none" });
  } catch (e) {
    discMsg({ kind: "net", raw: String((e && e.message) || e).slice(0, 80) });
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ---- v0.5.55 gateway car: structured endpoint manager ------------------
   Source of truth is GET /api/mycore/endpoints (the server resolves env >
   structured > one-line). The panel edits a LOCAL row list; nothing is
   written until the normal settings save runs, where the endpoints array
   joins the payload and the one-line field is sent empty (dual-track mutex —
   the server refuses both non-empty in one patch).
   Red lines kept: a key VALUE never enters the DOM (rows carry slot NAMES
   + presence booleans only; the pool editor writes through /api/secrets and
   clears its password input immediately); every fetch fails soft to an
   honest note, never to a fake row. */
let epData = null;        // last /api/mycore/endpoints payload
let epFailed = false;     // last load failed (the note must survive repaints)
let epRows = [];          // local editable rows (primary excluded)
let epPrim = null;        // primary row (read-only: edited in Kernel endpoint)
let epDirty = false;
// Car 4 (D6): the decision row edits independently of mycDirty/epDirty, so
// the settings payload ships `mycore.decision` only when the operator
// actually touched it (an untouched form never rewrites the document).
let decDirty = false;
let epPresence = {};      // slot -> bool (10-slot catalog)
let epKeysOpen = false;

const EP_SLOT_OPTIONS = ["MODEL_API_KEY", "OPENAI_API_TOKEN",
  "MODEL_UP_K1", "MODEL_UP_K2", "MODEL_UP_K3", "MODEL_UP_K4",
  "MODEL_UP_K5", "MODEL_UP_K6", "MODEL_UP_K7", "MODEL_UP_K8"];

async function loadEndpoints() {
  const list = document.getElementById("epList");
  if (!list) return;
  try {
    const doc = await fetch("/api/mycore/endpoints").then(r => r.json());
    if (!doc || typeof doc !== "object" || !Array.isArray(doc.endpoints))
      throw new Error("shape");
    epData = doc;
    epFailed = false;
    epPresence = (doc.key_presence && typeof doc.key_presence === "object")
      ? doc.key_presence : {};
    if (!epDirty) {                 // an in-progress edit is never clobbered
      epPrim = doc.endpoints.find(e => e && e.primary) || null;
      epRows = doc.endpoints.filter(e => e && !e.primary).map(e => ({
        url: String(e.url || ""),
        model: String(e.model || ""),
        keys: (Array.isArray(e.keys) ? e.keys : [])
          .map(k => String((k && k.slot) || "")).filter(Boolean),
      }));
    }
    renderEndpoints();
    paintDecisionStatus();
    if (epKeysOpen) renderKeyPool();
  } catch (e) {
    epData = null;
    epFailed = true;
    list.textContent = "";
    const note = document.createElement("div");
    note.className = "stat-note";
    note.textContent = t("myc.epUnavailable",
      "endpoint manager unavailable on this build");
    list.appendChild(note);
    const chip = document.getElementById("epSourceChip");
    if (chip) chip.hidden = true;
  }
}

function epSetMsg(text, isErr) {
  const msg = document.getElementById("epMsg");
  if (!msg) return;
  msg.className = isErr ? "save-state err" : "save-state";
  msg.textContent = text || "";
}

function renderEndpoints() {
  const list = document.getElementById("epList");
  const chip = document.getElementById("epSourceChip");
  if (!list) return;
  if (!epData) {                  // not loaded yet / load failed: no fake rows
    list.textContent = "";
    if (chip) chip.hidden = true;
    if (epFailed) {                // the honest failure note survives repaints
      const note = document.createElement("div");
      note.className = "stat-note";
      note.textContent = t("myc.epUnavailable",
        "endpoint manager unavailable on this build");
      list.appendChild(note);
    }
    return;
  }
  const src = epData.source || "none";
  if (chip) {
    const map = { env: ["envOverride", "ENV OVERRIDES", "env"],
                  invalid: ["invalid", "CONFIG INVALID", "invalid"],
                  endpoints: ["structured", "STRUCTURED", ""],
                  upstreams: ["oneLine", "ONE-LINE", ""] };
    const hit = map[src];
    chip.hidden = !hit;
    if (hit) {
      chip.textContent = t("myc.src." + hit[0], hit[1]);
      chip.className = "ep-src " + hit[2];
    }
  }
  if (src === "invalid" && epData && epData.note) {
    epSetMsg(String(epData.note).slice(0, 200), true);
  }
  list.textContent = "";
  const readonly = src === "env";
  const rowEl = (row, idx) => {
    const el = document.createElement("div");
    el.className = "ep-row";
    const line = document.createElement("div");
    line.className = "ep-line";
    const url = document.createElement("input");
    url.type = "text"; url.className = "ep-url";
    url.maxLength = 512; url.spellcheck = false;
    url.placeholder = "https://api.example/v1";
    url.value = row.url || "";
    url.disabled = readonly;
    url.addEventListener("input", () => { row.url = url.value; epDirty = true; epSetMsg(""); });
    const model = document.createElement("input");
    model.type = "text"; model.className = "ep-model";
    model.maxLength = 100; model.spellcheck = false;
    model.placeholder = t("myc.epModelPh", "model (empty = inherit)");
    model.value = row.model || "";
    model.disabled = readonly;
    model.addEventListener("input", () => { row.model = model.value; epDirty = true; epSetMsg(""); });
    const del = document.createElement("button");
    del.type = "button"; del.className = "btn ep-del";
    del.textContent = "✕";
    del.title = t("myc.epDel", "remove endpoint");
    del.disabled = readonly;
    del.addEventListener("click", () => {
      epRows.splice(idx, 1); epDirty = true; epSetMsg(""); renderEndpoints();
    });
    line.appendChild(url); line.appendChild(model); line.appendChild(del);
    el.appendChild(line);
    // keys: slot-name chips (presence dot from the catalog), click removes
    const keys = document.createElement("div");
    keys.className = "ep-keys";
    (row.keys || []).forEach((slot, ki) => {
      const c = document.createElement("button");
      c.type = "button";
      c.className = "ep-key" + (epPresence[slot] ? " set" : "");
      const dot = document.createElement("span");
      dot.className = "dot";
      c.appendChild(dot);
      c.appendChild(document.createTextNode(slot));
      c.title = t("myc.epKeyHint",
        "click to remove this key slot from the endpoint");
      if (!readonly) c.addEventListener("click", () => {
        row.keys.splice(ki, 1); epDirty = true; epSetMsg(""); renderEndpoints();
      });
      else c.disabled = true;
      keys.appendChild(c);
    });
    if (!readonly) {
      const add = document.createElement("select");
      add.className = "ep-key add-select";
      const opt0 = document.createElement("option");
      opt0.value = "";
      opt0.textContent = t("myc.epAddKey", "+ key slot…");
      add.appendChild(opt0);
      EP_SLOT_OPTIONS.filter(s => !(row.keys || []).includes(s)).forEach(s => {
        const o = document.createElement("option");
        o.value = s; o.textContent = s + (epPresence[s] ? " ●" : "");
        add.appendChild(o);
      });
      add.addEventListener("change", () => {
        if (!add.value) return;
        row.keys = (row.keys || []).concat([add.value]);
        epDirty = true; epSetMsg(""); renderEndpoints();
      });
      keys.appendChild(add);
    }
    const test = document.createElement("button");
    test.type = "button"; test.className = "btn ep-test";
    test.textContent = t("myc.epTest", "TEST");
    test.addEventListener("click", () => epTestRow(row, test));
    keys.appendChild(test);
    el.appendChild(keys);
    const res = document.createElement("div");
    res.className = "ep-result"; res.hidden = true;
    el.appendChild(res);
    return el;
  };
  if (epPrim) {
    const p = rowEl({ url: epPrim.url, model: epPrim.model,
                      keys: (epPrim.keys || []).map(k => k.slot).filter(Boolean) }, -1);
    const badge = document.createElement("span");
    badge.className = "ep-badge";
    badge.textContent = t("myc.tierPrimary", "PRIMARY");
    const line0 = p.querySelector(".ep-line");
    if (line0) line0.insertBefore(badge, line0.firstChild);
    // the primary row is display-only: its endpoint/model live in the
    // Kernel endpoint / Kernel model fields above; the test button stays.
    p.querySelectorAll("input").forEach(i => { i.disabled = true; });
    const del0 = p.querySelector(".ep-del");
    if (del0) del0.hidden = true;
    const sel0 = p.querySelector(".ep-key.add-select");
    if (sel0) sel0.remove();
    p.querySelectorAll(".ep-key").forEach(c => { c.disabled = true; });
    list.appendChild(p);
  }
  epRows.forEach((row, i) => list.appendChild(rowEl(row, i)));
  if (!epPrim && !epRows.length) {
    const note = document.createElement("div");
    note.className = "stat-note";
    note.textContent = t("myc.epEmpty",
      "primary endpoint only — add failover endpoints below");
    list.appendChild(note);
  }
}

async function epTestRow(row, btn) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const el = btn.closest(".ep-row");
  const res = el ? el.querySelector(".ep-result") : null;
  const show = (ok, text) => {
    if (!res) return;
    res.hidden = false;
    res.className = "ep-result " + (ok ? "ok" : "bad");
    res.textContent = (ok ? "✓ " : "✗ ") + text;
  };
  if (!token) return show(false, t("cred.needToken", "access token required — set it in Access"));
  const url = String(row.url || "").trim();
  if (!url) return show(false, t("myc.epNoUrl", "endpoint url required"));
  const slot = (row.keys || []).find(s => epPresence[s]) || "";
  btn.disabled = true;
  try {
    const r = await fetch("/api/mycore/upstreams/test", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(slot ? { url, slot } : { url }) });
    let body = null;
    try { body = await r.json(); } catch (e) { body = null; }
    if (!r.ok) {
      const det = (body && Array.isArray(body.details)) ? body.details : [];
      return show(false, r.status + " · " +
        (det[0] || (body && body.error) || t("myc.testFail", "test failed")));
    }
    const row0 = (Array.isArray(body) && body[0]) ? body[0] : {};
    if (row0.ok) {
      const ms = Number.isFinite(Number(row0.latency_ms))
        ? Math.round(Number(row0.latency_ms)) + "ms" : "—";
      return show(true, String(row0.name || url) + " · " + ms +
        (slot ? " · " + slot : ""));
    }
    return show(false, String(row0.error_class || "failed") +
      (slot ? " · " + slot : ""));
  } catch (e) {
    return show(false, t("myc.testNet", "request failed — ") +
      String((e && e.message) || e).slice(0, 60));
  } finally {
    btn.disabled = false;
  }
}

function renderKeyPool() {
  const pool = document.getElementById("epKeyPool");
  if (!pool) return;
  pool.textContent = "";
  if (!epKeysOpen) { pool.hidden = true; return; }
  pool.hidden = false;
  const hint = document.createElement("div");
  hint.className = "stat-note";
  hint.style.flexBasis = "100%";
  hint.textContent = t("myc.poolHint",
    "model key slots — presence only; a value is written once and never shown again");
  pool.appendChild(hint);
  EP_SLOT_OPTIONS.forEach(slot => {
    const wrap = document.createElement("span");
    wrap.className = "ep-key" + (epPresence[slot] ? " set" : "");
    wrap.style.cursor = "default";
    const dot = document.createElement("span");
    dot.className = "dot";
    wrap.appendChild(dot);
    wrap.appendChild(document.createTextNode(slot));
    const inp = document.createElement("input");
    inp.type = "password"; inp.autocomplete = "new-password";
    inp.placeholder = epPresence[slot] ? "•••• (set)" : "";
    inp.style.width = "110px"; inp.style.fontSize = "11px";
    inp.setAttribute("aria-label", slot + " value");
    const set = document.createElement("button");
    set.type = "button"; set.className = "btn";
    set.textContent = t("myc.poolSet", "SET");
    set.addEventListener("click", async () => {
      const token = localStorage.getItem(TOKEN_KEY) || "";
      if (!token) return epSetMsg(t("cred.needToken", "access token required — set it in Access"), true);
      const val = inp.value;
      inp.value = "";                       // value leaves the input at once
      try {
        const r = await fetch("/api/secrets", {
          method: "POST",
          headers: { "Content-Type": "application/json",
                     "Authorization": "Bearer " + token },
          body: JSON.stringify({ name: slot, value: val }) });
        const body = await r.json().catch(() => null);
        if (!r.ok) {
          const det = (body && Array.isArray(body.details)) ? body.details : [];
          return epSetMsg(slot + ": " + (det[0] || (body && body.error) || "error"), true);
        }
        epPresence[slot] = Boolean(val);
        epSetMsg(slot + (val ? " set ✓" : " cleared ✓"));
        renderKeyPool(); renderEndpoints();
      } catch (e) {
        epSetMsg(String(e).slice(0, 80), true);
      }
    });
    wrap.appendChild(inp); wrap.appendChild(set);
    pool.appendChild(wrap);
  });
}

function epToggleKeys() {
  epKeysOpen = !epKeysOpen;
  renderKeyPool();
  if (epKeysOpen) {
    const pool = document.getElementById("epKeyPool");
    const first = pool && pool.querySelector("input");
    if (first) first.focus();
  }
}

/* ---- self-metered usage block (Overview, /api/usage/self) -------------- */
let selfData = null;      // last payload — repainted on language switch
async function refreshSelf() {
  try {
    const doc = await fetch("/api/usage/self").then(r => r.json());
    selfData = (doc && typeof doc === "object") ? doc : null;
    renderSelf(selfData);
  } catch (e) { selfData = null; renderSelf(null); }
}

function renderSelf(doc) {
  const head = document.getElementById("selfHead");
  const grid = document.getElementById("selfStats");
  const note = document.getElementById("selfNote");
  if (!head || !grid || !note) return;
  if (!doc || doc.available !== true) {
    head.hidden = true; grid.hidden = true; note.hidden = true;
    return;                     // inactive meter = silent absence (honest)
  }
  head.hidden = false; grid.hidden = false; note.hidden = false;
  const d = (doc.display && typeof doc.display === "object") ? doc.display : null;
  const rate = d && Number(d.rate_cny_per_unit) > 0 ? Number(d.rate_cny_per_unit) : 0;
  const chip = (k, label, v) => `<div><div class="k">${escapeHtml(t(k, label))}</div>` +
    `<div class="v">${escapeHtml(v)}</div></div>`;
  const num = n => Number(n || 0).toLocaleString("en-US");
  let costTxt = "—";
  const pricing = (doc.pricing && typeof doc.pricing === "object") ? doc.pricing : {};
  if (pricing.configured && pricing.total_cny != null) {
    const cny = Number(pricing.total_cny);
    if (d && d.currency !== "CNY" && rate > 0) {
      const v = cny / rate;
      costTxt = (d.symbol || "") + (v >= 100 ? Math.round(v).toLocaleString()
        : v >= 1 ? v.toFixed(1) : v.toFixed(2));
    } else {
      costTxt = (d && d.currency === "CNY" ? "¥" : "") +
        (cny >= 100 ? Math.round(cny).toLocaleString()
          : cny >= 1 ? cny.toFixed(1) : cny.toFixed(2));
    }
  }
  grid.innerHTML =
    chip("usage.selfReq", "REQUESTS 7D", num(doc.requests)) +
    chip("usage.selfOk", "OK", num(doc.ok)) +
    chip("usage.selfFail", "FAIL", num(doc.fail)) +
    chip("usage.selfIn", "IN TOKENS", num(doc.in_tokens)) +
    chip("usage.selfOut", "OUT TOKENS", num(doc.out_tokens)) +
    chip("usage.selfCost", "COST 7D", costTxt);
  const bits = [];
  if (Array.isArray(doc.unpriced) && doc.unpriced.length &&
      pricing.total_cny == null) {
    bits.push(t("usage.selfNoPrice",
      "no price configured for {m} — cost stays unknown")
      .replace("{m}", doc.unpriced.slice(0, 4).join(", ")));
  }
  if (Number(doc.no_usage) > 0) {
    bits.push(t("usage.selfNoUsage",
      "{n} calls reported no token usage (provider silence — not counted as 0)"
      ).replace("{n}", String(doc.no_usage)));
  }
  if (doc.truncated) bits.push(t("usage.selfTrunc", "older rows truncated"));
  note.textContent = bits.join(" · ");
}

/* X6 rows from statusData.modeltier (Wave 3 beta contract): tri-state by
   design — true = observed & not cooling, false = cooling now (with a live
   countdown), null = never observed in this process. available:false or an
   absent block hides the whole box: an honest absence, never a guess. */
function renderModeltier() {
  const box = document.getElementById("modeltierBox");
  if (!box) return;
  const mt = statusData && statusData.modeltier;
  const rows = (mt && typeof mt === "object" && Array.isArray(mt.endpoints))
    ? mt.endpoints : [];
  if (!mt || typeof mt !== "object" || mt.available !== true || !rows.length) {
    box.hidden = true;
    box.textContent = "";
    return;
  }
  const frag = document.createDocumentFragment();
  const head = document.createElement("div");
  head.className = "mt-head";
  head.textContent = t("myc.tierHead", "failover health");
  frag.appendChild(head);
  rows.forEach(r => {
    const row = (r && typeof r === "object") ? r : {};
    const el = document.createElement("div");
    el.className = "mt-row";
    const name = document.createElement("span");
    name.className = "mt-name";
    name.textContent = String(row.name || "endpoint");
    el.appendChild(name);
    const state = document.createElement("span");
    const healthy = row.healthy;
    if (healthy === true) {
      state.className = "mt-state ok";
      state.textContent = t("myc.tierOk", "healthy ✓");
    } else if (healthy === false) {
      state.className = "mt-state bad";
      const rem = Number(row.cooling_remaining_s);
      const pattern = t("myc.tierCool", "cooling {s}s");
      if (Number.isFinite(rem) && rem > 0) {
        state.dataset.coolPattern = pattern;
        state.dataset.coolUntil = String(Date.now() + rem * 1000);
        state.textContent = pattern.replace("{s}", String(Math.ceil(rem)));
      } else {
        state.textContent = t("myc.tierCoolNow", "cooling");
      }
    } else {
      state.className = "mt-state unknown";
      state.textContent = t("myc.tierUnk", "not observed yet");
    }
    el.appendChild(state);
    if (row.primary === true) {
      const p = document.createElement("span");
      p.className = "mt-primary";
      p.textContent = t("myc.tierPrimary", "PRIMARY");
      el.appendChild(p);
    }
    frag.appendChild(el);
  });
  box.textContent = "";
  box.appendChild(frag);
  box.hidden = false;
}

function tickModeltier() {
  /* 1s countdown for cooling rows; the server stays the source of truth
     (each status repaint re-projects the remainder from its own clock). */
  const box = document.getElementById("modeltierBox");
  if (!box || box.hidden) return;
  box.querySelectorAll("[data-cool-until]").forEach(el => {
    const until = Number(el.dataset.coolUntil);
    if (!Number.isFinite(until)) return;
    const left = Math.max(0, Math.ceil((until - Date.now()) / 1000));
    el.textContent = String(el.dataset.coolPattern || "cooling {s}s")
      .replace("{s}", String(left));
  });
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
  const st = (k, v) => `<div><div class="k">${escapeHtml(t(k[0], k[1]))}</div><div class="v">${escapeHtml(v)}</div></div>`;
  /* Wave 3 (X4): the eight-key `pool` block of /api/usage (metrics source),
     seven of which the card never showed. Reserved label keys with English
     fallback (pack entries added by the 0.5.53 i18n lane — see
     renderMycoreFacts). Each
     chip hides itself when its value is null/absent; no `pool` object at all
     = no pool chips (honest shape-hide, never an invented 0). The ACTIVE
     KEYS chip travels here too, so the classic row keeps its fourth chip and
     the card never shows it twice. */
  const chip = (key, label, raw) => {
    if (raw === null || raw === undefined || raw === "") return "";
    if (typeof raw === "number" && !isFinite(raw)) return "";
    return st([key, label], typeof raw === "number"
      ? raw.toLocaleString("en-US") : String(raw));
  };
  const poolChips = () => {
    const p = usageData.pool;
    if (!p || typeof p !== "object") return "";
    return chip("usage.pool", "ACTIVE KEYS", p.keys_active) +
      chip("usage.queue", "QUEUE DEPTH", p.queue_depth) +
      chip("usage.accounts", "ACCOUNTS", p.accounts_total) +
      chip("usage.reqTotal", "REQUESTS TOTAL", p.requests_total) +
      chip("usage.inTok", "INPUT TOKENS", p.input_tokens) +
      chip("usage.outTok", "OUTPUT TOKENS", p.output_tokens) +
      chip("usage.healthMin", "HEALTH MIN", p.health_min) +
      chip("usage.healthAvg", "HEALTH AVG", p.health_avg);
  };
  if (!today) {
    document.getElementById("costVal").textContent = "—";
    setUnit();
    // no daily log today — the pool is an independent source, so its chips
    // still render (X4); the note keeps saying the *usage* source is down.
    document.getElementById("usageStats").innerHTML = poolChips();
    document.getElementById("usageNote").textContent = t("usage.off", "usage source unavailable");
    return;
  }
  document.getElementById("costVal").textContent = fmtCost(today.cost);
  setUnit();
  document.getElementById("usageStats").innerHTML =
    st(["usage.req", "REQUESTS"], String(today.requests)) +
    st(["usage.err", "ERRORS"], `${today.errors} (${today.requests ? Math.round(today.errors / today.requests * 100) : 0}%)`) +
    st(["usage.avg", "AVG LATENCY"], (today.avg_ms / 1000).toFixed(1) + "s") +
    poolChips();
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
   the dictionary loads — keys would otherwise stick as literal text).
   Round C: the level and source chips are baked with t() at build time too,
   so they must follow the message text — without this a zh -> en switch
   leaves those two chips sitting in the old language. */
function refreshEventTexts() {
  for (const [, node] of evNodes) {
    if (!node.__ev || node.classList.contains("typing")) continue;
    const e = node.__ev;
    const lv = node.querySelector(".lv");
    if (lv) lv.textContent = t("adv." + (e.level || "info"),
                              (e.level || "info").toUpperCase());
    const src = node.querySelector(".src");
    if (src) src.textContent = t("panel." + e.src, e.src);
    const msg = node.querySelector(".msg");
    if (msg) msg.textContent = eventText(e);
  }
}

/* ---------------------------- round C (P4): one status sentence --------- */
/* Painted ONLY from /api/status + /api/control + /api/setup: the sentence may
   never read "all good" while any of those says otherwise (approved copy
   draft, red line 1). {what} is filled from the EXISTING plain act.item.*
   keys — no new name keys — and unknown data lands on ov.state.checking. */
function ovName(key) {
  const M = {
    service: "act.item.service", model_endpoint: "act.item.model_endpoint",
    web_token: "act.item.web_token", model_key: "act.item.model_key",
    tg_token: "act.item.tg_token", tg_allowlist: "act.item.tg_allowlist",
    watchdog: "myc.wd",
  };
  return M[key] ? t(M[key], key) : null;
}
function ovList(keys) {
  return keys.map(ovName).filter(Boolean).join(" · ");
}
function renderOvState() {
  const el = document.getElementById("ovState");
  if (!el) return;
  // English defaults mirror the pack verbatim: a failed pack fetch must read
  // as English prose, never as a raw key (the fallback-quality class of bug
  // this round already fixed twice).
  const EN = {
    "ov.state.checking": "Checking the console…",
    "ov.state.down": "Not running: {what} — the details are right below.",
    "ov.state.paused": "You paused it — new turns are held until you resume. Everything else is running.",
    "ov.state.attention": "One thing wants a look: {what}. Everything else is running.",
    "ov.state.ok": "All good — console, service and watchdog are up, and nothing needs you.",
  };
  const say = (key, what) => {
    el.dataset.state = key.slice("ov.state.".length);
    const raw = t(key, EN[key] || key);
    el.textContent = what ? raw.replace("{what}", what) : raw;
  };
  const st = statusData, ctl = controlData, su = setupData;
  if (!st) { say("ov.state.checking"); return; }
  const mc = st.mycore || {};
  const svc = mc.service && mc.service.state;
  const wd = mc.watchdog || {};
  const suIssues = (su && Array.isArray(su.items))
    ? su.items.filter(it => it && it.required && !it.ok) : [];
  const down = [];
  if (svc && svc !== "running") down.push("service");
  if (wd.state && wd.state !== "up") down.push("watchdog");
  suIssues.filter(it => it.fmt === false).forEach(it => down.push(it.id));
  if (down.length) { say("ov.state.down", ovList([...new Set(down)])); return; }
  if (ctl && ctl.paused) { say("ov.state.paused"); return; }
  const attention = [];
  suIssues.forEach(it => attention.push(it.id));
  if (su && su.model && su.model.reachable === false) attention.push("model_endpoint");
  if (wd.escalation && Number(wd.escalation.level || 0) > 0) attention.push("watchdog");
  if (attention.length) { say("ov.state.attention", ovList([...new Set(attention)])); return; }
  say("ov.state.ok");
}
/* The technical rows the sentence summarises: everything cross-cutting that
   used to shout from three widgets, painted ONCE behind the disclosure. All
   four labels already exist in the packs; every value is server data. */
function renderOvTech() {
  const box = document.getElementById("ovTechGrid");
  if (!box) return;
  const rows = [];
  const st = statusData, su = setupData;
  const esc = s => escapeHtml(String(s == null ? "" : s));
  const cell = (k, v) => rows.push(`<div><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div></div>`);
  if (st) {
    const mc = st.mycore || {}, svc = mc.service || {}, wd = mc.watchdog || {};
    cell(t("sec.services", "Services"),
      svc.state === "running" ? t("act.st.running", "running ✓")
        : svc.state ? t("act.st.stopped", "stopped")
        : t("state.unknown", "UNKNOWN"));
    const wv = wd.state === "up" ? t("state.up", "ONLINE")
      : wd.state ? t("state.down", "FAULT") : t("state.unknown", "UNKNOWN");
    cell(t("myc.wd", "MYCORE WATCHDOG"),
      wv + (wd.age_sec != null ? " · " + Math.round(wd.age_sec) + "s" : ""));
    if (st.profile) cell(t("prof.active", "active profile"), String(st.profile));
  }
  if (su && su.model && su.model.endpoint) {
    cell(t("act.item.model_endpoint", "Model endpoint"),
      String(su.model.endpoint) + " · " +
      (su.model.reachable ? t("act.st.reachable", "reachable ✓")
                          : t("act.st.unreachable", "unreachable ✗")));
  }
  box.innerHTML = rows.join("");
}
function paintOvTechBtn() {
  const b = document.getElementById("ovTechBtn");
  const tech = document.getElementById("ovTech");
  if (!b || !tech) return;
  const open = !tech.hidden;
  b.setAttribute("aria-expanded", String(open));
  // keep the node translatable on the NEXT language sweep too
  b.dataset.i18n = open ? "ov.techHide" : "ov.techShow";
  b.textContent = t(b.dataset.i18n,
    open ? "hide technical details" : "TECHNICAL DETAILS");
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
    "margin:6px 0 0;padding:0;font-size:12.5px;";
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
        "border-radius:6px;padding:2px 8px;font-size:12.5px;cursor:pointer;" +
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
    chatProgEl.style.cssText = "font-size:12.5px;color:var(--muted);" +
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
    renderCtx(h.context);         // X14: context line rides the same fetch
    const msgs = (h.messages || []).slice(-40);
    if (!msgs.length) return;
    document.getElementById("chatEmpty")?.remove();
    for (const m of msgs) chatBubble(m.role, m.content);
  } catch (e) { /* empty state stays */ }
}

/* ------------------------------------ Wave 4 X7 + X14: chat card wiring ----
   X7 picker: options = settings.mycore.models — the SAME document the
   server gate and the TG /model pipeline read. Empty list = one honest
   "default" row; value "" means the turn carries no model override.
   X14 context line: renders GET /api/chat/history.context — a null counter
   is NEVER drawn as 0, and available:false says so next to the budget. */
function chatModelOptions() {
  const sel = document.getElementById("chatModel");
  if (!sel) return;
  const prev = sel.value;
  sel.textContent = "";
  const def = document.createElement("option");
  def.value = "";
  def.setAttribute("data-i18n", "chat.modelDefault");
  def.textContent = t("chat.modelDefault", "default model");
  sel.appendChild(def);
  const mc = (settings && typeof settings === "object" &&
              settings.mycore && typeof settings.mycore === "object")
    ? settings.mycore : {};
  const models = Array.isArray(mc.models) ? mc.models : [];
  models.forEach(m => {
    if (typeof m !== "string" || !m) return;
    const o = document.createElement("option");
    o.value = m;
    o.textContent = m;
    sel.appendChild(o);
  });
  sel.value = Array.from(sel.options).some(o => o.value === prev) ? prev : "";
}

let lastCtx = null;   // latest history.context payload (repaints on relang)

function fmtChars(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "?";
  if (v < 1000) return String(Math.round(v));
  return (Math.round((v / 1000) * 10) / 10).toFixed(1)
    .replace(/\.0$/, "") + "k";
}

function renderCtx(ctx) {
  const el = document.getElementById("ctxMeta");
  if (!el) return;
  lastCtx = (ctx && typeof ctx === "object") ? ctx : null;
  if (!lastCtx) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  const lbl = t("chat.ctxLbl", "context");
  el.classList.remove("ctx-over");
  const bud = typeof lastCtx.budget_chars === "number"
    ? fmtChars(lastCtx.budget_chars) : null;
  if (lastCtx.available !== true || typeof lastCtx.used_chars !== "number") {
    // honest degradation: budget stays, usage says it is unavailable now
    el.textContent = lbl + (bud ? " " + bud + " ·" : "") + " " +
      t("chat.ctxUnavail", "usage not available right now");
    el.hidden = false;
    return;
  }
  let text = lbl + " " + fmtChars(lastCtx.used_chars) + " / " +
    (bud || "?");
  if (lastCtx.over_budget === true) {
    el.classList.add("ctx-over");
    text += " · " + t("chat.ctxOver", "over budget");
  }
  el.textContent = text;
  el.hidden = false;
}

async function refreshCtx() {
  try {
    const h = await fetch("/api/chat/history").then(r => r.json());
    renderCtx(h && h.context);
  } catch (e) { /* keep the last honest reading */ }
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
  if (pendingImage && chatBusy) {
    // X16: an attachment is a one-shot per turn — refuse instead of letting
    // the queue silently pair it with a different message.
    chatNote("err", t("chat.imgBusy",
      "finish the current turn before sending an image"));
    return;
  }
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
  // X16: the picked image (if any) rides THIS turn only — consume it here so
  // no later queue item can ever resend it, and mark it honestly in the
  // transcript bubble.
  const img = pendingImage;
  pendingImage = null;
  const imgPick = document.getElementById("chatImg");
  if (imgPick) imgPick.value = "";
  const imgNoteEl = document.getElementById("chatImgNote");
  if (imgNoteEl) imgNoteEl.hidden = true;
  chatBubble("user", text + (img ? ` [image: ${img.name}]` : ""));
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
    // X7: the picker's value rides THIS turn; "" (default) carries nothing
    const model = ((document.getElementById("chatModel") || {}).value || "").trim();
    const payload = model ? { text, model } : { text };
    if (img) payload.image = img.dataUrl;      // X16: one data-URL attachment
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(payload) });
    if (!res.ok) {                       // 401/400/409/429 answered pre-stream
      const body = await res.json().catch(() => ({}));
      chatNote("err", res.status === 401
        ? t("chat.badToken", "401 · unauthorized")
        : gateDetail(body));
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
    refreshCtx();                  // X14: usage follows the finished turn
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
  chatModelOptions();   // X7 picker follows whatever resolved (default at minimum)
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
    // v0.5.55: self-metered price table — the textarea holds JSON; an
    // unparseable value is sent as-is so the SERVER's grammar refusal
    // ("usage.model_pricing: ...") reaches the save row verbatim instead of
    // being silently dropped or half-parsed here.
    const prEl = document.getElementById("usageModelPricing");
    if (prEl) {
      const raw = String(prEl.value || "").trim();
      if (!raw) {
        payload.usage.model_pricing = {};
      } else {
        try { payload.usage.model_pricing = JSON.parse(raw); }
        catch (e) { payload.usage.model_pricing = raw; }
      }
    }
  }
  const mycExtra = mycoreExtraPayload();
  if (mycExtra) Object.assign(payload.mycore, mycExtra);
  // keys the chat service reads ONCE at start: editing them needs a restart
  // (a models/budget edit is live and never raises mycRestartDirty)
  const needSvcRestart = !!(mycRestartDirty ||
    (toolsDirty && Object.prototype.hasOwnProperty.call(payload.mycore, "tools_off")));
  if (chanDirty) payload.channels = channelsPayload();
  // car 6: a half-edited bridge block joins the save like every other block
  if (bridgeDirty) payload.bridge = bridgePayload();
  // car 7 (D11): guardrails + obedience ride the same dirty-block contract.
  if (guardDirty) {
    payload.guardrails = {
      usd_day: parseFloat((document.getElementById("grUsdDay") || {}).value) || 0,
      token_day: parseInt((document.getElementById("grTokenDay") || {}).value, 10) || 0,
    };
  }
  if (obedDirty) {
    const lines = id => String((document.getElementById(id) || {}).value || "")
      .split("\n").map(s => s.trim()).filter(s => s.length > 0);
    payload.obedience = { rules: lines("obRules"), forbidden: lines("obForbidden") };
  }
  const hookPatch = hooksPayload();
  if (hookPatch) payload.hooks = hookPatch;
  const hookMsg = document.getElementById("hookMsg");
  const chanMsg = document.getElementById("chanMsg");
  const bridgeMsg = document.getElementById("bridgeMsg");
  if (hookMsg) { hookMsg.className = "save-state"; hookMsg.textContent = ""; }
  if (chanMsg) { chanMsg.className = "save-state"; chanMsg.textContent = ""; }
  if (bridgeMsg) { bridgeMsg.className = "save-state"; bridgeMsg.textContent = ""; }
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
      const bridgeErrs = details.filter(d => String(d).includes("bridge"));
      if (hookMsg && hookErrs.length) {
        hookMsg.className = "save-state err";
        hookMsg.textContent = hookErrs.join("; ");
      }
      if (chanMsg && chanErrs.length) {
        chanMsg.className = "save-state err";
        chanMsg.textContent = chanErrs.join("; ");
      }
      if (bridgeMsg && bridgeErrs.length) {
        bridgeMsg.className = "save-state err";
        bridgeMsg.textContent = bridgeErrs.join("; ");
      }
      const grErrs = details.filter(d => String(d).includes("guardrails"));
      const obErrs = details.filter(d => String(d).includes("obedience"));
      const grMsg = document.getElementById("grMsg");
      const obMsg = document.getElementById("obMsg");
      if (grMsg && grErrs.length) {
        grMsg.className = "save-state err";
        grMsg.textContent = grErrs.join("; ");
      }
      if (obMsg && obErrs.length) {
        obMsg.className = "save-state err";
        obMsg.textContent = obErrs.join("; ");
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
    bridgeDirty = false;
    guardDirty = false; obedDirty = false;
    // v0.5.55: the endpoint manager re-reads the server-normalized chain
    // (source/rows/presence may all have changed) and drops its edit flag.
    epDirty = false;
    loadEndpoints();
    if (hookPatch) { hookDirty.clear(); renderHooks(); }
    if (payload.channels) { renderChannels(); loadChanStatus(); }
    if (payload.bridge) { bridgePrefill(); loadBridgeStatus(); }
    if (toolsDirty) { toolsDirty = false; renderToolsOff(); }
    beep(880, 0.06);
    scheduleRefresh();
    refreshUsage();            // currency/fx may have changed (server cache cleared)
    refreshSelf();             // v0.5.55: pricing table may have changed
    refreshCaps();             // tools_off / shell / env flags may have changed
    refreshControl();          // car 7: guardrails/obedience blocks may have changed
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
/* Server event ids: data, never translated. Kept in lockstep with
   mycore/hooks.py::EVENTS (7 events — mission_tick landed in the server in
   car 3; the editor array only ever held six, so the 7th event was API-only
   until Wave 3 lane α / X1). */
const HOOK_EVENTS = ["turn_start", "turn_end", "tool_call", "compact",
                     "cron_fire", "error", "mission_tick"];
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
                     "connectors", "backup"];
const CRED_HINTS = {
  unavailable: ["cred.unavailable",
    "credential slots unavailable — this build has no GET /api/secrets"],
  readOnly: ["cred.readOnly",
    "read-only: no access token on this machine — set WEB_TOKEN in secrets.env"],
  needToken: ["cred.needToken",
    "access token required — set it in Access"],
};

let credsData = null;      // last GET /api/secrets payload (names/lengths only)
let credHintReason = "";   // "" | unavailable | readOnly | needToken
let chanStatusRows = [];   // GET /api/channels rows (name/enabled/configured/note)
let chanWebhookMeta = null;   // X8: top-level .webhook {url, secret_configured}
let chanDirty = false, usageDirty = false, mycDirty = false, toolsDirty = false;
// car 7 (D11): only the blocks the operator edited join the save payload.
let guardDirty = false, obedDirty = false;
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
    // car 5 (0.5.48): the Credentials card shows a PLAIN-LANGUAGE label; the
    // internal slot name moves to the hover title (and stays verbatim in the
    // server's 400 details, which are troubleshooting material). A slot the
    // packs do not know yet falls back to its name — honest, never invented.
    const slotKey = "cred.slot." + name;
    label.dataset.i18n = slotKey;
    label.textContent = dict[slotKey] !== undefined ? dict[slotKey] : name;
  }
  if (name) label.title = name;    // hover: the exact secrets.env slot name
  // 0.5.56 restyle: the row carries its own set/empty state for the CSS dot
  // (::before only — it never enters textContent, so every status-string
  // contract above is untouched).
  wrap.dataset.set = slot && slot.set ? "1" : "0";
  wrap.dataset.writable = writable ? "1" : "0";
  // stage 2 (0.5.56): edit-mode state machine. An EMPTY slot always opens in
  // the editing state (the box a Playwright fill() lands on must be visible);
  // a set slot opens in the read state and only EDIT reveals the controls.
  wrap.dataset.editing = !slot || !slot.set ? "1" : "0";
  row.appendChild(label);
  const st = document.createElement("div");
  st.className = "cred-status stat-note";
  st.textContent = credStatusText(slot);
  // 0.5.54: one VERIFY button per row (shape check everywhere, real remote
  // probe for the four slots that have one) + its own result line. The
  // result renders the server's reason word + expectation — never a value.
  const mk = (cls, key, fb, gated) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn " + cls;
    b.dataset.i18n = key;
    b.textContent = t(key, fb);
    // EDIT / CANCEL only move the row's own state — they never write, so they
    // stay clickable on a read-only console (gated === false).
    if (gated !== false && !writable) b.disabled = true;
    return b;
  };
  // stage 2: the row's `...` menu. VERIFY lives inside it, so hiding the menu
  // can never touch .cred-vstat (that line shares the .cred-verify class).
  const moreWrap = document.createElement("div");
  moreWrap.className = "cred-more-wrap";
  const moreBtn = document.createElement("button");
  moreBtn.type = "button";
  moreBtn.className = "btn cred-more";
  moreBtn.dataset.i18nT = "cred.more";
  moreBtn.title = t("cred.more", "more actions");
  moreBtn.setAttribute("aria-label", t("cred.more", "more actions"));
  moreBtn.setAttribute("aria-haspopup", "true");
  moreBtn.setAttribute("aria-expanded", "false");
  moreBtn.textContent = "\u22ef";                       // horizontal ellipsis
  const menu = document.createElement("div");
  menu.className = "cred-menu";
  menu.hidden = true;
  menu.appendChild(mk("cred-verify", "cred.verify", "VERIFY"));
  moreWrap.appendChild(moreBtn);
  moreWrap.appendChild(menu);
  const vst = document.createElement("div");
  vst.className = "cred-vstat cred-verify stat-note";
  vst.hidden = true;
  if (name === "WEB_TOKEN") {
    // presence-only: the console never rewrites its own gate
    wrap.classList.add("cred-presence");   // opts out of the read-state auto margin
    st.style.marginLeft = "auto";
    row.appendChild(st);
    row.appendChild(moreWrap);
    wrap.appendChild(row);
    wrap.appendChild(vst);
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
  row.appendChild(input);
  row.appendChild(mk("cred-set", "cred.set", "SET"));
  const clearBtn = mk("cred-clear", "cred.clear", "CLEAR");
  // an unset slot has nothing to clear — the button states it (SET success
  // re-enables it below; CLEAR success disables it again).
  if (!slot || !slot.set) clearBtn.disabled = true;
  row.appendChild(clearBtn);
  row.appendChild(moreWrap);
  row.appendChild(mk("cred-edit", "cred.edit", "EDIT", false));
  row.appendChild(mk("cred-cancel", "cred.cancel", "CANCEL", false));
  wrap.appendChild(row);
  wrap.appendChild(st);
  wrap.appendChild(vst);
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
    // car 6 (D10): the Connections card owns its two provider slots, so this
    // card renders every OTHER group — one editor per slot, never two.
    if (g === "connections") return;
    const bucket = buckets.find(b => b[0] === g);
    if (bucket) bucket[1].push(s); else buckets.push([g, [s]]);
  });
  buckets.filter(b => b[1].length).forEach(([g, items]) => {
    const head = document.createElement("div");
    head.style.cssText =
      "font-size:12.5px;letter-spacing:.14em;color:var(--blue-ink);" +
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

/* ------------------------------------------- Connections card (car 6) ------
   The two provider slots, each inside its own block so the scope checklist
   sits next to the credential it describes. Same presence-only credRow
   mechanism as Credentials: a value never round-trips, and a slot the server
   did not report simply does not render (an honest absence). */
const CONN_PROVIDERS = [
  ["CLOUDFLARE_API_TOKEN", "connListCF"],
  ["GITHUB_TOKEN", "connListGH"],
];

function renderConnections(body) {
  const slots = Array.isArray(body && body.slots) ? body.slots : [];
  const writable = !body || body.writable !== false;
  CONN_PROVIDERS.forEach(([name, id]) => {
    const box = document.getElementById(id);
    if (!box) return;
    box.textContent = "";
    const slot = slots.find(s => s && s.name === name);
    if (!slot) return;
    box.appendChild(credRow(slot, writable));
  });
}

/* One TEST button per provider: POST /api/slots/verify for that ONE slot,
   painted into that provider's own line with three honest colours —
   green = proven live, amber = inconclusive (the API could not be reached),
   red = rejected or wrong shape, and an unset slot stays neutral. The
   detail is the SERVER's wording; a value never appears here. */
const CONN_TESTS = [
  ["connTestCF", "CLOUDFLARE_API_TOKEN", "connTestCFOut"],
  ["connTestGH", "GITHUB_TOKEN", "connTestGHOut"],
];

async function connTest(btn, name, outId) {
  const out = document.getElementById(outId);
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const say = (text, cls) => {
    if (!out) return;
    out.hidden = false;
    out.className = "conn-test stat-note" + (cls ? " " + cls : "");
    out.textContent = text;
  };
  if (!token) {
    say(t("cred.needToken", "access token required — set it in Access"), "bad");
    return;
  }
  if (btn) btn.disabled = true;
  say(t("cred.verifying", "checking\u2026"), "");
  let res = null;
  try {
    res = await fetch("/api/slots/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ name: name }),
    });
  } catch (e) { res = null; }
  const doc = res ? await res.json().catch(() => ({})) : {};
  const row = doc && Array.isArray(doc.results) ? doc.results[0] : null;
  if (btn) btn.disabled = false;
  if (!row) {
    say(t("cred.verifyFail", "verification unavailable"), "bad");
    return;
  }
  if (row.ok === null) {
    say(t("conn.notSet", "not set — nothing to test"), "");
    return;
  }
  const detail = String(row.detail || row.reason || "");
  if (row.ok === true) { say("\u2713 " + detail, "ok"); return; }
  const soft = row.reason === "network" || row.reason === "upstream_error";
  say((soft ? "! " : "\u2717 ") + detail, soft ? "warn" : "bad");
}

/* --------------------------------------------------- bridge (car 6) -------
   The user-owned reverse proxy (line 5 / D9). Three independent ways to be
   OFF — the switch, an empty Worker URL and an empty allow-list — so the
   default is fail-closed without anyone having to remember it. The block is
   non-secret (a URL, host names, a number): credentials never enter it, and
   the DISABLE NOW button posts {enabled:false} ALONE so a kill switch can
   never be blocked by a half-typed field elsewhere on the card. */
let bridgeDirty = false;

function bridgeListValue(id) {
  const el = document.getElementById(id);
  if (!el) return [];
  return String(el.value || "").split(/[,;\r\n]/).map(s => s.trim())
    .filter(Boolean).slice(0, 32);
}

function bridgePayload() {
  const tog = document.getElementById("bridgeToggle");
  const raw = (document.getElementById("bridgeMaxBytes") || {}).value;
  const mb = parseInt(raw, 10);
  return {
    enabled: !!tog && tog.getAttribute("aria-pressed") === "true",
    worker_url: (((document.getElementById("bridgeUrl") || {}).value) || "").trim(),
    allow: bridgeListValue("bridgeAllow"),
    allow_auth: bridgeListValue("bridgeAuth"),
    max_bytes: Number.isFinite(mb) ? mb : 1048576,
  };
}

function bridgePrefill() {
  const b = (settings.bridge && typeof settings.bridge === "object")
    ? settings.bridge : {};
  const setF = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = v == null ? "" : String(v);
  };
  setF("bridgeUrl", b.worker_url);
  setF("bridgeAllow", Array.isArray(b.allow) ? b.allow.join(", ") : "");
  setF("bridgeAuth", Array.isArray(b.allow_auth) ? b.allow_auth.join(", ") : "");
  setF("bridgeMaxBytes", b.max_bytes == null ? 1048576 : b.max_bytes);
  const tog = document.getElementById("bridgeToggle");
  if (tog) tog.setAttribute("aria-pressed",
                            b.enabled === true ? "true" : "false");
  bridgeDirty = false;
}

async function postBridge(partial, msgId) {
  const msg = document.getElementById(msgId);
  const say = (text, err) => {
    if (!msg) return;
    msg.className = "save-state" + (err ? " err" : "");
    msg.textContent = text;
  };
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    say(t("cred.needToken", "access token required — set it in Access"), true);
    return false;
  }
  say(t("br.working", "saving\u2026"), false);
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ bridge: partial }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      // all-or-nothing, like every other block: show the server's details
      // verbatim (they name keys and grammar reasons, never a value).
      const details = Array.isArray(body.details) ? body.details : [];
      say(details.length ? details.join("; ")
           : (body.error || ("HTTP " + res.status)), true);
      return false;
    }
    settings = Object.assign(settings, body.settings || {});
    bridgeDirty = false;
    say(body.changed === true ? t("save.ok", "saved \u2713")
                              : t("save.same", "no change \u2713"), false);
    // sync ONLY the switch from the server truth: the other fields stay as
    // the operator typed them (a kill switch must not discard an edit).
    const tog = document.getElementById("bridgeToggle");
    const stored = settings.bridge || {};
    if (tog) tog.setAttribute("aria-pressed",
                              stored.enabled === true ? "true" : "false");
    loadBridgeStatus();
    beep(880, 0.06);
    return true;
  } catch (e) {
    say(String(e).slice(0, 80), true);
    return false;
  }
}

async function bridgeKill() {
  const tog = document.getElementById("bridgeToggle");
  if (tog) tog.setAttribute("aria-pressed", "false");
  return postBridge({ enabled: false }, "bridgeMsg");
}

async function bridgeTemplate() {
  const pre = document.getElementById("brTemplate");
  const btn = document.getElementById("brTemplateShow");
  if (!pre) return;
  if (!pre.hidden) {
    pre.hidden = true;
    if (btn) btn.textContent = t("br.showTemplate", "SHOW TEMPLATE");
    return;
  }
  if (!pre.textContent) {
    try {
      const res = await fetch("/api/bridge/template");
      pre.textContent = res.ok ? await res.text()
        : t("br.templateMissing", "template unavailable");
    } catch (e) {
      pre.textContent = t("br.templateMissing", "template unavailable");
    }
  }
  pre.hidden = false;
  if (btn) btn.textContent = t("br.hideTemplate", "HIDE TEMPLATE");
}

async function loadBridgeStatus() {
  const box = document.getElementById("brAudit");
  const msg = document.getElementById("brStatusMsg");
  if (!box) return;
  let doc = null;
  try {
    const res = await fetch("/api/bridge/status");
    doc = res.ok ? await res.json() : null;
  } catch (e) { doc = null; }
  if (!doc) {
    box.textContent = "";
    if (msg) {
      msg.className = "save-state err";
      msg.textContent = t("br.statusFail", "bridge status unavailable");
    }
    return;
  }
  const rows = Array.isArray(doc.audit) ? doc.audit : [];
  box.textContent = "";
  if (!rows.length) {
    const empty = document.createElement("div");
    empty.className = "br-empty";
    empty.dataset.i18n = "br.auditEmpty";
    empty.textContent = t("br.auditEmpty", "no proxied request yet");
    box.appendChild(empty);
  } else {
    rows.slice().reverse().forEach(row => {
      const line = document.createElement("div");
      line.className = "br-line";
      const dot = document.createElement("span");
      const good = row && row.ok === true;
      dot.className = good ? "ok" : "bad";
      dot.textContent = good ? "\u25cf " : "\u2717 ";
      line.appendChild(dot);
      line.appendChild(document.createTextNode(
        (row && row.host ? row.host : "") +
        (row && row.path ? row.path : "") + " \u00b7 " +
        String((row && row.bytes) || 0) + "B" +
        (row && row.reason ? " \u00b7 " + row.reason : "")));
      box.appendChild(line);
    });
  }
  if (msg) {
    msg.className = "save-state";
    msg.textContent = doc.routing === true
      ? t("br.routing", "routing on \u00b7 newest first")
      : t("br.idle", "not routing — switch, Worker URL or allow-list is off");
  }
}

function wireBridge() {
  const once = (el, ev, fn) => {
    if (!el || el.dataset.wired) return;
    el.dataset.wired = "1";
    el.addEventListener(ev, fn);
  };
  once(document.getElementById("bridgeToggle"), "click", () => {
    const tog = document.getElementById("bridgeToggle");
    const on = tog.getAttribute("aria-pressed") === "true";
    tog.setAttribute("aria-pressed", on ? "false" : "true");
    bridgeDirty = true;
  });
  ["bridgeUrl", "bridgeAllow", "bridgeAuth", "bridgeMaxBytes"].forEach(id =>
    once(document.getElementById(id), "input", () => { bridgeDirty = true; }));
  once(document.getElementById("bridgeSave"), "click",
       () => postBridge(bridgePayload(), "bridgeMsg"));
  once(document.getElementById("bridgeKill"), "click", bridgeKill);
  once(document.getElementById("brTemplateShow"), "click", bridgeTemplate);
  once(document.getElementById("brAuditRefresh"), "click", loadBridgeStatus);
  CONN_TESTS.forEach(([btnId, name, outId]) =>
    once(document.getElementById(btnId), "click",
         () => connTest(document.getElementById(btnId), name, outId)));
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
    renderConnections(body);
    list.hidden = false;
    credHintReason = body.writable === false ? "readOnly"
      : ((localStorage.getItem(TOKEN_KEY) || "") ? "" : "needToken");
  } catch (e) {                       // 404 / network: honest unavailable
    credsData = null;
    list.hidden = true;
    if (tgList) { tgList.textContent = ""; tgList.hidden = true; }
    renderConnections(null);
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
    syncCredSetState(wrap, slot.set === true);
    // the same slot may be rendered twice (Credentials + Telegram card):
    // keep every row for this name in sync with the fresh server truth
    document.querySelectorAll(`[data-slot="${name}"]`).forEach(w => {
      syncCredSetState(w, slot.set === true);
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
    const wrap = e.target.closest("[data-slot]");
    if (!wrap) return;
    // `...` menu: toggle this row's own menu only
    if (e.target.closest(".cred-more")) {
      const more = wrap.querySelector(".cred-more");
      setCredMenu(wrap, more && more.getAttribute("aria-expanded") !== "true");
      return;
    }
    // stage 2: EDIT opens the row, CANCEL folds it back to the read state
    const toggle = e.target.closest(".cred-edit, .cred-cancel");
    if (toggle) {
      const open = toggle.classList.contains("cred-edit");
      wrap.dataset.editing = open ? "1" : "0";
      setCredMenu(wrap, false);
      if (open) {
        const box = wrap.querySelector(".cred-input");
        if (box && !box.disabled) box.focus();
      }
      return;
    }
    const btn = e.target.closest(".cred-set, .cred-clear, .cred-verify");
    if (!btn) return;
    setCredMenu(wrap, false);          // an action closes the menu
    const name = wrap.dataset.slot;
    const input = wrap.querySelector(".cred-input");
    const clear = btn.classList.contains("cred-clear");
    const st = wrap.querySelector(".cred-status");
    if (btn.classList.contains("cred-verify")) {
      credVerify([name], wrap);
      return;
    }
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
  ["credList", "tgCredList", "connListCF", "connListGH"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", handler);
  });
  const all = document.getElementById("credVerifyAll");
  if (all) all.addEventListener("click", () => credVerify(null, null));
  // menus are transient: any click outside a row's menu folds it, Escape folds
  // them all (bound once — wireCredList runs a single time at boot)
  document.addEventListener("click", e => {
    if (e.target && e.target.closest && e.target.closest(".cred-more-wrap")) return;
    document.querySelectorAll(".cred-menu").forEach(m => {
      if (!m.hidden) setCredMenu(m.closest("[data-slot]"), false);
    });
  });
  document.addEventListener("keydown", e => {
    if (e.key !== "Escape") return;
    document.querySelectorAll(".cred-menu").forEach(m => {
      if (!m.hidden) setCredMenu(m.closest("[data-slot]"), false);
    });
  });
}

/* open/close one credential row's `...` menu (aria-expanded stays truthful) */
function setCredMenu(wrap, open) {
  if (!wrap) return;
  const menu = wrap.querySelector(".cred-menu");
  const more = wrap.querySelector(".cred-more");
  if (!menu || !more) return;
  menu.hidden = !open;
  more.setAttribute("aria-expanded", open ? "true" : "false");
}

/* ------------------------------------------------ slot verification (0.5.54) --
   POST /api/slots/verify with the Access token. One call covers a single
   row (the row's VERIFY button) or the whole catalog (VERIFY ALL). Each
   result lands in that row's own .cred-vstat line: the server's reason word
   mapped through the packs, plus its expectation/detail — the VALUE never
   appears anywhere. Level (format|live) is stated so a shape pass is never
   mistaken for a proven-working credential. */
async function credVerify(names, oneWrap) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const msg = document.getElementById("credVerifyMsg");
  const paint = (wrap, html, isErr) => {
    const v = wrap && wrap.querySelector(".cred-vstat");
    if (!v) return;
    v.hidden = false;
    v.className = "cred-vstat cred-verify stat-note" +
      (isErr ? " save-state err" : "");
    v.textContent = html;
  };
  if (!token) {
    const txt = t("cred.needToken",
      "access token required — set it in Access");
    if (oneWrap) paint(oneWrap, txt, true);
    else if (msg) { msg.className = "save-state err"; msg.textContent = txt; }
    return;
  }
  const body = names && names.length === 1
    ? { name: names[0] } : { all: true };
  if (oneWrap) paint(oneWrap, t("cred.verifying", "checking\u2026"), false);
  if (msg) { msg.className = "save-state";
    msg.textContent = t("cred.verifying", "checking\u2026"); }
  let res;
  try {
    res = await fetch("/api/slots/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(body),
    });
  } catch (e) {
    res = null;
  }
  const doc = res ? await res.json().catch(() => ({})) : {};
  const rows = doc && Array.isArray(doc.results) ? doc.results : null;
  if (!rows) {
    const txt = res ? t("cred.verifyFail", "verification unavailable")
                    : t("cred.verifyFail", "verification unavailable");
    if (oneWrap) paint(oneWrap, txt, true);
    if (msg) { msg.className = "save-state err"; msg.textContent = txt; }
    return;
  }
  let okCount = 0, badCount = 0;
  rows.forEach(r => {
    if (!r || !r.name) return;
    // the same slot may render twice (Credentials + Telegram card): paint
    // every row, exactly like credWrite keeps them in sync
    const wraps = document.querySelectorAll(`[data-slot="${r.name}"]`);
    const ok = r.ok === true;
    if (r.ok === true) okCount++;
    else if (r.ok === false) badCount++;
    const level = r.level === "live"
      ? t("cred.v.live", "live") : t("cred.v.format", "format");
    const reason = r.reason === "not_set"
      ? t("cred.v.not_set", "not set")
      : (ok ? t("cred.v.ok", "ok")
            : t("cred.v.reason." + String(r.reason || ""),
                String(r.detail || r.reason || "failed")));
    const detail = r.ok === false && r.detail ? " — " + r.detail : "";
    const prefix = r.ok === null ? "\u2013" : (ok ? "\u2713" : "\u2717");
    const txt = `${prefix} ${level} \u00b7 ${reason}${detail}`;
    wraps.forEach(w => paint(w, txt, r.ok === false));
  });
  if (msg) {
    const txt = t("cred.verifyDone", "{ok} ok \u00b7 {bad} need attention")
      .replace("{ok}", String(okCount)).replace("{bad}", String(badCount));
    msg.className = "save-state" + (badCount ? " err" : "");
    msg.textContent = txt;
  }
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
    /* Lane N (E3): inbound poll-health note — hidden until paintChanChips
       sees an object `inbound_health` (shape-hide when absent). */
    const note = document.createElement("div");
    note.className = "stat-note chan-inbound";
    note.dataset.chanInbound = route;
    note.hidden = true;
    blk.appendChild(note);
    CHAN_SCHEMA[route].forEach(([key, kind]) =>
      blk.appendChild(chanField(key, kind, sub[key])));
    if (route === "webhook") {
      // X8: endpoint hint line (plain note, never a form label/field)
      const ep = document.createElement("div");
      ep.className = "stat-note chan-webhook";
      ep.hidden = true;
      blk.appendChild(ep);
    }
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
  paintInboundNotes();
  paintWebhookHint();
}

/* Wave 4 (X8): the INBOUND webhook endpoint hint on the webhook route row —
   read-only address + one line of usage. The secret VALUE never renders
   (slot name CHANNEL_WEBHOOK_SECRET only); an absent/unset slot reads as an
   honest disabled note. Shape-hide: no .webhook facts = nothing shown. */
function paintWebhookHint() {
  const el = document.querySelector("#chanList .chan-webhook");
  if (!el) return;
  const m = chanWebhookMeta;
  if (!m || typeof m.url !== "string" || !m.url) {
    el.hidden = true;
    el.textContent = "";
    return;
  }
  const head = t("chan.webhookEp", "inbound endpoint: ") + m.url;
  const tail = m.secret_configured === true
    ? t("chan.webhookOn",
        "auth: Authorization Bearer header carrying the CHANNEL_WEBHOOK_SECRET " +
        "value (never shown here)")
    : t("chan.webhookOff",
        "inbound disabled — set the CHANNEL_WEBHOOK_SECRET slot in secrets.env " +
        "to enable");
  el.textContent = head + " · " + tail;
  el.hidden = false;
}

/* Lane N (E3): channel-row inbound poll-health note. Shape-hide discipline —
   a row without an object `inbound_health` (old backend / health read
   failed) and an outbound-only route (inbound:false) show nothing: honest
   absence beats a guessed status. States come from the server verbatim:
   healthy+cursor (green), healthy without cursor (amber, says both),
   consecutive failures (red, exact count), never polled (neutral gray). */
function inboundAgeText(sec) {
  const n = Math.round(Number(sec));
  if (!Number.isFinite(n) || n < 0) return null;
  return n < 60 ? n + "s"
    : n < 3600 ? Math.round(n / 60) + "m"
    : Math.round(n / 3600) + "h";
}

function paintInboundNotes() {
  document.querySelectorAll("#chanList [data-chan-inbound]").forEach(note => {
    const route = note.dataset.chanInbound;
    const row = chanStatusRows.find(r => r && String(r.name) === route);
    const h = (row && typeof row.inbound_health === "object"
               && row.inbound_health !== null) ? row.inbound_health : null;
    note.classList.remove("in-ok", "in-bad", "in-warn", "in-unknown");
    if (!h || !row || row.inbound === false) {
      note.hidden = true; note.textContent = ""; return;
    }
    const healthy = h.healthy;
    const nRaw = Number(h.consec_fails);
    const fails = Number.isFinite(nRaw) && nRaw > 0 ? Math.trunc(nRaw) : 0;
    const age = inboundAgeText(h.last_ok_age_sec);
    let text, cls;
    if (healthy === false) {
      text = t("chan.inbound.fail", "inbound: {n} consecutive failures")
        .replace("{n}", String(fails));
      cls = "in-bad";
    } else if (healthy === true && age !== null && h.cursor) {
      text = t("chan.inbound.ok", "inbound: healthy \u00b7 {age} ago")
        .replace("{age}", age);
      cls = "in-ok";
    } else if (healthy === true && age !== null) {
      text = t("chan.inbound.nocursor",
               "inbound: healthy \u00b7 {age} ago \u00b7 no cursor yet")
        .replace("{age}", age);
      cls = "in-warn";
    } else {
      text = t("chan.inbound.never", "inbound: not polled yet");
      cls = "in-unknown";
    }
    note.hidden = false;
    note.classList.add(cls);
    note.textContent = text;
  });
}

async function loadChanStatus() {
  try {
    const body = await fetch("/api/channels").then(r => r.json());
    chanStatusRows = (body && Array.isArray(body.channels)) ? body.channels : [];
    // X8: read-only endpoint facts ride the same fetch; absent = shape-hide
    chanWebhookMeta = (body && typeof body.webhook === "object" && body.webhook)
      ? body.webhook : null;
  } catch (e) { chanStatusRows = []; chanWebhookMeta = null; }
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
  // Car 4 (D6): the decision endpoint row — its own flag, its own block.
  if (decDirty) {
    const v = id => (((document.getElementById(id) || {}).value || "").trim());
    const on = id => ((document.getElementById(id) || {}).getAttribute
      ("aria-pressed") === "true");
    out.decision = { enabled: on("decToggle"), url: v("decUrl"),
                     model: v("decModel"), router_model: v("decRouter") };
  }
  // v0.5.55: the endpoint manager sets epDirty (NOT mycDirty) on row edits,
  // so the whole block must wake for either flag — otherwise an ADD + url
  // save would ship a payload without `endpoints` and silently write nothing.
  if (mycDirty || epDirty) {
    const g = id => ((document.getElementById(id) || {}).value || "");
    out.context_dirs = g("mycContextDirs").trim();
    out.mcp = g("mycMcp").trim();
    out.shell = g("mycShell").trim();
    // v0.5.55 dual-track: the endpoint manager and the one-line spec are two
    // representations of the SAME chain — the server refuses both non-empty
    // in one patch, so the payload always pairs a non-empty endpoints array
    // with an empty upstreams (and vice versa). An empty array still ships
    // when the panel had rows before (deep_merge replaces lists wholesale —
    // that is how stored rows get CLEARED).
    const stored = (settings.mycore && typeof settings.mycore === "object")
      ? settings.mycore : {};
    const hasRows = Array.isArray(epRows)
      && epRows.some(r => r && String(r.url || "").trim());
    if (epDirty || hasRows) {
      out.endpoints = epRows
        .filter(r => r && String(r.url || "").trim())
        .map(r => {
          const e = { url: String(r.url).trim() };
          const m = String(r.model || "").trim();
          if (m) e.model = m;
          const k = (Array.isArray(r.keys) ? r.keys : [])
            .filter(s => typeof s === "string" && s).slice(0, 8);
          if (k.length) e.keys = k;
          return e;
        });
      out.upstreams = "";
    } else {
      out.upstreams = g("mycUpstreams").trim();
      if (Array.isArray(stored.endpoints) && stored.endpoints.length)
        out.endpoints = [];      // rows existed server-side: clear them too
    }
    // rotation / rate caps: only ship what really changed (untouched form
    // never rewrites the document — B1 discipline).
    const rot = ((document.getElementById("mycRotation") || {}).value || "");
    if (rot !== (stored.rotation || "")) out.rotation = rot;
    const capOf = (id, ceiling) => {
      const raw = parseInt(((document.getElementById(id) || {}).value || ""), 10);
      return Number.isFinite(raw) ? Math.min(ceiling, Math.max(0, raw)) : 0;
    };
    const rpm = capOf("mycMaxRpm", 1000);
    if (rpm !== (Number(stored.max_rpm) || 0)) out.max_rpm = rpm;
    const tpm = capOf("mycMaxTpm", 10000000);
    if (tpm !== (Number(stored.max_tpm) || 0)) out.max_tpm = tpm;
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
    // X12/X15 opt-in switches: they enter the payload only when they really
    // say something (a non-zero day count, or a stored value being cleared) —
    // an untouched default never rides along and never rewrites the document.
    const pEl = document.getElementById("mycPrune");
    if (pEl) {
      const n = parseInt(pEl.value, 10);
      const days = Number.isFinite(n) ? Math.min(3650, Math.max(0, n)) : 0;
      if (days !== 0 || stored.session_prune_days) out.session_prune_days = days;
    }
    const eEl = document.getElementById("mycEffort");
    if (eEl) {
      const eff = eEl.value || "";
      if (eff || stored.reasoning_effort) out.reasoning_effort = eff;
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

/* car 7 (D11): dirty marks for the guardrails + obedience blocks. Attached
   once (initSettingsPanels can run again after a restore). */
let _guardObedWired = false;
function wireGuardObed() {
  if (_guardObedWired) return;
  _guardObedWired = true;
  ["grUsdDay", "grTokenDay"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { guardDirty = true; });
  });
  ["obRules", "obForbidden"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { obedDirty = true; });
  });
}

/* car 7 (D11): the leash read face — tail state + budget status. Refreshed
   on the SAME cadence as the status panel plus right after every control
   action/save, so the Overview row and the Guardrails card never show a
   stale verdict. Fail-soft: a network error keeps the last good paint. */
let controlData = null;
async function refreshControl() {
  try {
    controlData = await fetch("/api/control").then(r => r.json());
    renderControl();
    renderOvState();           // round C (P4): pause/resume flips the sentence
  } catch (e) { /* keep last */ }
}

function renderControl() {
  const doc = controlData;
  if (!doc || typeof doc !== "object") return;
  // --- tail state (Overview head) ---
  const tail = document.getElementById("tailState");
  if (tail) {
    // server state: idle | working | paused | blocked — mapped through the
    // packs; the pause reason rides along verbatim (server-supplied data).
    const map = { idle: "tail.idle", working: "tail.working",
                  paused: "tail.paused", blocked: "tail.blocked" };
    let txt = t(map[doc.tail_state] || "tail.idle", doc.tail_state || "idle");
    if (doc.tail_state === "paused" && doc.reason) txt += " · " + doc.reason;
    tail.textContent = txt;
  }
  const pbtn = document.getElementById("ctlPauseBtn");
  if (pbtn) {
    const paused = doc.paused === true;
    pbtn.dataset.i18n = paused ? "ctl.resume" : "ctl.pause";
    pbtn.textContent = t(paused ? "ctl.resume" : "ctl.pause",
                         paused ? "RESUME" : "PAUSE");
    pbtn.classList.toggle("primary", paused);
  }
  // --- guardrails status row ---
  const b = (doc.budget && typeof doc.budget === "object") ? doc.budget : {};
  const cell = (id, st, used, limit, money) => {
    const el = document.getElementById(id);
    if (!el) return;
    const label = st === "over" ? t("gr.over", "OVER")
      : st === "unknown" ? t("gr.unknown", "UNKNOWN")
      : st === "off" ? t("gr.off", "OFF") : t("gr.ok", "OK");
    let val = label;
    if (st === "ok" || st === "over") {
      const n = v => money ? "$" + Number(v || 0).toFixed(2)
                           : Number(v || 0).toLocaleString("en-US");
      val = `${label} ${n(used)} / ${n(limit)}`;
    } else if (st === "unknown" && limit > 0) {
      val = `${label} / ${money ? "$" + Number(limit).toFixed(2)
                                : Number(limit).toLocaleString("en-US")}`;
    } else if (st === "off") {
      val = t("gr.off", "OFF");
    }
    el.textContent = val;
    el.style.color = st === "over" ? "var(--urgent, #c0392b)"
      : st === "unknown" ? "var(--muted)" : "";
  };
  const lim = (b.limits && typeof b.limits === "object") ? b.limits : {};
  const usd = (b.usd && typeof b.usd === "object") ? b.usd : {};
  const tok = (b.tokens && typeof b.tokens === "object") ? b.tokens : {};
  cell("grUsdStat", usd.state || "off", usd.used, lim.usd_day != null
    ? lim.usd_day : usd.limit, true);
  cell("grTokStat", tok.state || "off", tok.used, lim.token_day != null
    ? lim.token_day : tok.limit, false);
  const gstat = document.getElementById("grStatus");
  if (gstat) {
    // why-UNKNOWN stays visible (never a silent pass): reason from the server
    gstat.textContent = (usd.state === "unknown" && (b.reason || ""))
      ? String(b.reason) : "";
  }
}

/* car 7 (D11): one POST /api/control for stop | pause | resume. */
async function postControl(action) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  const msg = document.getElementById("ctlMsg");
  const say = (cls, text) => { if (msg) { msg.className = "save-state " + cls; msg.textContent = text; } };
  if (!token) { say("err", t("save.needToken", "token required")); return; }
  say("", "…");
  try {
    const res = await fetch("/api/control", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      say("err", res.status === 401 ? t("save.badToken", "401 · unauthorized")
        : ((body.details || [body.error || "error"])[0] + ""));
      return;
    }
    say("", action === "stop" ? "✓ " + t("ctl.stop", "STOP")
      : action === "pause" ? "✓ " + t("ctl.pause", "PAUSE")
      : "✓ " + t("ctl.resume", "RESUME"));
    refreshControl(); refreshStatus();
    beep(660, 0.05);
  } catch (e) {
    say("err", String(e).slice(0, 50));
  }
}

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
  // v0.5.55: self-metered price table round-trips as pretty JSON. A value
  // that is not an object (hand-edited junk) shows as-is — the server's
  // refusal on save is the honest signal, never a silent rewrite here.
  const prEl = document.getElementById("usageModelPricing");
  if (prEl) {
    const mp = u.model_pricing;
    prEl.value = (mp && typeof mp === "object" && !Array.isArray(mp)
      && Object.keys(mp).length) ? JSON.stringify(mp, null, 2) : "";
  }
  setField("mycContextDirs", myc.context_dirs);
  setField("mycUpstreams", myc.upstreams);
  setField("mycMcp", myc.mcp);
  setField("mycShell", myc.shell);
  // activation car: mycore.models / mycore.budget are console-managed now
  setField("mycModels", Array.isArray(myc.models) ? myc.models.join(", ") : "");
  // D1: chips mirror the field, and the discovery panel starts clean again
  // (programmatic fills fire no input event, so the chips are painted here)
  renderModelChips();
  discState = { models: [], total: 0, ready: false };
  renderDiscResults();
  discMsg(null);
  discPaintAdd();
  setField("mycBudget", myc.budget);
  // Wave 6 delta2: the two opt-in switches round-trip like the other fields
  setField("mycPrune", myc.session_prune_days);
  setField("mycEffort", myc.reasoning_effort || "");
  // v0.5.55 gateway knobs + the endpoint manager's first load
  setField("mycRotation", myc.rotation || "");
  setField("mycMaxRpm", Number(myc.max_rpm) || 0);
  setField("mycMaxTpm", Number(myc.max_tpm) || 0);
  // car 7 (D11): guardrails + obedience prefill (raw stored values; the
  // server's grammar refusal on save is the honest signal).
  const guard = (settings.guardrails && typeof settings.guardrails === "object")
    ? settings.guardrails : {};
  setField("grUsdDay", guard.usd_day == null ? 0 : guard.usd_day);
  setField("grTokenDay", guard.token_day == null ? 0 : guard.token_day);
  const obed = (settings.obedience && typeof settings.obedience === "object")
    ? settings.obedience : {};
  setField("obRules", Array.isArray(obed.rules) ? obed.rules.join("\n") : "");
  setField("obForbidden", Array.isArray(obed.forbidden) ? obed.forbidden.join("\n") : "");
  // Car 4 (D6): decision endpoint row (raw stored strings; empty = default)
  const dec = (myc.decision && typeof myc.decision === "object")
    ? myc.decision : {};
  setField("decUrl", dec.url);
  setField("decModel", dec.model);
  setField("decRouter", dec.router_model);
  const decTog = document.getElementById("decToggle");
  if (decTog) decTog.setAttribute("aria-pressed",
                                  dec.enabled === true ? "true" : "false");
  wireDecisionPanel();
  epDirty = false;
  decDirty = false;
  loadEndpoints();
  chanDirty = false; usageDirty = false; mycDirty = false; toolsDirty = false;
  mycRestartDirty = false;
  guardDirty = false; obedDirty = false;
  wireGuardObed();             // car 7 (D11): dirty marks for the two blocks
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
  bridgePrefill();             // car 6: bridge fields <- settings.bridge
  wireBridge();                // car 6: switch, save, kill, test, template
  loadBridgeStatus();          // car 6: routing state + the audit tail
}

/* ------------------------------ round C (P6): sticky save bar ------------ */
/* ONE delegated listener marks the CARD that actually changed. Credential
   rows, the Access token, the send-test box and the four cards with their
   OWN write path (backup / bridge / connections / mission) are never dirty
   for saveSettings, so the bar can never promise a write it cannot do. The
   bar mirrors #saveState verbatim — never a second invented status — and its
   SAVE runs the very same saveSettings(). */
const SAVE_BAR_SKIP = "#credList, #tokenInput, #profCreate, #chanTestText, "
  + "#chanTestTarget, #chanTestRoute, #backupCard, #bridgeCard, "
  + "#connectionsCard, #missionCard, #orchCard, #discPanel";
let saveBarCardEl = null;

function saveBarPaint(text, kind) {
  const bar = document.getElementById("saveBar");
  const state = document.getElementById("saveBarState");
  const name = document.getElementById("saveBarCard");
  if (!bar || !state) return;
  state.dataset.kind = kind || "dirty";
  if (text !== undefined) state.textContent = text;
  if (name && saveBarCardEl) {
    const h = saveBarCardEl.querySelector("h2");
    name.textContent = h ? h.textContent.trim() : "";
  }
}

function saveBarShow(card, kind, text) {
  const bar = document.getElementById("saveBar");
  if (!bar) return;
  if (card) saveBarCardEl = card;
  saveBarPaint(text, kind);
  bar.hidden = false;
}

function saveBarHide(delayMs) {
  const bar = document.getElementById("saveBar");
  if (!bar) return;
  const drop = () => {
    if (!document.querySelector(".set-grid > .card[data-dirty='1']")) bar.hidden = true;
  };
  if (delayMs) setTimeout(drop, delayMs); else drop();
}

function saveBarDirty(e) {
  const el = e.target;
  if (!el || !el.closest) return;
  if (el.closest(SAVE_BAR_SKIP)) return;        // not a saveSettings field
  const card = el.closest(".set-grid > .card");
  if (!card) return;
  card.dataset.dirty = "1";
  saveBarShow(card, "dirty", t("save.dirty", "unsaved changes"));
}

function paintSaveBar() {                        // language switch (loadLang)
  const bar = document.getElementById("saveBar");
  if (!bar || bar.hidden) return;
  const state = document.getElementById("saveBarState");
  if (!state) return;
  const kind = state.dataset.kind;
  if (kind === "dirty") state.textContent = t("save.dirty", "unsaved changes");
  else if (kind === "ok") state.textContent = t("save.ok", "saved ✓");
  else if (kind === "same") state.textContent = t("save.same", "no change ✓");
  // kind === "err": the server's own detail stays verbatim, like repaintSaveState
}

function wireRoundC() {
  // P4 disclosure toggle
  const techBtn = document.getElementById("ovTechBtn");
  const tech = document.getElementById("ovTech");
  if (techBtn && tech && !techBtn.dataset.wired) {
    techBtn.dataset.wired = "1";
    techBtn.addEventListener("click", () => {
      tech.hidden = !tech.hidden;
      paintOvTechBtn();
    });
  }
  // P6: dirty tracking + the bar's own SAVE + success mirror
  const view = document.getElementById("view-settings");
  if (view && !view.dataset.wiredRoundC) {
    view.dataset.wiredRoundC = "1";
    ["input", "change"].forEach(ev => view.addEventListener(ev, saveBarDirty));
    const barBtn = document.getElementById("saveBarBtn");
    if (barBtn) barBtn.addEventListener("click", () => saveSettings());
    const src = document.getElementById("saveState");
    if (src && "MutationObserver" in window) {
      new MutationObserver(() => {
        const bar = document.getElementById("saveBar");
        if (!bar || bar.hidden) return;
        if (src.classList.contains("err")) {
          saveBarShow(saveBarCardEl, "err", src.textContent);
          return;                              // errors stay until the next try
        }
        const kind = src.dataset.saveKind;
        if (!kind) return;                     // still empty: nothing yet
        document.querySelectorAll(".set-grid > .card[data-dirty='1']")
          .forEach(c => delete c.dataset.dirty);
        saveBarShow(saveBarCardEl, kind, src.textContent);
        saveBarHide(2200);                     // then the bar steps aside
      }).observe(src, { attributes: true, attributeFilter: ["class", "data-save-kind"] });
    }
  }
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
  // stage 2: the `...` button carries a glyph, so its accessible name is set
  // here instead of through a data-i18n text node
  document.querySelectorAll(".cred-more").forEach(b =>
    b.setAttribute("aria-label", t("cred.more", "more actions")));
  repaintSaveState();      // activation car: saved row + restart hint follow
  renderEndpoints();       // v0.5.55: manager rows rebuilt with pack strings
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
  renderAgentBackup();         // car 8: agent-backup rows follow the pack too
  renderFileFace();            // round C: file face text follows the pack
  renderModelChips();          // D1: chip remove labels follow the pack
  discPaintMsg();              // D1: discovery status line repaints
  discPaintAdd();              // D1: ADD label repaints with the selection
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
    if (job.name) meta.push(String(job.name));   // X9: create-form label rides the row
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
        `style="padding:2px 9px;font-size:12.5px;" aria-label="delete">×</button>`
      : "";
    return `<div class="ctl-row" data-cron="${escapeHtml(jid)}">` +
      `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">${escapeHtml(title)}</label>` +
      `<span class="stat-note" style="margin-top:0;">${escapeHtml(meta.join(" · "))}</span>` +
      del +
      `</div>`;
  }).join("");
}

/* Wave 4 (X9): structured cron create — POST /api/cron {action:"create"}.
   The SAME registry/grammar as the NL/CLI paths, so one source of truth:
   a refusal renders the server's details[0] VERBATIM, a success takes the
   returned row (byte-identical to a GET row) straight into the list. The
   natural-language hint above stays — both creation paths coexist. */
async function cronCreate() {
  const btn = document.getElementById("cronCreateBtn");
  const msg = document.getElementById("cronCreateMsg");
  if (!btn || !msg) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken", "access token required — set it in Access");
    return;
  }
  const val = id => (((document.getElementById(id) || {}).value) || "");
  const payload = {
    action: "create",
    name: val("cronName").trim(),
    schedule: val("cronSpec").trim(),
    prompt: val("cronPrompt"),
  };
  btn.disabled = true;
  msg.className = "save-state";
  msg.textContent = t("cron.creating", "creating…");
  try {
    const res = await fetch("/api/cron", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      msg.className = "save-state err";
      msg.textContent = details[0] || body.error || ("HTTP " + res.status);
      return;
    }
    const job = body.job;
    if (job && typeof job === "object") {
      if (!cronData || typeof cronData !== "object" ||
          !Array.isArray(cronData.jobs)) cronData = { jobs: [], count: 0 };
      cronData.jobs.push(job);
      cronData.count = cronData.jobs.length;
      renderCronList();
    } else {
      refreshCron();                  // shape surprise: re-read, never guess
    }
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (el) el.value = v;
    };
    set("cronName", ""); set("cronSpec", ""); set("cronPrompt", "");
    msg.className = "save-state";
    msg.textContent = t("cron.created", "created ✓");
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("cron.fail", "request failed — ") +
      String((e && e.message) || e).slice(0, 80);
  } finally {
    btn.disabled = false;
  }
}

/* ============================ Wave 5 delta1: X10 checkpoints + X11 profiles
   ----------------------------------------------------------------------
   X10 (Overview, Services card): GET /api/checkpoints is METADATA ONLY —
   id / time / size / repo-relative path / hash. A snapshot may cover a
   credential file, so no snapshot byte is ever rendered; the row layout is
   fixed and a failed read HIDES the block (an honest absence beats a guess).
   Restore is a Bearer-gated POST whose server refusal renders verbatim.

   X11 (Settings, CONSOLE card): GET /api/profiles is a read-only catalog of
   presence + counts (a slot value never renders); create/remove are
   Bearer-gated POSTs that run the SAME `aidog profile` CLI rules — the
   server owns every refusal and its details[0] shows word for word.
   All copy below is a t(key, fallback) string whose pack entries were added
   by the 0.5.53 i18n completion lane (795 keys per pack). */
let ckptData = null;

async function refreshCheckpoints() {
  try {
    const r = await fetch("/api/checkpoints");
    const d = r.ok ? await r.json() : null;
    ckptData = (d && Array.isArray(d.entries)) ? d : null;
  } catch (e) { ckptData = null; }
  renderCheckpoints();
}

function renderCheckpoints() {
  const box = document.getElementById("ckptBlock");
  const list = document.getElementById("ckptList");
  if (!box || !list) return;
  if (!ckptData) { box.hidden = true; return; }   // failed/absent read: hide
  const rows = (ckptData.entries || []).filter(
    c => c && Number.isFinite(Number(c.id)));
  if (!rows.length) {
    list.innerHTML = `<div class="stat-note">${escapeHtml(
      t("ckpt.empty", "no checkpoints yet"))}</div>`;
    box.hidden = false;
    return;
  }
  list.innerHTML = rows.map(c => {
    const id = Number(c.id);
    const bits = ["#" + id];
    if (c.when) bits.push(String(c.when));
    bits.push((Number(c.size) || 0) + " B");
    const p = String(c.path || c.file || "");
    const restore = `<button class="btn" type="button" ` +
      `data-ckpt-restore="${id}" ` +
      `title="${escapeHtml(t("ckpt.restoreTip",
        "Restore this checkpoint — the current file is snapshotted first, so the restore is itself undoable"))}" ` +
      `style="padding:2px 9px;font-size:12.5px;" ` +
      `aria-label="restore checkpoint ${id}">` +
      `${escapeHtml(t("ckpt.restoreBtn", "RESTORE"))}</button>`;
    return `<div class="ctl-row" data-ckpt="${id}">` +
      `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
      `${escapeHtml(bits.join(" · "))}</label>` +
      `<span class="stat-note" style="margin-top:0;overflow-wrap:anywhere;" ` +
      `title="${escapeHtml(p)}">${escapeHtml(p)}</span>` +
      restore + `</div>`;
  }).join("");
  box.hidden = false;
}

async function checkpointRestore(id) {
  const msg = document.getElementById("ckptMsg");
  if (!msg) return;
  const ask = t("ckpt.restoreConfirm",
    "Restore checkpoint #{id}? The current file is snapshotted first, " +
    "so the restore is itself undoable.").replace("{id}", id);
  if (!window.confirm(ask)) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken",
      "access token required — set it in Access");
    return;
  }
  msg.className = "save-state";
  msg.textContent = t("ckpt.restoring", "restoring…");
  try {
    const res = await fetch("/api/checkpoints", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "restore", id: Number(id) }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      msg.className = "save-state err";
      msg.textContent = details[0] || body.error || ("HTTP " + res.status);
      return;
    }
    msg.className = "save-state";
    msg.textContent = t("ckpt.restored", "restored ✓");
    refreshCheckpoints();
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("cron.fail", "request failed — ") +
      String((e && e.message) || e).slice(0, 80);
  }
}

let profData = null;

async function refreshProfiles() {
  try {
    const r = await fetch("/api/profiles");
    const d = r.ok ? await r.json() : null;
    profData = (d && Array.isArray(d.profiles)) ? d : null;
  } catch (e) { profData = null; }
  renderProfiles();
}

function renderProfiles() {
  const list = document.getElementById("profList");
  if (!list) return;
  if (!profData) {
    list.innerHTML = `<div class="stat-note">${escapeHtml(
      t("prof.unavailable", "profiles unavailable"))}</div>`;
    return;
  }
  // active name reuses the status row's own `profile` field ("" = default)
  const active = (statusData && typeof statusData.profile === "string")
    ? statusData.profile : String(profData.active || "");
  const head = `<div class="stat-note" style="margin-top:0;">${escapeHtml(
    t("prof.active", "active profile") + ": " + (active || "default"))}</div>`;
  const rows = (profData.profiles || []).map(p => {
    const name = String((p && p.name) || "");
    if (!name) return "";
    const badge = p.default
      ? `<span class="chat-badge">${escapeHtml(
          t("prof.defaultBadge", "default"))}</span>` : "";
    const bits = [p.secrets ? "secrets ✓" : "secrets –",
                  p.settings ? "settings ✓" : "settings –",
                  p.web_port ? ("port " + p.web_port) : "port –",
                  (p.state_files === null || p.state_files === undefined)
                    ? "state unknown"
                    : ("state " + p.state_files + " file" +
                       (p.state_files === 1 ? "" : "s"))];
    if (p.active) bits.push(t("prof.activeMark", "active"));
    if (!p.default && p.can_remove === false) {
      bits.push(t("prof.stateNotEmpty", "state must be empty to remove"));
    }
    // the × stays for every non-default row: the SERVER owns the refusal and
    // its detail renders verbatim (hiding it would hide the honest error)
    const del = p.default ? "" :
      `<button class="btn" type="button" data-prof-rm="${escapeHtml(name)}" ` +
      `title="${escapeHtml(t("prof.rmTip", "Remove this profile (only when its state dir is empty)"))}" ` +
      `style="padding:2px 9px;font-size:12.5px;" ` +
      `aria-label="remove profile ${escapeHtml(name)}">×</button>`;
    return `<div class="ctl-row" data-prof="${escapeHtml(name)}">` +
      `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
      `${escapeHtml(name)}${badge}</label>` +
      `<span class="stat-note" style="margin-top:0;">` +
      `${escapeHtml(bits.join(" · "))}</span>` + del + `</div>`;
  }).join("");
  list.innerHTML = head + rows;
}

async function profileCreate() {
  const btn = document.getElementById("profCreateBtn");
  const msg = document.getElementById("profMsg");
  const input = document.getElementById("profName");
  if (!btn || !msg || !input) return;
  const name = String(input.value || "").trim();
  if (!name) {
    msg.className = "save-state err";
    msg.textContent = t("prof.nameRequired", "profile name required");
    return;
  }
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken",
      "access token required — set it in Access");
    return;
  }
  btn.disabled = true;
  msg.className = "save-state";
  msg.textContent = t("prof.creating", "creating…");
  try {
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "create", name: name }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      msg.className = "save-state err";
      msg.textContent = details[0] || body.error || ("HTTP " + res.status);
      return;
    }
    input.value = "";
    msg.className = "save-state";
    msg.textContent = t("prof.created", "created ✓");
    refreshProfiles();
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("cron.fail", "request failed — ") +
      String((e && e.message) || e).slice(0, 80);
  } finally {
    btn.disabled = false;
  }
}

async function profileRm(name) {
  const msg = document.getElementById("profMsg");
  if (!name || !msg) return;
  const ask = t("prof.rmConfirm",
    'Remove profile "{name}"? It is deleted only when its state dir is empty.')
    .replace("{name}", name);
  if (!window.confirm(ask)) return;
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    msg.className = "save-state err";
    msg.textContent = t("cred.needToken",
      "access token required — set it in Access");
    return;
  }
  msg.className = "save-state";
  msg.textContent = t("prof.removing", "removing…");
  try {
    const res = await fetch("/api/profiles", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "rm", name: name }) });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const details = Array.isArray(body.details) ? body.details : [];
      msg.className = "save-state err";
      msg.textContent = details[0] || body.error || ("HTTP " + res.status);
      return;
    }
    msg.className = "save-state";
    msg.textContent = t("prof.removed", "removed ✓");
    refreshProfiles();
  } catch (e) {
    msg.className = "save-state err";
    msg.textContent = t("cron.fail", "request failed — ") +
      String((e && e.message) || e).slice(0, 80);
  }
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

/* Internal identifier behind each checklist row — HOVER TITLE ONLY since
   car 5 (0.5.48): the visible label is plain language (packs act.item.*),
   the exact name rides `title` so a hover (or a server 400 detail) still
   shows what to look for in secrets.env / settings. */
const ACT_INTERNAL = {
  web_token: "WEB_TOKEN",
  model_key: "MODEL_API_KEY",
  tg_token: "MYCORE_TG_TOKEN",
  tg_allowlist: "AIDOG_TG_ALLOWED_USERS",
  model_endpoint: "mycore.base_url",
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
        `<label style="flex:1;min-width:0;overflow-wrap:anywhere;"` +
        `${ACT_INTERNAL[id] ? ` title="${escapeHtml(ACT_INTERNAL[id])}"` : ""}>` +
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
  renderOvState();             // round C (P4): ready / endpoint state follows
  renderOvTech();
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
  // credential slots: set count over the writable catalog (33) — the
  // presence-only rows from GET /api/secrets (never a value)
  const cr = (credsData && Array.isArray(credsData.slots)) ? credsData : null;
  if (cr) {
    const writable = cr.slots.filter(s => s && s.writable !== false);
    const setN = writable.filter(s => s.set).length;
    rows.push(orchLink("slots", t("orch.slots", "credential slots set"),
      `${setN}/${writable.length}`));
  }
  // skills (car 5, 0.5.48): the remote catalog's per-skill availability —
  // available X / Y plus an expandable one-line-per-skill breakdown. The
  // payload comes from GET /api/skills/remote and is shape-checked; a
  // server without the computed fields (or a failed fetch) hides this block
  // and the honest capabilities count below keeps the row.
  const remote = remoteSkillRows();
  if (remote) {
    const total = remote.length;
    const avail = remote.filter(s => s.available).length;
    rows.push(orchLink("skills", t("orch.skills", "skills installed"),
      t("orch.skillsAvail", "available {a} / {n}")
        .replace("{a}", String(avail)).replace("{n}", String(total))));
    rows.push(orchSkillsDetail(remote));
  } else if (Array.isArray(c.skills)) {
    rows.push(orchLink("skills", t("orch.skills", "skills installed"),
      String(c.skills.length)));
  }
  list.innerHTML = rows.join("");
}

/* car 5 (0.5.48): GET /api/skills/remote — the availability projection.
   Shape discipline (actChip posture): every row must carry the seven catalog
   fields plus a boolean `available` and a string `missing[]`; anything else
   means the payload predates this car and the availability block simply does
   not render — never an invented number. */
let skillsRemoteData = null;

function remoteSkillRows() {
  const d = skillsRemoteData;
  if (!d || typeof d !== "object" || !Array.isArray(d.skills)) return null;
  const bad = d.skills.some(s => !s || typeof s !== "object"
    || typeof s.available !== "boolean" || !Array.isArray(s.missing)
    || s.missing.some(m => typeof m !== "string"));
  return bad ? null : d.skills;
}

async function loadSkillsRemote() {
  try {
    const res = await fetch("/api/skills/remote");
    if (!res.ok) throw new Error("http " + res.status);
    const d = await res.json();
    skillsRemoteData = (d && typeof d === "object" && Array.isArray(d.skills))
      ? d : null;
  } catch (e) {
    skillsRemoteData = null;        // fail-soft: the row falls back, honestly
  }
  renderOrch();
}

/* the expandable breakdown under the skills row: one line per catalog skill
   ("name — available ✓" / "missing ✗"), with the missing requirement names
   on a small grey secondary line — troubleshooting material, never a label. */
function orchSkillsDetail(rows) {
  const lines = rows.map(s => {
    const name = String(s.name || s.slug || "");
    const state = s.available
      ? t("orch.skOk", "available ✓")
      : t("orch.skNo", "missing ✗");
    const miss = (s.available || !s.missing.length) ? "" :
      `<div class="stat-note orch-skill-miss">` +
      `${escapeHtml(t("orch.skMissing", "missing: {m}")
        .replace("{m}", s.missing.join(", ")))}</div>`;
    return `<div class="orch-skill" data-skill="${escapeHtml(String(s.slug || ""))}">` +
      `<div class="ctl-row"><label>${escapeHtml(name)}</label>` +
      `<span class="stat-note orch-skill-state">${escapeHtml(state)}</span></div>${miss}</div>`;
  }).join("");
  return `<details class="orch-skills" id="orchSkills">` +
    `<summary>${escapeHtml(
      t("orch.skillsDetail", "Skill availability (remote catalog)"))}</summary>` +
    lines + `</details>`;
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
let statusTimer = null, usageTimer = null, missionTimer = null, controlTimer = null;

async function refreshStatus() {
  try {
    statusData = await fetch("/api/status").then(r => r.json());
    renderStatus();
    document.getElementById("ts").textContent = statusData.time_utc || "";
    // stream lines rendered before status arrived still carry placeholders
    // (e.g. the boot event's {v}) — repaint them now that version is known
    refreshEventTexts();
    renderOvState();           // round C (P4): the sentence follows the data
    renderOvTech();            // round C (P4): so do the technical rows
  } catch (e) {
    document.getElementById("ts").textContent = t("tool.statusErr", "status error");
  }
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
  // car 7 (D11): the leash row (tail + guardrails) follows the same cadence
  clearInterval(controlTimer);
  controlTimer = setInterval(refreshControl, ms);
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

/* car 7 (D11): the leash gate's refusal body -> one localized line.
   The server sends {error: code, details: [en], params: {...}}; the packs
   carry gate.paused / gate.budgetUsd / gate.budgetTokens with {used}/{limit}
   slots, so the numbers stay exact while the sentence translates. Any other
   error keeps the server's verbatim detail (the standing contract). */
function gateDetail(body) {
  const code = body && body.error;
  const key = code === "paused" || code === "budget_usd"
    || code === "budget_tokens" ? "gate." + code : "";
  const fallback = (body && body.details && body.details[0])
    || (body && body.error) || "error";
  if (!key) return fallback + "";
  const p = (body && typeof body.params === "object" && body.params) || {};
  let out = t(key, fallback);
  if (code === "budget_usd") {
    out = out.replace("{used}", Number(p.used || 0).toFixed(2))
             .replace("{limit}", Number(p.limit || 0).toFixed(2));
  } else if (code === "budget_tokens") {
    out = out.replace("{used}", Number(p.used || 0).toLocaleString("en-US"))
             .replace("{limit}", Number(p.limit || 0).toLocaleString("en-US"));
  }
  return out;
}

/* localized "<panel>: <state>" advisory reason line (server sends raw ids) */
function reasonHtml(r) {
  const m = String(r).match(/^([a-z0-9_]+):\s*(.+)$/i);
  if (!m) return escapeHtml(r);
  return escapeHtml(t("panel." + m[1], m[1]) + ": " + t("state." + m[2], m[2]));
}
function reasonLi(r) { return `<li>&gt; ${reasonHtml(r)}</li>`; }

/* ------------------------------------------------------------ community -- */
/* Product GitHub Discussions panel (delta4): the SERVER reads the repo's
   discussions anonymously through GET /api/discussions (600s cache, one
   outbound fetch at a time); this side only renders the payload:
     - available:false  -> one honest note with the server's category reason
                           mapped through comm.reason.* (never a URL, never a
                           token); the header link to GitHub stays usable;
     - available:true   -> cards with category / title / author / replies /
                           relative time, a category filter built FROM the
                           data, and an honest empty state when there is
                           nothing to show.
   Lazy: the first fetch happens when the view opens (setView), a manual
   REFRESH sends ?fresh=1 to bust the server cache. All copy is t(key, fb). */
let commData = null;   // /api/discussions payload (null = never loaded)
let commCat = "";      // active category filter ("" = all categories)
let commCats = null;   // composer categories payload (null = never loaded)
let commThread = null; // discussion number whose thread is open (null = none)
let commThreadData = null;  // {number, replies[]} for the open thread

function commReasonText(reason) {
  const word = /^[a-z_]+$/.test(String(reason || "")) ? reason : "unknown";
  return t("comm.reason." + word,
    t("comm.reason.unknown",
      "discussions could not be loaded right now"));
}

/* one shared fetch for every POST face: Bearer token from the Access card,
   the server's {error, reason, detail} ladder mapped to honest copy. Never
   echoes the token — only the reason word reaches the status line. */
async function commPost(path, payload) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) return { ok: false, reason: "need_token" };
  try {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (res.ok && body && body.ok !== false) return { ok: true, body };
    return { ok: false,
             reason: /^[a-z_]+$/.test(String(body.reason || ""))
               ? body.reason : "upstream_error",
             detail: String(body.detail || body.error || "") };
  } catch (e) {
    return { ok: false, reason: "network",
             detail: String(e && e.message ? e.message : e).slice(0, 120) };
  }
}

function commErrorText(res) {
  if (res.reason === "need_token") {
    return t("comm.needToken",
      "access token required — set it in Settings, Access");
  }
  if (res.reason === "busy" || res.reason === "rate_limited") {
    const s = (res.body && res.body.retry_s) || 0;
    const base = commReasonText(res.reason);
    return s ? base.replace("{s}", String(s)) : base;
  }
  if (res.detail && res.reason !== "no_token" && res.reason !== "no_scope") {
    return commReasonText(res.reason) + " (" + res.detail + ")";
  }
  return commReasonText(res.reason);
}

function commRelTime(iso) {
  const ts = Date.parse(iso || "");
  if (!Number.isFinite(ts)) return "";
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return t("comm.justNow", "just now");
  if (s < 3600) return t("comm.agoM", "{n}m ago")
    .replace("{n}", String(Math.floor(s / 60)));
  if (s < 86400) return t("comm.agoH", "{n}h ago")
    .replace("{n}", String(Math.floor(s / 3600)));
  if (s < 86400 * 30) return t("comm.agoD", "{n}d ago")
    .replace("{n}", String(Math.floor(s / 86400)));
  return new Date(ts).toLocaleDateString();
}

async function refreshCommunity(fresh) {
  const list = document.getElementById("commList");
  if (list && !commData) {
    list.innerHTML = `<div class="stat-note">${escapeHtml(
      t("comm.loading", "loading discussions\u2026"))}</div>`;
  }
  try {
    const r = await fetch("/api/discussions" + (fresh ? "?fresh=1" : ""));
    const d = r.ok ? await r.json() : null;
    commData = (d && typeof d.available === "boolean") ? d
      : { available: false, discussions: [], checked_at: "",
          reason: "unknown" };
  } catch (e) {
    commData = { available: false, discussions: [], checked_at: "",
                 reason: "unknown" };
  }
  renderCommunity();
}

function renderCommunity() {
  const list = document.getElementById("commList");
  const badge = document.getElementById("commBadge");
  const note = document.getElementById("commNote");
  const sel = document.getElementById("commCat");
  const checked = document.getElementById("commChecked");
  if (!list || !commData) return;            // not loaded yet: leave as-is

  if (!commData.available) {
    if (badge) badge.textContent = "\u2013";
    if (note) note.textContent = "";
    if (sel) { sel.innerHTML = ""; sel.disabled = true; }
    if (checked) checked.hidden = true;
    list.innerHTML = `<div class="gal-empty">${escapeHtml(
      commReasonText(commData.reason))}</div>`;
    return;
  }

  const rows = Array.isArray(commData.discussions) ? commData.discussions : [];
  if (badge) badge.textContent = String(rows.length);
  if (sel) sel.disabled = false;

  // category filter: built FROM the data (never seeded), selection survives
  // a refresh while it still exists, otherwise it resets to "all".
  const cats = [];
  rows.forEach(r => {
    const c = String(r.category || "");
    if (c && cats.indexOf(c) < 0) cats.push(c);
  });
  if (commCat && cats.indexOf(commCat) < 0) commCat = "";
  if (sel) {
    const want = [""].concat(cats);
    const have = Array.prototype.map.call(sel.options, o => o.value);
    if (want.join("\u0000") !== have.join("\u0000")) {
      sel.innerHTML = `<option value="">${escapeHtml(
        t("comm.all", "All categories"))}</option>` +
        cats.map(c => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    }
    sel.value = commCat;
  }

  const shown = commCat
    ? rows.filter(r => String(r.category || "") === commCat) : rows;
  if (!shown.length) {
    // 0.5.54: the empty state carries an IN-APP entry (the audit's top
    // community pain point) — the external link stays in the header.
    list.innerHTML = `<div class="gal-empty">${escapeHtml(commCat
      ? t("comm.noCat", "no discussion in this category yet")
      : t("comm.empty", "no discussions yet \u2014 start the first one"))}</div>` +
      (commCat ? "" : `<div class="comm-actions"><button class="btn primary" ` +
        `id="commStart" type="button">${escapeHtml(
          t("comm.start", "START A DISCUSSION"))}</button></div>`);
    const start = document.getElementById("commStart");
    if (start) start.addEventListener("click", () => commOpenComposer());
  } else {
    list.innerHTML = shown.map(r => {
      const title = String(r.title || "");
      const url = String(r.url || "");
      // link only to the canonical github.com form the server passed through
      const head = /^https:\/\/github\.com\//.test(url)
        ? `<a class="comm-title" href="${escapeHtml(url)}" target="_blank" ` +
          `rel="noopener">${escapeHtml(title)}</a>`
        : `<span class="comm-title">${escapeHtml(title)}</span>`;
      const n = Number(r.comments) || 0;
      const replies = n === 1
        ? t("comm.reply", "{n} reply").replace("{n}", "1")
        : t("comm.replies", "{n} replies").replace("{n}", String(n));
      const bits = [
        String(r.category || ""),
        t("comm.by", "by {a}").replace("{a}", String(r.author || "\u2014")),
        replies,
        commRelTime(r.updated_at),
      ].filter(Boolean);
      const num = Number(r.number);
      const canOpen = Number.isInteger(num) && num > 0;
      const openBtn = canOpen
        ? `<button class="btn comm-open" type="button" data-num="${num}">` +
          escapeHtml(Number(commThread) === num
            ? t("comm.hideThread", "HIDE THREAD")
            : t("comm.showThread", "REPLIES")) + `</button>`
        : "";
      const thread = (canOpen && Number(commThread) === num)
        ? commThreadHtml(num) : "";
      return `<div class="ctl-row comm-row"><div class="comm-main">` +
        head + `<div class="stat-note comm-meta">${escapeHtml(bits.join(" \u00b7 "))}</div>` +
        `<div class="comm-actions">${openBtn}</div>${thread}` +
        `</div></div>`;
    }).join("");
  }

  if (checked) {
    const when = commRelTime(commData.checked_at);
    checked.textContent = when
      ? t("comm.checked", "checked {t}").replace("{t}", when) : "";
    checked.hidden = !checked.textContent;
  }
}

/* ------------------------------------------------- in-app posting (0.5.54) --
   The composer and the per-thread reply box are the console's WRITE faces:
   both POST through the Bearer token the owner typed in Access, both map the
   server's reason words to pack copy, and both say exactly what is missing
   (no token / no Discussions scope) instead of showing a dead form. */
function commComposerEl() { return document.getElementById("commComposer"); }

function commSetPostMsg(text, isErr) {
  const el = document.getElementById("commPostMsg");
  if (!el) return;
  el.className = "save-state" + (isErr ? " err" : "");
  el.textContent = text || "";
}

async function commLoadCategories(force) {
  if (!force && commCats && commCats.available) return commCats;
  try {
    const r = await fetch("/api/discussions/categories" + (force ? "?fresh=1" : ""));
    const d = await r.json();
    commCats = (d && typeof d.available === "boolean") ? d
      : { available: false, categories: [], reason: "unknown" };
  } catch (e) {
    commCats = { available: false, categories: [], reason: "network" };
  }
  return commCats;
}

async function commOpenComposer() {
  const card = commComposerEl();
  if (!card) return;
  card.hidden = false;
  commSetPostMsg("", false);
  const hint = document.getElementById("commSetupHint");
  const sel = document.getElementById("commNewCat");
  if (sel) { sel.innerHTML = ""; sel.disabled = true; }
  if (hint) { hint.hidden = false;
    hint.textContent = t("comm.loadingCats", "loading categories\u2026"); }
  const payload = await commLoadCategories(true);
  if (!payload.available) {
    // no_token / no_scope are SETUP states: the copy names the fix, and the
    // composer stays visible (never a silent no-op button).
    if (hint) hint.textContent = payload.reason === "no_token"
      ? t("comm.hint.no_token",
          "posting needs the community GitHub token — set AIDOG_COMMUNITY_PAT " +
          "in Settings, Credentials, then try again")
      : commReasonText(payload.reason);
    if (sel) sel.disabled = true;
    return;
  }
  const cats = Array.isArray(payload.categories) ? payload.categories : [];
  if (sel) {
    sel.innerHTML = cats.map(c =>
      `<option value="${escapeHtml(c.slug || c.id)}">${escapeHtml(c.name || c.slug)}</option>`
    ).join("");
    sel.disabled = cats.length === 0;
  }
  if (hint) hint.hidden = cats.length > 0;
  if (hint && !cats.length) {
    hint.textContent = t("comm.hint.noCats",
      "no discussion categories available yet");
  }
}

function commCloseComposer() {
  const card = commComposerEl();
  if (card) card.hidden = true;
  commSetPostMsg("", false);
  const title = document.getElementById("commTitle");
  const body = document.getElementById("commBody");
  if (title) title.value = "";
  if (body) body.value = "";
}

async function commSubmitPost() {
  const title = (document.getElementById("commTitle") || {}).value || "";
  const body = (document.getElementById("commBody") || {}).value || "";
  const sel = document.getElementById("commNewCat");
  const category = sel ? sel.value : "";
  if (!title.trim() || !body.trim()) {
    commSetPostMsg(t("comm.needFields", "title and message are required"), true);
    return;
  }
  if (!category) {
    commSetPostMsg(t("comm.needCat", "pick a category first"), true);
    return;
  }
  commSetPostMsg(t("comm.posting", "posting\u2026"), false);
  const res = await commPost("/api/discussions/create",
    { title: title.trim(), body: body.trim(), category });
  if (!res.ok) {
    commSetPostMsg(commErrorText(res), true);
    if (res.reason === "no_token" || res.reason === "no_scope") {
      const hint = document.getElementById("commSetupHint");
      if (hint) { hint.hidden = false;
        hint.textContent = commErrorText(res); }
    }
    return;
  }
  commSetPostMsg(t("comm.posted", "posted \u2014 opening the discussion"), false);
  const url = res.body && /^https:\/\/github\.com\//.test(String(res.body.url || ""))
    ? String(res.body.url) : "";
  commCloseComposer();
  commCats = null;              // a fresh list on the next open
  await refreshCommunity(true); // the new row is in the server's fresh read
  if (url) window.open(url, "_blank", "noopener");
}

function commThreadHtml(num) {
  const rows = (commThreadData && Number(commThreadData.number) === Number(num)
    && Array.isArray(commThreadData.replies)) ? commThreadData.replies : null;
  let inner;
  if (!rows) {
    inner = `<div class="stat-note">${escapeHtml(
      t("comm.threadLoading", "loading replies\u2026"))}</div>`;
  } else if (!rows.length) {
    inner = `<div class="stat-note">${escapeHtml(
      t("comm.noReplies", "no replies yet \u2014 start the conversation"))}</div>`;
  } else {
    inner = rows.map(r => {
      const bits = [
        t("comm.by", "by {a}").replace("{a}", String(r.author || "\u2014")),
        commRelTime(r.created_at),
      ].filter(Boolean);
      return `<div class="comm-reply">${escapeHtml(String(r.body || ""))}` +
        `<div class="stat-note comm-meta">${escapeHtml(bits.join(" \u00b7 "))}</div></div>`;
    }).join("");
  }
  return `<div class="comm-thread" data-thread="${num}">${inner}` +
    `<div class="ctl-row"><textarea rows="3" maxlength="2000" ` +
    `id="commReplyBody${num}" data-i18n-ph="comm.replyPh" ` +
    `placeholder="write a reply (markdown)"></textarea></div>` +
    `<div class="comm-actions">` +
    `<button class="btn primary comm-send" type="button" data-num="${num}">` +
    escapeHtml(t("comm.sendReply", "REPLY")) + `</button>` +
    `<span class="save-state" id="commReplyMsg${num}" role="status"></span>` +
    `</div></div>`;
}

async function commOpenThread(num) {
  const box = document.getElementById("commReplyMsg" + num);
  commThreadData = null;
  try {
    const r = await fetch(`/api/discussions/replies?number=${num}&fresh=1`);
    const d = await r.json();
    commThreadData = (d && d.available === true)
      ? { number: num, replies: Array.isArray(d.replies) ? d.replies : [] }
      : { number: num, replies: [], reason: d && d.reason };
    if (d && d.available === false && box) {
      box.className = "save-state err";
      box.textContent = commReasonText(d.reason);
    }
  } catch (e) {
    commThreadData = { number: num, replies: [], reason: "network" };
  }
  renderCommunity();
}

async function commSendReply(num) {
  const input = document.getElementById("commReplyBody" + num);
  const box = document.getElementById("commReplyMsg" + num);
  const body = input ? input.value : "";
  if (!box) return;
  if (!body.trim()) {
    box.className = "save-state err";
    box.textContent = t("comm.needBody", "write a reply first");
    return;
  }
  box.className = "save-state";
  box.textContent = t("comm.sending", "sending\u2026");
  const res = await commPost("/api/discussions/comment",
    { number: Number(num), body: body.trim() });
  if (!res.ok) {
    box.className = "save-state err";
    box.textContent = commErrorText(res);
    return;
  }
  if (input) input.value = "";
  await commOpenThread(num);   // the new comment is in the fresh read
  //  the thread re-render replaced the status node — re-query it and confirm
  //  AFTER the refresh, so "replied" survives instead of flashing away
  const fresh = document.getElementById("commReplyMsg" + num);
  if (fresh) {
    fresh.className = "save-state";
    fresh.textContent = t("comm.replied", "replied");
  }
}

/* wire the write faces once (they live in static markup, never re-created) */
(function wireCommunityWrites() {
  const on = (id, ev, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener(ev, fn);
  };
  on("commNew", "click", () => commOpenComposer());
  on("commCancel", "click", () => commCloseComposer());
  on("commPost", "click", () => commSubmitPost());
  // delegate: REPLIES toggle, thread REPLY button — both ride the list
  const list = document.getElementById("commList");
  if (list) list.addEventListener("click", async e => {
    const open = e.target.closest(".comm-open");
    if (open) {
      const num = Number(open.dataset.num);
      if (Number(commThread) === num) {
        commThread = null; commThreadData = null; renderCommunity();
      } else {
        commThread = num; await commOpenThread(num);
      }
      return;
    }
    const send = e.target.closest(".comm-send");
    if (send) await commSendReply(Number(send.dataset.num));
  });
})();

/* ---------------------------------------------------------------- views -- */
/* All nine views live in the same document; the inactive ones are hidden with
   CSS, so every poller keeps writing into real DOM and ids stay stable.
   ?view=<name> deep-links the active view (default: overview). */
const VIEWS = ["overview", "chat", "skills", "exchange", "evolution", "stream",
               "community", "sniff", "settings"];
const QS_VIEW = (params.get("view") || "").toLowerCase();
let currentView = VIEWS.includes(QS_VIEW) ? QS_VIEW : "overview";

/* ============================ Wave 6 delta2: X12/X13/X15/X16/X17 ==========
   Five parity faces, one lane:
     X17 gallery  — GET /api/artifacts lists generated images (Stream view),
                     each thumb opens the SAME single-image route
                     (/api/media/<name>); an empty dir says so honestly.
     X13 sessions — GET /api/sessions list/search/export in the CHAT view:
                     ?q= runs real FTS5 full-text search and the panel SAYS
                     which method ran (full-text vs the labelled substring
                     fallback), rows carry id/time/size/summary only, exports
                     download the server's own JSON/MD verbatim. Read-only.
     X12 prune    — the settings switch renders its state + the one-shot disk
                     advisory on the same panel (server owns every number).
     X15 effort   — settings round-trip lives in initSettingsPanels /
                     mycoreExtraPayload (opt-in, default = unchanged wire).
     X16 vision   — an attached image is resized client-side to <=1024px JPEG
                     and rides ONE chat turn; the server persists an honest
                     '[image]' marker and never base64 bytes.
   All copy is t(key, fallback): the 0.5.53 i18n completion lane added every
   pack entry (795 keys per pack), so both locales render translated text. */
let artData = null;      // X17 gallery payload (null = never loaded)
let sessData = null;     // X13 sessions payload (null = never loaded)
let pendingImage = null; // X16 {name, dataUrl} waiting for the next turn

function fmtBytes(n) {
  const v = Number(n) || 0;
  if (v < 1024) return v + " B";
  if (v < 1048576) return (v / 1024).toFixed(1) + " KB";
  return (v / 1048576).toFixed(1) + " MB";
}

/* ------------------------------------------------- X17 artifacts gallery -- */
async function refreshArtifacts() {
  try {
    const r = await fetch("/api/artifacts");
    const d = r.ok ? await r.json() : null;
    artData = (d && Array.isArray(d.images)) ? d : null;
  } catch (e) { artData = null; }
  renderArtifacts();
}

function renderArtifacts() {
  const box = document.getElementById("artList");
  const badge = document.getElementById("artBadge");
  if (!box) return;
  if (!artData) {                       // failed/absent read: honest, no fake
    box.innerHTML = `<div class="stat-note">${escapeHtml(
      t("art.unavailable", "artifacts unavailable"))}</div>`;
    if (badge) badge.textContent = "–";
    return;
  }
  const rows = artData.images || [];
  if (badge) badge.textContent = String(artData.count ?? rows.length);
  if (!rows.length) {
    box.innerHTML = `<div class="stat-note">${escapeHtml(
      t("art.empty", "no generated images yet — try IMAGE TRIAL in Chat"))}</div>`;
    return;
  }
  box.innerHTML = rows.map(im => {
    const url = "/api/media/" + encodeURIComponent(im.name);
    const when = im.when ? new Date(im.when * 1000).toLocaleString() : "";
    return `<div class="ctl-row art-row">` +
      `<a href="${url}" target="_blank" rel="noopener" ` +
      `title="${escapeHtml(t("art.open", "open full size"))}">` +
      `<img class="art-thumb" src="${url}" alt="${escapeHtml(im.name)}" ` +
      `loading="lazy"></a>` +
      `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
      `${escapeHtml(im.name)}</label>` +
      `<span class="stat-note" style="margin-top:0;">` +
      `${escapeHtml(when)} · ${fmtBytes(im.size)}</span></div>`;
  }).join("") + (artData.truncated
    ? `<div class="stat-note">${rows.length} of ${artData.count} shown ` +
      `(newest first)</div>` : "");
}

/* --------------------------------------------------- X13 sessions panel --- */
async function refreshSessions(q) {
  const list = document.getElementById("sessList");
  if (list) {
    list.innerHTML = `<div class="stat-note">${escapeHtml(
      t("sess.loading", "loading…"))}</div>`;
  }
  try {
    const url = "/api/sessions" +
      (q ? "?q=" + encodeURIComponent(q) : "");
    const r = await fetch(url);
    const d = r.ok ? await r.json() : null;
    sessData = (d && Array.isArray(d.sessions)) ? d : null;
  } catch (e) { sessData = null; }
  renderSessions(q);
}

function renderSessions(q) {
  const list = document.getElementById("sessList");
  const badge = document.getElementById("sessBadge");
  const pruneEl = document.getElementById("sessPrune");
  if (!list) return;
  if (!sessData) {
    if (badge) badge.textContent = "–";
    list.innerHTML = `<div class="stat-note">${escapeHtml(
      t("sess.unavailable", "sessions unavailable"))}</div>`;
    if (pruneEl) pruneEl.hidden = true;
    return;
  }
  const rows = sessData.sessions || [];
  if (badge) badge.textContent = String(sessData.count ?? rows.length);
  if (!rows.length) {
    // honest branch on how the SERVER ran the search: real FTS5 vs the
    // labelled substring fallback (search is absent without ?q=).
    const sm = sessData.search;
    const label = !sm ? t("sess.methodFts", "full-text (fts5)")
      : (sm.fallback || sm.method !== "fts5"
        ? t("sess.methodSub", "substring fallback")
        : t("sess.methodFts", "full-text (fts5)"));
    list.innerHTML = `<div class="stat-note">${escapeHtml(q
      ? t("sess.noMatch", "no session matches") + " (" + label + ")"
      : t("sess.empty", "no sessions yet"))}</div>`;
  } else {
    list.innerHTML = rows.map(s => {
      const sid = String(s.id || "");
      const bits = [sid,
        s.when ? new Date(s.when * 1000).toLocaleString() : "",
        fmtBytes(s.size)];
      const hits = (s.hits != null) ? ` · ${s.hits} hit(s)` : "";
      const partial = s.partial ? " · " + t("sess.partial", "partial scan") : "";
      const exp = fmt => `<button class="btn" type="button" ` +
        `data-sess-exp="${escapeHtml(sid)}" data-sess-fmt="${fmt}" ` +
        `aria-label="${escapeHtml("export session " + sid + " as " + fmt)}" ` +
        `style="padding:2px 9px;font-size:12.5px;">${fmt.toUpperCase()}</button>`;
      // X20: make THIS row the live chat session (the server answers an
      // honest no-op when it already is one).
      const open = `<button class="btn" type="button" ` +
        `data-sess-open="${escapeHtml(sid)}" ` +
        `aria-label="${escapeHtml("open session " + sid + " as the live chat session")}" ` +
        `style="padding:2px 9px;font-size:12.5px;">${t("sess.openBtn", "OPEN")}</button>`;
      return `<div class="ctl-row">` +
        `<label style="flex:1;min-width:0;overflow-wrap:anywhere;">` +
        `${escapeHtml(bits.filter(Boolean).join(" · "))}` +
        `${escapeHtml(hits + partial)}</label>` +
        `<span class="stat-note" style="margin-top:0;overflow-wrap:anywhere;">` +
        `${escapeHtml(s.summary || "")}</span>` +
        open + exp("json") + exp("md") + `</div>`;
    }).join("");
    if (sessData.search) {
      const m = sessData.search;
      // which method actually ran — never a baked-in "no index" claim
      const label = (m.fallback || m.method !== "fts5")
        ? t("sess.methodSub", "substring fallback")
        : t("sess.methodFts", "full-text (fts5)");
      const why = (m.fallback && m.reason) ? ` (${m.reason})` : "";
      list.innerHTML += `<div class="stat-note">${escapeHtml(
        t("sess.matchNote", "match: ") + label + why +
        (m.case_insensitive ? ", case-insensitive" : "") +
        " · " + sessData.count + " session(s)")}</div>`;
    }
  }
  // X12: prune state + the one-shot advisory, server-owned wording
  const pr = sessData.prune;
  if (pruneEl) {
    if (!pr) { pruneEl.hidden = true; return; }
    const bits = [pr.enabled
      ? t("sess.pruneOn", "session auto-prune: on · keep {n} day{s}")
          .replace("{n}", pr.days).replace("{s}", pr.days === 1 ? "" : "s")
      : t("sess.pruneOff", "session auto-prune: off (Settings → Model)")];
    if (pr.enabled && pr.ran_at) {
      bits.push(t("sess.pruneRun", "last run {t} · removed {n} · freed {b}")
        .replace("{t}", new Date(pr.ran_at * 1000).toLocaleString())
        .replace("{n}", pr.removed).replace("{b}", fmtBytes(pr.freed)));
    }
    if (pr.errors) {
      bits.push(t("sess.pruneErr", "{n} file error(s)").replace("{n}", pr.errors));
    }
    let txt = bits.join(" · ");
    if (pr.advisory) txt += " — " + pr.advisory;
    pruneEl.textContent = txt;
    pruneEl.className = pr.advisory ? "stat-note warn" : "stat-note";
    pruneEl.hidden = false;
  }
}

/* X20 live session switch: OPEN a listed session as the live chat session.
   The server does the crash-safe rotation (old live -> archive/, target ->
   live); on success the transcript and the X14 context line repaint from the
   NEW live file, and the list refetches (the opened row is gone — it became
   the live session). */
async function chatReload() {
  const log = document.getElementById("chatLog");
  if (log) {
    log.innerHTML = `<div class="chat-empty" id="chatEmpty">` +
      `${escapeHtml(t("chat.empty", "no messages yet"))}</div>`;
  }
  const meta = document.getElementById("chatMeta");
  if (meta) meta.textContent = "";
  await loadChatHistory();          // history + context ride one fetch
}

async function sessionResume(id) {
  const msg = document.getElementById("sessMsg");
  const say = (kind, text) => {
    if (msg) { msg.className = "save-state " + kind; msg.textContent = text; }
  };
  if (chatBusy) {                   // never swap the file under a live turn
    say("err", t("chat.qBusy", "wait for the current turn"));
    return;
  }
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    say("err", t("chat.needToken", "access token required (set it in Settings)"));
    return;
  }
  try {
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ action: "resume", id }) });
    const body = await r.json().catch(() => ({}));
    if (r.status === 401) {
      say("err", t("chat.badToken", "401 · unauthorized")); return;
    }
    if (!r.ok) {
      say("err", String((body.details || [body.error || "error"])[0] || ""));
      return;
    }
    if (body.resumed === false) {           // honest server no-op
      say("", t("sess.live", "already the live session"));
      return;
    }
    await chatReload();
    const q = ((document.getElementById("sessSearch") || {}).value || "").trim();
    refreshSessions(q);                     // the opened row is now live
    say("", t("sess.opened", "session opened ✓"));
    chatNote("ok", t("sess.opened", "session opened ✓"));
  } catch (e) {
    say("err", t("cron.fail", "request failed — ") +
      String((e && e.message) || e).slice(0, 80));
  }
}

async function sessionExport(id, fmt) {
  const msg = document.getElementById("sessMsg");
  try {
    const r = await fetch("/api/sessions?id=" + encodeURIComponent(id) +
                          "&format=" + encodeURIComponent(fmt));
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      if (msg) {
        msg.className = "save-state err";
        msg.textContent = String(
          (body.details || [body.error || "error"])[0] || "");
      }
      return;
    }
    const blob = await r.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = id.split("/").pop() + (fmt === "md" ? ".md" : ".json");
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
    if (msg) {
      msg.className = "save-state";
      msg.textContent = t("sess.exported", "exported ✓");
    }
  } catch (e) {
    if (msg) {
      msg.className = "save-state err";
      msg.textContent = t("cron.fail", "request failed — ") +
        String((e && e.message) || e).slice(0, 80);
    }
  }
}

/* ------------------------------------------------------ X16 vision input -- */
async function chatImgPrepare(file) {
  const dataUrl = await new Promise((res, rej) => {
    const fr = new FileReader();
    fr.onload = () => res(fr.result);
    fr.onerror = () => rej(new Error("read failed"));
    fr.readAsDataURL(file);
  });
  const img = await new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => rej(new Error("decode failed"));
    im.src = dataUrl;
  });
  const maxDim = 1024;                  // bounded before it ever hits the wire
  let w = img.naturalWidth || img.width || 1;
  let h = img.naturalHeight || img.height || 1;
  const scale = Math.min(1, maxDim / Math.max(w, h));
  w = Math.max(1, Math.round(w * scale));
  h = Math.max(1, Math.round(h * scale));
  const cv = document.createElement("canvas");
  cv.width = w; cv.height = h;
  const ctx = cv.getContext("2d");
  if (!ctx) throw new Error("no 2d context");
  ctx.drawImage(img, 0, 0, w, h);
  return { name: String(file.name || "image").slice(0, 120),
           dataUrl: cv.toDataURL("image/jpeg", 0.85) };
}

function chatImgNote(text, warn) {
  /* X16/X21: ONE chip element for every intake path. `warn` colours the note
     (honest hint), otherwise it is the ready state. */
  const el = document.getElementById("chatImgNote");
  if (!el) return;
  el.hidden = false;
  el.className = warn ? "stat-note warn" : "stat-note";
  el.textContent = text;
}

async function chatImgSet(file, inputEl) {
  /* X21: the single preparation pipeline shared by the file input, a
     clipboard paste and a drag-and-drop: image/* SINGLE file -> FileReader ->
     canvas thumbnail (≤1024px JPEG) -> one-shot `pendingImage` chip that
     rides the next message (X16 contract, unchanged). Anything else answers
     honestly and leaves the composer TEXT untouched — a rejected paste never
     clears what the user was typing. `inputEl` (the file input, when that is
     the source) is reset on a read failure so the same file can be retried. */
  if (!file) {
    pendingImage = null;
    const noteEl = document.getElementById("chatImgNote");
    if (noteEl) noteEl.hidden = true;
    return false;
  }
  if (!String(file.type || "").startsWith("image/")) {
    // non-image file (paste/drop bypasses the input's accept list)
    chatImgNote(t("chat.imgOnly", "only images can be attached"), true);
    return false;
  }
  try {
    pendingImage = await chatImgPrepare(file);
    chatImgNote(t("chat.imgReady", "image ready: ") + pendingImage.name +
      t("chat.imgRides", " — goes with your next message"), false);
    return true;
  } catch (e) {
    pendingImage = null;
    if (inputEl) inputEl.value = "";
    chatImgNote(t("chat.imgBad",
      "could not read that image — pick a PNG/JPEG/WEBP"), true);
    return false;
  }
}

function chatImgFiles(dt) {
  /* X21: files out of a DataTransfer (paste clipboardData / drop
     dataTransfer), via .files first and the item list as fallback. */
  if (!dt) return [];
  if (dt.files && dt.files.length) return Array.from(dt.files);
  const out = [];
  for (const it of Array.from(dt.items || [])) {
    if (it.kind === "file") {
      const f = it.getAsFile();
      if (f) out.push(f);
    }
  }
  return out;
}

async function chatImgIntake(dt) {
  const files = chatImgFiles(dt);
  if (files.length === 1) return chatImgSet(files[0], null);
  chatImgNote(files.length
    ? t("chat.imgOne", "only one image can be attached at a time")
    : t("chat.imgOnly", "only images can be attached"), true);
  return false;
}

/* --- wiring (every listener guarded: an absent element is a no-op) --- */
const sessGoEl = document.getElementById("sessGo");
if (sessGoEl) sessGoEl.addEventListener("click", () => {
  const q = ((document.getElementById("sessSearch") || {}).value || "").trim();
  refreshSessions(q);
});
const sessSearchEl = document.getElementById("sessSearch");
if (sessSearchEl) sessSearchEl.addEventListener("keydown", e => {
  if (e.key === "Enter") {
    e.preventDefault();
    refreshSessions(String(sessSearchEl.value || "").trim());
  }
});
const sessAllEl = document.getElementById("sessAll");
if (sessAllEl) sessAllEl.addEventListener("click", () => {
  const box = document.getElementById("sessSearch");
  if (box) box.value = "";
  refreshSessions("");
});
const sessListEl = document.getElementById("sessList");
if (sessListEl) sessListEl.addEventListener("click", e => {
  if (!e.target || !e.target.closest) return;
  const o = e.target.closest("[data-sess-open]");       // X20 resume
  if (o) { sessionResume(o.getAttribute("data-sess-open")); return; }
  const b = e.target.closest("[data-sess-exp]");
  if (b) sessionExport(b.getAttribute("data-sess-exp"),
                       b.getAttribute("data-sess-fmt"));
});
const chatImgEl = document.getElementById("chatImg");
if (chatImgEl) chatImgEl.addEventListener("change", () => {
  // X16 intake (file picker) — same chatImgSet pipeline as paste/drop (X21)
  const file = chatImgEl.files && chatImgEl.files[0];
  chatImgSet(file, chatImgEl);
});

/* X21 paste: image files from the clipboard enter the SAME pipeline; a text
   paste is left completely alone (no preventDefault, composer untouched). */
const chatInputEl = document.getElementById("chatInput");
if (chatInputEl) chatInputEl.addEventListener("paste", e => {
  const dt = e.clipboardData || (typeof window !== "undefined"
    ? window.clipboardData : null);
  const files = chatImgFiles(dt);
  if (!files.length) return;              // ordinary text paste — default
  e.preventDefault();                     // a file must never corrupt text
  chatImgIntake(dt);
});

/* X21 drag-and-drop over the CHAT card: Files-only drag is accepted, the
   drop feeds the same pipeline; anything else falls through to the browser. */
const chatDropZone = document.querySelector("#view-chat .card.chat");
if (chatDropZone) {
  const filesOver = e => !!(e && e.dataTransfer &&
    Array.from(e.dataTransfer.types || []).indexOf("Files") >= 0);
  chatDropZone.addEventListener("dragover", e => {
    if (!filesOver(e)) return;
    e.preventDefault();
    try { e.dataTransfer.dropEffect = "copy"; } catch (err) { /* read-only */ }
  });
  chatDropZone.addEventListener("drop", e => {
    if (!filesOver(e)) return;
    e.preventDefault();
    chatImgIntake(e.dataTransfer);
  });
}

/* ------------------------------------------------------------- SNIFF ---- */
/* SENSE board (SNIFF car): sources / rules / findings / controls, the fourth
   sidebar segment. Read-only towards the outside world: GET renders local
   state, POST runs ONE read-only sweep or marks a finding acknowledged —
   mycore.sniff carries no actuation path at all (the core suite asserts the
   absence). Config edits ride the same validated settings merge every other
   panel uses, always as the FULL partition (the rule grammar validates a
   patch against the sources carried in the same document, and lists are
   replaced wholesale by the server merge).
   Two reads: GET /api/sniff = the honest board (statuses, findings),
   GET /api/settings mycore.sniff = the stored source configs (url, slot
   NAME, entity patterns) which the board deliberately does not echo. */
let sniffData = null;   // GET /api/sniff payload (null = never loaded)
let sniffCfg = null;    // mycore.sniff partition as stored (null = not loaded)

function sniffCfgBlock() {
  return (sniffCfg && typeof sniffCfg === "object") ? sniffCfg : {};
}

function sniffCfgList(key) {
  const val = sniffCfgBlock()[key];
  return Array.isArray(val) ? val : [];
}

function sniffNote(kind, text) {
  const el = document.getElementById("sniffMsg");
  if (!el) return;
  el.className = "stat-note sniff-note " + kind;
  el.textContent = text;
}

/* age of a reading, honestly: null = never read yet, not "0s" */
function sniffAge(sec) {
  const n = Number(sec);
  if (!Number.isFinite(n) || n <= 0) return t("sniff.noRead", "no reading yet");
  if (n < 90) return Math.round(n) + "s";
  if (n < 5400) return Math.round(n / 60) + "m";
  if (n < 172800) return Math.round(n / 3600) + "h";
  return Math.round(n / 86400) + "d";
}

function sniffBadge(value) {
  const v = String(value || "pending");
  return `<span class="sniff-badge ${escapeHtml(v)}">` +
         `${escapeHtml(t("sniff.status." + v, v))}</span>`;
}

async function refreshSniff() {
  try {
    sniffData = await fetch("/api/sniff").then(r => r.json());
  } catch (e) { /* keep the last render */ }
  if (!sniffCfg) await sniffCfgRefresh();
  renderSniff();
}

/* the stored partition: refreshed before every write so an edit made in the
   Settings view (or a hand-edited file) is never clobbered by a stale copy */
async function sniffCfgRefresh() {
  try {
    const doc = await fetch("/api/settings").then(r => r.json());
    settings = Object.assign(settings, doc);
    sniffCfg = ((settings.mycore || {}).sniff) || {};
  } catch (e) { sniffCfg = sniffCfg || {}; }
  return sniffCfg;
}

function renderSniff() {
  if (!sniffData) return;
  const d = sniffData;

  // onboarding strip: expanded while no source exists yet; a header click
  // sticks (dataset.touched) so a configured board never re-flips it
  const howto = document.getElementById("sniffHowto");
  if (howto && howto.dataset.touched !== "1") {
    howto.classList.toggle("is-open", sniffCfgList("sources").length === 0);
    const head0 = document.getElementById("sniffHowtoHead");
    if (head0) head0.setAttribute("aria-expanded",
      howto.classList.contains("is-open") ? "true" : "false");
  }

  // --- sources -----------------------------------------------------------
  const srcs = Array.isArray(d.sources) ? d.sources : [];
  const cfgSrc = {};
  sniffCfgList("sources").forEach(s => {
    if (s && typeof s === "object" && s.id) cfgSrc[s.id] = s;
  });
  const srcCount = document.getElementById("sniffSrcCount");
  if (srcCount) srcCount.textContent = String(srcs.length);
  const srcBox = document.getElementById("sniffSources");
  if (srcBox) {
    srcBox.innerHTML = srcs.length ? srcs.map(s => {
      const id = String(s.id || "");
      const cfg = cfgSrc[id] || {};
      const cap = Number(cfg.rate_cap_per_min) || 0;
      const err = s.error
        ? `<div class="sniff-err">${escapeHtml(String(s.error))}</div>` : "";
      const meta = `${t("sniff.reads", "reads")} ${Number(s.reads) || 0}` +
        ` · ${t("sniff.lastRead", "last read")} ${escapeHtml(sniffAge(s.age_s))}` +
        (cap ? ` · ${t("sniff.cap", "cap {n}/min").replace("{n}", cap)}` : "");
      const on = cfg.enabled !== false;
      return `<div class="sniff-row">
        <div class="sniff-row-main">
          <span class="sniff-id">${escapeHtml(id)}</span>
          <span class="sniff-kind">${escapeHtml(String(s.kind || ""))}</span>
          ${sniffBadge(s.status)}
          <span class="sniff-meta">${escapeHtml(meta)}</span>
        </div>${err}
        <div class="sniff-row-acts">
          <button class="btn tiny" type="button" data-sniff-toggle="${escapeHtml(id)}">${
            escapeHtml(on ? t("sniff.disable", "disable")
                          : t("sniff.enable", "enable"))}</button>
          <button class="btn tiny" type="button" data-sniff-drop="${escapeHtml(id)}">${
            escapeHtml(t("sniff.remove", "remove"))}</button>
        </div></div>`;
    }).join("")
      : `<div class="sniff-empty">${escapeHtml(
          t("sniff.srcEmpty", "no sources yet — add one to start smelling"))}</div>`;
  }

  // --- rules -------------------------------------------------------------
  const rules = Array.isArray(d.rules) ? d.rules : [];
  const ruleErrs = (d.rule_errors && typeof d.rule_errors === "object")
    ? d.rule_errors : {};
  const ruleCount = document.getElementById("sniffRuleCount");
  if (ruleCount) ruleCount.textContent = String(rules.length);
  const ruleBox = document.getElementById("sniffRules");
  if (ruleBox) {
    const errRows = Object.keys(ruleErrs).map(id =>
      `<div class="sniff-row sniff-row-err">
         <span class="sniff-id">${escapeHtml(id)}</span>
         <span class="sniff-meta">${escapeHtml(t("sniff.ruleErr", "invalid rule"))}: ${
           escapeHtml(String(ruleErrs[id]))}</span></div>`).join("");
    ruleBox.innerHTML = (rules.length || errRows)
      ? rules.map(r => `<div class="sniff-row">
          <div class="sniff-row-main">
            <span class="sniff-id">${escapeHtml(String(r.id || ""))}</span>
            <span class="sniff-kind">${escapeHtml(String(r.source || ""))}</span>
            <span class="sniff-badge sev-${escapeHtml(String(r.severity || "info"))}">${
              escapeHtml(String(r.severity || "info"))}</span>
            ${r.speak ? `<span class="sniff-speak">${escapeHtml(
              t("sniff.speak", "reports"))}</span>` : ""}
            <button class="btn tiny" type="button" data-sniff-rule="${
              escapeHtml(String(r.id || ""))}">${escapeHtml(
              t("sniff.remove", "remove"))}</button>
          </div>
          <div class="sniff-when"><code>${escapeHtml(String(r.when || ""))}</code></div>
        </div>`).join("") + errRows
      : `<div class="sniff-empty">${escapeHtml(
          t("sniff.ruleEmpty", "no rules yet — a rule turns a reading into a finding"))}</div>`;
  }

  // --- findings -----------------------------------------------------------
  const finds = Array.isArray(d.findings) ? d.findings : [];
  const findCount = document.getElementById("sniffFindCount");
  if (findCount) {
    const total = Number(d.findings_total);
    findCount.textContent = String(Number.isFinite(total) ? total : finds.length);
  }
  const findBox = document.getElementById("sniffFindings");
  if (findBox) {
    findBox.innerHTML = finds.length ? finds.map(f => {
      const sig = String(f.signature || "");
      const sev = String(f.severity || "info");
      const cnt = Number(f.count) || 1;
      const acked = f.ack === true;
      const seen = t("sniff.seen", "seen {n}× · last {a}")
        .replace("{n}", String(cnt)).replace("{a}", escapeHtml(sniffAge(
          (Number(d.last_swept) || 0) - (Number(f.last_seen) || 0))));
      const payload = (f.payload && typeof f.payload === "object")
        ? Object.keys(f.payload).slice(0, 6).map(k =>
            `${k}=${escapeHtml(String(f.payload[k]))}`).join(" ") : "";
      return `<div class="sniff-row">
        <div class="sniff-row-main">
          <span class="sniff-badge sev-${escapeHtml(sev)}">${escapeHtml(sev)}</span>
          <span class="sniff-sum">${escapeHtml(String(f.summary || ""))}</span>
          <span class="sniff-meta">${escapeHtml(
            `${f.source || ""} · ${f.rule || ""}`)}</span>
        </div>
        <div class="sniff-row-sub">${escapeHtml(
          String(f.trigger || ""))}${payload ? " · " + payload : ""}</div>
        <div class="sniff-row-acts">
          <span class="sniff-meta">${seen}</span>
          ${acked ? `<span class="sniff-acked">${escapeHtml(
              t("sniff.acked", "acknowledged"))}</span>`
            : `<button class="btn tiny" type="button" data-sniff-ack="${
                escapeHtml(sig)}">${escapeHtml(t("sniff.ack", "ACK"))}</button>`}
        </div></div>`;
    }).join("")
      : `<div class="sniff-empty">${escapeHtml(
          t("sniff.findEmpty", "nothing to report yet — silence is the default"))}</div>`;
  }

  // --- controls -----------------------------------------------------------
  const dig = document.getElementById("sniffDigest");
  if (dig && document.activeElement !== dig) dig.value = String(d.digest_cap ?? 5);
  const iv = document.getElementById("sniffInterval");
  if (iv) iv.textContent = `${Number(d.interval_s) || 0}s`;
  const qh = document.getElementById("sniffQuiet");
  if (qh) qh.textContent = d.quiet_hours
    ? String(d.quiet_hours) : t("sniff.quietOff", "off");
  const pz = document.getElementById("sniffPause");
  if (pz) {
    const paused = d.pause === true;
    pz.setAttribute("aria-pressed", String(paused));
    pz.classList.toggle("primary", !paused);
    pz.textContent = paused
      ? t("sniff.resume", "RESUME") : t("sniff.pause", "PAUSE");
  }
  const note = document.getElementById("sniffSweepNote");
  if (note) {
    const last = Number(d.last_swept) || 0;
    note.hidden = !last;
    if (last) note.textContent = t("sniff.lastSweep", "last sweep {a} ago")
      .replace("{a}", sniffAge((Date.now() / 1000) - last));
  }
}

async function sniffPost(payload) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) { sniffNote("err", t("save.needToken", "token required")); return null; }
  try {
    const res = await fetch("/api/sniff", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify(payload) });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      sniffNote("err", t("save.badToken", "401 · unauthorized")); return null;
    }
    if (!res.ok) {
      const details = (body.details || [body.error || "error"])
        .map(x => String(x)).join("; ");
      sniffNote("err", details.slice(0, 140)); return null;
    }
    return body;
  } catch (e) {
    sniffNote("err", String(e).slice(0, 80)); return null;
  }
}

/* every config write sends the WHOLE partition (see the header note) */
async function sniffCfgWrite(mutate, okText) {
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) { sniffNote("err", t("save.needToken", "token required")); return false; }
  await sniffCfgRefresh();
  const block = {
    enabled: sniffCfgBlock().enabled !== false,
    pause: sniffCfgBlock().pause === true,
    interval_s: Number(sniffCfgBlock().interval_s) || 300,
    quiet_hours: String(sniffCfgBlock().quiet_hours || ""),
    digest_cap: Number(sniffCfgBlock().digest_cap) || 5,
    sources: JSON.parse(JSON.stringify(sniffCfgList("sources"))),
    rules: JSON.parse(JSON.stringify(sniffCfgList("rules"))) };
  try { mutate(block); } catch (e) {
    sniffNote("err", String(e).slice(0, 80)); return false;
  }
  try {
    const res = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "Authorization": "Bearer " + token },
      body: JSON.stringify({ mycore: { sniff: block } }) });
    const body = await res.json().catch(() => ({}));
    if (res.status === 401) {
      sniffNote("err", t("save.badToken", "401 · unauthorized")); return false;
    }
    if (!res.ok) {
      const details = (body.details || [body.error || "error"])
        .map(x => String(x)).join("; ");
      sniffNote("err", details.slice(0, 160)); return false;
    }
    await sniffCfgRefresh();
    await refreshSniff();
    if (okText) sniffNote("ok", okText);
    return true;
  } catch (e) {
    sniffNote("err", String(e).slice(0, 80)); return false;
  }
}

async function sniffSweep() {
  const body = await sniffPost({ action: "sweep" });
  if (!body) return;
  if (body.sniff) { sniffData = body.sniff; renderSniff(); }
  const rep = body.sweep || {};
  if (rep.skipped) {
    sniffNote("ok", t("sniff.skipped", "sweep skipped: {s}")
      .replace("{s}", String(rep.skipped)));
  } else if (rep.error) {
    sniffNote("err", String(rep.error).slice(0, 140));
  } else {
    const n = Number(rep.findings_new) || 0;
    sniffNote("ok", t("sniff.swept", "swept · {n} new finding(s)")
      .replace("{n}", String(n)));
  }
}

async function sniffAck(signature) {
  const body = await sniffPost({ action: "ack", signature });
  if (!body) return;
  if (body.sniff) { sniffData = body.sniff; renderSniff(); }
  sniffNote("ok", t("sniff.acked", "acknowledged"));
}

function sniffSlug(text) {
  const base = String(text || "").toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 24);
  return base || "rule";
}

function sniffNextRuleId(used, seed) {
  const base = sniffSlug(seed);
  if (!used.has(base)) return base;
  let n = 2;
  while (used.has(base + "-" + n)) n += 1;
  return base + "-" + n;
}

function wireSniff() {
  const on = (id, fn) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("click", fn);
  };
  on("sniffSweep", sniffSweep);
  on("sniffPause", async () => {
    const paused = sniffData && sniffData.pause === true;
    await sniffCfgWrite(b => { b.pause = !paused; },
      paused ? t("sniff.resumed", "sweeping resumed")
             : t("sniff.pausedText", "sweeps paused — nothing is read"));
  });
  on("sniffSrcNew", () => {
    const f = document.getElementById("sniffSrcForm");
    const a = document.getElementById("sniffSrcActions");
    if (f) f.hidden = false;
    if (a) a.hidden = true;
    sniffKindRows();
  });
  on("sniffSrcCancel", () => {
    const f = document.getElementById("sniffSrcForm");
    const a = document.getElementById("sniffSrcActions");
    if (f) f.hidden = true;
    if (a) a.hidden = false;
  });
  on("sniffRuleNew", () => {
    const f = document.getElementById("sniffRuleForm");
    const a = document.getElementById("sniffRuleActions");
    if (f) f.hidden = false;
    if (a) a.hidden = true;
    sniffRuleSources();
  });
  on("sniffRuleCancel", () => {
    const f = document.getElementById("sniffRuleForm");
    const a = document.getElementById("sniffRuleActions");
    if (f) f.hidden = true;
    if (a) a.hidden = false;
  });
  const kindSel = document.getElementById("sniffSrcKind");
  if (kindSel) kindSel.addEventListener("change", sniffKindRows);
  const howtoHead = document.getElementById("sniffHowtoHead");
  if (howtoHead) howtoHead.addEventListener("click", () => {
    const box = document.getElementById("sniffHowto");
    if (!box) return;
    box.dataset.touched = "1";
    const open = box.classList.toggle("is-open");
    howtoHead.setAttribute("aria-expanded", open ? "true" : "false");
  });
  on("sniffSrcAdd", sniffAddSource);
  on("sniffRuleAdd", sniffAddRule);
  const dig = document.getElementById("sniffDigest");
  if (dig) dig.addEventListener("change", async () => {
    const n = Math.min(50, Math.max(1, Math.round(Number(dig.value) || 0)));
    dig.value = String(n);
    await sniffCfgWrite(b => { b.digest_cap = n; },
      t("sniff.digestSaved", "digest cap = {n}").replace("{n}", String(n)));
  });
  // delegated row actions: the lists are re-rendered, so the handlers live on
  // the containers and are bound exactly once
  const view = document.getElementById("view-sniff");
  if (view) view.addEventListener("click", async e => {
    const btn = e.target.closest ? e.target.closest("[data-sniff-toggle],[data-sniff-drop],[data-sniff-rule],[data-sniff-ack]") : null;
    if (!btn) return;
    if (btn.dataset.sniffAck) { await sniffAck(btn.dataset.sniffAck); return; }
    if (btn.dataset.sniffToggle) {
      const id = btn.dataset.sniffToggle;
      await sniffCfgWrite(b => {
        b.sources = b.sources.map(s => s && s.id === id
          ? { ...s, enabled: s.enabled === false } : s);
      }, t("sniff.toggled", "source updated: {id}").replace("{id}", id));
      return;
    }
    if (btn.dataset.sniffDrop) {
      const id = btn.dataset.sniffDrop;
      await sniffCfgWrite(b => {
        b.sources = b.sources.filter(s => !(s && s.id === id));
        b.rules = b.rules.filter(r => !(r && r.source === id));
      }, t("sniff.dropped", "source removed: {id}").replace("{id}", id));
      return;
    }
    if (btn.dataset.sniffRule) {
      const id = btn.dataset.sniffRule;
      await sniffCfgWrite(b => {
        b.rules = b.rules.filter(r => !(r && r.id === id));
      }, t("sniff.ruleDropped", "rule removed: {id}").replace("{id}", id));
    }
  });
}

function sniffKindRows() {
  const sel = document.getElementById("sniffSrcKind");
  const kind = sel ? sel.value : "home";
  const home = ["sniffHomeRow", "sniffHomeRow2"];
  home.forEach(id => { const el = document.getElementById(id); if (el) el.hidden = kind !== "home"; });
  const web = document.getElementById("sniffWebRow");
  if (web) web.hidden = kind !== "web";
  // mcp: the bridge NAME resolves against settings mycore.mcp — there is no
  // url and no rate cap to ask for (the lane issues no HTTP call of its own).
  const url = document.getElementById("sniffSrcUrlRow");
  if (url) url.hidden = kind === "mcp";
  const srv = document.getElementById("sniffSrcServerRow");
  if (srv) srv.hidden = kind !== "mcp";
  const note = document.getElementById("sniffMcpNote");
  if (note) note.hidden = kind !== "mcp";
}

async function sniffAddSource() {
  const kind = (document.getElementById("sniffSrcKind") || {}).value || "home";
  const id = ((document.getElementById("sniffSrcId") || {}).value || "").trim();
  const url = ((document.getElementById("sniffSrcUrl") || {}).value || "").trim();
  const token = ((document.getElementById("sniffSrcToken") || {}).value || "").trim();
  const ents = ((document.getElementById("sniffSrcEntities") || {}).value || "").trim();
  const rx = ((document.getElementById("sniffSrcRegex") || {}).value || "").trim();
  const server = ((document.getElementById("sniffSrcServer") || {}).value || "").trim();
  const need = kind === "mcp" ? server : url;
  if (!id || !need) {
    sniffNote("err", t(kind === "mcp" ? "sniff.srcIncompleteMcp"
                                      : "sniff.srcIncomplete",
      kind === "mcp" ? "id and bridge name are required"
                     : "id and url are required"));
    return;
  }
  const ok = await sniffCfgWrite(b => {
    const base = kind === "mcp"
      ? { id, kind, server }
      : kind === "home"
      ? { id, kind, url, token,
          entities: ents ? ents.split(",").map(x => x.trim()).filter(Boolean) : ["*"] }
      : { id, kind, url, regex: rx, field: "value" };
    b.sources = b.sources.filter(s => !(s && s.id === id)).concat([{ ...base, enabled: true }]);
  }, t("sniff.srcAdded", "source added: {id}").replace("{id}", id));
  if (!ok) return;
  ["sniffSrcId", "sniffSrcUrl", "sniffSrcToken", "sniffSrcEntities", "sniffSrcRegex",
   "sniffSrcServer"]
    .forEach(k => { const el = document.getElementById(k); if (el) el.value = ""; });
  const f = document.getElementById("sniffSrcForm");
  const a = document.getElementById("sniffSrcActions");
  if (f) f.hidden = true;
  if (a) a.hidden = false;
}

function sniffRuleSources() {
  const sel = document.getElementById("sniffRuleSource");
  if (!sel) return;
  const srcs = sniffCfgList("sources");
  const have = Array.from(sel.options).map(o => o.value).join(",");
  const want = srcs.map(s => String(s && s.id || "")).filter(Boolean).join(",");
  if (have !== want) {
    sel.innerHTML = srcs.map(s =>
      `<option value="${escapeHtml(String(s && s.id || ""))}">` +
      `${escapeHtml(String(s && s.id || ""))}</option>`).join("");
  }
}

async function sniffAddRule() {
  const source = (document.getElementById("sniffRuleSource") || {}).value || "";
  const when = ((document.getElementById("sniffRuleWhen") || {}).value || "").trim();
  const severity = (document.getElementById("sniffRuleSeverity") || {}).value || "info";
  if (!source || !when) {
    sniffNote("err", t("sniff.ruleIncomplete", "source and condition are required"));
    return;
  }
  const ok = await sniffCfgWrite(b => {
    const used = new Set(b.rules.map(r => String(r && r.id || "")));
    const id = sniffNextRuleId(used, source + "-" + when.slice(0, 16));
    b.rules = b.rules.concat([{ id, source, when, severity, speak: false }]);
  }, t("sniff.ruleAdded", "rule added"));
  if (!ok) return;
  const w = document.getElementById("sniffRuleWhen");
  if (w) w.value = "";
  const f = document.getElementById("sniffRuleForm");
  const a = document.getElementById("sniffRuleActions");
  if (f) f.hidden = true;
  if (a) a.hidden = false;
}

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
  // Wave 6 delta2: the gallery (X17) and the session browser (X13) lazy-load
  // with the SAME rule — first open, then only on a user nav click.
  if (name === "stream" && (fromUser || !artData)) refreshArtifacts();
  if (name === "chat" && (fromUser || !sessData)) refreshSessions("");
  // delta4: the community panel fetches on first open, then only on a user
  // nav click (the server cache keeps the upstream read rate sane anyway).
  if (name === "community" && (fromUser || !commData)) refreshCommunity(false);
  // SNIFF car: the SENSE board lazy-loads on the same rule — first open, then
  // only on a user nav click (the sweep state changes server-side anyway).
  if (name === "sniff" && (fromUser || !sniffData)) refreshSniff();
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
/* delta4: community panel — manual refresh busts the server's 600s cache,
   the category select just re-filters the payload already in memory. */
const commRefreshEl = document.getElementById("commRefresh");
if (commRefreshEl) {
  commRefreshEl.addEventListener("click", () => refreshCommunity(true));
}
const commCatEl = document.getElementById("commCat");
if (commCatEl) {
  commCatEl.addEventListener("change", e => {
    commCat = e.target.value || "";
    renderCommunity();
  });
}
/* SNIFF car: the SENSE board wires once — the row actions are delegated and
   the two composer forms toggle their own visibility. */
wireSniff();
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
/* car 7 (D11): the leash buttons — STOP aborts the running turn, PAUSE /
   RESUME flips the fail-closed switch (absent element = no-op). */
const ctlStopBtnEl = document.getElementById("ctlStopBtn");
if (ctlStopBtnEl) ctlStopBtnEl.addEventListener("click", () => postControl("stop"));
const ctlPauseBtnEl = document.getElementById("ctlPauseBtn");
if (ctlPauseBtnEl) ctlPauseBtnEl.addEventListener("click", () =>
  postControl(controlData && controlData.paused ? "resume" : "pause"));
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
["mycUpstreams", "mycModels", "mycBudget", "mycPrune", "mycEffort"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => { mycDirty = true; });
});
// v0.5.55 gateway knobs: select fires change, number inputs fire input
["mycMaxRpm", "mycMaxTpm"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("input", () => { mycDirty = true; });
});
const rotEl = document.getElementById("mycRotation");
if (rotEl) rotEl.addEventListener("change", () => { mycDirty = true; });
const epAddBtnEl = document.getElementById("epAddBtn");
if (epAddBtnEl) epAddBtnEl.addEventListener("click", () => {
  epRows.push({ url: "", model: "", keys: [] });
  epDirty = true; epSetMsg(""); renderEndpoints();
  const rows = document.querySelectorAll("#epList .ep-row");
  const last = rows[rows.length - 1];
  const firstInput = last && last.querySelector("input.ep-url");
  if (firstInput) firstInput.focus();
});
const epKeysBtnEl = document.getElementById("epSaveChk");
if (epKeysBtnEl) epKeysBtnEl.addEventListener("click", epToggleKeys);
const prTextEl = document.getElementById("usageModelPricing");
if (prTextEl) prTextEl.addEventListener("input", () => { usageDirty = true; });
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
/* Wave 4: X5 endpoint test button + X9 cron create (form + Enter keys) */
const upTestBtnEl = document.getElementById("upTestBtn");
if (upTestBtnEl) upTestBtnEl.addEventListener("click", upstreamTest);
/* D1 (round D car 1): discovery panel + the whitelist chips' field mirror */
const discBtnEl = document.getElementById("discBtn");
if (discBtnEl) discBtnEl.addEventListener("click", runDiscover);
const discAddBtnEl = document.getElementById("discAddBtn");
if (discAddBtnEl) discAddBtnEl.addEventListener("click", discAdd);
const mycModelsEl = document.getElementById("mycModels");
if (mycModelsEl) mycModelsEl.addEventListener("input", renderModelChips);
const cronCreateEl = document.getElementById("cronCreateBtn");
if (cronCreateEl) cronCreateEl.addEventListener("click", cronCreate);
["cronName", "cronSpec", "cronPrompt"].forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); cronCreate(); }
  });
});
const cronListDelEl = document.getElementById("cronList");
if (cronListDelEl) cronListDelEl.addEventListener("click", e => {
  const btn = e.target.closest("#cronDelBtn, [data-job]");
  if (btn && btn.dataset.job) cronDel(btn.dataset.job);
});
/* Wave 5: X10 checkpoint restore row + X11 profile create/remove */
const ckptListEl = document.getElementById("ckptList");
if (ckptListEl) ckptListEl.addEventListener("click", e => {
  const btn = e.target.closest("[data-ckpt-restore]");
  if (btn) checkpointRestore(btn.getAttribute("data-ckpt-restore"));
});
const profCreateBtnEl = document.getElementById("profCreateBtn");
if (profCreateBtnEl) profCreateBtnEl.addEventListener("click", profileCreate);
const profNameEl = document.getElementById("profName");
if (profNameEl) profNameEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); profileCreate(); }
});
const profListEl = document.getElementById("profList");
if (profListEl) profListEl.addEventListener("click", e => {
  const btn = e.target.closest("[data-prof-rm]");
  if (btn) profileRm(btn.getAttribute("data-prof-rm"));
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
/* Phase B wiring: the quote card, the two thin launchers and the default
   symbol swap (A-shares <-> US) — every element id lives in index.html. */
const extQuoteBtnEl = document.getElementById("extQuoteBtn");
if (extQuoteBtnEl) extQuoteBtnEl.addEventListener("click", extQuoteFetch);
const extQuoteSymEl = document.getElementById("extQuoteSymbol");
if (extQuoteSymEl) extQuoteSymEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); extQuoteFetch(); }
});
const extQuoteMarketEl = document.getElementById("extQuoteMarket");
if (extQuoteMarketEl) extQuoteMarketEl.addEventListener("change", () => {
  const inp = document.getElementById("extQuoteSymbol");
  if (!inp) return;
  const cur = String(inp.value || "").trim();
  // only swap the PREFILLED default — a typed symbol is never overwritten
  if (!cur || cur === "sh600519" || cur === "AAPL")
    inp.value = extQuoteDefault(extQuoteMarketEl.value);
});
const extHomeGoEl = document.getElementById("extHomeGo");
if (extHomeGoEl) extHomeGoEl.addEventListener("click", () => setView("sniff", true));
const extVehicleGoEl = document.getElementById("extVehicleGo");
if (extVehicleGoEl) extVehicleGoEl.addEventListener("click", () => setView("sniff", true));
/* UX round 2 (B2): the Overview backup pointer opens the one real card */
const ovBackupOpenEl = document.getElementById("ovBackupOpen");
if (ovBackupOpenEl) ovBackupOpenEl.addEventListener("click", () => openBackupCard());
document.getElementById("chatSend").addEventListener("click", chatSend);
document.getElementById("chatClear").addEventListener("click", chatClear);
const chatStopEl = document.getElementById("chatStop");
if (chatStopEl) chatStopEl.addEventListener("click", () => postControl("stop"));
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


/* ------------------------------------------- restyle car (0.5.56) ------
   Three additions, all fail-soft and all pack-driven:
     1. syncCredSetState  — the credential row's set/empty state dot and the
        CLEAR button's disabled state (an unset slot has nothing to clear);
     2. setApplyFilter    — Settings group chips + search. PURE FILTER: the
        default state (All + empty search) adds no class and hides nothing,
        so the untouched page is byte-for-byte the page tests already know.
        The Access card is data-setgroup="always" and is never group-filtered
        (#saveBtn / #tokenInput must stay reachable);
     3. exNav             — Exchange nav visibility, tri-state localStorage
        `exNav`: "" (=auto) / "show" / "hide". auto = the nav item shows once
        ANY key-auth venue reports configured=true (chain/public venues are
        always configured, so they must NOT count). Hiding rides a class
        (.nav-item[hidden] would lose to the base rule's display:flex). A
        deep link ?view=exchange always lands, whatever the state — only a
        visible -> hidden FLIP moves a user sitting on the Exchange view back
        to Overview; a state that never changed never moves them. */

function syncCredSetState(w, isSet) {
  if (!w || !w.dataset) return;
  w.dataset.set = isSet ? "1" : "0";
  // stage 2: SET folds the row back to its read state, CLEAR re-opens it —
  // an empty slot is always in the editing state
  w.dataset.editing = isSet ? "0" : "1";
  const cb = w.querySelector(".cred-clear");
  if (cb) cb.disabled = w.dataset.writable === "1" ? !isSet : true;
}

/* --- 1. Settings group chips + search ------------------------------- */
let setGroup = "all";

/* --- round C (P3): two-tier Settings ---------------------------------- */
/* Advanced (DEFAULT, owner ruling) is today's view byte-for-byte; Guided
   shows the six cards most people touch and hides the group chips (they
   would filter a six-card set into nonsense). The choice is remembered per
   browser; Access and Backup stay visible in both tiers. */
const SET_TIER_KEY = "aidog_settings_tier";
const GUIDED_CARDS = ["consoleCard", "usageCard", "modelCard",
                      "backupCard", "tgCard", "accessCard"];
let setTier = (() => {
  try {
    const v = localStorage.getItem(SET_TIER_KEY);
    return (v === "guided" || v === "advanced") ? v : "advanced";
  } catch (e) { return "advanced"; }   // private mode: Advanced, no persistence
})();

function applyTier() {
  const tools = document.getElementById("setTools");
  document.querySelectorAll(".set-tier-btn").forEach(b =>
    b.classList.toggle("is-active", b.dataset.tier === setTier));
  if (tools) tools.classList.toggle("is-guided", setTier === "guided");
  if (setTier === "guided" && setGroup !== "all") {
    setGroup = "all";                    // chips are hidden in Guided
    document.querySelectorAll(".set-chip").forEach(b =>
      b.classList.toggle("is-active", b.dataset.setfilter === "all"));
  }
  setApplyFilter();
}

function setApplyFilter() {
  const grid = document.querySelector("#view-settings .set-grid");
  const searchEl = document.getElementById("setSearch");
  if (!grid || !searchEl) return;
  const q = (searchEl.value || "").trim().toLowerCase();
  grid.querySelectorAll(":scope > .card").forEach(card => {
    // UX round 2 (B2): a card may belong to SEVERAL groups (space separated
    // data-setgroup) — the Backup card is "general creds" so a user browsing
    // Credentials & channels still finds it instead of watching it vanish.
    const groups = (card.dataset.setgroup || "general").split(/\s+/).filter(Boolean);
    const groupOk = groups.includes("always") || setGroup === "all"
      || groups.includes(setGroup);
    // round C (P3): the tier decides whether the card exists at all — Guided
    // shows GUIDED_CARDS only, Advanced (default) shows every card.
    const tierOk = setTier === "advanced" || GUIDED_CARDS.includes(card.id);
    // reset any child-level marks from the previous pass first
    card.querySelectorAll(".is-filtered").forEach(el =>
      el.classList.remove("is-filtered"));
    let show = tierOk && groupOk;
    if (show && q) {
      const h2 = card.querySelector("h2");
      const title = ((h2 && h2.textContent) || "").toLowerCase();
      if (title.includes(q)) {
        show = true;                    // title hit: the whole card stays
      } else {
        let hits = 0;
        [...card.children].forEach(ch => {
          if (ch.tagName === "H2") return;
          let hay = (ch.textContent || "").toLowerCase();
          ch.querySelectorAll("input, textarea, select").forEach(f => {
            if (f.placeholder) hay += " " + f.placeholder.toLowerCase();
          });
          if (hay.includes(q)) { hits += 1; return; }
          ch.classList.add("is-filtered");
        });
        show = hits > 0;                // nothing matched: the card goes too
      }
    }
    if (!show) card.querySelectorAll(".is-filtered").forEach(el =>
      el.classList.remove("is-filtered"));
    card.classList.toggle("is-filtered", !show);
  });
}

function wireSettingsFilter() {
  const searchEl = document.getElementById("setSearch");
  if (searchEl && !searchEl.dataset.wired) {
    searchEl.dataset.wired = "1";
    searchEl.addEventListener("input", setApplyFilter);
  }
  document.querySelectorAll(".set-chip").forEach(btn => {
    if (btn.dataset.wired) return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", () => {
      setGroup = btn.dataset.setfilter || "all";
      document.querySelectorAll(".set-chip").forEach(b =>
        b.classList.toggle("is-active", b === btn));
      setApplyFilter();
    });
  });
  document.querySelectorAll(".set-tier-btn").forEach(btn => {
    if (btn.dataset.wired) return;
    btn.dataset.wired = "1";
    btn.addEventListener("click", () => {
      setTier = btn.dataset.tier === "guided" ? "guided" : "advanced";
      try { localStorage.setItem(SET_TIER_KEY, setTier); }
      catch (e) { /* private mode: this visit only */ }
      applyTier();
    });
  });
  applyTier();                // first paint honours the remembered tier
}

/* UX round 2 (B2): Overview "Backup & Restore / OPEN" pointer. Navigation
   only — it reveals the ONE existing card under whatever chip/search filter
   happens to be active (All + cleared search, chips repainted) and focuses
   it; no write, no second passphrase form, no duplicated controls. */
function openBackupCard() {
  setView("settings", true);
  setGroup = "all";
  const search = document.getElementById("setSearch");
  if (search) search.value = "";
  document.querySelectorAll(".set-chip").forEach(b =>
    b.classList.toggle("is-active", b.dataset.setfilter === "all"));
  setApplyFilter();
  requestAnimationFrame(() =>
    actFocusEl(document.getElementById("backupCard")));
}

/* --- 2. Exchange nav visibility (localStorage exNav) ----------------- */
const EXNAV_KEY = "exNav";

function exNavMode() {
  try {
    const v = localStorage.getItem(EXNAV_KEY) || "";
    return (v === "show" || v === "hide") ? v : "";
  } catch (e) { return ""; }
}

function exNavVisible() {
  const mode = exNavMode();
  if (mode === "show") return true;
  if (mode === "hide") return false;
  return exRows().some(v => v && v.auth === "key" && v.configured === true);
}

let exNavWasVisible = null;   // last painted state; null = first paint

function applyExNav() {
  const item = document.querySelector('.nav-item[data-view="exchange"]');
  const next = exNavVisible();
  if (item) item.classList.toggle("is-hidden", !next);
  const sel = document.getElementById("exNavSel");
  if (sel && sel.value !== exNavMode()) sel.value = exNavMode();
  // Only a visible -> hidden FLIP walks off the Exchange view: a deep link
  // ?view=exchange (first paint, whatever the state) always lands, and an
  // auto state that never changes never moves the user.
  if (exNavWasVisible === true && !next && currentView === "exchange") {
    setView("overview", true);
  }
  exNavWasVisible = next;
}

function wireExNav() {
  wireSettingsFilter();
  applyExNav();       // paint the rule now: auto must not flash the item
  const sel = document.getElementById("exNavSel");
  if (sel && !sel.dataset.wired) {
    sel.dataset.wired = "1";
    sel.addEventListener("change", () => {
      const v = (sel.value === "show" || sel.value === "hide") ? sel.value : "";
      try {
        if (v) localStorage.setItem(EXNAV_KEY, v);
        else localStorage.removeItem(EXNAV_KEY);
      } catch (e) { /* private mode: the choice simply does not persist */ }
      applyExNav();            // the flip rule above owns any walk-off
    });
  }
}

/* --- 3. Backup & Restore card (CKX-P1, car 3) ------------------------- */
/* The console's own encrypted backup plane. Everything runs IN THIS BROWSER
   over the loopback: the export is fetched with the Bearer token, the
   artifact is encrypted by the vendored CKX SDK (lazy-loaded on first use, so
   the first paint never pays for its ~39 KB), and the passphrase typed here
   is never sent anywhere — the server only ever sees the DECRYPTED payload,
   and only once the operator confirms after the preview. The CREATE flow
   round-trips its own ciphertext before offering the download: an artifact
   that cannot be decrypted with the passphrase that made it is never handed
   over. */

let ckxLoading = null;   // one shared lazy <script> injection for the SDK
let bkPending = null;    // decrypted envelope waiting for the confirm press

function loadCKX() {
  if (window.CKX) return Promise.resolve(window.CKX);
  if (ckxLoading) return ckxLoading;
  ckxLoading = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "/static/vendor/ckx.umd.js";
    s.async = true;
    s.onload = () => (window.CKX ? res(window.CKX)
                                 : rej(new Error("CKX missing after load")));
    s.onerror = () => { ckxLoading = null; rej(new Error("CKX failed to load")); };
    document.head.appendChild(s);
  });
  return ckxLoading;
}

function bkSet(el, msg, kind) {
  if (!el) return;
  el.className = "save-state" + (kind ? " " + kind : "");
  el.textContent = msg;
}

function bkScope() {
  const on = (id) => {
    const el = document.getElementById(id);
    return !!(el && el.checked);
  };
  const scope = { secrets: on("bkSec"), settings: on("bkSet"), memory: on("bkMem") };
  scope.any = scope.secrets || scope.settings || scope.memory;
  return scope;
}

function bkBytes(str) {
  try { return new TextEncoder().encode(String(str)).length; }
  catch (e) { return String(str).length; }
}

function bkCounts(payload, scope) {
  const bits = [];
  if (scope.secrets) {
    bits.push(`${Object.keys(payload.secrets || {}).length} ` +
              t("bk.nSlots", "credential slots"));
  }
  if (scope.settings) {
    bits.push(`${Object.keys(payload.settings || {}).length} ` +
              t("bk.nKeys", "settings keys"));
  }
  if (scope.memory) {
    const mem = payload.memory || {};
    const bytes = Object.values(mem).reduce((n, v) => n + bkBytes(v), 0);
    bits.push(`${Object.keys(mem).length} ${t("bk.nFiles", "memory files")}` +
              ` · ${bytes} ${t("bk.nBytes", "bytes")}`);
  }
  return bits;
}

function bkErrText(status, body) {
  const det = Array.isArray(body && body.details) ? body.details[0] : null;
  const base = (body && (body.reason || body.error)) || ("HTTP " + status);
  return det ? `${base} · ${det}` : String(base);
}

function bkStrengthText(pass) {
  if (!pass) return "";
  let score = 0;
  if (pass.length >= 8) score++;
  if (pass.length >= 14) score++;
  if (/[a-z]/.test(pass) && /[A-Z]/.test(pass)) score++;
  if (/\d/.test(pass)) score++;
  if (/[^A-Za-z0-9]/.test(pass)) score++;
  if (score >= 4) return t("bk.strong", "strength: strong");
  if (score >= 3) return t("bk.fair", "strength: fair");
  return t("bk.weak", "strength: weak — length helps most");
}

function bkInvalidate() {
  // any edit to the scope / file / passphrase invalidates a preview that was
  // already shown: the confirm press must apply exactly what was previewed.
  bkPending = null;
  const preview = document.getElementById("bkPreview");
  if (preview) preview.hidden = true;
}

async function bkCreate() {
  const msg = document.getElementById("bkMsg");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    return bkSet(msg, t("comm.needToken",
      "access token required — set it in Settings, Access"), "err");
  }
  const scope = bkScope();
  if (!scope.any) return bkSet(msg, t("bk.needSection", "select at least one section"), "err");
  const pass = (document.getElementById("bkPass") || {}).value || "";
  const pass2 = (document.getElementById("bkPass2") || {}).value || "";
  if (pass.length < 8) {
    return bkSet(msg, t("bk.shortPass", "passphrase must be at least 8 characters"), "err");
  }
  if (pass !== pass2) return bkSet(msg, t("bk.mismatch", "the two passphrases differ"), "err");
  const include = ["secrets", "settings", "memory"].filter(k => scope[k]).join(",");
  const btn = document.getElementById("bkCreate");
  if (btn) btn.disabled = true;
  try {
    bkSet(msg, t("bk.working", "exporting…"));
    let env;
    try {
      const res = await fetch("/api/backup/export?include=" + encodeURIComponent(include),
        { headers: { Authorization: "Bearer " + token } });
      const body = await res.json().catch(() => ({}));
      if (res.status === 401) return bkSet(msg, t("save.badToken", "401 · unauthorized"), "err");
      if (!res.ok) return bkSet(msg, bkErrText(res.status, body), "err");
      env = body;
    } catch (e) {
      return bkSet(msg, t("comm.netFail", "network error — the console is not answering"), "err");
    }
    let sdk;
    try { sdk = await loadCKX(); }
    catch (e) { return bkSet(msg, t("bk.sdkFail", "the encryption library could not load"), "err"); }
    bkSet(msg, t("bk.encrypting", "encrypting…"));
    const json = JSON.stringify(env);
    let box;
    try {
      box = await sdk.encryptToFile(json, pass, {
        app: location.origin,
        schema: "aidog.backup.v1",
        schemaVersion: "1",
        label: "aidog backup " + new Date().toISOString().slice(0, 10),
      });
    } catch (e) {
      return bkSet(msg, t("bk.encryptFail", "encryption failed") +
        (e && e.code ? " · " + e.code : ""), "err");
    }
    let ok = false;
    try {
      const back = await sdk.decrypt(box, pass);
      ok = !!back && typeof back.text === "string" && back.text === json;
    } catch (e) { ok = false; }
    if (!ok) {
      return bkSet(msg, t("bk.roundtripFail",
        "round-trip FAILED — no file was offered for download"), "err");
    }
    const name = "aidog-backup-" +
      new Date().toISOString().slice(0, 10).replace(/-/g, "") + ".ckx";
    const url = URL.createObjectURL(new Blob([box], { type: "application/octet-stream" }));
    const a = document.createElement("a");
    a.href = url; a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    ["bkPass", "bkPass2"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.value = "";
    });
    const strength = document.getElementById("bkStrength");
    if (strength) strength.textContent = "";
    bkSet(msg, t("bk.roundtripOk", "round-trip verified ✓") + " · " + name);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function bkApply(doc, scope, token, msg, preview) {
  const body = {
    schema: doc.schema,
    schemaVersion: doc.schemaVersion,
    payload: doc.payload,
    apply: { secrets: scope.secrets, settings: scope.settings, memory: scope.memory },
  };
  let res, out;
  try {
    res = await fetch("/api/backup/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 Authorization: "Bearer " + token },
      body: JSON.stringify(body),
    });
    out = await res.json().catch(() => ({}));
  } catch (e) {
    return bkSet(msg, t("comm.netFail", "network error — the console is not answering"), "err");
  }
  bkPending = null;
  if (res.status === 401) return bkSet(msg, t("save.badToken", "401 · unauthorized"), "err");
  if (res.status === 400) return bkSet(msg, bkErrText(400, out), "err");
  const bits = [];
  if (out.secrets) bits.push(`${(out.secrets.names || []).length} ` +
                              t("bk.nSlots", "credential slots"));
  if (out.settings) bits.push(`${(out.settings.changed || []).length} ` +
                               t("bk.nKeys", "settings keys"));
  if (out.memory) bits.push(`${(out.memory.files || []).length} ` +
                             t("bk.nFiles", "memory files"));
  const failed = [].concat(
    ((out.secrets && out.secrets.failed) || []).map(f => `${f.name}: ${f.reason}`),
    (out.settings && out.settings.errors) || [],
    ((out.memory && out.memory.failed) || []).map(f => `${f.name}: ${f.reason}`));
  const head = out.ok ? t("bk.done", "restored")
                      : t("bk.partial", "restored — some parts refused");
  const text = head + ": " + (bits.join(" · ") || "—") +
    (failed.length ? " · " + failed.join(" | ") : "");
  bkSet(msg, text, out.ok ? "" : "err");
  if (preview) preview.hidden = true;
  if (out.ok) {
    if (out.settings && typeof loadSettings === "function") loadSettings().catch(() => {});
    if (out.secrets && typeof loadCreds === "function") loadCreds();
  }
}

async function bkRestore() {
  const msg = document.getElementById("bkRMsg");
  const preview = document.getElementById("bkPreview");
  const token = localStorage.getItem(TOKEN_KEY) || "";
  if (!token) {
    return bkSet(msg, t("comm.needToken",
      "access token required — set it in Settings, Access"), "err");
  }
  const input = document.getElementById("bkFile");
  const file = input && input.files && input.files[0];
  if (!file) return bkSet(msg, t("bk.needFile", "choose a .ckx backup file first"), "err");
  const pass = (document.getElementById("bkRPass") || {}).value || "";
  if (!pass) return bkSet(msg, t("bk.needPass", "enter the passphrase of that file"), "err");
  const scope = bkScope();
  if (!scope.any) return bkSet(msg, t("bk.needSection", "select at least one section"), "err");
  const key = `${file.name}:${file.size}:${file.lastModified}:${pass}`;
  const btn = document.getElementById("bkRestore");
  if (btn) btn.disabled = true;
  try {
    if (bkPending && bkPending.key === key) {
      return await bkApply(bkPending.doc, scope, token, msg, preview);  // the CONFIRM press
    }
    bkPending = null;
    let sdk;
    try { sdk = await loadCKX(); }
    catch (e) { return bkSet(msg, t("bk.sdkFail", "the encryption library could not load"), "err"); }
    bkSet(msg, t("bk.decrypting", "decrypting…"));
    let text;
    try {
      const buf = new Uint8Array(await file.arrayBuffer());
      const back = await sdk.decrypt(buf, pass);
      text = back && back.text;
    } catch (e) {
      if (preview) preview.hidden = true;
      const code = String((e && e.code) || "");
      return bkSet(msg, code === "CKX_WRONG_PASSWORD"
        ? t("bk.badPass", "wrong passphrase")
        : t("bk.decryptFail", "this file cannot be read"), "err");
    }
    let doc = null;
    try { doc = JSON.parse(text); } catch (e) { doc = null; }
    if (!doc || doc.schema !== "aidog.backup.v1" ||
        typeof doc.schemaVersion !== "string" ||
        !doc.payload || typeof doc.payload !== "object") {
      if (preview) preview.hidden = true;
      return bkSet(msg, t("bk.badArtifact", "not an I'm AiDog backup file"), "err");
    }
    if (preview) {
      preview.hidden = false;
      preview.textContent = t("bk.previewHead", "preview") + ": " +
        bkCounts(doc.payload, scope).join(" · ");
    }
    bkPending = { key, doc };
    bkSet(msg, t("bk.ready",
      "decrypted — check the preview, then press RESTORE again to apply"));
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* Round C (owner report): our own face for the file input — the browser's
   native one is localized by the browser itself (UA shadow DOM), so an
   English session on a zh-locale browser used to show its Chinese words.
   Idle text comes from the pack; a chosen FILE NAME is data and is shown
   verbatim, in either language. */
function renderFileFace() {
  const input = document.getElementById("bkFile");
  const name = document.getElementById("bkFileName");
  if (!input || !name) return;
  const f = input.files && input.files[0];
  name.textContent = f ? f.name : t("bk.noFile", "no file chosen");
}

function wireBackup() {
  const create = document.getElementById("bkCreate");
  const restore = document.getElementById("bkRestore");
  if (!create || !restore || create.dataset.wired) return;
  create.dataset.wired = "1";
  const strength = document.getElementById("bkStrength");
  const pass = document.getElementById("bkPass");
  if (pass && strength) {
    pass.addEventListener("input", () => { strength.textContent = bkStrengthText(pass.value); });
  }
  ["bkScope", "bkFile", "bkRPass"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("change", bkInvalidate);
  });
  // round C: repaint our file face whenever the selection changes
  const fileEl = document.getElementById("bkFile");
  if (fileEl) {
    fileEl.addEventListener("change", renderFileFace);
    renderFileFace();           // idle state in the active language
  }
  const rpass = document.getElementById("bkRPass");
  if (rpass) rpass.addEventListener("input", bkInvalidate);
  create.addEventListener("click", () => { bkCreate(); });
  restore.addEventListener("click", () => { bkRestore(); });
  [pass, rpass].forEach(el => {
    if (!el) return;
    el.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      e.preventDefault();
      (el === pass ? bkCreate : bkRestore)();
    });
  });
  wireAgentBackup();
}

/* --- Car 8 (CKX-P2): the AGENT BACKUP section --------------------------- */
/* One read face (GET /api/backup/agent) paints every row; the three buttons
   and the meter reset are the only writes, each behind the existing Bearer
   gate via commPost. Nothing here ever receives or renders a slot VALUE, a
   filesystem path or a payload — the server does not send them, and no
   client-side state pretends otherwise. A missing endpoint degrades to an
   honest "unavailable" line, never to a fabricated capability. */

let agentDoc = null;

function agentMsg(text, kind) {
  bkSet(document.getElementById("agMsg"), text, kind || "");
}

async function refreshAgentBackup() {
  try {
    const res = await fetch("/api/backup/agent", { cache: "no-store" });
    agentDoc = res.ok ? await res.json() : null;
  } catch (e) { agentDoc = null; }
  renderAgentBackup();
}

function renderAgentBackup() {
  const node = document.getElementById("agNode");
  const pw = document.getElementById("agPw");
  const state = document.getElementById("agState");
  const mcpBtn = document.getElementById("agMcp");
  const num = document.getElementById("agNum");
  const bar = document.getElementById("agBar");
  const doc = agentDoc;
  if (!doc || typeof doc !== "object") {
    if (node) node.textContent = t("bk.agentUnavailable",
      "agent backup status unavailable — the endpoint did not answer");
    if (pw) pw.textContent = "";
    if (state) state.textContent = "";
    if (num) num.textContent = "–";
    if (bar) bar.style.width = "0%";
    return;
  }
  const n = (doc.node && typeof doc.node === "object") ? doc.node : {};
  if (node) {
    node.className = "stat-note" + (n.ok === true ? "" : " warn");
    node.textContent = n.ok === true
      ? t("bk.agentNodeOk", "Node runtime: {v} (>= 20 required)")
          .replace("{v}", String(n.version || "?"))
      : t("bk.agentNodeOff", "Node runtime unavailable: {r} — scheduled backup stays off; the manual card above still works")
          .replace("{r}", String(n.reason || "unknown"));
  }
  const pwSet = !!(doc.password_slot && doc.password_slot.set === true);
  if (pw) {
    pw.className = "stat-note" + (pwSet ? "" : " warn");
    pw.textContent = pwSet
      ? t("bk.agentPwOk", "AIDOG_CKX_PASSWORD slot: set")
      : t("bk.agentPwOff", "AIDOG_CKX_PASSWORD slot: empty — set it in Credentials");
  }
  const sched = (doc.scheduled && typeof doc.scheduled === "object")
    ? doc.scheduled : null;
  if (state) {
    state.className = "stat-note";
    state.textContent = sched
      ? t("bk.agentSchedAt", "scheduled · {s} · next {n}")
          .replace("{s}", String(sched.spec || ""))
          .replace("{n}", String(sched.next || "–"))
      : t("bk.agentNotSched", "not scheduled");
  }
  if (mcpBtn) {
    const on = !!(doc.mcp && doc.mcp.enabled === true);
    mcpBtn.dataset.i18n = on ? "bk.agentMcpOff" : "bk.agentMcpOn";
    mcpBtn.textContent = t(on ? "bk.agentMcpOff" : "bk.agentMcpOn",
      on ? "DISABLE MCP" : "ENABLE MCP");
    mcpBtn.dataset.on = on ? "1" : "";
  }
  const q = (doc.quota && typeof doc.quota === "object") ? doc.quota : {};
  const used = Number(q.used) || 0;
  const cap = Number(q.cap) || 0;
  const st = String(q.state || "ok");
  if (num) {
    num.textContent = cap
      ? t("bk.agentQuotaNum", "{u} / {c} · {s}")
          .replace("{u}", used.toLocaleString("en-US"))
          .replace("{c}", cap.toLocaleString("en-US"))
          .replace("{s}", st.toUpperCase())
      : t("bk.agentQuotaUnknown", "meter unavailable");
    num.style.color = st === "over" ? "var(--urgent, #c0392b)"
      : st === "warn" ? "var(--amber)" : "";
  }
  if (bar) {
    bar.style.width = cap ? Math.min(100, Math.round(used * 100 / cap)) + "%" : "0%";
    bar.style.background = st === "over" ? "var(--urgent, #c0392b)"
      : st === "warn" ? "var(--amber)" : "";
  }
}

async function agentPost(action) {
  const spec = (document.getElementById("agSpec") || {}).value || "";
  agentMsg(t("bk.agentBusy", "working…"));
  const res = await commPost("/api/backup/agent",
    action === "schedule" ? { action, spec: spec.trim() } : { action });
  if (!res.ok) {
    const detail = String(res.detail || "");
    agentMsg(detail || t("bk.agentFail", "request refused"), "err");
    refreshAgentBackup();
    return false;
  }
  const words = {
    schedule: "bk.agentSchedOk", unschedule: "bk.agentRemoved",
    quota_reset: "bk.agentQuotaReset", mcp_on: "bk.agentMcpOnOk",
    mcp_off: "bk.agentMcpOffOk" };
  agentMsg("✓ " + t(words[action] || "bk.agentDone", "done"));
  refreshAgentBackup();
  beep(660, 0.05);
  return true;
}

function wireAgentBackup() {
  const ids = ["agSched", "agUnsched", "agMcp", "agReset"];
  const btn = {};
  let any = false;
  ids.forEach(id => { btn[id] = document.getElementById(id);
                      if (btn[id]) any = true; });
  if (!any) return;
  ids.forEach(id => {
    const b = btn[id];
    if (!b || b.dataset.wired) return;
    b.dataset.wired = "1";
    b.addEventListener("click", () => {
      const action = id === "agSched" ? "schedule"
        : id === "agUnsched" ? "unschedule"
        : id === "agReset" ? "quota_reset"
        : (b.dataset.on === "1" ? "mcp_off" : "mcp_on");
      agentPost(action);
    });
  });
  refreshAgentBackup();
}

/* --- 4. Car 4 (D6): decision endpoint row + intent routing --------------- */
/* The endpoint row lives in the existing Model panel (no new view): fields
   load from settings, presence/status from GET /api/mycore/endpoints, and an
   untouched form ships nothing. Intent routing (call site 1) is a button —
   never automatic — and answers either a tab or an honest "no fast path"
   reason word; a reason WORD is never invented client-side. */

function paintDecisionStatus() {
  const el = document.getElementById("decStatus");
  if (!el) return;
  const d = (typeof epData === "object" && epData) ? epData.decision : null;
  if (!d || typeof d !== "object") {
    el.className = "stat-note";
    el.textContent = t("myc.decUnknown", "decision endpoint: unavailable");
    return;
  }
  if (d.enabled !== true) {
    el.className = "stat-note";
    el.textContent = t("myc.decOff",
      "off — every decision call site keeps its current behavior");
    return;
  }
  if (d.key_set !== true) {
    el.className = "stat-note warn";
    el.textContent = t("myc.decNoKey",
      "on — set the DECISION_API_KEY slot (Credentials) to activate");
    return;
  }
  el.className = "stat-note";
  el.textContent = t("myc.decOn", "active") + " · " +
    (d.url || "https://jevmodel.org") + " · " +
    (d.model || "jev-latest") + " · " +
    (d.router_model
      ? t("myc.decRouterOn", "router → ") + d.router_model
      : t("myc.decRouterOff", "router off"));
}

function wireDecisionPanel() {
  const toggle = document.getElementById("decToggle");
  if (!toggle || toggle.dataset.wired) return;
  toggle.dataset.wired = "1";
  toggle.addEventListener("click", () => {
    const pressed = toggle.getAttribute("aria-pressed") === "true";
    toggle.setAttribute("aria-pressed", pressed ? "false" : "true");
    decDirty = true;
  });
  ["decUrl", "decModel", "decRouter"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => { decDirty = true; });
  });
}

function wireIntentRoute() {
  const btn = document.getElementById("extRouteBtn");
  const input = document.getElementById("extIntent");
  const msg = document.getElementById("extRouteMsg");
  if (!btn || !input || btn.dataset.wired) return;
  btn.dataset.wired = "1";
  const say = (text, kind) => {
    if (!msg) return;
    msg.className = "save-state" + (kind ? " " + kind : "");
    msg.textContent = text;
  };
  const go = async () => {
    const text = (input.value || "").trim();
    if (!text) {
      say(t("ext.routeEmpty", "type a request first"), "err");
      return;
    }
    btn.disabled = true;
    say(t("ext.routeBusy", "routing…"));
    try {
      const res = await commPost("/api/extensions/intent", { text });
      if (!res.ok) { say(commErrorText(res), "err"); return; }
      const body = res.body || {};
      if (body.available !== true) {
        const word = String(body.reason || "unavailable");
        const why = t("ext.reason." + word, word);
        say(t("ext.routeWhy", "no fast path: {r}").replace("{r}", why), "err");
        return;
      }
      setExtTab(String(body.tab || ""));
      const conf = Number(body.confidence);
      say("→ " + t("ext.tab." + body.tab, String(body.tab)) +
          (Number.isFinite(conf) ? " · " + Math.round(conf * 100) + "%" : ""));
    } finally {
      btn.disabled = false;
    }
  };
  btn.addEventListener("click", () => { go(); });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    go();
  });
}

wireIntentRoute();
wireBackup();
wireRoundC();                // round C: P4 disclosure + P6 sticky save bar

const startLang = QS_LANG || localStorage.getItem(LANG_KEY) || "en";
runBoot();
setView(currentView, false);      // deep link applied before the first paint
loadLang(startLang);
loadSettings().then(() => { scheduleRefresh(); renderVoice(); initSettingsPanels(); });
refreshStatus(); refreshUsage(); refreshSelf(); refreshEvents(); refreshVersion(); refreshSkills();
refreshControl();      // car 7 (D11): tail row + guardrails status at boot
loadSkillsRemote();  // car 5: per-skill availability for the ORCHESTRATION row
refreshEvolution();
refreshCron();           // cron list for the Overview card (+ delete row)
refreshCheckpoints();    // X10: checkpoint catalog (Overview, metadata only)
refreshProfiles();       // X11: profile catalog (Settings, read-only)
refreshMission();        // mission board: honest idle until the first probe
refreshSetup();          // activation car: Overview checklist (hidden if 404)
loadChatHistory();   // console session restored at boot (read-only route)
refreshVoiceCap();   // engine status visible before the first orb tap (#4)
refreshCaps();       // media face: stt/image presence for the chat cards
renderExchange();    // exchange panel paints its honest fallback at once
wireExNav();         // 0.5.56: group chips + search + exchange nav toggle
setInterval(tickClock, 1000); tickClock();
setInterval(tickModeltier, 1000);   // X6 cooling countdown (hidden = no-op)
setInterval(refreshUsage, 60000);
setInterval(refreshSelf, 60000);   // v0.5.55 self-metered block rides along
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
