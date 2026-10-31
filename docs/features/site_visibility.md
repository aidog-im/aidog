# Site visibility (aidog.im): PWA icons, AI-readable surface, visit counter

Scope: everything under `site/` that makes the marketing site installable,
machine-readable and countable. Design decisions and degradation semantics
for the three pieces shipped together (owner instruction, 2026-10-22).

## 1. PWA — installable, correct "Add to Home Screen" logo

Problem: the page only linked a 128 px `favicon.png`; mobile install prompts
therefore picked a wrong/low-res image. The canonical logo is
`site/assets/logo.jpg` (1192x1167, opaque, background already the page white).

Generated assets (`site/assets/icons/`, derived from `logo.jpg` with PIL —
regenerate any time by re-running the same script; sizes are asserted by
`deploy/deploy_site.sh --check` reaching them over the network):

| File | Size | Treatment |
|---|---|---|
| `icon-192.png` | 192x192 | square centre crop, full bleed |
| `icon-512.png` | 512x512 | square centre crop, full bleed |
| `icon-maskable-512.png` | 512x512 | logo at ~76% (12% safe-zone padding) on opaque `#fcfcfa`, `purpose: maskable` |
| `apple-touch-icon.png` | 180x180 | opaque `#fcfcfa` base + cover logo (JPEG has no alpha, so the base colour is painted first) |

`site/manifest.webmanifest`: name `I'm AiDog`, short name `AiDog`,
`start_url: /`, `display: standalone`, `background_color: #fcfcfa`,
`theme_color: #2892ff`, all four icons (maskable carries
`"purpose": "maskable"`). `<head>` links: `rel=manifest`,
`rel=apple-touch-icon`, `<meta name="theme-color" content="#2892ff">`.

## 2. AI-readable surface (pure English, verifiable facts only)

- `site/llms.txt` — llmstxt.org format: H1 + one-line blockquote positioning,
  then `## Product` / `## Interfaces` / `## Live stats` / `## Links`.
  Policy: only facts a reader can verify on the live site (permanently free,
  local-first console, self-developed core, Telegram-first channels, section
  anchors, the visits endpoint contract). No version numbers unless verified,
  no third-party brand words (the publish gate scans for them), no claims
  about endpoints that do not exist.
- `site/robots.txt` — `User-agent: * / Allow: /` plus the sitemap line.
- `site/sitemap.xml` — single URL `https://aidog.im/` with `lastmod`.

## 3. Visit counter (record + public display, Cloudflare free tier)

### Architecture

- **Storage**: Workers KV namespace `aidog-site-visits` (free tier — enough
  quota for a personal site; no paid plan anywhere in this product).
  Created/looked up idempotently by title in `deploy/deploy_site.sh`.
- **Endpoint**: `site/functions/api/visits.js`, a Pages Function (bundled into
  the Pages project; no extra Worker, no separate hostname).
  - `GET /api/visits` → `200`
    `{"count":<int>,"unit":"page views","note":"approximate, includes crawlers and refreshes"}`
  - `GET /api/visits?format=text` → `200` `text/plain` body `visits: <int>`
    (the AI/CLI-friendly form).
  - `Cache-Control: no-store` on every response.
  - **Failure semantics (red line)**: KV binding missing or a KV call throws →
    `503 {"error":"counter not configured"}`. The function never returns a
    fabricated number; the frontend never renders a fabricated number.
- **Binding**: KV is attached to the Pages project's `deployment_configs`
  (both `production` and `preview`) as binding name `VISITS`, via
  `PATCH /accounts/{id}/pages/projects/aidog-site` with
  `{"deployment_configs":{...,"kv_namespaces":{"VISITS":{"namespace_id":...}}}}`.
  The ensure step in `deploy/deploy_site.sh` merges into the *existing*
  deployment config (GET → merge → PATCH), so re-runs are idempotent and can
  never drop other bindings/vars.
- **Wrangler quirk (measured)**: `wrangler pages deploy` resolves the Pages
  Functions directory as `<process cwd>/functions`, *not* `<output>/functions`.
  The deploy step therefore runs from `site/`, where `functions/api/visits.js`
  lives. The functions directory is on wrangler's static-asset ignore list, so
  it is compiled into the Functions bundle only — fetching
  `/functions/api/visits.js` publicly falls back to `index.html` (no source
  exposure), which the deploy verification relies on.

### Counting semantics (honest scope)

One increment per endpoint GET. The footer block fetches once per page load,
so loads, refreshes and crawlers all count — approximate by design, stated in
the `note` field and in `llms.txt`. Deploy verification itself performs two
GETs (that is how increment is asserted).

### Frontend + i18n

- `site/index.html` footer: `<span id="visitsWrap" hidden>` wrapping a
  `data-i18n="footer.views"` label and `<span id="visits">` count.
- `site/assets/main.js`: one `fetch("/api/visits", {cache:"no-store"})` on
  load; on any failure (network error, non-2xx, non-numeric count) the whole
  block stays `hidden`. Success reveals it with the integer.
- Locale packs (`site/i18n/en.json`, `zh.json`, 262 keys each, same-name
  parity): `footer.views` = `"Page views:"` / `"页面浏览："`.

### Degradation ladder (what happens when a permission is missing)

1. KV `Storage:Edit` on the API token → namespace + binding ensured → live
   200 counter (current state; token verified to hold both KV and Pages edit).
2. Namespace/binding ensure fails → deploy still succeeds; `/api/visits`
   answers `503` honestly, `deploy_site.sh --check` prints `PENDING` (not a
   failure), footer block hidden, report carries the missing permission.
3. Worker custom-domain fallback and "hide the block" are the last resorts;
   no path ever displays a fake zero.

## 4. Verification gates

- `bash deploy/deploy_site.sh --check` (no writes) publicly asserts:
  `/llms.txt` 200 + first line `# I'm AiDog`, `/robots.txt`, `/sitemap.xml`,
  `/manifest.webmanifest` 200 + valid JSON with `name`/`icons`,
  `/assets/icons/icon-512.png`, `/assets/icons/apple-touch-icon.png`,
  `/api/visits` 200 with two-GET increment + `?format=text` (or `PENDING`
  with an `error` body when the binding is absent). All curl calls use
  `--noproxy '*'`.
- `node tmp/shoot_site.js` (Playwright, local static server) — 62 assertions,
  including per-shot: manifest link in head, apple-touch-icon in head,
  theme-color `#2892ff`, footer visits block `hidden` without an endpoint or
  strictly numeric with one. The local server has no Pages Function, so that
  one legitimate 404 is filtered out of the js-clean console check (with a
  comment in the script); the real counter is asserted against the public
  domain by the deploy check instead.
