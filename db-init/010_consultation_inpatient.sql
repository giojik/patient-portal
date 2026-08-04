-- ახალი კატეგორია: სტაციონარული კონსულტაცია ("კონსულტაცია (სტაციონარი)")
-- ცალკდება ჩვეულებრივი კონსულტაციისგან და დამალულია ნაგულისხმევად.
-- გაუშვით:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/010_consultation_inpatient.sql

INSERT INTO feature_flags (feature_key, label, enabled) VALUES
    ('consultation_inpatient', 'კონსულტაცია (სტაციონარი)', FALSE)
ON CONFLICT (feature_key) DO NOTHING;