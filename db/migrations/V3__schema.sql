-- BG Automation — V3 schema parity (PostgreSQL 15/16)
--
-- V1/V2 are the historical baseline and are never edited. V3 brings the
-- database up to what the application code actually reads:
--
--   * sessions and lookup_cache tables (the app creates these today only in
--     SQLite, at runtime);
--   * lockout columns on users;
--   * master metadata columns (state, round, managers, chainage, provenance,
--     active flag) used by the master importer and the dropdowns;
--   * the full audit action list the app writes;
--   * audit_logs.ip as TEXT (FastAPI tests send "testclient", not an IP);
--   * a deterministic dropdown order for bg_status / bg_type (SQLite used
--     rowid, which Postgres does not have).
--
-- Idempotent: safe to re-run.

-- ============ missing tables ============

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  last_seen  TIMESTAMPTZ,
  ip         TEXT,
  user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions (expires_at);

CREATE TABLE IF NOT EXISTS lookup_cache (
  field      TEXT NOT NULL,
  query      TEXT NOT NULL,
  payload    JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (field, query)
);

-- ============ missing columns ============

ALTER TABLE users ADD COLUMN IF NOT EXISTS failed_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS locked_until TIMESTAMPTZ;

ALTER TABLE company
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE project
  ADD COLUMN IF NOT EXISTS state TEXT,
  ADD COLUMN IF NOT EXISTS "round" INTEGER,
  ADD COLUMN IF NOT EXISTS project_manager TEXT,
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE toll_plaza
  ADD COLUMN IF NOT EXISTS chainage TEXT,
  ADD COLUMN IF NOT EXISTS toll_manager TEXT,
  ADD COLUMN IF NOT EXISTS maintenance_manager TEXT,
  ADD COLUMN IF NOT EXISTS corridor_manager TEXT,
  ADD COLUMN IF NOT EXISTS source TEXT,
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true;

ALTER TABLE bank       ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE contractor ADD COLUMN IF NOT EXISTS source TEXT;

-- Dropdown ordering. SQLite kept the declaration order through rowid.
ALTER TABLE bg_status ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bg_type   ADD COLUMN IF NOT EXISTS sort_order INTEGER NOT NULL DEFAULT 0;

UPDATE bg_status SET sort_order = CASE code
  WHEN 'Draft'    THEN 1
  WHEN 'Active'   THEN 2
  WHEN 'Expiring' THEN 3
  WHEN 'Expired'  THEN 4
  WHEN 'Extended' THEN 5
  WHEN 'Closed'   THEN 6
  ELSE 100
END;

UPDATE bg_type SET sort_order = CASE code
  WHEN 'PBG' THEN 1
  WHEN 'ABG' THEN 2
  ELSE 100
END;

-- ============ behavioural fixes ============

-- The app audits more actions than the V1 list allowed. The live SQLite data
-- already contains logout and workbook_reset, so the CHECK must be widened
-- before the first sign-in.
ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_action_check;
ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_action_check CHECK (action IN (
  'upload', 'extract', 'confirm', 'export', 'remind', 'extend', 'close',
  'login', 'login_failed', 'logout', 'locked', 'submit', 'review_save',
  'user_create', 'user_update', 'password_change', 'role_change',
  'reminder_sent', 'reminder_resend', 'workbook_reset'
));

-- INET rejects "testclient" and proxy hostnames. Store the observed text.
ALTER TABLE audit_logs ALTER COLUMN ip TYPE TEXT USING ip::text;

-- ============ supporting indexes ============

CREATE INDEX IF NOT EXISTS idx_toll_plaza_project ON toll_plaza (project_id);
CREATE INDEX IF NOT EXISTS idx_project_company ON project (company_id);
CREATE INDEX IF NOT EXISTS idx_bg_to_date_plain ON bg_records (to_date);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders (status, scheduled_for);
