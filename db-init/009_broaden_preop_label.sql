-- preop_epicrisis კატეგორია ახლა მოიცავს არა მხოლოდ "წინასაოპერაციო ეპიკრიზს",
-- არამედ ანესთეზიოლოგის წინასაოპერაციო ჩანაწერსაც და ოპერაციისთვის
-- მომზადების ფურცელსაც — ამიტომ label-იც ვაზუსტებთ.
-- გაუშვით:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/009_broaden_preop_label.sql

UPDATE feature_flags
SET label = 'წინასაოპერაციო დოკუმენტები (ეპიკრიზი, ანესთეზიოლოგის ჩანაწერი, მომზადების ფურცელი)'
WHERE feature_key = 'preop_epicrisis';