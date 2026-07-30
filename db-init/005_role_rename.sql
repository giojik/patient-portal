-- როლების მოდელის დაზუსტება: 'editor' -> 'manager'.
-- superadmin: ყველაფრის მართვა (მომხმარებლები, პარამეტრები, ფუნქციები).
-- manager: მხოლოდ ნახვა — კონფიგურაცია (ფუნქციები/პარამეტრები) + აუდიტ ლოგი.
-- viewer: მხოლოდ ნახვა — კონფიგურაცია (რა არის გააქტიურებული), აუდიტის გარეშე.
--
-- იდემპოტენტურია — შეგიძლიათ გაუშვათ მიუხედავად იმისა, გაუშვით თუ არა
-- ადრე 004_admin_extras.sql ძველი 'editor' როლით.
-- გაუშვით:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/005_role_rename.sql

-- ძველი შეზღუდვა (თუ არსებობს) მოვხსნათ, სანამ მონაცემებს განვაახლებთ
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'admin_users_role_check') THEN
        ALTER TABLE admin_users DROP CONSTRAINT admin_users_role_check;
    END IF;
END $$;

UPDATE admin_users SET role = 'manager' WHERE role = 'editor';

ALTER TABLE admin_users ADD CONSTRAINT admin_users_role_check
    CHECK (role IN ('superadmin', 'manager', 'viewer'));
