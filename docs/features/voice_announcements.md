# Feature spec — Voice announcements (Phase ② owner-approved pilot)

Status: **live** (2026-10-18). Owner decision: `edge-tts` free pilot; ElevenLabs/XTTS
remain untouched until approved (cost decree: every paid action needs a budget report first).

## 1. What it does

The console can **speak** advisory lines instead of only showing them:

| Trigger | Line spoken |
|---|---|
| Orb tap (first time) | greeting ("Voice channel online.") — doubles as the autoplay-unlock gesture |
| Advisory level change (info ↔ warn ↔ urgent) | the ticker line for the new level |
| URGENT takeover layer appears (once per takeover, not per poll) | "Urgent. Intervention required." |
| (nothing else auto-speaks — no chatty background narration) |

Auto-announcements are gated by the existing **UI sound** switch (owner: sound
setting reused, no parallel switch). The orb tap itself always speaks the
greeting — it is an explicit user request.

## 2. Playback chain (honest degradation, never fake audio)

```
POST /api/say  ->  edge-tts mp3  ->  Audio.play()
      | (engine absent / network / rate-limited)
      v
browser speechSynthesis (zero-dependency local fallback)
      | (both fail)
      v
orb shows the failure state + error text once (no pretending)
```

Autoplay policy: playback arms only after a **user gesture** (orb tap); before
that the orb renders `VOICE LOCKED` and nothing plays.

## 3. Server (`web/server.py`, stdlib only)

| Route | Behavior |
|---|---|
| `GET /api/voice` | capability: engine presence, voices, `max_chars`, rate limit — presence booleans only |
| `POST /api/say` | `{text, lang}` → `audio/mpeg` (cached in `web/.voice/`, gitignored) |

Guards, in order:

1. `Content-Type` must be `application/json` (415) — blocks cross-site form posts;
2. `Origin`, when present, must be loopback (403);
3. engine presence (503 when absent);
4. validation: non-empty text, `lang ∈ {en,zh}`, `≤200` chars (400) — all **before**
   the rate budget is spent;
5. sanitize through `sanitize_event_text` (tokens/bearer/brand redaction);
6. sliding-window rate limit: 8 synth/min → `429` + `Retry-After`;
7. engine failure → `502` **with the error detail** (honest, never fake audio).

Voices: `en-GB-RyanNeural` / `zh-CN-YunxiNeural` (A/B against the Bettany timbre
per `docs/research/aidog_voice_and_clients.md` is still an owner ear-test).
Text travels via a temp file (`--file`), never argv — process listings must not
expose spoken content.

## 4. Console (frontend)

- **Voice orb** in the ADVISORY card: `locked → armed → speaking`, plus `muted`
  (UI sound off) and `offline` (engine gone) states; failure text shows once;
- **Voice volume** slider in CONSOLE (0–100, persisted via `voice_volume`);
- i18n: all strings in `en.json` / `zh.json` (`voice.*`, `ctl.volume`, `alert.speak`);
- one utterance at a time; the same line will not repeat within 8 s.

## 5. Settings schema

`voice_volume: int 0..100` (default 80) added to the allow-list; booleans are
explicitly rejected (Python `bool` is an `int` subclass — validated).

## 6. Verification

- `tests/web_console_p3.py`: **27/27** (was 17; +10 voice checks — capability
  shape, validation 400s, 415/403 guards, live synthesis accepting only
  `200+mp3-magic` / `429` / honest `502` / `503`, settings validation);
- live browser E2E (Playwright): orb tap → arm → greeting; UI sound on +
  urgent preview → takeover line spoken via `/api/say`; zero page errors;
- dual-viewport screenshots (1440×900 + 390×844): no overflow, js-clean.

## 7. Out of scope (needs owner approval first)

- ElevenLabs / voice-cloning XTTS (legal risk on cloning a real actor — flagged,
  not decided unilaterally);
- wake word / always-on listening;
- spoken narration of every event stream line.
