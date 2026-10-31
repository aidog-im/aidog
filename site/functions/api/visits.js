// Pages Function — site-wide page-view counter (D1, free tier; migrated
// from Workers KV in the D2 car — KV's 1,000 writes/day was the binding
// constraint, D1 allows 100,000 rows/day; docs/design/usage_ledger_design.md).
//
//   GET /api/visits             -> 200 {"count":N,"unit":"page views",
//                                       "note":"approximate, includes crawlers and refreshes"}
//   GET /api/visits?format=text -> 200 text/plain "visits: N"
//   D1 binding missing / D1 call fails
//                              -> 503 {"error":"counter not configured"}
//
// Counting semantics: one increment per request. Page loads, refreshers and
// crawlers all count — approximate by design (documented in site/llms.txt).
// Failure semantics are strict: when the counter cannot be incremented we
// answer 503 with an error object; we never fabricate a 0 (or any other
// number) to look like a working counter. `cache-control: no-store` keeps
// the CDN from serving a stale count.
//
// Binding: D1 database bound as LEDGER (both environments); the historical
// KV count is seeded once by deploy/deploy_site.sh (max-seed), so the
// public number continues from its history instead of restarting at 0.
// The KV namespace/binding stay provisioned for instant rollback this car.

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
  const db = env.LEDGER;
  if (!db || typeof db.prepare !== "function") {
    return json({ error: "counter not configured" }, 503);
  }

  let row = null;
  try {
    row = await db
      .prepare(
        "INSERT INTO site_visits (name, count) VALUES ('count', 1)" +
          " ON CONFLICT(name) DO UPDATE SET count = count + 1" +
          " RETURNING count"
      )
      .first("count");
  } catch (err) {
    return json({ error: "counter not configured" }, 503);
  }
  if (row === null || row === undefined) {
    return json({ error: "counter not configured" }, 503);
  }
  const count = Number(row);
  if (!Number.isFinite(count) || count < 0) {
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
