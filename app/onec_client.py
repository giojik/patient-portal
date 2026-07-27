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
from datetime import datetime
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


# ============ შედეგების წამოღება (Document_МедицинскийДокумент) ============

_indicator_name_cache = {}
_unit_name_cache = {}
_nomenclature_name_cache = {}

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
    "მრტ", "ექოსკოპ", "ანგიოგრაფ", "რენტგენოგრაფ",
    "xray", "x-ray", "ct ", "mri", "ultrasound", "ultra sound",
]


def _classify_category(panel_name: str) -> str:
    """
    Catalog_ШаблоныМедицинскихДокументов-ის საქაღალდეები ლაბ./რადიოლ.-ს
    სუფთად არ ყოფენ (ერთი საერთო "ინსტრუმენტული კვლევების" საქაღალდეა),
    ამიტომ ვიყენებთ keyword-based კლასიფიკაციას პანელის სახელზე.
    """
    name = (panel_name or "").lower()
    for kw in RADIOLOGY_KEYWORDS:
        if kw.lower() in name:
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

    category = _classify_category(panel_name)

    health_indicators = doc.get("ПоказателиЗдоровья", [])

    items = []

    if not health_indicators:
        # ЛПоказателиЗдоровья ცარიელია — ეს ჩვეულებრივ ნიშნავს, რომ ეს
        # რადიოლოგიური/ინსტრუმენტული კვლევაა თხრობითი დასკვნით (CDA)
        cda_key = doc.get("CDAДокумент_Key")
        sections = get_cda_narrative(cda_key)
        for section in sections:
            title = section["title"] or "დასკვნა"
            items.append({
                "panel_group_id": doc_ref,
                "panel_name": panel_name or title,
                "category": category,
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

    is_narrative = items[0]["category"] == "radiology"

    return {
        "panel_name": items[0]["panel_name"],
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