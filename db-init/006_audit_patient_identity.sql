-- აუდიტ ლოგში პაციენტის ვინაობის ჩვენებისთვის (პირადი ნომერი + სახელი გვარი),
-- დაშიფრული (იგივე AES-256-GCM, რაც patients/results ცხრილებში).
-- გაუშვით:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/006_audit_patient_identity.sql

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS patient_personal_id_enc TEXT;
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS patient_full_name_enc TEXT;