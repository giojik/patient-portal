"""
პერიოდულად აწამოღებს ახალ/განახლებულ დოკუმენტებს 1C-დან (ბოლო sync-ის
შემდეგ, პლუს მცირე overlap ბუფერი საათებში — იხ. ONEC_SYNC_OVERLAP_HOURS —
გლობალურად, ყველა პაციენტისთვის ერთდროულად, არა თითო-თითო loop-ით) და
წერს Portal DB-ში (Postgres), onec_documents ცხრილში, ON CONFLICT DO
UPDATE-ით.

ეს ცალკეა ცალკეული პაციენტის "backfill"-ისგან (იხ. app/onec_cache.py:
run_patient_backfill) — backfill აწამოღებს ერთი კონკრეტული პაციენტის
*მთელ* ისტორიას, პირველი login-ისას; ეს worker-ი კი მხოლოდ *ცვლილებებს*
აწამოღებს, უკვე backfill-ილი პაციენტებისთვის, ისე რომ portal API
(/api/results) ყოველთვის "თბილ" cache-ს კითხულობდეს.

გაეშვება loop() ფუნქციით, პერიოდულად (ONEC_SYNC_INTERVAL_SECONDS).
--once ფლაგით — ერთხელ გაშვება (მაგ. ხელით, დიაგნოსტიკისთვის).
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg2

from app.onec_client import fetch_by_date_windows
from app.onec_cache import upsert_document

PG_DSN = os.environ["PORTAL_DB_DSN"]

# overlap buffer — ყოველ გაშვებაზე ბოლო N *საათს* ხელახლა ვამუშავებთ კიდეც
# (idempotent, ON CONFLICT DO UPDATE), დაგვიანებული/რედაქტირებული 1C
# ჩანაწერების დასაჭერად. ⚠️ განზრახ არის საათებში, არა დღეებში — 5-წუთიან
# ციკლში 7-დღიანი overlap ყოველ გაშვებაზე ნიშნავდა ათასობით დოკუმენტის
# ხელახალ დამუშავებას ყოველ 5 წუთში, რაც 1C-ს მუდმივად დატვირთავდა
# (ზუსტად ის, რისი თავიდან აცილებაც გვინდოდა).
ONEC_SYNC_OVERLAP_HOURS = int(os.environ.get("ONEC_SYNC_OVERLAP_HOURS", "6"))

# პირველი გაშვების fallback, თუ sync_state ცარიელია (ჯერ არასდროს
# გაშვებულა) — ეს რჩება დღეებში, რადგან ეს არის ერთჯერადი, დიდი
# "cold start" ფანჯარა, არა ყოველ ციკლზე განმეორებადი overlap.
# ⚠️ ეს *არ* არის სრული backfill — ის ცალკე, თითო-პაციენტიანი
# მექანიზმია (app/onec_cache.py).
ONEC_SYNC_INITIAL_DAYS = int(os.environ.get("ONEC_SYNC_INITIAL_DAYS", "7"))


def get_last_synced(pg_con) -> datetime:
    cur = pg_con.cursor()
    cur.execute("SELECT last_synced_at FROM sync_state WHERE source = 'onec'")
    row = cur.fetchone()
    if row:
        dt = row[0]
        # sync_state.last_synced_at არის TIMESTAMPTZ, ანუ Postgres-იდან
        # timezone-aware datetime დაბრუნდება — მაგრამ datetime.utcnow()
        # (რასაც ამ ფაილში ყველგან ვიყენებთ) naive-ია. შედარება
        # (fetch_by_date_windows-ში) ჩავარდება, თუ ორივეს ერთ ტიპზე არ
        # მოვიყვანთ, ამიტომ ვშლით tzinfo-ს, UTC-ზე ნორმალიზების შემდეგ.
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return datetime.utcnow() - timedelta(days=ONEC_SYNC_INITIAL_DAYS)


def set_last_synced(pg_con, dt: datetime):
    cur = pg_con.cursor()
    cur.execute(
        """
        INSERT INTO sync_state (source, last_synced_at) VALUES ('onec', %s)
        ON CONFLICT (source) DO UPDATE SET last_synced_at = EXCLUDED.last_synced_at
        """,
        (dt,),
    )
    pg_con.commit()


def sync():
    pg_con = psycopg2.connect(PG_DSN)
    try:
        run_started_at = datetime.utcnow()
        since = get_last_synced(pg_con) - timedelta(hours=ONEC_SYNC_OVERLAP_HOURS)
        since_str = since.strftime("%Y-%m-%dT%H:%M:%S")

        # ⚠️ დადასტურდა (2026-08-02): ამ 1C OData კონფიგურაციაზე $skip
        # საერთოდ არ არის მხარდაჭერილი (501 Not Implemented, $orderby-იანაც
        # და $skip=0-ზეც კი). ამიტომ offset-based pagination-ის ნაცვლად
        # თარიღის window-ებით ვიტერირებთ — fetch_by_date_windows ამას
        # აკეთებს $skip-ის გამოყენების გარეშე.
        documents = list(fetch_by_date_windows(
            "Document_МедицинскийДокумент",
            since=since,
            until=run_started_at,
            window_hours=int(os.environ.get("ONEC_SYNC_WINDOW_HOURS", "24")),
            top=int(os.environ.get("ONEC_SYNC_PAGE_SIZE", "2000")),
        ))
        print(f"[{datetime.now()}] 1C-დან წამოღებულია {len(documents)} დოკუმენტი (Date >= {since_str})")

        seen = 0
        commit_every = int(os.environ.get("ONEC_SYNC_COMMIT_EVERY", "200"))
        for doc in documents:
            kartoteka_ref = doc.get("Пациент_Key")
            if not kartoteka_ref:
                continue
            upsert_document(pg_con, kartoteka_ref, doc)
            seen += 1

            # პერიოდული commit — რომ შეწყვეტამ/crash-მა შუაში არ დაკარგოს
            # უკვე დამუშავებული ყველა დოკუმენტი (5000+ დოკუმენტზე, თითოეულს
            # შესაძლოა დამატებითი 1C round-trip სჭირდებოდეს CDA/lookup-ებისთვის,
            # ამიტომ ერთი გაშვება საკმაოდ დიდხანს გრძელდება).
            if seen % commit_every == 0:
                pg_con.commit()
                print(f"[{datetime.now()}] პროგრესი: {seen}/{len(documents)} დამუშავებულია...")

        pg_con.commit()
        set_last_synced(pg_con, run_started_at)
        print(f"[{datetime.now()}] დასრულდა. დამუშავებულია {seen} დოკუმენტი.")
    finally:
        pg_con.close()


def loop():
    interval = int(os.environ.get("ONEC_SYNC_INTERVAL_SECONDS", "300"))  # ნაგულისხმევი 5 წუთი
    print(f"[{datetime.now()}] onec-sync-worker გაშვებულია loop რეჟიმში, ინტერვალი: {interval}წმ")
    while True:
        try:
            sync()
        except Exception as e:
            print(f"[{datetime.now()}] onec sync ჩავარდა: {e}")
        time.sleep(interval)


if __name__ == "__main__":
    if "--once" in sys.argv or os.environ.get("SYNC_RUN_ONCE") == "1":
        sync()
    else:
        loop()