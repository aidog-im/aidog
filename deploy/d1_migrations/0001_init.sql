-- D2 usage ledger car: the D1 tables behind /api/ledger, /api/stats and the
-- migrated /api/visits counter (docs/design/usage_ledger_design.md §2).
-- Applied idempotently by deploy/deploy_site.sh (CREATE ... IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS ledger_daily (
  install_id    TEXT NOT NULL,                 -- random, resettable, anonymous
  day           TEXT NOT NULL,                 -- YYYY-MM-DD (UTC)
  requests      INTEGER NOT NULL DEFAULT 0,    -- successful model calls
  tok_in        INTEGER NOT NULL DEFAULT 0,    -- provider-reported totals
  tok_out       INTEGER NOT NULL DEFAULT 0,
  models_json   TEXT NOT NULL DEFAULT '[]',    -- [{model,requests,in,out}]
  scenarios_json TEXT NOT NULL DEFAULT '{}',   -- {chat,coding,research,voice,publishing}
  updated_at    TEXT NOT NULL DEFAULT '',      -- receive time (UTC ISO)
  PRIMARY KEY (install_id, day)
);

CREATE INDEX IF NOT EXISTS idx_ledger_daily_day ON ledger_daily(day);

CREATE TABLE IF NOT EXISTS site_visits (
  name  TEXT PRIMARY KEY,
  count INTEGER NOT NULL DEFAULT 0
);
