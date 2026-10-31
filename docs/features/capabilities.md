# Capabilities Catalog

Status: shipped in car 4a (`web/server.py`). Companion page:
[openai_api_server.md](openai_api_server.md).

## Endpoint

`GET /api/capabilities` — **read-only, no auth** (same contract as
`GET /api/status`: loopback status surfaces stay open, writes stay gated).

```json
{
  "version": "<VERSION file>",
  "mycore": {
    "model": "<configured model id>",
    "upstreams_configured": true,
    "mcp_servers": {"count": 2, "names": ["demo", "files"]}
  },
  "tools":   [{"name": "read_file", "description": "…"}],
  "skills":  [{"name": "…", "description": "…"}],
  "channels": {
    "all": ["discord","slack","email","webhook","dingtalk","feishu","wechat"],
    "enabled":   ["webhook"],
    "configured": []
  },
  "hooks": {"events": [], "count": 0},
  "cron":  {"jobs": 0}
}
```

Field sources:

| Field | Source | Notes |
|---|---|---|
| `version` | `VERSION` file | single source of truth |
| `mycore.model` / `upstreams_configured` | `cli.resolve_model_config()` | boolean only — the upstream spec (urls, env names) never serializes |
| `mycore.mcp_servers` | `mcp.parse_config(mcp.raw_config(...))` | **names + count only**; an invalid config reports `note`, never the raw text |
| `tools` | `build_registry().schema()` | names + descriptions of the agent's live tool set |
| `skills` | skill frontmatter discovery | read, never executed — same source as the console gallery |
| `channels` | `collect_channels()` + `channel_routes()` | `all` lists all 7 routes; `enabled`/`configured` are name lists (`configured()` consults slot **presence** only) |
| `hooks` | settings `hooks` block | absent block (before the event-hooks car) → empty list — honest, not fabricated |
| `cron` | scheduler state file | absent/corrupt registry → `0` |

## Zero-credential promise

The response contains **names, counts, and booleans only**:

- no `secrets.env` value ever appears (the web smoke asserts the serialized
  response against every slot value in the file);
- no credential-bearing URL, no raw MCP config, no upstream spec;
- `configured` is presence-of-a-slot, never the slot's content.

## Console display status

**Wave 1 = API only.** The catalog ships as an endpoint + this document; no
console DOM was touched in this car (the console app shell was owned by a
parallel workstream in the same wave, and the `getElementById` contract audit
cost more than a decorative read-only panel was worth). Planned display: a
read-only "Capabilities" line/panel fed from `/api/capabilities` (model,
tool count, skill count, enabled channels, cron jobs) — deferred to the
follow-up that owns `web/static/app.js`.

## Testing evidence

`tests/web_console_p3.py`: shape check (all six blocks present with their
inner types), the 7-route list, and the zero-credential assertion (response
text contains no slot value and no secret-like keys).
