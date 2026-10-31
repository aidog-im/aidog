# OpenAI-Compatible API Server (v1)

Status: shipped in car 4a (`web/server.py`). Read the companion page
[capabilities.md](capabilities.md) for the catalog endpoint that describes
what this gateway can reach.

The console exposes an OpenAI-shaped HTTP gateway so stock OpenAI clients
(base URL `http://127.0.0.1:8790/v1`) can drive the same agent loop the
console chat uses — one gateway, one loop, one tool registry.

## Endpoints

| Method | Path | Auth | Answer |
|---|---|---|---|
| GET | `/v1/models` | Bearer | `{"object":"list","data":[{id, object:"model", created, owned_by}]}` with exactly one entry: the **actually configured** model |
| POST | `/v1/chat/completions` | Bearer | `chat.completion` JSON, or `chat.completion.chunk` SSE when `"stream": true` |

`created` on `/v1/models` is the product build time (mtime of `VERSION`) —
not a provenance claim about any upstream model.

## Authentication

- Dedicated slot: `OPENAI_API_TOKEN`, an own line in `secrets.env`. It is
  **independent** of every other slot (web token, channel slots).
- Missing slot → `503` with an error body that **names the slot**
  (`OPENAI_API_TOKEN not configured in secrets.env`).
- Wrong or absent `Authorization: Bearer …` → `401`
  `{"error":{"message":"unauthorized","type":"invalid_request_error","code":null}}`.
- The slot value is read per request (`read_env_slot`) and is never echoed
  into any response, log line, or exception (zero-echo red line).

## Message folding rules (stateless gateway)

The OpenAI client brings its own `messages` array. The gateway folds it into
exactly **one** agent turn:

1. `messages` must be a non-empty array of objects with a string `role`.
2. The **last** message must be `role:"user"` and carry non-empty text
   (string, or an array of `{type:"text",text}` parts, concatenated). That
   message is this turn's input. Otherwise → `400`.
3. Client `system` messages anywhere in the array are concatenated
   (blank-line joined) into **one supplementary system line appended after
   our own system stack** — our constitution/stance always leads.
4. Earlier `user`/`assistant` messages become this request's history, in
   order.
5. Other roles (`tool`, `function`) and unrepresentable contents are
   **dropped**: v1 carries no client-side tool transcripts.

**Stateless by design**: nothing is persisted to any session file — each
request is a fresh gateway call (no `/api/chat` session interaction, no
console history growth). In-request history is honored, cross-request
continuity is the client's job (it re-sends its own transcript).

Our system stack is built with the same `build_system_stack` +
`build_registry` + `ChatClient` path as `POST /api/chat`, so the gateway
turn has the full tool set, compaction budget, and failover chain of the
console.

## Response shapes

Non-stream (200):

```json
{"id":"chatcmpl-…","object":"chat.completion","created":1792650000,
 "model":"<configured model>",
 "choices":[{"index":0,"message":{"role":"assistant","content":"…"},
             "finish_reason":"stop"}],
 "usage":{"prompt_tokens":123,"completion_tokens":4,"total_tokens":127}}
```

- `finish_reason`: `stop` for a normal final answer, `length` when the agent
  hit its iteration cap (our loop's honest stop mapped onto the closest
  OpenAI reason). `model_error` never reaches this shape — it becomes a 502.
- `model` **echoes the configured model**; a `model` field in the request is
  accepted and ignored (never matched, never selected).

Stream (`"stream": true`, SSE, `data:` frames only):

1. role chunk — `delta:{"role":"assistant","content":""}`
2. content chunks — `delta:{"content": "…"}` from the live preview
3. finish chunk — empty `delta` + `finish_reason`
4. optional usage chunk (`choices: []` + `usage`) **iff** the request sent
   `stream_options: {"include_usage": true}`
5. `data: [DONE]`

Model failure on the stream → a single
`data: {"error":{…,"type":"server_error"}}` frame before `[DONE]`; the
stream never raises through the socket.

## Usage accounting (declared approximate)

We run **no tokenizer**, so token counts are honestly approximate:

- `prompt_tokens` = `ceil(prompt_chars / 4)`
- `completion_tokens` = `ceil(completion_chars / 4)`
- `total_tokens` = exact sum of the two reported integers

`prompt_chars` is the total character length of the message list handed to
the model (our stack + folded client system line + history + input).
**This is the `chars/4` heuristic — approximate by construction, never a
claim of exact counts.** The alternative (reporting `0`) was rejected
because a labelled industry-standard estimate carries more information than
a fake-zero that reads like "no tokens were used".

## v1 limitations (stated, not hidden)

- **Client `tools` are not executed.** A `tools` array is accepted and
  silently unused — our agent dispatches its **own** registry tools.
  Returning client tool_calls would require executing foreign code we never
  run.
- **`model` is ignored** (see above); `/v1/models` always lists the
  configured model.
- **No cross-request state** (stateless folding, no session persistence).
- **Ignored sampling params**: `temperature`, `top_p`, `n`, `stop`,
  `response_format`, etc. are accepted without effect (single-candidate,
  loop-controlled generation).
- **Multimodal content parts** (image/audio) are not representable on this
  path and are dropped by the fold rules.
- Body cap: `MAX_BODY` (16384 bytes) → `413` OpenAI-shaped; input text cap:
  `CHAT_MAX_CHARS` (4000) → `400` OpenAI-shaped.

## Error mapping

| Condition | HTTP | Body |
|---|---|---|
| slot missing | 503 | `{"error":{"message":"OPENAI_API_TOKEN not configured in secrets.env","type":"server_error","code":null}}` |
| bad bearer | 401 | `{"error":{"message":"unauthorized","type":"invalid_request_error","code":null}}` |
| malformed body / messages / cap exceeded | 400 (413 for oversize) | `{error:{message,type:"invalid_request_error",code:null}}` |
| model call failure (non-stream) | 502 | `{error:{message:"[mycore: model call failed …]","type":"server_error",code:null}}` — the loop's vetted note (status/class names only, never keys) |
| model failure (stream) | 200 + error frame | `data: {"error":{…,"type":"server_error"}}` then `[DONE]` |

## Testing evidence

`tests/web_console_p3.py` (web smoke) drives all three auth states against
the live console — missing slot 503 naming the slot, wrong bearer 401 with
zero echo, correct bearer 200 — plus the completion shape (live model turn),
the `model`-ignored echo with a `tools` array in the same request, SSE frame
order ending in `[DONE]`, the 400 legs, `usage` presence, and this file's
`chars/4` declaration matching the implementation. The dedicated slot is
snapshotted and restored byte-for-byte around the run.

**Console coverage (frontend car, 0.5.39)**: `OPENAI_API_TOKEN` now has a
`gateway`-group row in the Settings credential editor — presence and length
only, the value never serializes; the 503/401 contract above is unchanged.
Evidence: `tmp/e2e_frontend_coverage.py` `L1` (slot catalog + set/clear
roundtrip) and `tmp/e2e_frontend_ui.js` `U2` (editor + DOM value scan) —
see [frontend_coverage.md](frontend_coverage.md).
