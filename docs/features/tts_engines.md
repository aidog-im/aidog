# TTS Engines (the ×10 matrix)

Status: shipped in car 4c (engine parameter on `POST /api/say` +
`settings.media.tts_engine` in `web/server.py`). Companion pages:
[voice_announcements.md](voice_announcements.md) (the advisory speech chain),
[voice_session.md](voice_session.md) (spoken replies), [capabilities.md](capabilities.md)
(`media.tts` block).

Facts about engines we did **not** adopt come from the shortlist research in
`docs/research/aidog_voice_and_clients.md` (captured sources, claims marked
there as *Not found / Not verified* stay unverified here) — this document
invents no facts about them and their current status is recorded in that
file's *Implementation status (car 4c)* section.

## 1. The matrix (10 rows, honestly stated)

| # | Engine | Class | Status | Notes / source |
|---|---|---|---|---|
| 1 | **edge-tts** | free online service | ✅ **implemented — default engine** (`engine=edge`, default) | Python CLI wrapping the Edge online TTS service (pypi capture in the shortlist §B); SSML customization limited to a single `<voice>` + `<prosody>` tag; needs network (proxy honored from env) |
| 2 | **BYOK OpenAI-compatible speech** (`/v1/audio/speech`) | operator's endpoint (free or paid — theirs) | ✅ **implemented** (`engine=byok`, opt-in per request or via settings) | our own leg: `POST {base}/audio/speech` `{model, voice, input}`; key = `TTS_API_KEY` → `MODEL_API_KEY`; loopback may run keyless, remote fail-closed |
| 3 | **Browser `speechSynthesis`** | local, zero-dependency | ✅ **implemented — client fallback** | the console `speak()` chain: `/api/say` fails → browser voice plays, state text says *which* voice spoke (never pretends the server spoke) |
| 4 | ElevenLabs | commercial API | ❌ not introduced — **paid: budget approval required** | shortlist §B: credits are the unit of consumption, TTS costs one credit per character of input; quality claims *Not verified* |
| 5 | Azure Speech TTS | commercial API | ❌ not introduced — **paid: budget approval required** | shortlist §B: official Learn docs captured, pricing *Not verified* in the capture; SSML-rich |
| 6 | Google cloud TTS | commercial API | ❌ **not evaluated** — no source captured | outside the shortlist research; this table claims nothing about it beyond "not adopted" |
| 7 | Piper | local neural TTS, MIT | ❌ not introduced — offline candidate only | shortlist §B: fast local system, but the repository was **archived by owner on 2025-10-06** (maintenance risk); our needs are covered by rows 1–3 |
| 8 | Coqui XTTS v2 | local voice-cloning, MPL-2.0 | ❌ not introduced — heavy local model + **rights review never done** | shortlist §B: needs `speaker_wav` reference + language; cloning a living actor's voice is a legal/ethical risk flagged for owner decision, not decided unilaterally; GPU needs *Not verified* |
| 9 | OpenAI TTS (official hosted) | commercial API | reachable through row 2; hosted service itself **Not verified** | shortlist §B: the docs fetch was blocked (403) — no facts recorded; using it = point `media.tts_base_url` at the official endpoint with your own key (engine=byok) |
| 10 | anything else | — | **not evaluated** — no facts claimed | engines outside this table are deliberately absent rather than guessed |

Paid usage (rows 4, 5, and row 9 *via the official hosted endpoint*) is
blocked by the owner cost decree — every paid action needs a budget report
first; nothing in this product ships a paid dependency (free-for-life rule).
Row 9's **mechanism** (the OpenAI-compatible protocol shape) is what row 2
implements against whatever endpoint the operator supplies. Local rows (7, 8)
are honest non-adoptions: no half-wired adapter, no slot, no claim of
support.

## 2. Engine selection and the no-silent-fallback rule

`POST /api/say` `{text, lang, engine?}`:

- `engine` is optional and **validated before the rate budget is spent**
  (a bad engine costs no synthesis slot): `edge | byok`, anything else → 400.
- absent → `settings.media.tts_engine` (default `edge`).
- **Server-side there is no engine fallback**: an unconfigured/failing
  `byok` answers **503 with the real reason** ("no base URL", "no credential
  slot for a remote endpoint", upstream HTTP line ≤160 chars) — we never
  pretend byok silently fell back to edge. `edge` absent → 503 `voice engine
  not installed`; engine failure → 502 with the honest detail.
- **Client-side fallback exists and is labeled**: the console `speak()` chain
  tries `/api/say`, then the browser's `speechSynthesis`, and the state text
  distinguishes *reply spoken* / *reply spoken by the browser voice* / a
  failure line — degradation is disclosed, not hidden.

Shared guards (both engines, unchanged from the edge pilot): JSON-only body,
loopback Origin, text ≤ 200 chars, sanitize through `sanitize_event_text`,
sliding **8 synth/min** window shared by both engines (429 + `Retry-After`).
Upstream audio cap for byok: 5 MB; timeout 25 s.

## 3. BYOK speech configuration

| Item | Source (first non-empty wins) |
|---|---|
| base URL | env `AIDOG_TTS_URL` → settings `media.tts_base_url` → settings `mycore.base_url` |
| key | `TTS_API_KEY` → `MODEL_API_KEY` (values never echoed/logged/part of any reason) |
| model | settings `media.tts_model` (≤64 chars, empty → engine default `tts-1`) |
| voice | fixed `alloy` (both languages — single voice slot) |

Credential policy mirrors image generation: a **loopback** base may run
keyless; any **remote** endpoint without a slot is fail-closed
(`byok: false` in the capability block). Response must be `audio/*`;
non-audio, oversized or transport failures → honest 503 with the reason.

`settings.media.tts_engine` is validated as `edge | byok` (illegal writes →
400 with the exact key, settings byte-identical). `GET /api/voice` reports
edge presence/voices/rate; `GET /api/capabilities` → `media.tts` reports
`engines {edge, byok}` booleans + `active` name — presence and names only,
no URL, no key (zero-credential promise, see [capabilities.md](capabilities.md)).

## 4. Honest boundaries

- edge-tts needs outbound network; on a blocked host it degrades to a real
  502/503 with the engine's own last stderr line (bounded), never fake audio.
- We ship **two engines + one browser fallback**, not ten — the matrix above
  is a truthful adoption ledger, not a claim of ten integrated engines.
- The shortlist's listening test against the research-file timbre reference
  remains an owner ear-test; voice A/B was never performed by us.
- No SSML surface: text is passed verbatim (via `--file`, never argv —
  process listings must not expose spoken content).

## 5. Verification (what actually exercises this)

- **Web smoke** (`tests/web_console_p3.py`): `/api/say` validation matrix
  (empty/overlong/bad-lang → 400 before the budget, non-JSON → 415,
  cross-origin → 403), live synthesis → `200 audio/mpeg` or an honest
  degradation code, `engine=bogus` → 400, `engine=byok` → real audio **or**
  a 503 carrying an `error` string (never a silent edge swap), plus the
  `media.tts_engine` settings-validation legs and the zero-credential
  `media.tts` capability assertion.
- **E2E** (`tmp/e2e_car4c.py`): `media.tts_base_url` pointed at a local mock
  `/audio/speech` answering ID3 audio → `POST /api/say engine=byok` →
  `200 audio/mpeg`, mock hit confirmed; settings snapshotted and restored
  byte-for-byte with a final green smoke re-run.
- The advisory/orb chain (rate window, cache, autoplay unlock) is covered by
  [voice_announcements.md](voice_announcements.md) §6 and is unchanged by
  this car.
- **Console coverage (frontend car, 0.5.39)**: `TTS_API_KEY` joins the
  `media` group of the Settings credential editor, so BYOK presence
  (`media.tts.byok`) is now visible as a slot row next to the engine/
  endpoint/model fields. Evidence: `tmp/e2e_frontend_coverage.py` `L1`
  (slot catalog + roundtrip + zero-echo sweep) and
  `tmp/e2e_frontend_ui.js` `U2` (editor + DOM value scan) — see
  [frontend_coverage.md](frontend_coverage.md).
