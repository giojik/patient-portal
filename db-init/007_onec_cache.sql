-- 1C-ის cache ფენა: პაციენტის ყოველ login-ზე 1C-სთან live-კავშირის
-- ნაცვლად, შედეგები ინახება Postgres-ში და იკითხება იქიდან.
--
-- გაუშვით არსებულ ბაზაზე:
--   docker exec -i labportal-portal-db-1 psql -U portal_app -d labportal < db-init/007_onec_cache.sql

-- ერთი დოკუმენტის (Document_МедицинскийДокумент) დამუშავებული,
-- დაშიფრული shape — იმავე ფორმატით, რასაც onec_client._document_to_results()
-- აბრუნებდა live call-ზე. ერთი დოკუმენტი = ერთი ან რამდენიმე "items" ჩანაწერი,
-- ამიტომ items_enc ინახავს JSON მასივს (base64(nonce+ciphertext) სახით).
CREATE TABLE IF NOT EXISTS onec_documents (
    id             SERIAL PRIMARY KEY,
    kartoteka_ref  TEXT NOT NULL,          -- Пациент_Key (1C GUID)
    doc_ref        TEXT UNIQUE NOT NULL,   -- Document_МедицинскийДокумент.Ref_Key
    doc_date       TIMESTAMPTZ,
    items_enc      TEXT NOT NULL,          -- დაშიფრული JSON, _document_to_results()-ის output
    synced_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_onec_documents_kartoteka ON onec_documents(kartoteka_ref);
CREATE INDEX IF NOT EXISTS idx_onec_documents_doc_date ON onec_documents(doc_date);

-- ტრეკავს, თითო პაციენტზე (kartoteka_ref) სრული ისტორიის backfill
-- უკვე დასრულდა თუ არა — რომ ერთხელ backfill-ილი პაციენტისთვის
-- აღარასდროს გაეშვას სრული ისტორიის ხელახალი წამოღება.
--
-- status: 'in_progress' | 'done'
-- (row-ის არარსებობა ნიშნავს "ჯერ არასდროს დაწყებულა")
CREATE TABLE IF NOT EXISTS onec_patient_sync (
    kartoteka_ref    TEXT PRIMARY KEY,
    status           TEXT NOT NULL DEFAULT 'in_progress',
    backfilled_at    TIMESTAMPTZ,          -- NULL სანამ status != 'done'
    last_checked_at  TIMESTAMPTZ,
    error            TEXT                  -- ბოლო ჩავარდნის მიზეზი, თუ იყო
);

-- periodic (7-დღიანი) worker-ის watermark — საიდან გააგრძელოს შემდეგმა
-- გაშვებამ. source = 'onec' (მომავალში სხვა წყაროც რომ დაემატოს, ცხრილი
-- უკვე მზადაა).
CREATE TABLE IF NOT EXISTS sync_state (
    source          TEXT PRIMARY KEY,
    last_synced_at  TIMESTAMPTZ NOT NULL
);