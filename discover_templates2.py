"""
დიაგნოსტიკური სკრიპტი #2: კონკრეტული პაციენტის ყველა დოკუმენტი,
თითოეულის ШаблонМедицинскогоДокумента_Key-ით და შესაბამისი Description-ით.
გაშვება: docker compose exec api python3 discover_templates2.py <პირადი_ნომერი>
"""
import sys
from app.onec_client import _get, find_patient_by_personal_id

personal_id = sys.argv[1] if len(sys.argv) > 1 else "41001011487"
patient = find_patient_by_personal_id(personal_id)
if not patient:
    print(f"პაციენტი {personal_id} ვერ მოიძებნა")
    sys.exit(1)

kartoteka_ref = patient["ref_key"]
print(f"პაციენტი: {patient['full_name']}  ({kartoteka_ref})")
print("=" * 70)

templates_data = _get(
    "Catalog_ШаблоныМедицинскихДокументов",
    params={"$format": "json", "$select": "Ref_Key,Description"},
)
template_by_key = {t["Ref_Key"]: t.get("Description") for t in templates_data.get("value", [])}

data = _get(
    "Document_МедицинскийДокумент",
    params={
        "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
        "$format": "json",
        "$select": "Ref_Key,Date,ШаблонМедицинскогоДокумента_Key,Number",
    },
)
docs = data.get("value", [])
docs.sort(key=lambda d: d.get("Date", ""), reverse=True)

for d in docs:
    tkey = d.get("ШаблонМедицинскогоДокумента_Key")
    tname = template_by_key.get(tkey, f"?? ({tkey})")
    print(f"  {d.get('Date')}  |  {tname}")

print()
print(f"სულ: {len(docs)} დოკუმენტი")
