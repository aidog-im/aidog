// Shared helpers for the D2 usage-ledger Pages Functions.
// (Underscore prefix = not a route; imported by ledger.js / stats.js and by
// the offline node probe tmp/d2_lib_probe.mjs.)
//
// The privacy whitelist is enforced HERE, once, for every writer:
// a report is exactly {v, install_id, day, requests, tokens{in,out},
// models[{model,requests,in,out}], scenarios{chat,coding,research,voice,
// publishing}}. Unknown keys are dropped, wrong types are refused. Prompts,
// file names, paths, URLs and credentials have no representation in this
// schema at all (docs/design/usage_ledger_design.md §5).

export const SCENARIOS = ["chat", "coding", "research", "voice", "publishing"];
export const MODELS_MAX = 32;
export const MODEL_NAME_MAX = 100;
export const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;
export const INSTALL_ID_RE = /^[A-Za-z0-9_-]{8,64}$/;

export function json(body, status) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
      "access-control-allow-origin": "*"
    }
  });
}

function boundedInt(value, min, max) {
  return typeof value === "number" && Number.isInteger(value) &&
    value >= min && value <= max;
}

// Day window: reports for a real date, never in the future (+1d slack for
// UTC edges), never older than 400 days (client retention is 90).
export function dayOk(day, nowMs) {
  if (typeof day !== "string" || !DAY_RE.test(day)) return false;
  const t = Date.parse(day + "T00:00:00Z");
  if (!Number.isFinite(t)) return false;
  return t <= nowMs + 86400000 && t >= nowMs - 400 * 86400000;
}

// Strict whitelist validation -> {ok:true, value} | {ok:false, error}.
export function validateReport(doc, nowMs) {
  nowMs = typeof nowMs === "number" ? nowMs : Date.now();
  if (!doc || typeof doc !== "object" || Array.isArray(doc)) {
    return { ok: false, error: "report must be an object" };
  }
  if (doc.v !== 1) return { ok: false, error: "unsupported report version" };
  if (typeof doc.install_id !== "string" || !INSTALL_ID_RE.test(doc.install_id)) {
    return { ok: false, error: "bad install_id" };
  }
  if (!dayOk(doc.day, nowMs)) return { ok: false, error: "bad day" };
  if (!boundedInt(doc.requests, 0, 10000000)) {
    return { ok: false, error: "bad requests" };
  }
  const tok = doc.tokens;
  if (!tok || typeof tok !== "object" ||
      !boundedInt(tok.in, 0, 1e15) || !boundedInt(tok.out, 0, 1e15)) {
    return { ok: false, error: "bad tokens" };
  }
  const models = [];
  if (!Array.isArray(doc.models) || doc.models.length > MODELS_MAX) {
    return { ok: false, error: "bad models" };
  }
  for (const m of doc.models) {
    if (!m || typeof m !== "object" || Array.isArray(m)) {
      return { ok: false, error: "bad model row" };
    }
    if (typeof m.model !== "string" || m.model.length === 0 ||
        m.model.length > MODEL_NAME_MAX) {
      return { ok: false, error: "bad model name" };
    }
    if (!boundedInt(m.requests, 0, 10000000) ||
        !boundedInt(m.in, 0, 1e15) || !boundedInt(m.out, 0, 1e15)) {
      return { ok: false, error: "bad model counts" };
    }
    models.push({ model: m.model, requests: m.requests, in: m.in, out: m.out });
  }
  const scenarios = {};
  for (const key of SCENARIOS) scenarios[key] = 0;
  if (doc.scenarios !== undefined) {
    const sc = doc.scenarios;
    if (!sc || typeof sc !== "object" || Array.isArray(sc)) {
      return { ok: false, error: "bad scenarios" };
    }
    for (const key of SCENARIOS) {
      if (sc[key] === undefined) continue;
      if (!boundedInt(sc[key], 0, 1e9)) return { ok: false, error: "bad scenario count" };
      scenarios[key] = sc[key];
    }
  }
  return {
    ok: true,
    value: {
      v: 1,
      install_id: doc.install_id,
      day: doc.day,
      requests: doc.requests,
      tokens: { in: tok.in, out: tok.out },
      models,
      scenarios
    }
  };
}

// Aggregates exclude canary rows: verification probes own install ids that
// start with "_" (reserved by convention, documented in the design doc).
export const REAL_ROW = "substr(install_id, 1, 1) <> '_'";
export const PROBE_ROW = "substr(install_id, 1, 1) = '_'";
