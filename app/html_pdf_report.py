"""
HTML-დაფუძნებული PDF გენერაცია (wkhtmltopdf), რომელიც სტილურად ჰგავს
კლინიკის ორიგინალურ 1C ბეჭდვის ფორმას (ШМД.html-დან ამოღებული სტილი).

reportlab-ისგან განსხვავებით, აქ HTML+CSS-ს ვწერთ პირდაპირ და
wkhtmltopdf-ს ვაქცევთ რენდერერად — ეს იძლევა ბევრად უფრო ზუსტ
ვიზუალურ თანხვედრას ორიგინალურ ფორმასთან (ცხრილის სტრუქტურა,
ფერები, პოზიციები), ვიდრे reportlab-ის ხელით აწყობილი Flowable-ები.

⚠️ შრიფტი: ორიგინალი იყენებს Sylfaen-ს (Microsoft-ის proprietary
შრიფტი) — ჩვენ ვერ ჩავრთავთ მას ლიცენზირების გაურკვევლობის გამო,
ამიტომ ვიყენებთ DejaVu Sans-ს (ღია ლიცენზია). ვიზუალურად მსგავსია,
მაგრამ ასოთბეჭდვა იდენტური არ იქნება.
"""
import os
import base64
from io import BytesIO
from datetime import datetime

import barcode
from barcode.writer import ImageWriter
from weasyprint import HTML

LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "innova_logo.png")
BRAND_GREEN = "#76c003"


def _logo_base64() -> str:
    if not os.path.exists(LOGO_PATH):
        return ""
    with open(LOGO_PATH, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _barcode_base64(document_number: str) -> str:
    if not document_number:
        return ""
    try:
        buffer = BytesIO()
        code = barcode.get("code128", str(document_number), writer=ImageWriter())
        code.write(buffer, options={"module_height": 8.0, "font_size": 8, "text_distance": 3, "quiet_zone": 2})
        return base64.b64encode(buffer.getvalue()).decode()
    except Exception:
        return ""


def _escape_html(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _html_to_pdf_bytes(html: str) -> bytes:
    return HTML(string=html).write_pdf()


PAGE_CSS = f"""
@page {{ margin: 15mm; }}
body {{
    font-family: 'DejaVu Sans', sans-serif;
    font-size: 10pt;
    color: #1f2d2b;
    margin: 0;
}}
.header {{
    display: table;
    width: 100%;
    margin-bottom: 8mm;
}}
.header-left {{ display: table-cell; vertical-align: top; width: 60%; }}
.header-right {{ display: table-cell; vertical-align: top; width: 40%; text-align: right; }}
.logo {{ height: 20mm; }}
.barcode {{ height: 12mm; }}
.history-number {{ font-size: 8pt; color: #6b7a78; margin-top: 2mm; }}
.doc-meta {{ font-size: 9pt; color: #9aa8a5; margin-bottom: 6mm; }}
.body-text {{
    font-size: 10pt;
    line-height: 1.55;
    white-space: pre-line;
    margin-bottom: 3mm;
}}
.exam-title {{
    text-align: center;
    font-weight: bold;
    font-size: 15pt;
    margin-bottom: 6mm;
    color: #1f2d2b;
}}
.info-table {{ width: 100%; border-collapse: collapse; margin-bottom: 2mm; }}
.info-table td {{ padding: 1mm 2mm 1mm 0; font-size: 10pt; vertical-align: top; }}
.info-label {{ font-weight: bold; white-space: nowrap; }}
.label {{ font-weight: bold; }}
.footer {{
    margin-top: 10mm;
    font-size: 7.5pt;
    color: #9aa8a5;
}}
"""


def _render_blocks(blocks: list) -> str:
    """
    სტრუქტურირებულ ბლოკებს (row/text) გარდაქმნის HTML-ად — row-ები
    ნამდვილ ცხრილის row-ებად (ლეიბლი მუქად), თავისუფალი ტექსტი კი
    ცალკე პარაგრაფებად.
    """
    parts = []
    for block in blocks:
        if block["type"] == "row":
            cells_html = "".join(
                f'<td class="{"info-label" if c.strip().endswith(":") else ""}">{_escape_html(c)}</td>'
                for c in block["cells"]
            )
            parts.append(f'<table class="info-table"><tr>{cells_html}</tr></table>')
        else:
            content = block["content"]
            if content:
                parts.append(f'<div class="body-text">{_escape_html(content)}</div>')
    return "".join(parts)


def generate_radiology_pdf(patient_name: str, panel_data: dict) -> bytes:
    """
    panel_data: {"panel_name", "sample_date", "document_number", "items": [
        {"test_name", "result_value", ...}, ...
    ]}
    items[].result_value შეიცავს CDA-დან ამოღებულ სრულ თხრობით ტექსტს
    (რომელიც უკვე თავისთავად შეიცავს პაციენტის/ექიმის ინფოს, დასკვნას,
    დიაგნოზს და ხელმოწერის ხაზს — 1C-ის ორიგინალური ფორმის მსგავსად).
    """
    logo_b64 = _logo_base64()
    doc_number = panel_data.get("document_number", "") or ""
    barcode_b64 = _barcode_base64(doc_number)

    sample_date = panel_data.get("sample_date")
    date_str = sample_date.strftime("%d.%m.%Y") if isinstance(sample_date, datetime) else str(sample_date or "")

    body_parts = []
    items = panel_data.get("items", [])
    exam_title = panel_data.get("panel_name", "")

    if exam_title:
        body_parts.append(f'<div class="exam-title">{_escape_html(exam_title)}</div>')

    first_item_processed = False
    for item in items:
        blocks = item.get("blocks")
        if blocks:
            if not first_item_processed:
                # გამოვტოვოთ ყველა თანმიმდევრული "text" ბლოკი დასაწყისში —
                # ეს ჩვეულებრივ კვლევის სახელის გამეორებაა (რაც უკვე
                # საიმედო წყაროდან, panel_name-იდან, ავჩვენეთ ზემოთ)
                idx = 0
                while idx < len(blocks) and idx < 3 and blocks[idx]["type"] == "text":
                    idx += 1
                blocks = blocks[idx:]
                first_item_processed = True
            body_parts.append(_render_blocks(blocks))
        else:
            text = item.get("result_value") or ""
            if text:
                body_parts.append(f'<div class="body-text">{_escape_html(text)}</div>')

    logo_img = f'<img class="logo" src="data:image/png;base64,{logo_b64}">' if logo_b64 else ""
    barcode_img = f'<img class="barcode" src="data:image/png;base64,{barcode_b64}">' if barcode_b64 else ""

    html = f"""<!DOCTYPE html>
<html lang="ka">
<head><meta charset="utf-8"><style>{PAGE_CSS}</style></head>
<body>
    <div class="header">
        <div class="header-left">{logo_img}</div>
        <div class="header-right">
            {barcode_img}
            <div class="history-number">{_escape_html(doc_number)}</div>
        </div>
    </div>
    <div class="doc-meta">ისტორიის № {_escape_html(doc_number) or '—'} &nbsp;•&nbsp; {date_str}</div>
    {"".join(body_parts)}
    <div class="footer">ეს დოკუმენტი გენერირებულია ავტომატურად Innova Medical-ის პაციენტის პორტალიდან.</div>
</body>
</html>"""

    return _html_to_pdf_bytes(html)