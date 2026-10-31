// Pages Function — site-wide page-view counter (Workers KV, free tier).
//
//   GET /api/visits             -> 200 {"count":N,"unit":"page views",
//                                       "note":"approximate, includes crawlers and refreshes"}
//   GET /api/visits?format=text -> 200 text/plain "visits: N"
//   KV binding missing / KV call fails
//                              -> 503 {"error":"counter not configured"}
//
// Counting semantics: one increment per request. Page loads, refreshes and
// crawlers all count — approximate by design (documented in site/llms.txt).
// Failure semantics are strict: when the counter cannot be incremented we
// answer 503 with an error object; we never fabricate a 0 (or any other
// number) to look like a working counter. `cache-control: no-store` keeps
// the CDN from serving a stale count.
//
// Binding: KV namespace "aidog-site-visits" attached to the Pages project as
// VISITS (both environments); see docs/features/site_visibility.md.

function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*"
    }
  });
}

export async function onRequestGet({ request, env }) {
  const kv = env.VISITS;
  if (!kv || typeof kv.get !== "function" || typeof kv.put !== "function") {
    return json({ error: "counter not configured" }, 503);
  }

  let count = 0;
  try {
    const raw = await kv.get("count");
    count = raw === null || raw === "" ? 0 : Number(raw);
    if (!Number.isFinite(count) || count < 0) count = 0;
    count += 1;
    await kv.put("count", String(count));
  } catch (err) {
    return json({ error: "counter not configured" }, 503);
  }

  const url = new URL(request.url);
  if (url.searchParams.get("format") === "text") {
    return new Response("visits: " + count + "\n", {
      status: 200,
      headers: {
        "content-type": "text/plain; charset=utf-8",
        "cache-control": "no-store",
        "access-control-allow-origin": "*"
      }
    });
  }
  return json(
    {
      count,
      unit: "page views",
      note: "approximate, includes crawlers and refreshes"
    },
    200
  );
}
