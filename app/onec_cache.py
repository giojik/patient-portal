"""
Postgres cache ფენა 1C-ის შედეგებისთვის.

მიზანი: Portal API (/api/results) აღარასდროს ელაპარაკება 1C-ს პირდაპირ.
ორი დამოუკიდებელი მექანიზმი წერს ამ cache-ში:

  1. ერთჯერადი "backfill" — კონკრეტული პაციენტის პირველი login-ისას,
     fire-and-forget (BackgroundTasks), მთელი ისტორია ერთხელ.
  2. periodic "onec_sync_worker.py" — გლობალურად, ყველა უკვე
     backfill-ილი პაციენტის ბოლო 7 დღის ცვლილებები.

ორივე წერს ერთსა და იმავე onec_documents ცხრილში, ON CONFLICT DO UPDATE-ით
(doc_ref უნიკალურია) — ამიტომ ორივეს ერთდროულად გაშვება უსაფრთხოა.
"""
import json
import os
import psycopg2

from app.crypto import encrypt_field, decrypt_field
from app.onec_client import fetch_all_paginated, _document_to_results

PG_DSN = os.environ["PORTAL_DB_DSN"]


def _connect():
    return psycopg2.connect(PG_DSN)


# ============ backfill სტატუსი ============

def get_backfill_status(kartoteka_ref: str) -> str:
    """'none' | 'in_progress' | 'done'"""
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT status FROM onec_patient_sync WHERE kartoteka_ref = %s",
            (kartoteka_ref,),
        )
        row = cur.fetchone()
        return row[0] if row else "none"
    finally:
        con.close()


def claim_backfill(kartoteka_ref: str) -> bool:
    """
    ატომურად "იჭერს" backfill-ის უფლებას ამ პაციენტზე — race condition-ის
    დაცვა, თუ ერთი და იმავე პაციენტის ორი პარალელური request მოვიდა
    (მაგ. ორი tab, ან double-click login-ზე).

    აბრუნებს True-ს, თუ ამ request-მა დაიკავა backfill (ანუ ის უნდა
    გაუშვას), False-ს — თუ უკვე სხვამ დაიკავა/დაასრულა.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO onec_patient_sync (kartoteka_ref, status, last_checked_at)
            VALUES (%s, 'in_progress', now())
            ON CONFLICT (kartoteka_ref) DO NOTHING
            RETURNING kartoteka_ref
            """,
            (kartoteka_ref,),
        )
        claimed = cur.fetchone() is not None
        con.commit()
        return claimed
    finally:
        con.close()


def mark_backfill_done(kartoteka_ref: str):
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            """
            UPDATE onec_patient_sync
            SET status = 'done', backfilled_at = now(), last_checked_at = now(), error = NULL
            WHERE kartoteka_ref = %s
            """,
            (kartoteka_ref,),
        )
        con.commit()
    finally:
        con.close()


def mark_backfill_error(kartoteka_ref: str, error: str):
    """
    ჩავარდნისას status უბრუნდება 'none'-ს (row-ის წაშლით), რომ შემდეგმა
    login-მა თავიდან სცადოს — თორემ პაციენტი სამუდამოდ 'in_progress'-ში
    გაჭედავს, თუ worker-ი ერთხელ ჩამოვარდა (timeout, 1C მიუწვდომელია).
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM onec_patient_sync WHERE kartoteka_ref = %s", (kartoteka_ref,))
        con.commit()
    finally:
        con.close()
    print(f"[onec_cache] backfill ჩავარდა {kartoteka_ref}-სთვის: {error}")


# ============ დოკუმენტების ჩაწერა (backfill + periodic worker ორივესთვის) ============

def upsert_document(con, kartoteka_ref: str, doc: dict):
    """
    ერთი 1C დოკუმენტის დამუშავება და cache-ში ჩაწერა. con უკვე ღია
    კავშირი უნდა იყოს — caller-მა უნდა გააკეთოს commit() batch-ის ბოლოს
    (performance-ისთვის, backfill/worker-ში ბევრი დოკუმენტია).
    """
    doc_ref = doc.get("Ref_Key")
    doc_date = doc.get("Date")
    if not doc_ref:
        return

    items = _document_to_results(doc)
    if not items:
        return

    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO onec_documents (kartoteka_ref, doc_ref, doc_date, items_enc)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (doc_ref) DO UPDATE SET
            items_enc = EXCLUDED.items_enc,
            doc_date  = EXCLUDED.doc_date,
            synced_at = now()
        """,
        (kartoteka_ref, doc_ref, doc_date, encrypt_field(json.dumps(items))),
    )


def backfill_single_patient(kartoteka_ref: str):
    """
    ერთი პაციენტის მთელი ისტორიის წამოღება 1C-დან — მოსალოდნელია
    გამოძახებულ იქნას background task-იდან (main.py-ში), არა request-ის
    thread-ში.

    Caller პასუხისმგებელია claim_backfill()-ის გამოძახებაზე *ადრე*,
    და mark_backfill_done() / mark_backfill_error()-ზე ბოლოს.
    """
    con = _connect()
    try:
        for doc in fetch_all_paginated(
            "Document_МедицинскийДокумент",
            f"Пациент_Key eq guid'{kartoteka_ref}'",
        ):
            upsert_document(con, kartoteka_ref, doc)
        con.commit()
    finally:
        con.close()


def run_patient_backfill(kartoteka_ref: str):
    """
    სრული, თავდაცვითი wrapper — ეს არის ფუნქცია, რომელსაც
    BackgroundTasks.add_task() უნდა გამოიძახებდეს main.py-დან.
    """
    try:
        backfill_single_patient(kartoteka_ref)
        mark_backfill_done(kartoteka_ref)
    except Exception as e:
        mark_backfill_error(kartoteka_ref, str(e))


# ============ Cache-დან წაკითხვა (Portal API-ს გამოსაყენებლად) ============

def get_patient_results_cached(kartoteka_ref: str) -> list:
    """
    ცვლის onec_client.get_patient_results()-ის live call-ს. აბრუნებს
    იმავე ბრტყელ სიას, უბრალოდ Postgres cache-დან.
    """
    con = _connect()
    try:
        cur = con.cursor()
        cur.execute(
            "SELECT items_enc FROM onec_documents WHERE kartoteka_ref = %s ORDER BY doc_date DESC",
            (kartoteka_ref,),
        )
        results = []
        for (items_enc,) in cur.fetchall():
            results.extend(json.loads(decrypt_field(items_enc)))
        return results
    finally:
        con.close()