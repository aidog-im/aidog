# Voice Session (console wake word + Telegram voice notes)

Status: shipped in car 4c (`mycore/stt.py`, console `web/static/app.js` voice
session, `mycore/service.py::_handle_voice`; settings keys in
`web/server.py`). Companion pages: [voice_announcements.md](voice_announcements.md)
(the advisory speech chain), [tts_engines.md](tts_engines.md) (what speaks the
reply), [capabilities.md](capabilities.md) (the `media` block).

Speech-to-text is **fully local** (faster-whisper, weights in the HuggingFace
cache): audio never leaves this machine for transcription. The wake word is
**phrase matching over STT output**, not an acoustic wake model — see §5 for
what that honestly means.

## 1. Console session — the timing chain

```
mic button (Chat view · VOICE SESSION card)
  → getUserMedia + MediaRecorder, one self-contained segment every 2.5 s
  → POST /api/stt (raw audio bytes, local contract — no bearer)
  → wake phase: normalized phrase match against settings.media.wake_phrases
      miss → keep listening (live transcript shown, nothing sent)
      hit  → beep(880 Hz), phase = "cmd", command window opens
  → cmd phase: further segments append words (max 10 s; an EMPTY segment
    ends the command early — silence means "done talking")
  → auto-fill chatInput + chatSend (same streamed turn as typed chat)
  → wait for the reply (≤ 120 s), take the FIRST 200 chars of the last
    assistant bubble → POST /api/say → played (browser speechSynthesis
    fallback — same honest chain as voice_announcements.md §2)
```

Constants: segment cycle 2 500 ms, command window 10 000 ms, reply wait
120 000 ms, spoken reply truncated at `SAY_MAX_CHARS` (200). Wake matching
normalizes both sides (lowercase, punctuation/symbols → spaces, collapsed
whitespace) and uses substring containment, so "hey aidog, what's up" and
"HEY AIDOG!" both hit. Phrases come from `settings.media.wake_phrases`
(comma-separated in the MEDIA card); with an empty/absent setting the
front end falls back to the built-in bilingual defaults.

Every failure shows its real reason and stops honestly: no MediaRecorder →
"cannot record audio"; mic denied → "microphone access denied"; server STT
unavailable → session refuses to start; a 503/413/415/403 from `/api/stt`
stops the loop instead of silently retrying forever.

## 2. `POST /api/stt` — the local contract

Same family as `/api/say`: **no bearer** (loopback console feature), guarded
by Origin + rate limit + size caps. The body IS the audio — it runs on its
own `STT_MAX_BODY` budget, not the JSON `MAX_BODY`.

| Leg | Answer |
|---|---|
| `Content-Type` not `audio/*` | 415 (names the accepted audio types) |
| `Origin` present and not loopback | 403 |
| empty / bad content-length | 400 |
| body > **2 000 000 bytes** | 413 |
| > **30 transcriptions per sliding minute** | 429 + `Retry-After` (window shared with neither `/api/say` nor anything else) |
| STT module/weights absent | 503 (real reason, never a fake transcript) |
| undecodable / empty audio (`SttError`) | 422 with the sanitized reason (≤160 chars) |
| decoded audio > **60 s** | 422 (`audio too long`) |
| success | `200 {"text", "language", "duration"}` |

Model selection: `settings.media.stt_model` ∈ `tiny | base | small`
(default `base`), validated by the settings partition. The model lazy-loads
as a per-size singleton — **the first transcription after a restart pays the
load** (measured on the dev host: tiny ≈0.7 s, base ≈1.6 s, small ≈4.9 s to
load; a short clip then transcribes in well under a second on tiny/base, ~1.3 s
on small). Subsequent segments reuse the loaded model.

Decode chain (in `mycore/stt.py`): `av.open(BytesIO)` → `AudioResampler`
to s16 mono 16 kHz → float32/32768 → `transcribe(beam_size=1)`. We decode
the container ourselves because upstream `decode_audio` is incompatible with
the installed `av` major version — the failure it avoids is documented in the
module docstring. Verified in practice on mp3, ogg-opus (Telegram) and
webm-opus (browser MediaRecorder).

## 3. Telegram voice notes

A `voice`/`audio` attachment with no usable text enters the pipeline as
**just another input method** — same command dispatch, same session-key
rule, same single reply:

1. duration check against metadata first: > **60 s** → honest locale line
   `voice.stt.tooLong` (nothing downloaded);
2. `getFile` → `file_size` check: > **5 MB** → `voice.stt.failed`
   ("file too large"), rejected **before** downloading;
3. binary download (its own bytes seam — the text transport decodes UTF-8
   and cannot carry audio; bounded at 5 MB + 1);
4. `stt.transcribe(...)` → empty transcript → `voice.stt.empty`;
   exception → `voice.stt.failed` with a scrubbed reason;
5. non-empty text → the exact typed-text path → **text reply** (this car
   ships no voice replies on Telegram).

Logs carry `chat/ms/lang/chars` only — never transcript content, never a
URL (the download/getFile URLs embed the bot token; the TG error path
sanitizes). Honest note: the TG leg calls `transcribe()` with its default
model, while the console honors `settings.media.stt_model`.

## 4. Configuration

`web/settings.json` → `media` block (validated by `POST /api/settings`,
illegal writes answer 400 with the exact key and change nothing on disk):

| key | type | default | meaning |
|---|---|---|---|
| `stt_model` | `tiny\|base\|small` | `base` | console STT model (precision vs. first-load latency) |
| `wake_phrases` | list, ≤ 3 items, each ≤ 64 chars, single-line | `["hey aidog", "你好艾多"]` | phrases that open the command window |

No credential slot exists for this feature — local STT needs none
(zero-key by construction). The console MEDIA card edits both keys.

## 5. Honest boundaries

- **Wake word = STT phrase matching, not an acoustic wake model.** While the
  mic is on, every 2.5 s segment is transcribed (locally) and matched — so
  false accepts are possible and detection quality equals the STT model's;
  false *misses* are the real observed case — the E2E proved whisper renders
  the default "hey aidog" as "hey a dog", so a literal settings phrase may not
  match. Tune `wake_phrases` to what the model actually renders (the E2E
  reports this gap as a finding instead of papering over it).
- **Browser preconditions**: `getUserMedia` + `MediaRecorder` support, mic
  permission, and a loopback origin (media APIs are unavailable on
  `about:blank`-style origins). Each of these fails with its own message.
- **Serial transcription**: segments are strictly sequential (one in-flight
  request; a pending segment skips the tick) — no parallel decode queue, so
  the 30/min budget is never burst by the client itself.
- **Always-on while toggled**: listening runs only between MIC-on and
  STOP; nothing listens when the card is idle.
- **TG replies are text**: voice-in is implemented, voice-out on Telegram is
  out of scope for this car (console replies are spoken — §1).
- **Model availability**: with weights absent (offline host, missing cache)
  `/api/stt` answers 503 and the console says so — never a fabricated
  transcript.

## 6. Verification (what actually exercises this)

- **Core suite** (`mycore/tests/run.py`): STT structure legs (sine fixture —
  decode+transcribe contract, invalid model / empty / junk → `SttError`,
  availability probe), TG media legs (getFile payload, bytes seam, bounds,
  token sentinel never surfaces), service media legs (voice note → same text
  path, photo drain ordering — offline via injected transports).
- **Web smoke** (`tests/web_console_p3.py`): the full `/api/stt` guard matrix
  (415/400/403/413), live transcription of a synthesized clip →
  `200 text+duration`, the 429 window leg (kept last by design), `media`
  settings validation (each illegal key → 400 + exact reason + byte-identical
  settings), and the `media` capability block's zero-credential assertion.
- **E2E** (`tmp/e2e_car4c.py`, tmp harness): real edge-tts synthesis →
  `/api/stt` → key words present (the ASR's "a dog" rendering of "aidog"
  is tolerated, not asserted); a 61 s fixture refused with 422 + honest
  reason; a wake utterance matched through a replica of the console matcher
  driven by **settings** phrases (with a negative control, and the default-
  phrase/ASR gap printed as a finding); an optional Playwright leg with
  fake-mic flags records the wake audio in-page and POSTs the real
  MediaRecorder blob (missing tooling degrades to SKIP, never FAIL).
  Settings and `secrets.env` are snapshotted and restored byte-for-byte,
  smoke re-run green at the end.
