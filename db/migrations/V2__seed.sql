-- BG Automation — V2 seed (masters, lookups, roles)
-- Idempotent: INSERT ... ON CONFLICT DO NOTHING.
-- Distinct values harvested from BG Details-Updated.xlsx (85 BGs).

-- ---------- company ----------
INSERT INTO company (name) VALUES ('NSPPL')
ON CONFLICT (name) DO NOTHING;

-- ---------- projects (Tracker col-1 codes) ----------
-- NOTE: 'RS,SB' is a data-entry combo in the Excel; seeded so FK import works,
-- clean up to separate rows in the app later.
INSERT INTO project (code, name) VALUES
  ('APA', NULL), ('APTNK', NULL), ('BS', NULL), ('CM', NULL),
  ('GDK', NULL), ('GMP', NULL), ('MH', NULL), ('NR', NULL),
  ('RH', NULL), ('RS', NULL), ('RS,SB', NULL), ('SB', NULL)
ON CONFLICT (code) DO NOTHING;

-- link all projects to NSPPL where unlinked (single-company pilot)
UPDATE project SET company_id = (SELECT id FROM company WHERE name='NSPPL')
WHERE company_id IS NULL;

-- ---------- toll plazas (Tracker col-2; attached per project best-effort) ----------
-- Seeded WITHOUT project link first would violate UX; instead attach each toll
-- to its majority project observed in the Excel. Tolls seen under multiple
-- projects keep the first mapping; adjust in admin UI later.
-- (project, toll) pairs observed:
DO $$
DECLARE
  pairs TEXT[][] := ARRAY[
    ['SB','Mudipar'], ['SB','Bhojpuri'], ['RS','Tarpongi'],
    ['MH','Chappar'], ['CM','Gadanki'], ['APA','Marripalem'],
    ['NR','Madapam'], ['GDK','Veeravali'], ['APTNK','Mahasamundaram'],
    ['APA','Dukkavaniplem'], ['RH','Nathavalsa'],
    ['MH','Chappar & Bahadrabad'], ['RH','Nathavalsa'],
    ['APTNK','Mahasamudaram'], ['SB','Mudipar & Bhojpuri'],
    ['NR','Madapam'], ['MH','Chappar/Bahadrabad'],
    ['APA','Dukkawalipalem & Maripalem'], ['APA','Dukkavaniplem & Marriplem'],
    ['BS','Faridpur'], ['BS','Faridpur & Maigalganj'], ['BS','Maigalganj'],
    ['GMP','Mokha'], ['MH','Muzafarnagar - Haridwar'],
    ['MH','Muzafarnagar to Haridwar'], ['APTNK','Mahasamundram'],
    ['RS','Anandapuram to Hanumanthavaka'],
    ['BS','Bareilly Sitapur'], ['BS','Barielly - Sitapur'],
    ['RS','Simga - Bilaspur'], ['RS','Simga to Bilaspur'],
    ['RS','Raipur'], ['RS','Raipur - Simga'],
    ['SB','Bhojpuri & Mudipar']
  ];
  pr TEXT[]; pid SMALLINT;
BEGIN
  FOREACH pr SLICE 1 IN ARRAY pairs LOOP
    SELECT id INTO pid FROM project WHERE code = pr[1];
    IF pid IS NOT NULL THEN
      INSERT INTO toll_plaza (project_id, name)
      VALUES (pid, pr[2])
      ON CONFLICT (project_id, name) DO NOTHING;
    END IF;
  END LOOP;
END $$;

-- ---------- banks (7) ----------
INSERT INTO bank (name) VALUES
  ('ICICI Bank Limited'), ('Punjab National Bank'), ('HDFC Bank'),
  ('Axis Bank Limited'), ('AU SMALL FINANCE BANK'),
  ('Yes Bank'), ('Bank of Baroda')
ON CONFLICT (name) DO NOTHING;

-- ---------- contractors (31; TBR dup spellings kept for import fidelity) ----------
INSERT INTO contractor (name) VALUES
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
  ('Vinod Kumar Malik')
ON CONFLICT (name) DO NOTHING;

-- ---------- bg types ----------
INSERT INTO bg_type (code) VALUES ('PBG'), ('ABG')
ON CONFLICT (code) DO NOTHING;

-- ---------- natures of service (25) ----------
INSERT INTO nature_of_service (name) VALUES
  ('ATMS'), ('ATMS Building'), ('ATMS_ABG'), ('ATMS_Tendering'),
  ('BESS_Bahadrabad Toll Plaza'), ('BESS_Bhojpuri Toll Plaza'),
  ('BESS_Maigalganj Toll Plaza'), ('BESS_Mudipar Toll Plaza'),
  ('BESS_Nathavalasa Toll Plaza'), ('BESS_Tarpongi Toll Plaza'),
  ('General Management Consultancy Service'), ('Incident Management'),
  ('Initial Improvement'), ('Manpower'), ('Manpower Chappar Toll Plaza'),
  ('Operation'), ('PQC Panel Widen Gap Rectification'),
  ('Rehabilitation of bridge'), ('Repair & rehabilititation'),
  ('Repir&Maintaince -Board'), ('Road Marking'), ('Routing Mait.'),
  ('Signboard Installation'), ('Street Light_ABG'), ('TMS')
ON CONFLICT (name) DO NOTHING;

-- ---------- lifecycle statuses (approved set) ----------
INSERT INTO bg_status (code) VALUES
  ('Draft'), ('Active'), ('Expiring'), ('Expired'), ('Extended'), ('Closed')
ON CONFLICT (code) DO NOTHING;

-- ---------- roles (R5: admin / uploader / viewer) ----------
INSERT INTO roles (name) VALUES ('admin'), ('uploader'), ('viewer')
ON CONFLICT (name) DO NOTHING;
