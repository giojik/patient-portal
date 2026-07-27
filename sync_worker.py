"""
პერიოდულად აწამოღებს ახალ დასრულებულ შედეგებს Terra-დან
და წერს Portal DB-ში (Postgres) დაშიფრული სახით.
გაეშვება loop() ფუნქციით, პერიოდულად (SYNC_INTERVAL_SECONDS).

Performance შენიშვნა: დიდი მოცულობის (100k+) ჩანაწერებზე row-by-row
insert ძალიან ნელია. ამიტომ:
  - პაციენტების login_hash -> id მეპინგი ერთხელ იტვირთება მეხსიერებაში
  - შედეგები batch-ებად (BATCH_SIZE) insert-დება execute_values-ით

Schema evolution: insert-ები იყენებენ ON CONFLICT DO UPDATE-ს (არა
DO NOTHING-ს) — ეს ნიშნავს, რომ Postgres-ის სქემაში ახალი სვეტის
დამატებისას (მაგ. norm_low_enc) საკმარისია მხოლოდ:
  1) ALTER TABLE-ით სვეტის დამატება (არსებული მონაცემები არ იშლება)
  2) sync_worker-ის ჩვეულებრივი გაშვება/loop-ის გაგრძელება
და ის ავტომატურად "ჩაავსებს" ახალ სვეტს ყველა უკვე არსებულ row-ზეც,
TRUNCATE-ისა და თავიდან სრული სინქრონიზაციის გარეშე.
"""
import os
import sys
import time
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import execute_values
from firebird.driver import connect as fb_connect

from app.crypto import encrypt_field, hash_lookup

TERRA_DSN = os.environ["DB_DSN"]
TERRA_USER = os.environ["DB_USER"]
TERRA_PASSWORD = os.environ["DB_PASSWORD"]

PG_DSN = os.environ["PORTAL_DB_DSN"]

SYNC_WINDOW_DAYS = int(os.environ.get("SYNC_WINDOW_DAYS", "7"))
BATCH_SIZE = int(os.environ.get("SYNC_BATCH_SIZE", "2000"))


def fetch_recent_results(terra_con, since_date):
    """
    რეალური სქემა (დადასტურებულია production მონაცემებზე):
      JOR_CHECKS_DT  — თითო ტესტ-ხაზი, აქვს CHECK_CLIENT_ID პირდაპირ,
                       DATE_DONE (შევსებულია, როცა შედეგი ფაქტობრივად მზადაა)
      JOR_RESULTS_DT — შედეგები, უკავშირდება HD_ID = JOR_CHECKS_DT.ID-ს

    STATUS ველი JOR_CHECKS_DT-ში არასანდოა "დასრულებულის" ინდიკატორად
    (ნანახია NULL რეალურად დასრულებულ ტესტებზეც) — ამიტომ ვიყენებთ
    dt.DATE_DONE IS NOT NULL-ს ამის მაგივრად.

    დაბრუნებულია python generator (fetchmany-ით), რომ ძალიან დიდი
    შედეგი ერთბაშად მეხსიერებაში არ ჩაიტვირთოს.
    """
    cur = terra_con.cursor()
    cur.execute(
        """
        SELECT
            r.ID               AS result_id,
            dt.ID              AS panel_group_id,
            dt.GOODS_NAME      AS panel_name,
            dt.CHECK_CLIENT_ID AS client_id,
            dt.DATE_DONE       AS sample_date,
            COALESCE(r.NAME, dt.GOODS_NAME) AS test_name,
            r.RESULT           AS result_value,
            r.RESULT_TEXT      AS result_text,
            r.UNIT_NAME        AS unit,
            r.LOW              AS norm_low,
            r.HIGH             AS norm_high,
            r.IS_OUT_OF_NORM   AS is_out_of_norm,
            cl.LOGIN           AS login,
            cl.SURNAME         AS surname,
            cl.NAME            AS client_name
        FROM JOR_CHECKS_DT dt
        JOIN JOR_RESULTS_DT r ON r.HD_ID = dt.ID
        JOIN DIC_CLIENTS cl ON cl.ID = dt.CHECK_CLIENT_ID
        WHERE dt.DATE_DONE >= ?
          AND dt.DATE_DONE IS NOT NULL
          AND cl.LOGIN IS NOT NULL
        ORDER BY dt.DATE_DONE
        """,
        (since_date,),
    )
    columns = [d[0].lower() for d in cur.description]
    while True:
        batch = cur.fetchmany(BATCH_SIZE)
        if not batch:
            break
        for row in batch:
            yield dict(zip(columns, row))


def load_patient_cache(pg_con) -> dict:
    """წინასწარ ჩატვირთავს login_hash -> patient_id მეპინგს მეხსიერებაში."""
    cur = pg_con.cursor()
    cur.execute("SELECT login_hash, id FROM patients")
    return {login_hash: pid for login_hash, pid in cur.fetchall()}


def get_or_create_patient(pg_con, cache: dict, row) -> int:
    login = row["login"].strip()
    login_hash = hash_lookup(login)

    if login_hash in cache:
        return cache[login_hash]

    full_name = f"{row['surname']} {row['client_name']}"
    cur = pg_con.cursor()
    cur.execute(
        """
        INSERT INTO patients (terra_client_id, login_hash, login_enc, full_name_enc)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (terra_client_id) DO UPDATE SET
            login_hash    = EXCLUDED.login_hash,
            login_enc     = EXCLUDED.login_enc,
            full_name_enc = EXCLUDED.full_name_enc
        RETURNING id
        """,
        (row["client_id"], login_hash, encrypt_field(login), encrypt_field(full_name)),
    )
    patient_id = cur.fetchone()[0]
    cache[login_hash] = patient_id
    return patient_id


def sync():
    since_date = datetime.now() - timedelta(days=SYNC_WINDOW_DAYS)

    terra_con = fb_connect(TERRA_DSN, user=TERRA_USER, password=TERRA_PASSWORD)
    pg_con = psycopg2.connect(PG_DSN)
    try:
        patient_cache = load_patient_cache(pg_con)
        print(f"[{datetime.now()}] მეხსიერებაშია {len(patient_cache)} პაციენტი")

        batch = []
        total_seen = 0
        total_inserted = 0

        def flush_batch():
            nonlocal total_inserted
            if not batch:
                return
            cur = pg_con.cursor()
            execute_values(
                cur,
                """
                INSERT INTO results
                    (patient_id, terra_sample_id, panel_group_id, panel_name_enc,
                     test_name_enc, result_value_enc, unit_enc,
                     norm_low_enc, norm_high_enc, is_out_of_norm, sample_date)
                VALUES %s
                ON CONFLICT (terra_sample_id) DO UPDATE SET
                    panel_group_id   = EXCLUDED.panel_group_id,
                    panel_name_enc   = EXCLUDED.panel_name_enc,
                    test_name_enc    = EXCLUDED.test_name_enc,
                    result_value_enc = EXCLUDED.result_value_enc,
                    unit_enc         = EXCLUDED.unit_enc,
                    norm_low_enc     = EXCLUDED.norm_low_enc,
                    norm_high_enc    = EXCLUDED.norm_high_enc,
                    is_out_of_norm   = EXCLUDED.is_out_of_norm
                """,
                batch,
            )
            total_inserted += cur.rowcount
            pg_con.commit()
            batch.clear()

        for row in fetch_recent_results(terra_con, since_date):
            total_seen += 1
            patient_id = get_or_create_patient(pg_con, patient_cache, row)

            display_value = row["result_value"]
            if display_value is None:
                display_value = row["result_text"]

            batch.append((
                patient_id,
                row["result_id"],
                row["panel_group_id"],
                encrypt_field(row["panel_name"] or ""),
                encrypt_field(row["test_name"] or ""),
                encrypt_field(str(display_value) if display_value is not None else ""),
                encrypt_field(row["unit"] or ""),
                encrypt_field(str(row["norm_low"])) if row["norm_low"] is not None else None,
                encrypt_field(str(row["norm_high"])) if row["norm_high"] is not None else None,
                bool(row["is_out_of_norm"]) if row["is_out_of_norm"] is not None else None,
                row["sample_date"],
            ))

            if len(batch) >= BATCH_SIZE:
                flush_batch()
                print(f"[{datetime.now()}] დამუშავებულია {total_seen} ჩანაწერი...")

        flush_batch()
        print(f"[{datetime.now()}] დასრულდა. სულ ნანახი: {total_seen}, ახალი ჩანაწერი: {total_inserted}")
    finally:
        terra_con.close()
        pg_con.close()


def loop():
    """
    მუდმივად გაშვებული ციკლი — sync() ყოველ SYNC_INTERVAL_SECONDS-ში.
    ერთი გაშვების ჩავარდნა არ აჩერებს პროცესს — ლოგდება და აგრძელებს.
    """
    interval = int(os.environ.get("SYNC_INTERVAL_SECONDS", "300"))  # ნაგულისხმევი 5 წუთი
    print(f"[{datetime.now()}] sync-worker გაშვებულია loop რეჟიმში, ინტერვალი: {interval}წმ")
    while True:
        try:
            sync()
        except Exception as e:
            print(f"[{datetime.now()}] sync ჩავარდა: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    if "--once" in sys.argv or os.environ.get("SYNC_RUN_ONCE") == "1":
        sync()
    else:
        loop()