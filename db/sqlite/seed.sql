-- BG Automation — SQLite seed (port of migrations/V2__seed.sql)
-- Idempotent: INSERT OR IGNORE everywhere; safe to re-run.
-- Distinct values harvested from BG Details-Updated.xlsx (85 BGs).

PRAGMA foreign_keys = ON;

-- ---------- company ----------
INSERT OR IGNORE INTO company (name) VALUES ('NSPPL');

-- ---------- projects (Tracker col-1 codes) ----------
-- NOTE: 'RS,SB' is a data-entry combo in the Excel; seeded so FK import works,
-- clean up to separate rows in the app later.
INSERT OR IGNORE INTO project (code, name) VALUES
  ('APA', NULL), ('APTNK', NULL), ('BS', NULL), ('CM', NULL),
  ('GDK', NULL), ('GMP', NULL), ('MH', NULL), ('NR', NULL),
  ('RH', NULL), ('RS', NULL), ('RS,SB', NULL), ('SB', NULL);

-- link all projects to NSPPL where unlinked (single-company pilot)
UPDATE project SET company_id = (SELECT id FROM company WHERE name = 'NSPPL')
WHERE company_id IS NULL;

-- ---------- toll plazas (attached to majority project observed in Excel) ----------
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mudipar' FROM project WHERE code = 'SB';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Bhojpuri' FROM project WHERE code = 'SB';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Bhojpuri & Mudipar' FROM project WHERE code = 'SB';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mudipar & Bhojpuri' FROM project WHERE code = 'SB';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Tarpongi' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Anandapuram to Hanumanthavaka' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Simga - Bilaspur' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Simga to Bilaspur' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Raipur' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Raipur - Simga' FROM project WHERE code = 'RS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Chappar' FROM project WHERE code = 'MH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Chappar & Bahadrabad' FROM project WHERE code = 'MH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Chappar/Bahadrabad' FROM project WHERE code = 'MH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Muzafarnagar - Haridwar' FROM project WHERE code = 'MH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Muzafarnagar to Haridwar' FROM project WHERE code = 'MH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Gadanki' FROM project WHERE code = 'CM';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Marripalem' FROM project WHERE code = 'APA';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Dukkavaniplem' FROM project WHERE code = 'APA';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Dukkavaniplem & Marriplem' FROM project WHERE code = 'APA';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Dukkawalipalem & Maripalem' FROM project WHERE code = 'APA';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Madapam' FROM project WHERE code = 'NR';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Veeravali' FROM project WHERE code = 'GDK';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mahasamundaram' FROM project WHERE code = 'APTNK';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mahasamudaram' FROM project WHERE code = 'APTNK';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mahasamundram' FROM project WHERE code = 'APTNK';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Nathavalsa' FROM project WHERE code = 'RH';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Faridpur' FROM project WHERE code = 'BS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Faridpur & Maigalganj' FROM project WHERE code = 'BS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Maigalganj' FROM project WHERE code = 'BS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Bareilly Sitapur' FROM project WHERE code = 'BS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Barielly - Sitapur' FROM project WHERE code = 'BS';
INSERT OR IGNORE INTO toll_plaza (project_id, name)
  SELECT id, 'Mokha' FROM project WHERE code = 'GMP';

-- ---------- banks (7) ----------
INSERT OR IGNORE INTO bank (name) VALUES
  ('ICICI Bank Limited'), ('Punjab National Bank'), ('HDFC Bank'),
  ('Axis Bank Limited'), ('AU SMALL FINANCE BANK'),
  ('Yes Bank'), ('Bank of Baroda');

-- ---------- contractors (31; TBR dup spellings kept for import fidelity) ----------
INSERT OR IGNORE INTO contractor (name) VALUES
  ('Aryaomnitalk'),
  ('Bheema Tolling and Traffic Solutions Pvt Ltd'),
  ('Cube Tech'),
  ('Devaditya Technocrats LLP'),
  ('Gangarati Engg. India pvt Ltd'),
  ('KL Enterprises'),
  ('KRS Infrasys Pvt Limited'),
  ('Lloyd Insulations (India) LTD'),
  ('M/s Ador Powertron Limited'),
  ('M/s Imagine Powertree Private Limited.'),
  ('M/s Qualix Information'),
  ('Mahinder Singh Godara'),
  ('Matro Infra'),
  ('Metro Infrasys Pvt Ltd'),
  ('RH Agency'),
  ('Racy Project'),
  ('Riddhi Siddhi Associate'),
  ('SKM Contractor and Services Pvt'),
  ('STRUCT REHAB'),
  ('Sharda Infra Solution pvt Ltd'),
  ('Sri Sai Enterprises'),
  ('Star O&M'),
  ('Sun Shine Eng. Souution'),
  ('SuperWave Communication'),
  ('TBR Infra Private Limited'),
  ('TBR Infra Pvt Ltd'),
  ('Trackstar Industries'),
  ('Translink Infrastructure Consultants Private Limited'),
  ('UnM Life Care Pvt Ltd'),
  ('Unicorn Infra Construction'),
  ('Vinod Kumar Malik');

-- ---------- bg types ----------
INSERT OR IGNORE INTO bg_type (code) VALUES ('PBG'), ('ABG');

-- ---------- natures of service (25) ----------
INSERT OR IGNORE INTO nature_of_service (name) VALUES
  ('ATMS'), ('ATMS Building'), ('ATMS_ABG'), ('ATMS_Tendering'),
  ('BESS_Bahadrabad Toll Plaza'), ('BESS_Bhojpuri Toll Plaza'),
  ('BESS_Maigalganj Toll Plaza'), ('BESS_Mudipar Toll Plaza'),
  ('BESS_Nathavalasa Toll Plaza'), ('BESS_Tarpongi Toll Plaza'),
  ('General Management Consultancy Service'), ('Incident Management'),
  ('Initial Improvement'), ('Manpower'), ('Manpower Chappar Toll Plaza'),
  ('Operation'), ('PQC Panel Widen Gap Rectification'),
  ('Rehabilitation of bridge'), ('Repair & rehabilititation'),
  ('Repir&Maintaince -Board'), ('Road Marking'), ('Routing Mait.'),
  ('Signboard Installation'), ('Street Light_ABG'), ('TMS');

-- ---------- lifecycle statuses (approved set) ----------
INSERT OR IGNORE INTO bg_status (code) VALUES
  ('Draft'), ('Active'), ('Expiring'), ('Expired'), ('Extended'), ('Closed');

-- ---------- roles (R5: admin / uploader / viewer) ----------
INSERT OR IGNORE INTO roles (name) VALUES ('admin'), ('uploader'), ('viewer');
