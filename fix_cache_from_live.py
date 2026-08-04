"""
გამოსასწორებელი სკრიპტი — ხელახლა წამოიღებს ყველა უკვე დაქეშილი
პაციენტის ისტორიას ცოცხლად 1C-დან (არა cache-დან).

გაშვება: docker compose exec api python3 fix_cache_from_live.py
"""
import os
import time
import psycopg2

from app.onec_cache import backfill_single_patient

PG_DSN = os.environ["PORTAL_DB_DSN"]


def main():
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT kartoteka_ref FROM onec_patient_sync WHERE status = 'done'")
        refs = [row[0] for row in cur.fetchall()]
    finally:
        con.close()

    print(f"სულ {len(refs)} დაქეშილი პაციენტი — ვიწყებთ ხელახალ backfill-ს ცოცხლად 1C-დან...")

    ok, failed = 0, 0
    for i, ref in enumerate(refs, 1):
        try:
            backfill_single_patient(ref)
            ok += 1
        except Exception as e:
            failed += 1
            print(f"  [{i}/{len(refs)}] შეცდომა {ref}: {e}")
        if i % 10 == 0:
            print(f"  [{i}/{len(refs)}] დამუშავებულია...")
        time.sleep(0.1)

    print(f"\nდასრულდა: {ok} წარმატებული, {failed} ჩავარდნილი")


if __name__ == "__main__":
    main()
