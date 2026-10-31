# Feature spec — Streaming / typing UX + console chat (task 2 / Phase B1)

Status: **implemented** (2026-10-20). Ships the first half of the Phase B
interaction package: a model reply that *starts arriving* instead of appearing
all at once, on both channels — a Telegram `typing` heartbeat while a turn
runs, and a streaming agent chat inside the web console.

**Honest boundary:** deltas are a *preview*. The `done` event (and
`TurnResult.text`) always carries the authoritative final text and the browser
replaces its preview wholesale — a tool turn may emit intermediate text that
must never be mistaken for the answer. Nothing here changes what the loop can
do; it only changes how fast the user sees it.

## 1. Provider streaming (`mycore/model.py`)

`ChatClient.chat(messages, tools, **opts)` keeps its contract. One optional
key was added:

| key | effect |
|---|---|
| `on_delta: Callable[[str], None]` | absent/uncallable → **the classic whole-body path, byte for byte the previous behavior** (zero regression by construction). Callable → payload gains `"stream": true` and the stream transport is used. |

- `StreamTransport = (url, headers, payload, timeout, on_line) -> str` — a
  line-by-line POST read (`_urllib_stream_transport`) that hands each line to
  `on_line` as it lands and returns the full body text. Errors are mapped
  exactly like the non-stream transport: `HTTP <code> <reason>` / `transport
  failure: <ExceptionClass>` — never a response body, never the key.
- `_SseAssembler` folds the chunk stream into **the same `chat.completions`
  dict shape** the classic path returns, so `loop.py` needs no provider
  knowledge:
  - `delta.content` pieces are concatenated into `choices[0].message.content`
    and each piece is forwarded to `on_delta` as it lands;
  - `delta.tool_calls` fragments are merged **by `index`**: `id` and
    `function.name` take the first non-empty value (the local gateway sends
    them once, then `null`; a provider that repeats them every chunk must not
    accumulate duplicates), `function.arguments` fragments are concatenated
    (that is the field that actually splits);
  - `finish_reason`, `id`, `model`, `created` are captured; SSE framing rules
    apply (`data:` lines accumulate until a blank line; `event:`/`id:`/`retry:`
    and `:comment` lines are ignored; `[DONE]` closes the stream).
- **Degradation (honest, never a fake):** if the endpoint ignores `stream`
  and answers one plain JSON document, that document is returned unchanged and
  the whole text is reported as a *single* `on_delta` call. If the body is
  neither usable SSE nor JSON → `ModelError` (same message as before).

Probed 2026-10-20: the local gateway (`127.0.0.1:3460`) **does** stream —
`text/event-stream`, one JSON chunk per `data:` line, `reasoning_content`
present in deltas (ignored: only `content` is surfaced), tool calls delivered
as complete `id`/`name` in the first chunk with `arguments` split across
subsequent chunks.

## 2. Loop (`mycore/loop.py`)

`run_turn(..., on_delta=None)` forwards `on_delta` to `chat()` **only when it
is set** — a chat callable that knows nothing about streaming keeps working
unchanged. The kwarg is attached to *every* model call of the turn (each
iteration streams); intermediate previews are superseded by the final
authoritative text.

## 3. Telegram typing heartbeat (`mycore/tg.py` + `mycore/service.py`)

- `TgClient.send_chat_action(chat_id, action="typing")` → `sendChatAction`
  (stateless, safe alongside the live gateway, same sanitizing error path as
  `sendMessage`).
- `ChatService._typing_heartbeat(chat_id)` starts a daemon thread that beats
  immediately and then every `TYPING_INTERVAL_SEC` (4 s, Telegram's indicator
  lives ~5 s). It is started by `_pipeline` only — **commands (`/help`, …)
  never trigger it** — and the returned stop callable is invoked in a
  `finally`, so the beat dies with the turn.
- **Failure contract:** silent. `dry_run` starts no thread at all; any
  transport/API error ends the loop immediately with no log line and no
  exception — a lost heartbeat must never cost a reply.

## 4. Console chat (`web/server.py` + console UI)

| Route | Auth | Answer |
|---|---|---|
| `POST /api/chat` `{text}` | Bearer `WEB_TOKEN` | SSE stream (below) |
| `POST /api/chat` `{action:"clear"}` | Bearer `WEB_TOKEN` | `{"ok": true, "archived": bool}` — rotates the session into `sessions/archive/` (same rule as TG `/reset`) |
| `GET /api/chat/history` | none (loopback read) | `{messages:[{role,content}], count}` — **user/assistant turns only** (system constitution and tool payloads never leave the server), content capped at 2000 chars |

SSE contract (HTTP/1.0 handler: no `Content-Length`, `Connection: close`, so
the browser's `fetch` reader runs until EOF):

```
event: meta   data: {"model": "..."}
event: delta  data: {"text": "..."}          (repeat, live preview)
event: tool   data: {"name": "...", "ok": null|bool}   (call / result)
event: done   data: {"text","stop","iterations","tool_calls"}
event: error  data: {"error": "internal"}    (only if the turn dies mid-stream)
```

- **401 / 400 are answered before the stream starts** (plain JSON) — an
  unauthorized client never gets an open connection to read.
- The turn uses the *same* pieces as the TG pipeline: `build_system_stack()`
  + the session file + `run_turn()` + the 8-tool registry + the same
  compaction budget rule (`mycore.budget` from settings, cli default
  otherwise — **schema-managed since 0.5.40**: the key is validated and
  editable in the console, so the console view and the service view agree).
  Session file: `<state>/sessions/web.jsonl` (state =
  `AIDOG_MC_STATE`, default `data/mycore_service`; numeric file names there
  belong to TG chat_ids, so `web` cannot collide).
- Tool **arguments are never sent to the browser** (machine paths) — only
  names and ok/fail, mirroring the service-log rule.
- Client disconnect: SSE writes are best-effort (`_sse` returns False), the
  server finishes the model call, persists the turn, and stops observing.

### UI

New `CHAT` card between the three-column grid and the skill armory: message
bubbles, streaming append with a pending caret, tool chips, SEND/CLEAR, Enter
to send, `token required / 401 / stream failed` inline status, iteration +
tool-call count under the final answer, history restored at boot. All strings
come from the locale packs (`sec.chat`, `chat.*`).

## 5. Config carriers made first-class (activation car, 0.5.40)

- **`mycore.models` + `mycore.budget` are schema keys now**: they join
  `MYCORE_KEYS`, so `GET/POST /api/settings` accept them (models: ≤32
  single-line ids, `[]` legal; budget: int 4000..1000000, bool refused) and
  the console exposes both in Settings → Model (`#mycModels`, `#mycBudget`).
  The raw-file readers (`service._budget`, `service._allowed_models`,
  `subagent._allowed_models`, ACP) consume the very document the console
  writes — one source of truth for both channels.
- **A console save can no longer destroy hand-written keys**: the settings
  merge lands on the RAW stored document (unknown keys such as `zzz_custom`
  survive), while `GET` keeps returning the validated view.
- **The chat model key reaches the client**: `mycore/model.py::_headers`
  resolves env first, then the own-line `MODEL_API_KEY` secrets slot — the
  store the console writes. Env still wins; loopback keeps its placeholder;
  no value is ever logged or echoed.

Evidence: `tmp/e2e_activation.py` `L2` (schema matrix + raw consumer +
unknown-key survival) and `L8` (env → slot → placeholder, in-process),
`tmp/e2e_activation_ui.js` `U6` (save → server read-back + restart hint) —
see [frontend_coverage.md](frontend_coverage.md) §4.

## 6. Ride-along items (same car)

- **Weekday localization**: `wd.mon … wd.sun` keys in both packs; the TG
  `/evolution` head line and the console `evo.status` line now render the
  configured weekday in the active language (previously raw `sun` leaked into
  zh output).
- **Site `?lang=`**: `site/assets/main.js` accepts `?lang=en|zh` (validated)
  *over* the saved preference, and the language button keeps the URL in sync
  via `history.replaceState` — links are shareable in a language. The console
  applies the same precedence (`QS_LANG` first).

## 7. Gates

| Suite | Before | After |
|---|---|---|
| `python3 -m mycore.tests.run` | 240/240 | **286/286** (SSE assembly, non-SSE fallback, error mapping, loop kwarg pass-through, `sendChatAction`, typing heartbeat start/stop/dry-run/silent-failure) |
| `python3 tests/web_console_p3.py` | 46/46 | **57/57** (401/400 gates, history shape, one **live model SSE turn** ≥1 delta + done, persistence, clear/archive) |
| router / memory / srclint | 15/15 · 58/58 · 0 | unchanged |

The live-SSE smoke case is deliberately real: if the local gateway is down it
goes red, and that red is a true signal rather than flake.
