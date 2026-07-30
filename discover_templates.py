"""
დიაგნოსტიკური სკრიპტი: 1C-დან ამოიღებს ყველა სამედიცინო დოკუმენტის
შაბლონს (Catalog_ШаблоныМедицинскихДокументов), რომ ვნახოთ ზუსტად
რომელი ველი/მნიშვნელობა განასხვავებს:
  ფორმა 100 / ლაბ. ანალიზები / დანიშნულებები / რადიოლოგია /
  რეკომენდაციები გაწერისას / გასინჯვის ფურცელი / ეპიკრიზი /
  ოპერაციის ოქმი / წინასაოპერაციო ეპიკრიზი / გაუტკივარების ოქმი /
  მიმღები ექიმის ჩანაწერი

გაშვება (api კონტეინერის შიგნიდან, სადაც ONEC_* env ცვლადები უკვე არის):
    docker compose exec api python3 discover_templates.py
"""
from app.onec_client import _get

print("=" * 70)
print("Catalog_ШаблоныМедицинскихДокументов — ყველა შაბლონი")
print("=" * 70)

data = _get(
    "Catalog_ШаблоныМедицинскихДокументов",
    params={"$format": "json", "$select": "Ref_Key,Description,бит_Форма100"},
)
templates = data.get("value", [])
for t in templates:
    print(f"  [{t.get('бит_Форма100')}] {t.get('Description')}  ({t.get('Ref_Key')})")

print()
print(f"სულ: {len(templates)} შაბლონი")

print()
print("=" * 70)
print("Document_МедицинскийДокумент — ბოლო 30 ჩანაწერი (ნებისმიერი პაციენტი)")
print("=" * 70)

docs = _get(
    "Document_МедицинскийДокумент",
    params={
        "$format": "json",
        "$select": "Ref_Key,Date,ШаблонМедицинскогоДокумента_Key,Number",
        "$top": "30",
        "$orderby": "Date desc",
    },
)
rows = docs.get("value", [])
template_by_key = {t["Ref_Key"]: t.get("Description") for t in templates}
for d in rows:
    tkey = d.get("ШаблонМедицинскогоДокумента_Key")
    tname = template_by_key.get(tkey, f"?? ({tkey})")
    print(f"  {d.get('Date')}  {tname}")