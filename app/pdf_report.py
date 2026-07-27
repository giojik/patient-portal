"""
PDF report გენერაცია ერთი პანელისთვის (მაგ. "სისხლის საერთო ანალიზი").
იყენებს DejaVu Sans-ს ქართული ტექსტის რენდერისთვის — reportlab-ის
ჩაშენებულ ფონტებს (Helvetica და ა.შ.) ქართული გლიფები არ აქვს.
"""
import os
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import ParagraphStyle
from reportlab.graphics.barcode import code128

FONT_REGULAR_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
LOGO_PATH = os.path.join(os.path.dirname(__file__), "assets", "innova_logo.png")
BRAND_GREEN = colors.HexColor("#76c003")

_fonts_registered = False


def _ensure_fonts():
    global _fonts_registered
    if _fonts_registered:
        return
    pdfmetrics.registerFont(TTFont("DejaVuSans", FONT_REGULAR_PATH))
    pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", FONT_BOLD_PATH))
    _fonts_registered = True


def _build_branded_header(document_number: str, extra_meta: str = ""):
    """
    ლოგო (მარცხნივ) + ბარკოდი (მარჯვნივ, დინამურად გენერირებული ამ
    კონკრეტული დოკუმენტის ნომრით) — ორივე PDF template-ისთვის საერთო.
    """
    logo_cell = ""
    if os.path.exists(LOGO_PATH):
        logo_cell = RLImage(LOGO_PATH, width=20 * mm, height=15.7 * mm)

    barcode_cell = ""
    if document_number:
        barcode_cell = code128.Code128(str(document_number), barHeight=10 * mm, barWidth=0.35)

    number_style = ParagraphStyle(
        "NumberKa", fontName="DejaVuSans", fontSize=8,
        textColor=colors.HexColor("#6b7a78"), alignment=2,
    )
    number_para = Paragraph(document_number or "", number_style)

    header_table = Table(
        [[logo_cell, "", barcode_cell], ["", "", number_para]],
        colWidths=[30 * mm, 90 * mm, 52 * mm],
    )
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return header_table


STATUS_COLORS = {
    "normal": colors.HexColor("#1e8a4c"),
    "borderline": colors.HexColor("#b8860b"),
    "abnormal": colors.HexColor("#c0392b"),
    "unknown": colors.HexColor("#1f2d2b"),
}
STATUS_LABELS = {
    "normal": "ნორმაში",
    "borderline": "ზღვარზე",
    "abnormal": "ნორმიდან გადახრილი",
    "unknown": "",
}


def generate_panel_pdf(patient_name: str, panel_data: dict) -> bytes:
    """
    panel_data = {"panel_name": str, "sample_date": datetime, "items": [
        {"test_name", "result_value", "unit", "norm_low", "norm_high", "status"}, ...
    ]}
    აბრუნებს PDF-ის raw bytes-ს.
    """
    _ensure_fonts()

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=20 * mm, bottomMargin=18 * mm,
        leftMargin=18 * mm, rightMargin=18 * mm,
    )

    title_style = ParagraphStyle(
        "TitleKa", fontName="DejaVuSans-Bold", fontSize=15,
        textColor=BRAND_GREEN, spaceAfter=4,
    )
    meta_style = ParagraphStyle(
        "MetaKa", fontName="DejaVuSans", fontSize=10,
        textColor=colors.HexColor("#6b7a78"), spaceAfter=2,
    )
    panel_title_style = ParagraphStyle(
        "PanelKa", fontName="DejaVuSans-Bold", fontSize=12,
        textColor=colors.HexColor("#1f2d2b"), spaceBefore=10, spaceAfter=8,
    )
    footer_style = ParagraphStyle(
        "FooterKa", fontName="DejaVuSans", fontSize=8,
        textColor=colors.HexColor("#9aa8a5"),
    )

    elements = []
    elements.append(_build_branded_header(panel_data.get("document_number", "")))
    elements.append(Spacer(1, 6 * mm))
    elements.append(Paragraph("ლაბორატორიული კვლევების პორტალი", title_style))
    elements.append(Paragraph(f"პაციენტი: {patient_name}", meta_style))

    sample_date = panel_data["sample_date"]
    date_str = sample_date.strftime("%d.%m.%Y") if isinstance(sample_date, datetime) else str(sample_date)
    elements.append(Paragraph(f"კვლევის თარიღი: {date_str}", meta_style))
    elements.append(Paragraph(f"რეპორტის დაბეჭდვის თარიღი: {datetime.now().strftime('%d.%m.%Y %H:%M')}", meta_style))
    elements.append(Spacer(1, 6 * mm))

    elements.append(Paragraph(panel_data["panel_name"], panel_title_style))

    table_data = [["კვლევა", "შედეგი", "ნორმა", "სტატუსი"]]
    row_colors = []

    for item in panel_data["items"]:
        norm_range = (
            f"{item['norm_low']} – {item['norm_high']}"
            if item.get("norm_low") is not None and item.get("norm_high") is not None
            else "—"
        )
        result_display = f"{item['result_value'] or '—'} {item.get('unit') or ''}".strip()
        status = item.get("status", "unknown")
        table_data.append([
            item["test_name"],
            result_display,
            norm_range,
            STATUS_LABELS.get(status, ""),
        ])
        row_colors.append(STATUS_COLORS.get(status, colors.black))

    table = Table(table_data, colWidths=[70 * mm, 40 * mm, 30 * mm, 35 * mm])

    style_commands = [
        ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "DejaVuSans"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6f2ef")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#124d42")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#124d42")),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, colors.HexColor("#e0e6e4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for i, color in enumerate(row_colors, start=1):
        style_commands.append(("TEXTCOLOR", (1, i), (1, i), color))
        style_commands.append(("TEXTCOLOR", (3, i), (3, i), color))

    table.setStyle(TableStyle(style_commands))
    elements.append(table)

    elements.append(Spacer(1, 10 * mm))
    elements.append(Paragraph(
        "ეს დოკუმენტი გენერირებულია ავტომატურად ლაბორატორიული კვლევების პორტალიდან. "
        "შედეგების ინტერპრეტაციისთვის მიმართეთ თქვენს ექიმს.",
        footer_style,
    ))

    doc.build(elements)
    return buffer.getvalue()