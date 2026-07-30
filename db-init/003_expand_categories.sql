-- ახალი დოკუმენტის კატეგორიები, 1C-ის Catalog_ШаблоныМедицинскихДокументов-ის
-- რეალურ მონაცემებზე დაფუძნებული დიაგნოსტიკის შედეგად.
-- გაუშვით არსებულ ბაზაზე:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/003_expand_categories.sql

-- პაციენტმა უნდა ნახოს ნაგულისხმევად:
INSERT INTO feature_flags (feature_key, label, enabled) VALUES
    ('discharge_recommendation', 'რეკომენდაციები გაწერისას', TRUE),
    ('consultation',             'კონსულტაციები',            TRUE)
ON CONFLICT (feature_key) DO NOTHING;

-- პაციენტმა არ უნდა ნახოს ნაგულისხმევად (ადმინს შეუძლია ჩართოს):
INSERT INTO feature_flags (feature_key, label, enabled) VALUES
    ('exam_diary',            'პაციენტის გასინჯვის ფურცელი (დღიური)',              FALSE),
    ('discharge_epicrisis',   'გაწერის ეპიკრიზი',                                   FALSE),
    ('preop_epicrisis',       'წინასაოპერაციო ეპიკრიზი',                            FALSE),
    ('anesthesia_protocol',   'გაუტკივარების ოქმი',                                 FALSE),
    ('operation_protocol',    'ოპერაციის/ჩარევის ოქმი',                             FALSE),
    ('admitting_doctor_note', 'მიმღები (მორიგე) მკურნალი ექიმის ჩანაწერი',          FALSE),
    ('surgical_team',         'საოპერაციო ბრიგადა',                                 FALSE)
ON CONFLICT (feature_key) DO NOTHING;

-- ძველი label-ების დაზუსტება (lab/radiology/forma100/prescription 002-დან უკვე არსებობს)
UPDATE feature_flags SET label = 'დანიშნულებები (მომსახურებების დანიშვნა)' WHERE feature_key = 'prescription';