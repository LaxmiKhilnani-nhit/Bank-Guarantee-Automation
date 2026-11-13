-- BG Automation — V1 schema (PostgreSQL 15/16)
-- Approved design: normalised masters, 1 LOA:M BGs, extended_date split,
-- status lookup (Draft/Active/Expiring/Expired/Extended/Closed), OCR + reminders + audit.
-- Idempotent: safe to re-run (IF NOT EXISTS everywhere).

-- ============ extensions ============
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ============ helper: updated_at ============
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END $$;

-- ============ lookups / masters ============

-- Company master (Tracker col "Company Name"; Excel sample: NSPPL)
CREATE TABLE IF NOT EXISTS company (
  id   SMALLSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

-- Project master (Tracker "Project Name" col 1 = code e.g. SB/MH/BS)
CREATE TABLE IF NOT EXISTS project (
  id         SMALLSERIAL PRIMARY KEY,
  company_id SMALLINT REFERENCES company(id) ON DELETE RESTRICT,
  code       TEXT NOT NULL UNIQUE,
  name       TEXT
);

-- Toll plaza master (Tracker "Toll Plaza Name" / Details col 2 e.g. Mudipar)
CREATE TABLE IF NOT EXISTS toll_plaza (
  id         SERIAL PRIMARY KEY,
  project_id SMALLINT REFERENCES project(id) ON DELETE RESTRICT,
  name       TEXT NOT NULL,
  UNIQUE (project_id, name)
);

CREATE TABLE IF NOT EXISTS bank (
  id   SMALLSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS contractor (
  id   SERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

-- BG type: PBG / ABG
CREATE TABLE IF NOT EXISTS bg_type (
  code TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS nature_of_service (
  id   SMALLSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

-- Lifecycle status (replaces Excel free-text + #REF!/NULL)
CREATE TABLE IF NOT EXISTS bg_status (
  code TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS roles (
  id   SMALLSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

-- ============ users / access ============

CREATE TABLE IF NOT EXISTS users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  username      CITEXT NOT NULL UNIQUE,
  email         CITEXT UNIQUE,
  full_name     TEXT,
  password_hash TEXT NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  last_login    TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_roles (
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role_id SMALLINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
  PRIMARY KEY (user_id, role_id)
);

-- ============ LOA / PO parent (1 LOA : M BGs, ST007 extend chain) ============

CREATE TABLE IF NOT EXISTS loa_records (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  loa_po_number  TEXT NOT NULL UNIQUE,
  description    TEXT,
  created_by     UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ core BG register ============
-- Excel "Sr" is NOT stored (generated row number); BG No is the dedupe key (UC003).

CREATE TABLE IF NOT EXISTS bg_records (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bg_no            TEXT NOT NULL UNIQUE,
  company_id       SMALLINT REFERENCES company(id) ON DELETE RESTRICT,
  project_id       SMALLINT REFERENCES project(id) ON DELETE RESTRICT,
  toll_plaza_id    INTEGER REFERENCES toll_plaza(id) ON DELETE RESTRICT,
  bg_type          TEXT REFERENCES bg_type(code) ON DELETE RESTRICT,
  nature_id        SMALLINT REFERENCES nature_of_service(id) ON DELETE RESTRICT,
  contractor_id    INTEGER REFERENCES contractor(id) ON DELETE RESTRICT,
  bank_id          SMALLINT REFERENCES bank(id) ON DELETE RESTRICT,
  loa_id           UUID REFERENCES loa_records(id) ON DELETE RESTRICT,
  po_number        TEXT,
  bg_amount        NUMERIC(15,2) NOT NULL CHECK (bg_amount > 0),
  from_date        DATE NOT NULL,
  to_date          DATE NOT NULL,
  claim_period     DATE,
  extended_date    DATE,                       -- Excel "Extended": date (or legacy "Yes" -> status only)
  status           TEXT NOT NULL DEFAULT 'Draft' REFERENCES bg_status(code) ON DELETE RESTRICT,
  remarks          TEXT,
  source_pdf       TEXT NOT NULL,              -- Tracker "source_pdf"
  created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  reminder_sent_at TIMESTAMPTZ,                -- Tracker "reminder_sent_at"
  supersedes_bg_id UUID REFERENCES bg_records(id) ON DELETE SET NULL,  -- ST007 chain
  CHECK (to_date >= from_date),
  CHECK (claim_period IS NULL OR claim_period >= to_date),
  CHECK (id <> supersedes_bg_id)
);

DROP TRIGGER IF EXISTS trg_bg_records_updated_at ON bg_records;
CREATE TRIGGER trg_bg_records_updated_at
  BEFORE UPDATE ON bg_records
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
  BEFORE UPDATE ON users
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_loa_updated_at ON loa_records;
CREATE TRIGGER trg_loa_updated_at
  BEFORE UPDATE ON loa_records
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============ OCR extraction attempts (ST002/ST004/ST008) ============
-- bg_id NULLABLE: failed/low-confidence extractions exist before a BG row is confirmed.

CREATE TABLE IF NOT EXISTS ocr_results (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bg_id            UUID REFERENCES bg_records(id) ON DELETE SET NULL,
  source_pdf       TEXT NOT NULL,
  di_model         TEXT NOT NULL,              -- Datalab processing mode, e.g. fast / accurate
  raw_json         JSONB NOT NULL,             -- Datalab page tree for audit/retrain
  extracted_fields JSONB NOT NULL DEFAULT '{}'::jsonb,
  avg_confidence   NUMERIC(5,4) CHECK (avg_confidence IS NULL OR (avg_confidence >= 0 AND avg_confidence <= 1)),
  status           TEXT NOT NULL DEFAULT 'needs_review'
                   CHECK (status IN ('queued','success','needs_review','failed')),
  error            TEXT,
  created_by       UUID REFERENCES users(id) ON DELETE SET NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ reminder log (ST005/ST006, SMTP-only) ============

CREATE TABLE IF NOT EXISTS reminders (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  bg_id           UUID NOT NULL REFERENCES bg_records(id) ON DELETE CASCADE,
  scheduled_for   DATE NOT NULL,               -- to_date - 7 days
  sent_at         TIMESTAMPTZ,
  recipients      TEXT[] NOT NULL,
  subject         TEXT,
  body            TEXT,
  provider_msg_id TEXT,
  status          TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','sent','failed','skipped')),
  error           TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (bg_id, scheduled_for)
);

-- ============ audit log (ST009, user action history) ============

CREATE TABLE IF NOT EXISTS audit_logs (
  id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts        TIMESTAMPTZ NOT NULL DEFAULT now(),
  user_id   UUID REFERENCES users(id) ON DELETE SET NULL,
  action    TEXT NOT NULL
            CHECK (action IN ('upload','extract','confirm','export','remind',
                              'extend','close','login','user_create','user_update')),
  bg_id     UUID REFERENCES bg_records(id) ON DELETE SET NULL,
  bg_no     TEXT,
  old_data  JSONB,
  new_data  JSONB,
  ip        INET
);

-- ============ indexes ============

-- T-7 scheduler hot path (ST005): To == today+7, unsent, not closed
CREATE INDEX IF NOT EXISTS idx_bg_to_date
  ON bg_records (to_date)
  WHERE status NOT IN ('Closed','Extended');

CREATE INDEX IF NOT EXISTS idx_bg_status ON bg_records (status);
CREATE INDEX IF NOT EXISTS idx_bg_contractor ON bg_records (contractor_id);
CREATE INDEX IF NOT EXISTS idx_bg_project ON bg_records (project_id);
CREATE INDEX IF NOT EXISTS idx_bg_bank ON bg_records (bank_id);
CREATE INDEX IF NOT EXISTS idx_bg_loa ON bg_records (loa_id);
CREATE INDEX IF NOT EXISTS idx_bg_supersedes ON bg_records (supersedes_bg_id);

CREATE INDEX IF NOT EXISTS idx_ocr_bg ON ocr_results (bg_id);
CREATE INDEX IF NOT EXISTS idx_ocr_status ON ocr_results (status);
CREATE INDEX IF NOT EXISTS idx_ocr_fields_gin ON ocr_results USING GIN (extracted_fields);

CREATE INDEX IF NOT EXISTS idx_rem_bg ON reminders (bg_id);
CREATE INDEX IF NOT EXISTS idx_rem_sched ON reminders (status, scheduled_for);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_logs (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_bg_no ON audit_logs (bg_no);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs (user_id);

-- ============ register view (dashboard table + Excel export shape) ============

CREATE OR REPLACE VIEW v_bg_register AS
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

COMMENT ON TABLE bg_records IS 'One row per BG; dedupe on bg_no; supersedes_bg_id chains extensions (ST007). Excel Sr is not stored.';
COMMENT ON COLUMN bg_records.extended_date IS 'Excel Extended held dates + one Yes; Yes migrates to status=Extended with NULL date.';
COMMENT ON TABLE loa_records IS 'Parent for 1 LOA:M BGs; revised BG against same LOA inserts new bg_records row.';
COMMENT ON TABLE ocr_results IS 'Every Datalab attempt incl. raw_json; low-confidence rows stay needs_review/failed and are never auto-written (ST008).';
COMMENT ON TABLE reminders IS 'T-7 SMTP log; UNIQUE(bg_id, scheduled_for) prevents double-send.';
