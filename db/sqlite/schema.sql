-- BG Automation — SQLite schema (port of migrations/V1__schema.sql)
-- Approved design: normalised masters, 1 LOA:M BGs, extended_date split,
-- status lookup (Draft/Active/Expiring/Expired/Extended/Closed), OCR + reminders + audit.
-- Idempotent: safe to re-run (IF NOT EXISTS / OR IGNORE in seed file).
--
-- Postgres -> SQLite notes:
--   UUID PKs         -> TEXT PKs (app generates uuid4 hex; DEFAULT random hex fallback)
--   CITEXT           -> TEXT COLLATE NOCASE
--   SMALLSERIAL      -> INTEGER PRIMARY KEY AUTOINCREMENT
--   TIMESTAMPTZ      -> TEXT DEFAULT (datetime('now'))  (UTC, 'YYYY-MM-DD HH:MM:SS')
--   JSONB            -> TEXT with json_valid() CHECK
--   TEXT[] recipients-> TEXT holding a JSON array string (e.g. '["a@x","b@x"]')
--   INET ip          -> TEXT
--   GIN index        -> dropped (SQLite has no GIN); status index kept
--   plpgsql trigger  -> native SQLite trigger (non-recursive by default)

PRAGMA foreign_keys = ON;

-- ============ lookups / masters ============

CREATE TABLE IF NOT EXISTS company (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  name      TEXT NOT NULL UNIQUE,
  source    TEXT,
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS project (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id      INTEGER REFERENCES company(id) ON DELETE RESTRICT,
  code            TEXT NOT NULL UNIQUE,
  name            TEXT,
  state           TEXT,
  "round"         INTEGER,
  project_manager TEXT,
  source          TEXT,
  is_active       INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS toll_plaza (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id          INTEGER REFERENCES project(id) ON DELETE RESTRICT,
  name                TEXT NOT NULL,
  chainage            TEXT,
  toll_manager        TEXT,
  maintenance_manager TEXT,
  corridor_manager    TEXT,
  source              TEXT,
  is_active           INTEGER NOT NULL DEFAULT 1,
  UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS bank (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  name   TEXT NOT NULL UNIQUE,
  source TEXT
);

CREATE TABLE IF NOT EXISTS contractor (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  name   TEXT NOT NULL UNIQUE,
  source TEXT
);

CREATE TABLE IF NOT EXISTS bg_type (
  code TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS nature_of_service (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS bg_status (
  code TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS roles (
  id   INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE
);

-- ============ users / access ============

CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  username      TEXT COLLATE NOCASE NOT NULL UNIQUE,
  email         TEXT COLLATE NOCASE UNIQUE,
  full_name     TEXT,
  password_hash TEXT NOT NULL,
  is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
  last_login    TEXT,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until  TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id INTEGER NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  PRIMARY KEY (user_id, role_id)
);

-- Server-side sessions: the browser only holds an opaque token. The token is
-- stored hashed so a copy of the database cannot be replayed as a login.
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL,
  last_seen  TEXT,
  ip         TEXT,
  user_agent TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Answers from the optional Firecrawl name lookup, keyed by exact input so the
-- same wording is never billed twice. Not a matcher: identical text only.
CREATE TABLE IF NOT EXISTS lookup_cache (
  field      TEXT NOT NULL,
  query      TEXT NOT NULL,
  payload    TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (field, query)
);

-- ============ LOA / PO parent (1 LOA : M BGs, ST007 extend chain) ============

CREATE TABLE IF NOT EXISTS loa_records (
  id             TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  loa_po_number  TEXT NOT NULL UNIQUE,
  description    TEXT,
  created_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ core BG register ============
-- Excel "Sr" is NOT stored (generated row number); BG No is the dedupe key (UC003).

CREATE TABLE IF NOT EXISTS bg_records (
  id               TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  bg_no            TEXT NOT NULL UNIQUE,
  company_id       INTEGER REFERENCES company(id) ON DELETE RESTRICT,
  project_id       INTEGER REFERENCES project(id) ON DELETE RESTRICT,
  toll_plaza_id    INTEGER REFERENCES toll_plaza(id) ON DELETE RESTRICT,
  bg_type          TEXT REFERENCES bg_type(code) ON DELETE RESTRICT,
  nature_id        INTEGER REFERENCES nature_of_service(id) ON DELETE RESTRICT,
  contractor_id    INTEGER REFERENCES contractor(id) ON DELETE RESTRICT,
  bank_id          INTEGER REFERENCES bank(id) ON DELETE RESTRICT,
  loa_id           TEXT REFERENCES loa_records(id) ON DELETE RESTRICT,
  po_number        TEXT,
  bg_amount        NUMERIC NOT NULL CHECK (bg_amount > 0),
  from_date        TEXT NOT NULL,                       -- 'YYYY-MM-DD'
  to_date          TEXT NOT NULL,                       -- 'YYYY-MM-DD'
  claim_period     TEXT,                                -- 'YYYY-MM-DD'
  extended_date    TEXT,                                -- Excel "Extended": date (legacy "Yes" -> status only)
  status           TEXT NOT NULL DEFAULT 'Draft' REFERENCES bg_status(code) ON DELETE RESTRICT,
  remarks          TEXT,
  source_pdf       TEXT NOT NULL,
  created_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
  reminder_sent_at TEXT,
  supersedes_bg_id TEXT REFERENCES bg_records(id) ON DELETE SET NULL,
  CHECK (date(to_date) >= date(from_date)),
  CHECK (claim_period IS NULL OR date(claim_period) >= date(to_date)),
  CHECK (id <> supersedes_bg_id)
);

DROP TRIGGER IF EXISTS trg_bg_records_updated_at;
CREATE TRIGGER trg_bg_records_updated_at
  AFTER UPDATE ON bg_records
  FOR EACH ROW
BEGIN
  UPDATE bg_records SET updated_at = datetime('now') WHERE id = OLD.id;
END;

DROP TRIGGER IF EXISTS trg_users_updated_at;
CREATE TRIGGER trg_users_updated_at
  AFTER UPDATE ON users
  FOR EACH ROW
BEGIN
  UPDATE users SET updated_at = datetime('now') WHERE id = OLD.id;
END;

DROP TRIGGER IF EXISTS trg_loa_updated_at;
CREATE TRIGGER trg_loa_updated_at
  AFTER UPDATE ON loa_records
  FOR EACH ROW
BEGIN
  UPDATE loa_records SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- ============ OCR extraction attempts (ST002/ST004/ST008) ============
-- bg_id NULLABLE: failed/low-confidence extractions exist before a BG row is confirmed.

CREATE TABLE IF NOT EXISTS ocr_results (
  id               TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  bg_id            TEXT REFERENCES bg_records(id) ON DELETE SET NULL,
  source_pdf       TEXT NOT NULL,
  di_model         TEXT NOT NULL,
  raw_json         TEXT NOT NULL CHECK (json_valid(raw_json)),
  extracted_fields TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(extracted_fields)),
  avg_confidence   NUMERIC CHECK (avg_confidence IS NULL
                        OR (avg_confidence >= 0 AND avg_confidence <= 1)),
  status           TEXT NOT NULL DEFAULT 'needs_review'
                   CHECK (status IN ('queued','success','needs_review','failed')),
  error            TEXT,
  created_by       TEXT REFERENCES users(id) ON DELETE SET NULL,
  created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============ reminder log (ST005/ST006, SMTP-only) ============
-- recipients holds a JSON array string, e.g. '["finance@x","team@x"]'.

CREATE TABLE IF NOT EXISTS reminders (
  id              TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
  bg_id           TEXT NOT NULL REFERENCES bg_records(id) ON DELETE CASCADE,
  scheduled_for   TEXT NOT NULL,                       -- 'YYYY-MM-DD' = to_date - 7 days
  sent_at         TEXT,
  recipients      TEXT NOT NULL CHECK (json_valid(recipients)),
  subject         TEXT,
  body            TEXT,
  provider_msg_id TEXT,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','sent','failed','skipped')),
  error           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (bg_id, scheduled_for)
);

-- ============ audit log (ST009, user action history) ============

CREATE TABLE IF NOT EXISTS audit_logs (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL DEFAULT (datetime('now')),
  user_id   TEXT REFERENCES users(id) ON DELETE SET NULL,
  action    TEXT NOT NULL
            CHECK (action IN ('upload','extract','confirm','export','remind',
                              'extend','close','login','login_failed','locked',
                              'logout','submit','review_save','user_create',
                              'user_update','password_change','role_change',
                              'reminder_sent','reminder_resend','workbook_reset')),
  bg_id     TEXT REFERENCES bg_records(id) ON DELETE SET NULL,
  bg_no     TEXT,
  old_data  TEXT CHECK (old_data IS NULL OR json_valid(old_data)),
  new_data  TEXT CHECK (new_data IS NULL OR json_valid(new_data)),
  ip        TEXT
);

-- ============ indexes ============

CREATE INDEX IF NOT EXISTS idx_bg_to_date ON bg_records (to_date)
  WHERE status NOT IN ('Closed','Extended');
CREATE INDEX IF NOT EXISTS idx_bg_status ON bg_records (status);
CREATE INDEX IF NOT EXISTS idx_bg_contractor ON bg_records (contractor_id);
CREATE INDEX IF NOT EXISTS idx_bg_project ON bg_records (project_id);
CREATE INDEX IF NOT EXISTS idx_bg_bank ON bg_records (bank_id);
CREATE INDEX IF NOT EXISTS idx_bg_loa ON bg_records (loa_id);
CREATE INDEX IF NOT EXISTS idx_bg_supersedes ON bg_records (supersedes_bg_id);

CREATE INDEX IF NOT EXISTS idx_ocr_bg ON ocr_results (bg_id);
CREATE INDEX IF NOT EXISTS idx_ocr_status ON ocr_results (status);

CREATE INDEX IF NOT EXISTS idx_rem_bg ON reminders (bg_id);
CREATE INDEX IF NOT EXISTS idx_rem_sched ON reminders (status, scheduled_for);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_bg_no ON audit_logs (bg_no);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs (user_id);

-- ============ register view (dashboard table + Excel export shape) ============

DROP VIEW IF EXISTS v_bg_register;
CREATE VIEW v_bg_register AS
SELECT
  b.id, b.bg_no, b.bg_amount,
  b.from_date AS "From", b.to_date AS "To",
  b.claim_period AS "Claim Period", b.extended_date AS "Extended",
  b.status AS "Status", b.remarks AS "Remarks",
  b.source_pdf, b.created_at, b.reminder_sent_at,
  c.name  AS "Company Name",
  p.code  AS "Project Name",
  t.name  AS "Toll Plaza Name",
  b.bg_type AS "Types of BG",
  n.name  AS "Nature of Service",
  ct.name AS "Name of Contractor",
  bk.name AS "Bank Name",
  l.loa_po_number AS "LOA Number",
  b.po_number AS "PO Number",
  u.username AS created_by_name
FROM bg_records b
LEFT JOIN company c       ON c.id = b.company_id
LEFT JOIN project p       ON p.id = b.project_id
LEFT JOIN toll_plaza t    ON t.id = b.toll_plaza_id
LEFT JOIN nature_of_service n ON n.id = b.nature_id
LEFT JOIN contractor ct   ON ct.id = b.contractor_id
LEFT JOIN bank bk         ON bk.id = b.bank_id
LEFT JOIN loa_records l   ON l.id = b.loa_id
LEFT JOIN users u         ON u.id = b.created_by;
