/**
 * aidog bridge — a reverse proxy YOU own and YOU deploy (car 6, ruling D9).
 *
 * Why it exists: the agent sometimes needs a URL that this machine cannot
 * reach. This Worker runs on your own Cloudflare account, on a sub-domain you
 * create, and forwards GET requests to targets you explicitly allow. The
 * product never operates a relay for you: you deploy this, you own its
 * ToS/legal exposure, and you can delete it at any time.
 *
 * Guardrails baked in here (they do not depend on the product's settings):
 *   - EXPLICIT ALLOW-LIST: a host that is not in ALLOW is refused with 403.
 *     There is no open-proxy mode and no wildcard default.
 *   - NO CREDENTIAL FORWARDING: only the headers listed in FORWARD_HEADERS
 *     are forwarded, deny by default. An incoming Authorization/Cookie is
 *     dropped unless the target host is listed in ALLOW_AUTH.
 *   - RATE CAP: RATE_MAX requests per RATE_WINDOW_MS per client, best effort.
 *   - SIZE CAP: a response body larger than MAX_BODY is truncated, never
 *     buffered whole.
 *   - GET/HEAD only: this bridge never executes a write on your behalf.
 *
 * Deploy (from your machine, inside the product repo):
 *   1. edit ALLOW below and save
 *   2. npx wrangler login
 *   3. npx wrangler deploy deploy/bridge/worker.js \
 *        --name aidog-bridge --compatibility-date 2026-10-01
 *   4. paste the printed https://<name>.<account>.workers.dev URL into the
 *      console's Settings -> Connections -> Bridge card, add the same hosts
 *      to its target allow-list, switch it on.
 */

// ---- edit me: the only hosts this Worker will ever talk to -----------------
const ALLOW = new Set([
  // "api.example.com",
  // "*.internal.example.com",   // sub-domains only; the apex needs its own line
]);

// Hosts a credential MAY be sent to. Empty by default: credentials are
// stripped from every other request, whatever the client asked for.
const ALLOW_AUTH = new Set([]);

// Request headers worth forwarding. Anything else (Cookie, Authorization,
// X-*, ...) is dropped unless the host is in ALLOW_AUTH.
const FORWARD_HEADERS = ["accept", "accept-language", "content-type", "user-agent"];

// Response headers worth returning. Content-Encoding / Content-Length are
// deliberately absent: the body is already decoded and may be truncated.
const RETURN_HEADERS = ["content-type", "cache-control", "etag",
                        "last-modified", "x-robots-tag"];

const MAX_BODY = 2 * 1024 * 1024;   // bytes returned per response
const TIMEOUT_MS = 15000;           // one upstream request
const RATE_WINDOW_MS = 60_000;      // sliding window
const RATE_MAX = 60;                // requests per window per client

const hits = new Map();             // client -> [windowStart, count]

function rateLimited(key) {
  const now = Date.now();
  const row = hits.get(key);
  if (!row || now - row[0] > RATE_WINDOW_MS) {
    hits.set(key, [now, 1]);
    return false;
  }
  row[1] += 1;
  if (hits.size > 10000) hits.clear();   // bounded memory, best effort
  return row[1] > RATE_MAX;
}

function refuse(status, message) {
  return new Response(message, {
    status,
    headers: { "content-type": "text/plain; charset=utf-8",
               "x-bridge": "refused" },
  });
}

function hostAllowed(host) {
  const h = String(host || "").toLowerCase().replace(/\.$/, "");
  if (!h) return false;
  for (const pat of ALLOW) {
    const p = String(pat).toLowerCase().replace(/\.$/, "");
    if (p.startsWith("*.")) {
      const base = p.slice(2);
      if (base && h.endsWith("." + base)) return true;   // sub-domain only
    } else if (h === p) {
      return true;
    }
  }
  return false;
}

export default {
  async fetch(request) {
    if (request.method !== "GET" && request.method !== "HEAD") {
      return refuse(405, "this bridge only forwards GET and HEAD");
    }

    const client = request.headers.get("cf-connecting-ip")
      || request.headers.get("x-forwarded-for") || "unknown";
    if (rateLimited(String(client).split(",")[0].trim())) {
      return refuse(429, "rate limit: too many proxied requests");
    }

    // The target rides in the path: /https://host/path?query
    const self = new URL(request.url);
    const raw = self.pathname.replace(/^\//, "") + self.search;
    if (!/^https?:\/\//i.test(raw) || raw.length > 2048) {
      return refuse(400, "target must be an http(s) URL in the path");
    }

    let target;
    try {
      target = new URL(raw);
    } catch (e) {
      return refuse(400, "target is not a valid URL");
    }
    if (target.protocol !== "http:" && target.protocol !== "https:") {
      return refuse(400, "only http(s) targets are allowed");
    }
    if (!hostAllowed(target.hostname)) {
      return refuse(403, "target host is not on this Worker's allow-list");
    }

    const headers = new Headers();
    for (const name of FORWARD_HEADERS) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    const mayForwardAuth = ALLOW_AUTH.has(target.hostname.toLowerCase());
    if (mayForwardAuth) {
      const auth = request.headers.get("authorization");
      if (auth) headers.set("authorization", auth);
      const cookie = request.headers.get("cookie");
      if (cookie) headers.set("cookie", cookie);
    }

    let upstream;
    try {
      upstream = await fetch(target.toString(), {
        method: request.method,
        headers,
        redirect: "follow",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (e) {
      return refuse(502, "upstream unreachable: " + (e && e.name ? e.name : "error"));
    }

    // Stream up to MAX_BODY bytes: a huge response is cut, never buffered.
    const chunks = [];
    let total = 0;
    let truncated = false;
    if (upstream.body) {
      const reader = upstream.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        if (total + value.byteLength > MAX_BODY) {
          const slice = value.subarray(0, Math.max(0, MAX_BODY - total));
          if (slice.byteLength) chunks.push(slice);
          total += slice.byteLength;
          truncated = true;
          try { await reader.cancel(); } catch (e) { /* already closed */ }
          break;
        }
        chunks.push(value);
        total += value.byteLength;
      }
    }

    const out = new Headers();
    for (const name of RETURN_HEADERS) {
      const value = upstream.headers.get(name);
      if (value) out.set(name, value);
    }
    out.set("x-bridge-status", String(upstream.status));
    out.set("x-bridge-bytes", String(total));
    out.set("x-bridge-truncated", truncated ? "1" : "0");
    out.set("access-control-allow-origin", "*");

    return new Response(request.method === "HEAD" ? null : concat(chunks, total),
                        { status: upstream.status, headers: out });
  },
};

function concat(chunks, total) {
  const out = new Uint8Array(total);
  let at = 0;
  for (const chunk of chunks) {
    out.set(chunk, at);
    at += chunk.byteLength;
  }
  return out;
}
