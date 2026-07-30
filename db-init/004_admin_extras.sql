-- კლინიკის პარამეტრები, ადმინის როლები და აუდიტ ლოგი.
-- გაუშვით არსებულ ბაზაზე:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/004_admin_extras.sql

-- ერთადერთი მწკრივი (singleton) კლინიკის ზოგადი პარამეტრებისთვის
CREATE TABLE IF NOT EXISTS clinic_settings (
    id          INT PRIMARY KEY DEFAULT 1,
    timezone    TEXT NOT NULL DEFAULT 'Asia/Tbilisi',
    clinic_name TEXT NOT NULL DEFAULT 'Innova Medical',
    address     TEXT NOT NULL DEFAULT '',
    website     TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL DEFAULT '',
    updated_at  TIMESTAMP DEFAULT now(),
    CHECK (id = 1)
);

INSERT INTO clinic_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- ადმინის როლები: superadmin (სრული წვდომა, მათ შორის მომხმარებლების მართვა),
-- manager (კონფიგურაციისა და აუდიტ ლოგის ნახვა), viewer (მხოლოდ კონფიგურაციის ნახვა)
ALTER TABLE admin_users ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'superadmin';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'admin_users_role_check'
    ) THEN
        ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_check
            CHECK (role IN ('superadmin', 'manager', 'viewer'));
    END IF;
END $$;

-- აუდიტ ლოგი: ვინ შემოვიდა საიდან, რა ნახა, რა ჩამოტვირთა/დაბეჭდა და ა.შ.
CREATE TABLE IF NOT EXISTS audit_log (
    id          BIGSERIAL PRIMARY KEY,
    occurred_at TIMESTAMP NOT NULL DEFAULT now(),
    actor_type  TEXT NOT NULL CHECK (actor_type IN ('admin', 'patient')),
    actor_label TEXT NOT NULL,      -- admin: username; patient: დაშიფრული/ნიღბიანი იდენტიფიკატორი
    ip_address  TEXT,
    action      TEXT NOT NULL,      -- მაგ. 'login', 'view_results', 'download_report', 'toggle_feature'
    details     TEXT                -- თავისუფალი ტექსტი დამატებითი კონტექსტისთვის
);

CREATE INDEX IF NOT EXISTS idx_audit_occurred_at ON audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_type, actor_label);