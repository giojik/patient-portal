-- 007-ის გაუქმება: მენიუს პუნქტი საბოლოოდ არ შეცვლილა, მხოლოდ გვერდის
-- სათაური — ამიტომ ადმინის პანელის label-იც ვაბრუნებთ თავდაპირველზე.
-- გაუშვით მხოლოდ იმ შემთხვევაში, თუ უკვე გაუშვით 007_rename_radiology_label.sql.
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/008_revert_radiology_label.sql

UPDATE feature_flags SET label = 'რადიოლოგიური პასუხები' WHERE feature_key = 'radiology';