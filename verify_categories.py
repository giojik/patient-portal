"""
მხოლოდ სანახავი სკრიპტი — თვლის, რამდენი დოკუმენტია თითოეულ
კატეგორიაში cache-ში.

გაშვება: docker compose exec api python3 verify_categories.py
"""
import json
import os
from collections import Counter
import psycopg2

from app.crypto import decrypt_field

PG_DSN = os.environ["PORTAL_DB_DSN"]


def main():
    con = psycopg2.connect(PG_DSN)
    try:
        cur = con.cursor()
        cur.execute("SELECT items_enc FROM onec_documents")
        rows = cur.fetchall()
    finally:
        con.close()

    counts = Counter()
    for (items_enc,) in rows:
        items = json.loads(decrypt_field(items_enc))
        if items:
            counts[items[0].get("category")] += 1

    print(f"სულ {len(rows)} დოკუმენტი cache-ში\n")
    for category, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {category:<25} {count}")


if __name__ == "__main__":
    main()
