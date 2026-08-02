"""
1C:Enterprise OData client — პაციენტის მოძებნა პირადი ნომრით და
სამედიცინო შედეგების წამოღება.

საბოლოო, დადასტურებული architecture:

    პირადი ნომერი
        ↓ (InformationRegister_ПаспортныеДанныеПациентов_RecordType.ДокументНомер)
    Catalog_Картотека.Ref_Key  ("Пациент_Key")
        ↓ (Document_МедицинскийДокумент.Пациент_Key)
    სამედიცინო შედეგები

ეს არის ოფიციალური, ცალსახა ბმა — არა name-matching ან heuristic.
Catalog_ФизическиеЛица საერთოდ არ გამოიყენება ამ flow-ში (დადასტურდა,
რომ Картотека-სა და ФизическиеЛица-ს შორის schema-level ბმა არ
არსებობს ამ კონფიგურაციაში).

⚠️ 1C-ის OData-ს არ მოსწონს space-ის `+`-ით encoding (რასაც requests-ის
`params=` dict ავტომატურად აკეთებს) — მხოლოდ `%20` მუშაობს. ამიტომ query
string-ს ხელით ვაწყობთ `urllib.parse.quote`-ით (არა `quote_plus`).
"""
import os
import re
import base64
import html
import xml.etree.ElementTree as ET
import requests
from datetime import datetime, timedelta
from urllib.parse import quote

ONEC_BASE = os.environ["ONEC_BASE_URL"].rstrip("/")
ONEC_USER = os.environ["ONEC_USER"]
ONEC_PASSWORD = os.environ["ONEC_PASSWORD"]

_session = requests.Session()
_session.auth = (ONEC_USER, ONEC_PASSWORD)


def _get(path, params=None):
    url = f"{ONEC_BASE}/{path}"
    if params:
        query = "&".join(f"{quote(k, safe='$')}={quote(v, safe='')}" for k, v in params.items())
        url = f"{url}?{query}"
    resp = _session.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_all_paginated(entity: str, filter_str: str, page_size: int = 2000):
    """
    ⚠️ დადასტურდა (2026-08-02): ეს 1C OData კონფიგურაცია საერთოდ არ უჭერს
    მხარს `$skip`-ს ამ entity-ზე — 501 Not Implemented, მიუხედავად
    `$orderby`-ის დამატებისა, და თუნდაც `$skip=0`-ზეც კი (მხოლოდ
    პარამეტრის არსებობაც კმარა შეცდომისთვის).

    ამიტომ ეს ფუნქცია აღარ cycle-ავს `$skip`-ით — მხოლოდ ერთ page-ს
    აბრუნებს (`$top`-ით შემოსაზღვრული). თუ result set `page_size`-ს
    აღემატება, დარჩენილი ჩანაწერები **დუმილში დაიკარგება** — ამიტომ ეს
    ფუნქცია გამოსადეგია მხოლოდ იქ, სადაც result set საიმედოდ მცირეა
    (მაგ. ერთი პაციენტის ისტორია `Пациент_Key`-ით გაფილტრული).

    გლობალური, თარიღით შემოსაზღვრული query-ებისთვის (periodic worker)
    გამოიყენე `fetch_by_date_windows` ამის მაგივრად.
    """
    data = _get(
        entity,
        params={"$filter": filter_str, "$format": "json", "$top": str(page_size)},
    )
    rows = data.get("value", [])
    if len(rows) == page_size:
        print(
            f"[fetch_all_paginated] გაფრთხილება: '{entity}' დააბრუნა ზუსტად "
            f"page_size ({page_size}) ჩანაწერი filter-ით '{filter_str}' — "
            f"შესაძლოა მეტიც არსებობდეს, მაგრამ $skip არ არის მხარდაჭერილი "
            f"ამ 1C კონფიგურაციაზე, ამიტომ დარჩენილი დაიკარგა."
        )
    yield from rows


def fetch_by_date_windows(entity: str, since, until, window_hours: int = 24,
                           top: int = 2000, extra_filter: str = None):
    """
    თარიღის დიაპაზონებით (`Date ge X and Date lt Y`) დაყოფილი fetch —
    `$skip`-ის გამოყენების გარეშე, რადგან ეს 1C კონფიგურაცია მას არ
    უჭერს მხარს. თითოეული "გვერდი" თარიღის window-ითაა ბუნებრივად
    შემოსაზღვრული, არა offset-ით.

    since/until: datetime ობიექტები (UTC-ს ვვარაუდობთ, ისევე როგორც
    დანარჩენი კოდი).
    window_hours: რაც უფრო პატარაა, მით უფრო ნაკლები ჩანაწერი ხვდება
    ერთ page-ში — თუ ხშირად ხედავ truncation-ის გაფრთხილებას, შეამცირე.
    """
    from datetime import timedelta

    window = timedelta(hours=window_hours)
    cur = since
    while cur < until:
        window_end = min(cur + window, until)
        date_filter = (
            f"Date ge datetime'{cur.strftime('%Y-%m-%dT%H:%M:%S')}' "
            f"and Date lt datetime'{window_end.strftime('%Y-%m-%dT%H:%M:%S')}'"
        )
        filt = f"({date_filter}) and ({extra_filter})" if extra_filter else date_filter

        data = _get(entity, params={"$filter": filt, "$format": "json", "$top": str(top)})
        rows = data.get("value", [])
        if len(rows) == top:
            print(
                f"[fetch_by_date_windows] გაფრთხილება: ფანჯარა {cur}–{window_end} "
                f"დააბრუნა ზუსტად top ({top}) ჩანაწერი — შესაძლოა მეტიც არსებობდეს. "
                f"შეამცირე window_hours."
            )
        yield from rows
        cur = window_end


def find_patient_by_personal_id(personal_id: str):
    """
    აბრუნებს {"ref_key": ..., "full_name": ..., "phone": ...} თუ ნაპოვნია,
    სხვანაირად None.

    წყარო: InformationRegister_ПаспортныеДанныеПациентов_RecordType —
    ოფიციალური რეესტრი, სადაც "ДокументНомер" არის პირადობის მოწმობის
    ნომერი და "Пациент_Key" პირდაპირ მიუთითებს Catalog_Картотека-ზე
    (იმავე ცნობარზე, რომელსაც Document_МедицинскийДокумент იყენებს).
    """
    personal_id = personal_id.strip()

    data = _get(
        "InformationRegister_ПаспортныеДанныеПациентов_RecordType",
        params={
            "$filter": f"ДокументНомер eq '{personal_id}'",
            "$format": "json",
        },
    )
    rows = data.get("value", [])
    if not rows:
        return None

    kartoteka_ref = rows[0]["Пациент_Key"]

    card = _get(f"Catalog_Картотека(guid'{kartoteka_ref}')", params={"$format": "json"})
    full_name = (card.get("Description") or "").strip()

    phone = None
    try:
        contact_data = _get(
            "InformationRegister_КонтактнаяИнформацияПациента_RecordType",
            params={
                "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
                "$format": "json",
            },
        )
        for row in contact_data.get("value", []):
            if row.get("Тип") == "Телефон":
                phone = row.get("Представление")
                break
    except requests.exceptions.RequestException:
        pass

    return {"ref_key": kartoteka_ref, "full_name": full_name, "phone": phone}


_personal_id_cache = {}


def get_personal_id_by_kartoteka(kartoteka_ref: str) -> str:
    """
    საპირისპირო ძებნა Catalog_Картотека Ref_Key-დან პირად ნომერზე —
    იგივე InformationRegister_ПаспортныеДанныеПациентов_RecordType რეესტრი,
    რასაც find_patient_by_personal_id იყენებს, უბრალოდ საწინააღმდეგო
    მიმართულებით (Пациент_Key-ით ვფილტრავთ, არა ДокументНомер-ით).
    """
    if kartoteka_ref in _personal_id_cache:
        return _personal_id_cache[kartoteka_ref]

    personal_id = ""
    try:
        data = _get(
            "InformationRegister_ПаспортныеДанныеПациентов_RecordType",
            params={
                "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
                "$format": "json",
            },
        )
        rows = data.get("value", [])
        if rows:
            personal_id = rows[0].get("ДокументНомер", "") or ""
    except requests.exceptions.RequestException:
        pass

    _personal_id_cache[kartoteka_ref] = personal_id
    return personal_id


# ============ შედეგების წამოღება (Document_МедицинскийДокумент) ============

_indicator_name_cache = {}
_unit_name_cache = {}
_nomenclature_name_cache = {}
_template_name_cache = {}
_address_type_cache = {}

_EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def _cached_lookup(cache: dict, entity: str, key: str) -> str:
    if not key or key == _EMPTY_GUID:
        return ""
    if key not in cache:
        try:
            data = _get(f"{entity}(guid'{key}')", params={"$format": "json"})
            cache[key] = data.get("Description", "")
        except requests.exceptions.RequestException:
            cache[key] = ""
    return cache[key]


def _get_indicator_name(key: str) -> str:
    return _cached_lookup(_indicator_name_cache, "ChartOfCharacteristicTypes_ВидыПоказателейЗдоровья", key)


def _get_unit_name(key: str) -> str:
    return _cached_lookup(_unit_name_cache, "Catalog_ЕдиницыИзмеренияПоказателей", key)


def _get_nomenclature_name(key: str) -> str:
    return _cached_lookup(_nomenclature_name_cache, "Catalog_Номенклатура", key)


def _get_template_name(key: str) -> str:
    return _cached_lookup(_template_name_cache, "Catalog_ШаблоныМедицинскихДокументов", key)


def _get_address_type_name(key: str) -> str:
    return _cached_lookup(_address_type_cache, "Catalog_ВидыАдресовПациентов", key)


def _to_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _compute_status(value, low, high, is_out_of_norm) -> str:
    if is_out_of_norm is True:
        return "abnormal"

    v, lo, hi = _to_float(value), _to_float(low), _to_float(high)
    if v is None or lo is None or hi is None or hi <= lo:
        return "unknown"

    if v < lo or v > hi:
        return "abnormal"

    margin = (hi - lo) * 0.1
    if v <= lo + margin or v >= hi - margin:
        return "borderline"

    return "normal"


RADIOLOGY_KEYWORDS = [
    "ულტრაბგერითი", "ტომოგრაფია", "რენტგენ", "მამოგრაფ",
    "მრტ", "ექოსკოპ", "ანგიოგრაფ", "რენტგენოგრაფ", "კარდიოგრაფ",
    "ენდოსკოპ", "სკოპია", "დუპლექს",
    "xray", "x-ray", "ct ", "mri", "ultrasound", "ultra sound", "ecg",
]

# კონკრეტული, ცალსახა შაბლონის სახელები (substring-ით), დალაგებული
# specificity-ის მიხედვით — ზოგადი keyword-ები (ეპიკრიზი, ოპერაცია)
# რომ არასწორად არ დაემთხვეს სპეციფიკურ ტიპებს, კონკრეტული ჯერ მოწმდება.
#
# კატეგორიები, რომლებიც პაციენტს უნდა უჩვენდეს ნაგულისხმევად:
#   forma100, lab, radiology, prescription, discharge_recommendation, consultation
# კატეგორიები, რომლებიც პაციენტს არ უნდა უჩვენდეს ნაგულისხმევად
# (ადმინს შეუძლია ეს ჩართოს feature_flags-იდან):
#   exam_diary, discharge_epicrisis, preop_epicrisis, anesthesia_protocol,
#   operation_protocol, admitting_doctor_note, surgical_team
_TEMPLATE_KEYWORD_RULES = [
    ("100/ა", "forma100"),
    ("100/a", "forma100"),
    ("პაციენტის გასინჯვის ფურცელი", "exam_diary"),
    ("გასინჯვის ფურცელი", "exam_diary"),
    ("გაწერის ეპიკრიზი", "discharge_epicrisis"),
    ("წინასაოპერაციო ეპიკრიზი", "preop_epicrisis"),
    ("ეპიკრიზი", "discharge_epicrisis"),  # უცნობი ეპიკრიზის ვარიანტები — უსაფრთხოებისთვის დამალული
    ("გაუტკივარების ოქმი", "anesthesia_protocol"),
    ("საოპერაციო ბრიგადა", "surgical_team"),
    ("ოპერაციის ოქმი", "operation_protocol"),
    ("ჩარევის ოქმი", "operation_protocol"),
    ("მიმღები", "admitting_doctor_note"),  # "მიმღები (მორიგე) მკურნალი ექიმის ჩანაწერი..."
    ("რეკომენდაციები გაწერისას", "discharge_recommendation"),
    ("მომსახურებების დანიშვნა", "prescription"),
    ("დანიშნულების ფურცელი", "prescription"),
    ("კონსულტაცია", "consultation"),
]


def _classify_category(template_name: str, panel_name: str = None) -> str:
    """
    კატეგორიის კლასიფიკაცია, უპირატესად დოკუმენტის შაბლონის სახელით
    (Catalog_ШаблоныМедицинскихДокументов.Description) — ეს ცალსახად
    განასხვავებს დოკუმენტის ტიპს (ეპიკრიზი, ოქმი, ფორმა 100 და ა.შ.),
    ნაცვლად ადრინდელი მიდგომისა, სადაც მხოლოდ პანელის/მომსახურების
    სახელი გამოიყენებოდა (რაც ვერ ასხვავებდა ამ ტიპებს ერთმანეთისგან).

    panel_name — fallback, თუ შაბლონის სახელი ცარიელია/უცნობია.
    """
    name = template_name or panel_name or ""
    lname = name.lower()

    for keyword, category in _TEMPLATE_KEYWORD_RULES:
        if keyword in name:
            return category

    for kw in RADIOLOGY_KEYWORDS:
        if kw in lname:
            return "radiology"

    return "lab"


CDA_NS = {"cda": "urn:hl7-org:v3"}


def _strip_ns(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _extract_text_blocks(text_el) -> list:
    """
    CDA-ს <text> ელემენტს გარდაქმნის სტრუქტურირებულ "ბლოკების" სიად:
      {"type": "row", "cells": [...]}  — ცხრილის row (ლეიბლი|მნიშვნელობა წყვილები)
      {"type": "text", "content": "..."} — თავისუფალი აბზაცი (დასკვნის ტექსტი)

    ეს საშუალებას იძლევა PDF-ში ცხრილის row-ები ნამდვილ ცხრილად ავაწყოთ
    (ლეიბლი მუქად, მნიშვნელობა გვერდით), არა უბრალო ტექსტურ ხაზად.
    """
    blocks = []

    if text_el.text and text_el.text.strip():
        blocks.append({"type": "text", "content": text_el.text.strip()})

    for child in text_el:
        tag = _strip_ns(child.tag)
        if tag == "table":
            for node in child.iter():
                if _strip_ns(node.tag) != "tr":
                    continue
                cells = [
                    "".join(td.itertext()).strip()
                    for td in node
                    if _strip_ns(td.tag) == "td"
                ]
                cells = [c for c in cells if c]
                if cells:
                    blocks.append({"type": "row", "cells": cells})
        else:
            txt = "".join(child.itertext()).strip()
            if txt:
                blocks.append({"type": "text", "content": txt})

        if child.tail and child.tail.strip():
            blocks.append({"type": "text", "content": child.tail.strip()})

    return blocks


def _blocks_to_text(blocks: list) -> str:
    """ბლოკებს გარდაქმნის flat ტექსტად (frontend-ის ჩვენებისთვის)."""
    lines = []
    for b in blocks:
        if b["type"] == "row":
            lines.append(" ".join(b["cells"]))
        else:
            lines.append(b["content"])
    return "\n".join(lines)


def _extract_cda_sections(cda_xml: str) -> list:
    """
    HL7 CDA XML-იდან ამოიღებს {title, text, blocks} სექციების სიას.
    """
    try:
        root = ET.fromstring(cda_xml)
    except ET.ParseError:
        return []

    sections = []
    for section in root.iter("{urn:hl7-org:v3}section"):
        title_el = section.find("cda:title", CDA_NS)
        text_el = section.find("cda:text", CDA_NS)
        title = (title_el.text or "").strip() if title_el is not None and title_el.text else ""
        blocks = _extract_text_blocks(text_el) if text_el is not None else []
        text_content = re.sub(r"\n\s*\n+", "\n", _blocks_to_text(blocks))
        if title or blocks:
            sections.append({"title": title, "text": text_content, "blocks": blocks})
    return sections


def get_cda_narrative(cda_key: str) -> list:
    """
    ჩამოტვირთავს და ამოშლის CDA დოკუმენტს (Catalog_CDAДокументы),
    რომელიც ჩვეულებრივ შეიცავს რადიოლოგიური/ინსტრუმენტული კვლევის
    თხრობით დასკვნას (ПоказателиЗдоровья-ს ნაცვლად, რომელიც ასეთ
    დოკუმენტებზე ცარიელია).
    """
    if not cda_key or cda_key == _EMPTY_GUID:
        return []

    try:
        doc = _get(f"Catalog_CDAДокументы(guid'{cda_key}')", params={"$format": "json"})
    except requests.exceptions.RequestException:
        return []

    b64 = doc.get("ТелоДокумента_Base64Data", "")
    if not b64:
        return []

    try:
        outer_text = base64.b64decode(b64).decode("utf-8")
        match = re.search(r"<String[^>]*>(.*)</String>", outer_text, re.DOTALL)
        if not match:
            return []
        cda_xml = html.unescape(match.group(1))
        return _extract_cda_sections(cda_xml)
    except (ValueError, UnicodeDecodeError):
        return []


def _document_to_results(doc: dict) -> list:
    """Document_МедицинскийДокумент-ის ერთი ჩანაწერი → ბრტყელი შედეგების სია."""
    doc_ref = doc.get("Ref_Key")
    sample_date = doc.get("Date")

    services = doc.get("ВыполненныеУслуги", [])
    panel_name = None
    if services:
        panel_name = _get_nomenclature_name(services[0].get("Номенклатура_Key"))

    template_name = _get_template_name(doc.get("ШаблонМедицинскогоДокумента_Key"))
    category = _classify_category(template_name, panel_name)

    health_indicators = doc.get("ПоказателиЗдоровья", [])
    is_narrative = not health_indicators

    items = []

    if is_narrative:
        # ЛПоказателиЗдоровья ცარიელია — ეს ნიშნავს, რომ ეს თხრობითი
        # ტიპის დოკუმენტია (CDA), მიუხედავად კონკრეტული კატეგორიისა
        # (რადიოლოგია, ეპიკრიზი, ოქმი, ფორმა 100, კონსულტაცია და ა.შ.)
        cda_key = doc.get("CDAДокумент_Key")
        sections = get_cda_narrative(cda_key)
        for section in sections:
            title = section["title"] or "დასკვნა"
            items.append({
                "panel_group_id": doc_ref,
                "panel_name": panel_name or template_name or title,
                "category": category,
                "is_narrative": True,
                "test_name": title,
                "result_value": section["text"],
                "blocks": section.get("blocks", []),
                "unit": "",
                "norm_low": None,
                "norm_high": None,
                "status": "unknown",
                "sample_date": sample_date,
            })
        return items

    for ind in health_indicators:
        value = ind.get("Значение")
        low = ind.get("ЗначениеМинимум") or None
        high = ind.get("ЗначениеМаксимум") or None
        is_out = ind.get("бит_ЕстьОтклонениеОтНормы")
        test_name = _get_indicator_name(ind.get("Показатель_Key")) or "კვლევა"
        unit = _get_unit_name(ind.get("ЕдиницаИзмерения_Key"))

        items.append({
            "panel_group_id": doc_ref,
            "panel_name": panel_name or test_name,
            "category": category,
            "is_narrative": False,
            "test_name": test_name,
            "result_value": value,
            "unit": unit,
            "norm_low": low,
            "norm_high": high,
            "status": _compute_status(value, low, high, is_out),
            "sample_date": sample_date,
        })
    return items


def get_patient_results(kartoteka_ref: str) -> list:
    """
    აბრუნებს ბრტყელ სიას, იმავე ფორმატით რასაც Terra-ს
    get_results_for_patient() აბრუნებდა — რომ frontend-მა უცვლელად
    შეძლოს ჩვენება.

    kartoteka_ref: ავტორიზაციისას მიღებული Catalog_Картотека.Ref_Key
    (პირდაპირ, resolve-ის საჭიროების გარეშე).
    """
    data = _get(
        "Document_МедицинскийДокумент",
        params={
            "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
            "$format": "json",
        },
    )
    documents = data.get("value", [])
    documents.sort(key=lambda d: d.get("Date", ""), reverse=True)

    results = []
    for doc in documents:
        results.extend(_document_to_results(doc))
    return results


def get_pending_tests(kartoteka_ref: str) -> list:
    """
    "დანიშნული, ჯერ არშესრულებული" კვლევები/ანალიზები.

    ⚠️ პირველი ვერსია ეყრდნობოდა Document_МедицинскийДокумент.
    НазначенныеУслуги-ს УникальныйИдентификаторУслуги-ით დამთხვევას —
    რეალურ მონაცემზე ტესტმა დაადასტურა, რომ ეს ველი პრაქტიკულად
    ცარიელია უმეტეს დოკუმენტში (50-დან მხოლოდ 1-ს ჰქონდა), ამიტომ
    უკვე დასრულებული ტესტებიც მცდარად ჩნდებოდა pending-ად.

    გასწორებული ლოგიკა: ვამოწმებთ ტესტის ტიპის (Номенклатура_Key)
    დამთხვევას — ЗАКАЗ-ის (Document_ЗаказПациента.МедицинскиеУслуги)
    ერთეული ითვლება შესრულებულად, თუ არსებობს დასრულებული დოკუმენტი
    (Document_МедицинскийДокумент.ВыполненныеУслуги) იმავე Номенклатура_Key-ით,
    შეკვეთის თარიღის შემდეგ დათარიღებული.

    მხოლოდ ბოლო 30 დღეში დანიშნული კვლევები ბრუნდება — ძველი,
    სავარაუდოდ მიტოვებული შეკვეთები აღარ ჩანს.

    ⚠️ ცნობილი ლიმიტაცია (2026-07-30): ჰისტომორფოლოგიის/პათოლოგიის
    ტიპის კვლევები (Номенклатура "ჰისტომორფოლოგიური გამოკვლევა" და
    მისთანები) ამ ფუნქციაში ყოველთვის pending-ად გამოჩნდება, თუნდაც
    რეალურად შესრულებული იყოს — რადგან ეს შედეგები საერთოდ არ ჩანს
    get_patient_results()/Document_МедицинскийДокумент-ში (0 დამთხვევა
    448 ჩანაწერიდან რეალურ ტესტზე). სავარაუდოდ პათოლოგია ცალკე
    დოკუმენტის ტიპში ინახება 1C-ში, რომელიც ჯერ არ არის აღმოჩენილი —
    ეს ცალკე, უფრო დიდი discovery-ის თემაა (მთელი შედეგების
    pipeline-ის ხარვეზი, არა მხოლოდ ამ ფუნქციისა), განზრახ
    გადადებულია მომავალი სესიისთვის.
    """
    ordered_data = _get(
        "Document_ЗаказПациента",
        params={
            "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
            "$format": "json",
        },
    )

    completed_data = _get(
        "Document_МедицинскийДокумент",
        params={
            "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
            "$format": "json",
        },
    )
    completed_dates_by_nomenclature = {}
    for doc in completed_data.get("value", []):
        doc_date = doc.get("Date")
        for svc in doc.get("ВыполненныеУслуги", []):
            key = svc.get("Номенклатура_Key")
            if key:
                completed_dates_by_nomenclature.setdefault(key, []).append(doc_date)

    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()

    pending = []
    for order in ordered_data.get("value", []):
        order_date = order.get("Date")
        if not order_date or order_date < cutoff:
            continue
        for svc in order.get("МедицинскиеУслуги", []):
            nomen_key = svc.get("Номенклатура_Key")
            completed_dates = completed_dates_by_nomenclature.get(nomen_key, [])
            fulfilled = any(d and order_date and d >= order_date for d in completed_dates)
            if fulfilled:
                continue
            pending.append({
                "test_name": _get_nomenclature_name(nomen_key) or "კვლევა",
                "planned_time": svc.get("ЗапланированноеВремя"),
                "order_date": order_date,
            })

    pending.sort(key=lambda p: p.get("order_date") or "", reverse=True)
    return pending


def get_onec_profile(kartoteka_ref: str):
    """
    Read-only პროფილის მონაცემები 1C წყაროსთვის: სახელი, პირადი ნომერი,
    ტელეფონი, ელფოსტა, მისამართი.

    მისამართი: InformationRegister_АдресПациента_RecordType (დადასტურებული
    ველები: ВидАдреса_Key, Представление, Active). ერთ პაციენტს შეიძლება
    ჰქონდეს რამდენიმე ტიპის მისამართი (ფაქტობრივი/იურიდიული/დროებითი
    რეგისტრაცია) — ვირჩევთ ფაქტობრივს, თუ არა — იურიდიულს, თუ არც
    ერთი — პირველ არააქტიურ-არშემცველს.

    InformationRegister_КонтактнаяИнформацияПациента_RecordType-იც
    პერიოდული რეესტრია — ერთი ტიპის რამდენიმე ისტორიული ჩანაწერი
    შეიძლება არსებობდეს, ამიტომ თითო ტიპზე ბოლო მნიშვნელობას ვინახავთ.
    """
    card = _get(f"Catalog_Картотека(guid'{kartoteka_ref}')", params={"$format": "json"})

    phone = None
    email = None
    try:
        contact_data = _get(
            "InformationRegister_КонтактнаяИнформацияПациента_RecordType",
            params={
                "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
                "$format": "json",
            },
        )
        for row in contact_data.get("value", []):
            raw_type = (row.get("Тип") or "").strip().lower()
            value = row.get("Представление")
            if not value:
                continue
            if "телефон" in raw_type:
                phone = value
            elif "почт" in raw_type or "e-mail" in raw_type or "email" in raw_type:
                email = value
    except requests.exceptions.RequestException:
        pass

    address = None
    try:
        address_data = _get(
            "InformationRegister_АдресПациента_RecordType",
            params={
                "$filter": f"Пациент_Key eq guid'{kartoteka_ref}'",
                "$format": "json",
            },
        )
        address_by_type = {}
        for row in address_data.get("value", []):
            if row.get("Active") is False:
                continue
            value = row.get("Представление")
            if not value:
                continue
            type_name = _get_address_type_name(row.get("ВидАдреса_Key"))
            address_by_type[type_name] = value
        address = (
            address_by_type.get("ფაქტობრივი მისამართი")
            or address_by_type.get("იურიდიული მისამართი")
            or next(iter(address_by_type.values()), None)
        )
    except requests.exceptions.RequestException:
        pass

    return {
        "full_name": (card.get("Description") or "").strip(),
        "personal_id": get_personal_id_by_kartoteka(kartoteka_ref) or None,
        "phone": phone,
        "email": email,
        "address": address,
        "source": "onec",
    }


def get_patient_name(kartoteka_ref: str) -> str:
    card = _get(f"Catalog_Картотека(guid'{kartoteka_ref}')", params={"$format": "json"})
    return card.get("Description", "პაციენტი")


def get_panel_by_id(document_ref: str, expected_kartoteka_ref: str):
    """
    ერთი დოკუმენტის (პანელის) დეტალები PDF report-ისთვის.
    ამოწმებს, რომ დოკუმენტი ეკუთვნის expected_kartoteka_ref-ს — რომ
    ვერავინ ვერ ჩამოტვირთოს სხვისი პაციენტის პანელი.
    """
    doc = _get(f"Document_МедицинскийДокумент(guid'{document_ref}')", params={"$format": "json"})
    if doc.get("Пациент_Key") != expected_kartoteka_ref:
        return None

    items = _document_to_results(doc)
    if not items:
        return None

    sample_date = items[0]["sample_date"]
    try:
        sample_date = datetime.fromisoformat(sample_date)
    except (ValueError, TypeError):
        pass

    is_narrative = items[0]["is_narrative"]

    return {
        "panel_name": items[0]["panel_name"],
        "category": items[0]["category"],
        "sample_date": sample_date,
        "is_narrative": is_narrative,
        "document_number": doc.get("Number", ""),
        "items": [
            {
                "test_name": i["test_name"],
                "result_value": i["result_value"],
                "blocks": i.get("blocks", []),
                "unit": i["unit"],
                "norm_low": i["norm_low"],
                "norm_high": i["norm_high"],
                "status": i["status"],
            }
            for i in items
        ],
    }