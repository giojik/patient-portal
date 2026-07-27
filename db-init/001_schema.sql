-- Portal DB სქემა: მგრძნობიარე ველები დაშიფრულია აპლიკაციის დონეზე.

CREATE TABLE IF NOT EXISTS patients (
    id               SERIAL PRIMARY KEY,
    terra_client_id  TEXT UNIQUE NOT NULL,   -- Terra DIC_CLIENTS.ID (hex string)
    login_hash       TEXT UNIQUE NOT NULL,   -- HMAC(LOGIN), საძიებლად
    login_enc        TEXT NOT NULL,          -- დაშიფრული LOGIN, საჩვენებლად
    full_name_enc    TEXT NOT NULL,          -- დაშიფრული SURNAME+NAME
    created_at       TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS results (
    id               SERIAL PRIMARY KEY,
    patient_id       INT REFERENCES patients(id),
    terra_sample_id  TEXT UNIQUE NOT NULL,   -- Terra JOR_RESULTS_DT.ID
    panel_group_id   TEXT,                   -- Terra JOR_CHECKS_DT.ID — აჯგუფებს ერთი შეკვეთის ანალიტებს
    panel_name_enc   TEXT,                   -- დაშიფრული პანელის სახელი (მაგ. "სისხლის საერთო ანალიზი")
    test_name_enc    TEXT NOT NULL,
    result_value_enc TEXT NOT NULL,
    unit_enc         TEXT,
    norm_low_enc     TEXT,                   -- დაშიფრული ნორმის ქვედა ზღვარი
    norm_high_enc    TEXT,                   -- დაშიფრული ნორმის ზედა ზღვარი
    is_out_of_norm   BOOLEAN,                -- Terra-ს IS_OUT_OF_NORM ალამი
    sample_date      TIMESTAMP NOT NULL,
    synced_at        TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_panel_group ON results(panel_group_id);

-- 1C-ის პირადი ნომერი + OTP ავტორიზაციისთვის
CREATE TABLE IF NOT EXISTS otp_codes (
    id                SERIAL PRIMARY KEY,
    personal_id_hash  TEXT NOT NULL,
    code_hash         TEXT NOT NULL,
    onec_ref          TEXT NOT NULL,
    full_name_enc     TEXT NOT NULL,
    phone_enc         TEXT,
    expires_at        TIMESTAMP NOT NULL,
    attempts          INT DEFAULT 0,
    created_at        TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_otp_personal_id ON otp_codes(personal_id_hash);

CREATE INDEX IF NOT EXISTS idx_login_hash ON patients(login_hash);
CREATE INDEX IF NOT EXISTS idx_patient_results ON results(patient_id);