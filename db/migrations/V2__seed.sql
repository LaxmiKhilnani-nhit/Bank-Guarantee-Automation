-- BG Automation — V2 seed (masters, lookups, roles)
-- Idempotent: INSERT ... ON CONFLICT DO NOTHING.
-- Distinct values harvested from BG Details-Updated.xlsx (85 BGs).

-- ---------- company ----------
-- `NSPPL` / `NEPPL` / `NWPPL` are aliases of the three SPVs, not companies.
INSERT INTO company (name) VALUES
  ('NHIT Eastern Projects Private Limited'),
  ('NHIT Southern Projects Private Limited'),
  ('NHIT Western Projects Private Limited')
ON CONFLICT (name) DO NOTHING;

-- ---------- projects (28 active SPV corridors) ----------
-- Canonical operations-master names. One plaza belongs to exactly one project;
-- corridor names and combined-plaza spellings are not seeded as plazas.
INSERT INTO project (code, name) VALUES
  ('KRK', 'Kachugaon-Kalijhar'),
  ('RKJL', 'Rewa-Katni-Jabalpur-Lakhnadon'),
  ('CHK', 'Chichra-Kharagpur'),
  ('OB', 'Orai-Bara'),
  ('LK', 'Lakhnadon-Khawasa'),
  ('HHC', 'Hubli-Haveri-Chitradurga'),
  ('KP', 'Kalijhar-Patacharkuchi'),
  ('NR', 'Narasannapeta-Ranasthalam'),
  ('RH', 'Ranasthalam to Hanumanthvaka'),
  ('APA', 'Anandapuram-Pendurthi-Anakapalle'),
  ('GDK', 'Gundugolanu Devarapalli Kovvur'),
  ('CM', 'Chittoor-Mallavaram'),
  ('APTNK', 'AP/TN Border to Nalagampalli, AP/Karnataka Border Section'),
  ('BS', 'Bareilly-Sitapur'),
  ('MH', 'Muzaffarnagar-Haridwar'),
  ('RS', 'Raipur-Simga'),
  ('SB', 'Simga-Bilaspur'),
  ('GMP', 'Gandhidham-Mundra Port'),
  ('AS', 'Abu Road-Swaroopganj'),
  ('PA', 'Palanpur-Abu Road'),
  ('BK', 'Belgaum-Karnataka Border'),
  ('CK', 'Chittorgarh-Kota'),
  ('AB', 'Agra-Bypass'),
  ('SJ', 'Shivpuri-Jhansi'),
  ('BW', 'Borkhedi-Wadner-Deodhari-Kelapur'),
  ('KK', 'Kothkota Kurnool'),
  ('ACT', 'Amravati-Chikhali-Tarsod'),
  ('GCA', 'Gundugolanu-Chinna Avutapalli')
ON CONFLICT (code) DO NOTHING;

-- Link every project to its SPV.
UPDATE project SET company_id = (SELECT id FROM company WHERE name = 'NHIT Eastern Projects Private Limited')
WHERE code IN ('KRK', 'RKJL', 'CHK', 'OB', 'LK', 'HHC', 'KP');
UPDATE project SET company_id = (SELECT id FROM company WHERE name = 'NHIT Southern Projects Private Limited')
WHERE code IN ('NR', 'RH', 'APA', 'GDK', 'CM', 'APTNK', 'BS', 'MH', 'RS', 'SB', 'GMP');
UPDATE project SET company_id = (SELECT id FROM company WHERE name = 'NHIT Western Projects Private Limited')
WHERE code IN ('AS', 'PA', 'BK', 'CK', 'AB', 'SJ', 'BW', 'KK', 'ACT', 'GCA');

-- ---------- toll plazas (46 canonical rows; one project each) ----------
INSERT INTO toll_plaza (project_id, name)
SELECT p.id, plaza.name
FROM (VALUES
  ('KRK', 'Patagaon'), ('KRK', 'Dhalapara'),
  ('RKJL', 'Odhaki'), ('RKJL', 'Mohtara'), ('RKJL', 'Kherwasani'), ('RKJL', 'Boharipar'),
  ('CHK', 'Balibhasa'), ('OB', 'Usaka'),
  ('LK', 'Madai'), ('LK', 'Khawasa'),
  ('HHC', 'Bankapur'), ('HHC', 'Chalageri'), ('HHC', 'Hebbalu'),
  ('KP', 'Galia'),
  ('NR', 'Madapam'), ('RH', 'Nathavalasa'),
  ('APA', 'Marripalem'), ('APA', 'Dukkavanipalem'),
  ('GDK', 'Veeravalli'), ('CM', 'Gadnaki'), ('APTNK', 'Mahasamudram'),
  ('BS', 'Faridpur'), ('BS', 'Maigalganj'),
  ('MH', 'Bahadrabad'), ('MH', 'Chapar'),
  ('RS', 'Tarpongi'), ('SB', 'Bhojpuri'), ('SB', 'Mudipar'), ('GMP', 'Mokha'),
  ('AS', 'Undvariya'), ('PA', 'Khemana'),
  ('BK', 'Hattargi'), ('BK', 'Kognoli'),
  ('CK', 'Aroli'), ('CK', 'Bassi'), ('CK', 'Dhaneshwar'),
  ('AB', 'Raibha'), ('SJ', 'Raksha'),
  ('BW', 'Daroda'), ('BW', 'Kelapur'), ('KK', 'Pullur'),
  ('ACT', 'Tarod Kasba'), ('ACT', 'Dasarkhed'), ('ACT', 'Kurankhed'), ('ACT', 'Nashirabad'),
  ('GCA', 'Kalaparru')
) AS plaza(project, name)
JOIN project p ON p.code = plaza.project
ON CONFLICT (project_id, name) DO NOTHING;

-- ---------- banks ----------
-- No prefilled bank list: the first confirmed BG creates the issuing bank.
-- `bg_records` only stores its id.

-- ---------- contractors ----------
-- No prefilled contractor list: the first confirmed BG creates the applicant.
-- `bg_records` only stores its id.

-- ---------- bg types ----------
INSERT INTO bg_type (code) VALUES ('PBG'), ('ABG')
ON CONFLICT (code) DO NOTHING;

-- ---------- natures of service (27) ----------
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
  ('Signboard Installation'), ('Street Light_ABG'), ('TMS'),
  ('Routine Maintenance'), ('Supply & Installation of Overhead Gantry')
ON CONFLICT (name) DO NOTHING;

-- ---------- lifecycle statuses (approved set) ----------
INSERT INTO bg_status (code) VALUES
  ('Draft'), ('Active'), ('Expiring'), ('Expired'), ('Extended'), ('Closed')
ON CONFLICT (code) DO NOTHING;

-- ---------- roles (R5: admin / uploader / viewer) ----------
INSERT INTO roles (name) VALUES ('admin'), ('uploader'), ('viewer')
ON CONFLICT (name) DO NOTHING;
