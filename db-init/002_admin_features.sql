-- ადმინის პანელი: ფუნქციების ჩართვა/გამორთვა + პაციენტთა ჯგუფები.
-- შენიშვნა: db-init მხოლოდ ცარიელ Postgres volume-ზე მუშაობს ავტომატურად.
-- არსებულ სერვერზე გაუშვით ხელით:
--   docker exec -i <postgres-container> psql -U portal_app -d labportal < db-init/002_admin_features.sql

CREATE TABLE IF NOT EXISTS admin_users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMP DEFAULT now()
);

-- ფუნქციების გადამრთველები (გლობალური ჩართვა/გამორთვა კატეგორიის მიხედვით).
-- feature_key ემთხვევა onec_client.py-ის "category" მნიშვნელობებს
-- (lab, radiology, forma100, prescription, ...), ამიტომ ახალი კატეგორიის
-- დამატებისას საკმარისია აქაც დაემატოს შესაბამისი row.
CREATE TABLE IF NOT EXISTS feature_flags (
    feature_key   TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMP DEFAULT now()
);

INSERT INTO feature_flags (feature_key, label, enabled) VALUES
    ('lab',          'ლაბორატორიული ანალიზები',  TRUE),
    ('radiology',    'რადიოლოგიური პასუხები',    TRUE),
    ('forma100',     'ფორმა 100',                TRUE),
    ('prescription', 'დანიშნულებები',            TRUE)
ON CONFLICT (feature_key) DO NOTHING;

-- პაციენტთა ჯგუფები (მოქნილი, როლის მსგავსი სეგმენტაცია ფუნქციების override-ისთვის)
CREATE TABLE IF NOT EXISTS patient_groups (
    id         SERIAL PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);

-- ჯგუფის წევრები. source განასხვავებს Terra-ს (patients.id) და
-- 1C-ის (Catalog_Картотека Ref_Key) პაციენტებს, რადგან ისინი
-- ცალკე ცხრილებში/სისტემებში ცხოვრობენ.
CREATE TABLE IF NOT EXISTS patient_group_members (
    id               SERIAL PRIMARY KEY,
    group_id         INT NOT NULL REFERENCES patient_groups(id) ON DELETE CASCADE,
    source           TEXT NOT NULL CHECK (source IN ('terra', 'onec')),
    subject_ref      TEXT NOT NULL,
    display_name_enc TEXT,
    added_at         TIMESTAMP DEFAULT now(),
    UNIQUE (group_id, source, subject_ref)
);

CREATE INDEX IF NOT EXISTS idx_group_members_lookup ON patient_group_members(source, subject_ref);

-- კონკრეტული ჯგუფისთვის override (გლობალურის ნაცვლად)
CREATE TABLE IF NOT EXISTS feature_flag_overrides (
    feature_key TEXT NOT NULL REFERENCES feature_flags(feature_key) ON DELETE CASCADE,
    group_id    INT NOT NULL REFERENCES patient_groups(id) ON DELETE CASCADE,
    enabled     BOOLEAN NOT NULL,
    PRIMARY KEY (feature_key, group_id)
);