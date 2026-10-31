# Bridge — a reverse proxy you own (optional)

Some URLs the agent needs are not reachable from the machine it runs on. The
bridge answers that with a Cloudflare Worker **you** deploy on **your own**
account, on a sub-domain **you** create. The product never operates a relay:
it ships the template, the configuration fields, an enable switch and one
audit line per proxied request.

## What you get

| Piece | Where |
|---|---|
| Worker template | `deploy/bridge/worker.js` (also served by `GET /api/bridge/template`) |
| Target allow-list, enable switch, size cap | Settings -> Connections -> Bridge |
| One audit line per proxied request | `<state>/bridge_audit.jsonl` + the Bridge card tail |
| Kill switch | the Bridge card's **DISABLE NOW** button (one click) |

## Guardrails

- **Explicit allow-list, no open proxy.** The Worker refuses any host that is
  not in its own `ALLOW` set, even if someone reaches it directly. The
  product's allow-list decides which requests are routed; the Worker's copy
  decides what it will ever touch. Keep them equal.
- **No credential forwarding.** The product never attaches a credential to a
  bridge request. The Worker forwards only an explicit request-header
  allow-list and drops `Authorization` / `Cookie` unless the host is listed in
  `ALLOW_AUTH`.
- **Rate and size caps.** `RATE_MAX` per `RATE_WINDOW_MS` per client inside
  the Worker; `max_bytes` bounds one proxied read on the product side; a body
  over `MAX_BODY` is truncated, never buffered whole.
- **Visible audit trail.** One JSON line per attempt: timestamp, host, path,
  byte count, ok, reason word. Query strings are never recorded — they often
  carry a token.
- **GET/HEAD only.** The bridge never executes a write on your behalf.

## Deploy

```bash
# 1. edit ALLOW in worker.js and save
# 2. authenticate once
npx wrangler login
# 3. deploy
npx wrangler deploy deploy/bridge/worker.js \
  --name aidog-bridge --compatibility-date 2026-10-01
# 4. copy the printed https://<name>.<account>.workers.dev URL into
#    Settings -> Connections -> Bridge, add the same hosts to its
#    target allow-list, and switch the bridge on.
```

Deleting the Worker from your account removes the bridge completely; the
product falls back to direct requests and says so honestly.

## Ownership

The template is part of this repository and carries this repository's
license. It runs in *your* Cloudflare account under *your* terms; you are the
operator of anything you deploy.
