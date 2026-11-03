-- BG Automation — SQLite seed (port of migrations/V2__seed.sql)
-- Idempotent: INSERT OR IGNORE everywhere; safe to re-run.
--
-- Company, project and toll-plaza rows mirror the operations master document
-- (the same shape accepted by `python -m bgauto.cli import-masters`). They are
-- the canonical names: one plaza belongs to exactly one project, corridor names
-- and combined-plaza spellings are NOT seeded. OCR variants are handled by the
-- fuzzy matcher in bgauto/extract.py, so aliases do not need their own rows.

PRAGMA foreign_keys = ON;

-- ---------- company ----------
-- The three SPVs are the only companies. `NSPPL` is an alias of the Southern
-- SPV and is deactivated in the legacy-cleanup section below.
INSERT OR IGNORE INTO company (name, source) VALUES
  ('NHIT Eastern Projects Private Limited', 'seed'),
  ('NHIT Southern Projects Private Limited', 'seed'),
  ('NHIT Western Projects Private Limited', 'seed');

-- ---------- projects (28 active SPV corridors) ----------
WITH project_seed(code, name, company, state, rnd, project_manager) AS (
  VALUES
    -- Eastern
    ('KRK',  'Kachugaon-Kalijhar', 'NHIT Eastern Projects Private Limited', 'Assam', 3, 'K Bala Subramaniyam'),
    ('RKJL', 'Rewa-Katni-Jabalpur-Lakhnadon', 'NHIT Eastern Projects Private Limited', 'Madhya Pradesh', 3, 'M. Ravindran'),
    ('CHK',  'Chichra-Kharagpur', 'NHIT Eastern Projects Private Limited', 'West Bengal', 3, 'Mintu Nandi'),
    ('OB',   'Orai-Bara', 'NHIT Eastern Projects Private Limited', 'Uttar Pradesh', 3, 'Uttam Singh'),
    ('LK',   'Lakhnadon-Khawasa', 'NHIT Eastern Projects Private Limited', 'Madhya Pradesh', 3, 'M. Ravindran'),
    ('HHC',  'Hubli-Haveri-Chitradurga', 'NHIT Eastern Projects Private Limited', 'Karnataka', 3, 'Suresh Shankar Narayana'),
    ('KP',   'Kalijhar-Patacharkuchi', 'NHIT Eastern Projects Private Limited', 'Assam', 3, 'K Bala Subramaniyam'),
    -- Southern
    ('NR',    'Narasannapeta-Ranasthalam', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, 'Krishna Rao (RO)'),
    ('RH',    'Ranasthalam to Hanumanthvaka', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, 'Krishna Rao (RO)'),
    ('APA',   'Anandapuram-Pendurthi-Anakapalle', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, 'Krishna Rao'),
    ('GDK',   'Gundugolanu Devarapalli Kovvur', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, 'Krishna Rao (RO)'),
    ('CM',    'Chittoor-Mallavaram', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, NULL),
    ('APTNK', 'AP/TN Border to Nalagampalli, AP/Karnataka Border Section', 'NHIT Southern Projects Private Limited', 'Andhra Pradesh', 4, 'Krishna Rao'),
    ('BS',    'Bareilly-Sitapur', 'NHIT Southern Projects Private Limited', 'Uttar Pradesh', 4, 'Anjani Kumar'),
    ('MH',    'Muzaffarnagar-Haridwar', 'NHIT Southern Projects Private Limited', 'Uttar Pradesh / Uttarakhand', 4, 'M Santosh Kumar'),
    ('RS',    'Raipur-Simga', 'NHIT Southern Projects Private Limited', 'Chhattisgarh', 4, 'Kuldeep Singh'),
    ('SB',    'Simga-Bilaspur', 'NHIT Southern Projects Private Limited', 'Chhattisgarh', 4, 'Kuldeep Singh'),
    ('GMP',   'Gandhidham-Mundra Port', 'NHIT Southern Projects Private Limited', 'Gujarat', 4, 'Krishna Rao (RO)'),
    -- Western
    ('AS',  'Abu Road-Swaroopganj', 'NHIT Western Projects Private Limited', 'Gujarat', 1, 'Shailendrasinh Rajput'),
    ('PA',  'Palanpur-Abu Road', 'NHIT Western Projects Private Limited', 'Rajasthan', 1, 'Shailendrasinh Rajput'),
    ('BK',  'Belgaum-Karnataka Border', 'NHIT Western Projects Private Limited', 'Karnataka', 1, 'M. Ravindran'),
    ('CK',  'Chittorgarh-Kota', 'NHIT Western Projects Private Limited', 'Rajasthan', 1, 'Manish Srivastava'),
    ('AB',  'Agra-Bypass', 'NHIT Western Projects Private Limited', 'Uttar Pradesh', 2, 'Rohit Kumar'),
    ('SJ',  'Shivpuri-Jhansi', 'NHIT Western Projects Private Limited', 'Madhya Pradesh', 2, 'Ranjay Singh'),
    ('BW',  'Borkhedi-Wadner-Deodhari-Kelapur', 'NHIT Western Projects Private Limited', 'Maharashtra', 2, 'Amit Rana'),
    ('KK',  'Kothkota Kurnool', 'NHIT Western Projects Private Limited', 'Telangana', 1, 'Sreedhar'),
    ('ACT', 'Amravati-Chikhali-Tarsod', 'NHIT Western Projects Private Limited', 'Maharashtra', 1, 'Vivek Choudhary'),
    ('GCA', 'Gundugolanu-Chinna Avutapalli', 'NHIT Western Projects Private Limited', 'Andhra Pradesh', 1, 'Kiran Kumar')
)
INSERT OR IGNORE INTO project
  (code, name, company_id, state, "round", project_manager, source, is_active)
SELECT project_seed.code, project_seed.name,
       (SELECT id FROM company WHERE name = project_seed.company),
       project_seed.state, project_seed.rnd, project_seed.project_manager, 'seed', 1
FROM project_seed;

-- ---------- toll plazas (46 canonical rows; one project each) ----------
WITH plaza(project, name, chainage, toll_manager, maintenance_manager, corridor_manager) AS (
  VALUES
    -- Eastern
    ('KRK',  'Patagaon',        '62.6',  'Deo Narayan Raut', 'Bal Krishna Dubey', 'Veerababu'),
    ('KRK',  'Dhalapara',       '51.5',  'Ranjan', 'Prabhash Kumar', 'Veerababu'),
    ('RKJL', 'Odhaki',          '69.1',  'Arpit Gupta', 'Veeramani', 'Kundan Kumar Gautam'),
    ('RKJL', 'Mohtara',         '88.07', 'Prathvi Rathi', 'Veeramani', 'Kamal'),
    ('RKJL', 'Kherwasani',      '67.3',  'Manjit Yadav', 'Veeramani', 'Kundan Kumar Gautam'),
    ('RKJL', 'Boharipar',       '81.7',  'Sunil Shukla', 'Veeramani', NULL),
    ('CHK',  'Balibhasa',       '56.1',  'Sanjiv kumar Singh', 'Mintu Nandi', 'Nripendra Dubey'),
    ('OB',   'Usaka',           '62.9',  'Ankesh Kumar', NULL, 'Inglesh'),
    ('LK',   'Madai',           '107.3', 'Rachit Agarwal', 'Avinish Tiwari', 'Vipi Singh'),
    ('LK',   'Khawasa',         '107.3', 'Hemant Kumar Rajput', 'Avinish Tiwari', 'Vipi Singh'),
    ('HHC',  'Bankapur',        '214.3', 'Matam Santosh', NULL, NULL),
    ('HHC',  'Chalageri',       '214.3', 'C Kesvareddy', NULL, NULL),
    ('HHC',  'Hebbalu',         '214.3', 'Raja shekhar', NULL, NULL),
    ('KP',   'Galia',           '27.3',  'Ravi Dubey', 'Bal Krishna Dubey', 'Veerababu'),
    -- Southern
    ('NR',    'Madapam',        '53.79', 'Ravella Satish Babu', NULL, NULL),
    ('RH',    'Nathavalasa',    '66.5',  'Ravi Babu', NULL, NULL),
    ('APA',   'Marripalem',     '48.6',  'Bodireddy Pydiraju', NULL, NULL),
    ('APA',   'Dukkavanipalem', '48.6',  'Tej Kumar', NULL, NULL),
    ('GDK',   'Veeravalli',     '70',    'Samarla Ramesh', NULL, 'Bukka Giri'),
    ('CM',    'Gadnaki',        '61.128', 'Senthil Kumar', NULL, NULL),
    ('APTNK', 'Mahasamudram',   '84.79', 'Griddaluru V.M.B. Krishnachaitanya', 'Kukuniri Subhramanya Kiran Kumar', 'Bukka Giri'),
    ('BS',    'Faridpur',       '157.59', 'Kasam Khan', 'Satendra Singh', 'Mohit Pandey'),
    ('BS',    'Maigalganj',     '157.59', 'Brijesh', 'Satendra Singh', 'Mohit Pandey'),
    ('MH',    'Bahadrabad',     '78.56', 'Jaldeep Singh', 'Arjun Singh (Dy. Manager)', 'PB Chamoli'),
    ('MH',    'Chapar',         '78.56', 'Azad Sharma', 'Arjun Singh (Dy. Manager)', 'PB Chamoli'),
    ('RS',    'Tarpongi',       '48.5',  'Binayak Dhal', 'Puddu Venkat Santosh Kumar', 'Vinay Pandey / Nikhil Ranjan'),
    ('SB',    'Bhojpuri',       '77.94', 'Naresh', 'Puddu Venkat Santosh Kumar', 'Vinay Pandey / Nikhil Ranjan'),
    ('SB',    'Mudipar',        '77.94', 'Pallav Kumar Jha', 'Puddu Venkat Santosh Kumar', 'Vinay Pandey / Nikhil Ranjan'),
    ('GMP',   'Mokha',          '71.4',  'Rajesh Patel', NULL, NULL),
    -- Western
    ('AS',  'Undvariya',        '31',    'Vikas', 'Shreejit Nair', 'Bipin Mevada'),
    ('PA',  'Khemana',          '45',    'Raghubhir Singh', 'Shreejit Nair', 'Bipin Mevada'),
    ('BK',  'Hattargi',         '78',    'Gauri Soma Naidu', NULL, NULL),
    ('BK',  'Kognoli',          '78',    'Gauri Soma Naidu', NULL, NULL),
    ('CK',  'Aroli',            '160.5', 'Kuldeep', 'Yogesh Kumar', 'Rajendra Raval'),
    ('CK',  'Bassi',            '160.5', 'Dharmendra Singh', 'Yogesh Kumar', 'Rajendra Raval'),
    ('CK',  'Dhaneshwar',       '160.5', 'Arun Sharma', 'Yogesh Kumar', 'Rajendra Raval'),
    ('AB',  'Raibha',           '32.8',  'Ishwar Sharma', 'Rohit Kumar', 'Deepak Sharma'),
    ('SJ',  'Raksha',           '75.3',  'Sandeep Bhadana', 'Arif Shakoor', 'Nitish Bahuguna'),
    ('BW',  'Daroda',           '138.4', 'Malkeet Singh', NULL, 'Bhanu Pratap Singh'),
    ('BW',  'Kelapur',          '138.4', 'Neeraj Ramyani', NULL, 'Bhanu Pratap Singh'),
    ('KK',  'Pullur',           '74.62', 'Sreedhar', 'Unam Rajesh', 'M D Samiulla'),
    ('ACT', 'Tarod Kasba',      '73',    'Anand Mandoli', NULL, NULL),
    ('ACT', 'Dasarkhed',        '45',    'Sachin Choudhary', NULL, NULL),
    ('ACT', 'Kurankhed',        '76',    'Vikrant', NULL, NULL),
    ('ACT', 'Nashirabad',       '62.7',  'Omvir Singh', NULL, NULL),
    ('GCA', 'Kalaparru',        '54.4',  'Panduranga Raju', 'Satyapal Sopanrao Vaidya', 'Teja Swaroop')
)
INSERT OR IGNORE INTO toll_plaza
  (project_id, name, chainage, toll_manager, maintenance_manager, corridor_manager, source)
SELECT p.id, plaza.name, plaza.chainage, plaza.toll_manager,
       plaza.maintenance_manager, plaza.corridor_manager, 'seed'
FROM plaza
JOIN project p ON p.code = plaza.project;

-- ---------- legacy cleanup (rows harvested from the old Excel tracker) ----------
-- These were corridor names, combined spellings or OCR aliases attached to the
-- wrong project. They stay as inactive rows only so an old BG that already
-- points at one still resolves; they are not matchable and not in the dropdown.
-- NSPPL/NEPPL/NWPPL are short forms of the three SPVs, so they must not appear
-- as companies of their own.
UPDATE company SET is_active = 0 WHERE name IN ('NSPPL', 'NEPPL', 'NWPPL');
UPDATE project SET is_active = 0 WHERE code = 'RS,SB';
UPDATE toll_plaza SET is_active = 0
WHERE name IN (
  'Anandapuram to Hanumanthavaka',
  'Simga - Bilaspur', 'Simga to Bilaspur',
  'Raipur', 'Raipur - Simga',
  'Chappar', 'Chappar & Bahadrabad', 'Chappar/Bahadrabad',
  'Muzafarnagar - Haridwar', 'Muzafarnagar to Haridwar',
  'Bareilly Sitapur', 'Barielly - Sitapur',
  'Faridpur & Maigalganj',
  'Bhojpuri & Mudipar', 'Mudipar & Bhojpuri',
  'Dukkavaniplem', 'Dukkavaniplem & Marriplem',
  'Dukkawalipalem & Maripalem',
  'Gadanki', 'Veeravali', 'Nathavalsa',
  'Mahasamundaram', 'Mahasamudaram', 'Mahasamundram'
);

-- ---------- banks ----------
-- No prefilled bank list. The first confirmed BG creates the issuing bank,
-- after the Firecrawl name check; `bg_records` only stores its id.

-- ---------- contractors ----------
-- No prefilled contractor list. The first confirmed BG creates the applicant,
-- after the Firecrawl name check; `bg_records` only stores its id.

-- ---------- bg types ----------
INSERT OR IGNORE INTO bg_type (code) VALUES ('PBG'), ('ABG');

-- ---------- natures of service (27) ----------
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
  ('Signboard Installation'), ('Street Light_ABG'), ('TMS'),
  ('Routine Maintenance'), ('Supply & Installation of Overhead Gantry');

-- ---------- lifecycle statuses (approved set) ----------
INSERT OR IGNORE INTO bg_status (code) VALUES
  ('Draft'), ('Active'), ('Expiring'), ('Expired'), ('Extended'), ('Closed');

-- ---------- roles (R5: admin / uploader / viewer) ----------
INSERT OR IGNORE INTO roles (name) VALUES ('admin'), ('uploader'), ('viewer');
