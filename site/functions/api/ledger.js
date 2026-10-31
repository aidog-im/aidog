// Pages Function — POST /api/ledger: one anonymous daily usage report per
// install, upserted into the D1 ledger table (D2 car).
//
//   POST body = the whitelist-exact report of docs/design/usage_ledger_design.md §3
//        -> 200 {"ok": true}
//        -> 400 {"error": ...}        (validation refused; nothing stored)
//        -> 503 {"error": "ledger not configured"}   (D1 binding missing)
//        -> 500 {"error": "write failed"}            (D1 error; honest)
//
// Semantics: last write wins per (install_id, day) — a same-day re-report
// replaces, which makes client retries and E2E replays idempotent. Canary
// rows (install_id starting with "_") are stored but never aggregated
// (see _ledger_lib.js). Rate discipline: body capped at 8KB, validation
// strict, unknown fields ignored by construction.
//
// Binding: D1 database bound as LEDGER (both environments); see
// deploy/deploy_site.sh (ensure + migration) and
// docs/design/usage_ledger_design.md.

import { json, validateReport } from "./_ledger_lib.js";

const BODY_MAX = 8192;

export async function onRequestPost({ request, env }) {
  const db = env.LEDGER;
  if (!db || typeof db.prepare !== "function") {
    return json({ error: "ledger not configured" }, 503);
  }
  let text = "";
  try {
    text = await request.text();
  } catch (err) {
    return json({ error: "bad body" }, 400);
  }
  if (text.length > BODY_MAX) {
    return json({ error: "body too large" }, 400);
  }
  let doc = null;
  try {
    doc = JSON.parse(text);
  } catch (err) {
    return json({ error: "invalid json" }, 400);
  }
  const v = validateReport(doc);
  if (!v.ok) return json({ error: v.error }, 400);
  const r = v.value;
  try {
    await db
      .prepare(
        "INSERT INTO ledger_daily (install_id, day, requests, tok_in, tok_out," +
          " models_json, scenarios_json, updated_at)" +
          " VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)" +
          " ON CONFLICT(install_id, day) DO UPDATE SET" +
          " requests = excluded.requests, tok_in = excluded.tok_in," +
          " tok_out = excluded.tok_out, models_json = excluded.models_json," +
          " scenarios_json = excluded.scenarios_json," +
          " updated_at = excluded.updated_at"
      )
      .bind(
        r.install_id,
        r.day,
        r.requests,
        r.tokens.in,
        r.tokens.out,
        JSON.stringify(r.models),
        JSON.stringify(r.scenarios),
        new Date().toISOString()
      )
      .run();
  } catch (err) {
    return json({ error: "write failed" }, 500);
  }
  return json({ ok: true }, 200);
}
