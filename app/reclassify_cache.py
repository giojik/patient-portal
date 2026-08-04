"""
Cache-ში უკვე დამახსოვრებული დოკუმენტების კატეგორიის ხელახლა გამოთვლა,
1C-სთან ხელახალი დაკავშირების გარეშე.

საჭიროა მაშინ, როცა onec_client.py-ის კლასიფიკატორი (_classify_category)
შეიცვალა/გაუმჯობესდა, მაგრამ Postgres cache (onec_documents.items_enc)
ჯერ კიდევ ინახავს ძველი გაშვებისას გამოთვლილ category მნიშვნელობებს.

გამოსაყენებელი სიგნალები (რომლებიც უკვე ინახება items_enc-ში):
  - panel_name  — მომსახურების/შაბლონის სახელი (ან მათი fallback-ი)
  - test_name   — narrative დოკუმენტებისთვის ეს არის CDA-ს პირველი
                  სექციის სათაური (extra_text კლასიფიკატორისთვის)
  - is_narrative

კატეგორია დოკუმენტის დონეზეა გაზიარებული (ყველა item ერთი და იმავე
დოკუმენტიდან) — ამიტომ ხელახლა ვითვლით ერთხელ თითო დოკუმენტზე, პირველი
item-ის მონაცემებით, და ვუწერთ ყველა item-ს.

გაშვება (api კონტეინერის შიგნიდან):
    docker compose exec api python3 reclassify_cache.py
"""
import json
import os
import psycopg2

from app.crypto import encrypt_field, decrypt_field
from app.onec_client import _classify_category

PG_DSN = os.environ["PORTAL_DB_DSN"]


def main():
    con = psycopg2.connect(PG_DSN)
    try:
        read_cur = con.cursor()
        read_cur.execute("SELECT doc_ref, items_enc FROM onec_documents")
        rows = read_cur.fetchall()
        print(f"სულ {len(rows)} დოკუმენტი cache-ში")

        write_cur = con.cursor()
        updated = 0
        examples = []

        for doc_ref, items_enc in rows:
            items = json.loads(decrypt_field(items_enc))
            if not items:
                continue

            first = items[0]
            panel_name = first.get("panel_name")
            extra_text = first.get("test_name") if first.get("is_narrative") else None
            old_category = first.get("category")
            new_category = _classify_category(panel_name, None, extra_text)

            if new_category != old_category:
                for item in items:
                    item["category"] = new_category
                write_cur.execute(
                    "UPDATE onec_documents SET items_enc = %s WHERE doc_ref = %s",
                    (encrypt_field(json.dumps(items)), doc_ref),
                )
                updated += 1
                if len(examples) < 25:
                    examples.append((old_category, new_category, panel_name, extra_text))

        con.commit()
        print(f"განახლდა {updated} დოკუმენტი\n")
        print("მაგალითები (მაქს. 25):")
        for old, new, panel_name, extra_text in examples:
            print(f"  [{old} -> {new}]  panel_name={panel_name!r}  cda_title={extra_text!r}")
    finally:
        con.close()


if __name__ == "__main__":
    main()