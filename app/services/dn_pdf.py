"""Utilities for generating DN detail PDF reports."""

from __future__ import annotations

import datetime
import html
import os
from io import BytesIO
from typing import Any, Callable, Dict, Iterable, Mapping, Sequence, Tuple
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.services.dn_early_bird import EarlyBirdResult
from app.utils.logging import logger

__all__ = ["generate_dn_details_pdf", "generate_early_bird_pdf"]


MAP_IMAGE_WIDTH = 80
MAP_IMAGE_HEIGHT = 80
PHOTO_IMAGE_WIDTH = 80
PHOTO_IMAGE_HEIGHT = 120
MAP_ZOOM_LEVEL = 13

EARLY_BIRD_MAP_WIDTH = 128
EARLY_BIRD_MAP_HEIGHT = int(EARLY_BIRD_MAP_WIDTH * 3 / 2)
EARLY_BIRD_PHOTO_WIDTH = EARLY_BIRD_MAP_WIDTH
EARLY_BIRD_PHOTO_HEIGHT = EARLY_BIRD_MAP_HEIGHT

TITLE_BACKGROUND = colors.HexColor("#528CD9")
TITLE_TEXT_COLOR = colors.HexColor("#ffffff")
CARD_BACKGROUND = colors.HexColor("#F7F9FF")
CARD_BORDER_COLOR = colors.HexColor("#D6E2FF")
LABEL_TEXT_COLOR = colors.HexColor("#51607A")
HEADER_VALUE_FONT = "HeiseiKakuGo-W5"

STATUS_COLOR_MAP: Dict[str, Tuple[colors.Color, colors.Color]] = {
    "no status": (colors.HexColor("#FFF3B0"), colors.HexColor("#8A6A03")),
    "arrived at site": (colors.HexColor("#D9F7BE"), colors.HexColor("#2F6B2F")),
    "pod": (colors.HexColor("#A8E0D1"), colors.HexColor("#1E5B4F")),
}
DEFAULT_STATUS_COLORS: Tuple[colors.Color, colors.Color] = (
    colors.HexColor("#E6EBFF"),
    colors.HexColor("#3E4C78"),
)

# Ensure font is registered once for Unicode support.
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))

styles = getSampleStyleSheet()
style_title = ParagraphStyle(
    "dn-title",
    parent=styles["Title"],
    fontName="STSong-Light",
    fontSize=14,
    leading=18,
    spaceAfter=6,
    textColor=colors.black,
)
style_info = ParagraphStyle(
    "dn-info",
    parent=styles["Normal"],
    fontName="STSong-Light",
    fontSize=10,
    leading=13,
)
style_small = ParagraphStyle(
    "dn-small",
    parent=styles["Normal"],
    fontName="STSong-Light",
    fontSize=9,
    textColor=colors.grey,
)
style_early_title = ParagraphStyle(
    "early-title",
    parent=styles["Title"],
    fontName="Helvetica-Bold",
    fontSize=16,
    textColor=colors.HexColor("#21324B"),
    leading=20,
)
style_early_header = ParagraphStyle(
    "early-header",
    parent=styles["Normal"],
    fontName="Helvetica-Bold",
    fontSize=16,
    leading=20,
    textColor=colors.HexColor("#1B1D2A"),
    wordWrap="LTR",
    spaceAfter=2,
)
style_early_subtext = ParagraphStyle(
    "early-subtext",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=10,
    leading=13,
    textColor=colors.HexColor("#687385"),
)
style_early_attr_label = ParagraphStyle(
    "early-attr-label",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#9AA1B3"),
)
style_early_attr_value = ParagraphStyle(
    "early-attr-value",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=10,
    leading=13,
    textColor=colors.HexColor("#1D232F"),
    wordWrap="LTR",
)
style_early_region = ParagraphStyle(
    "early-region",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=9,
    leading=12,
    textColor=colors.HexColor("#7B8395"),
    alignment=2,
    wordWrap="LTR",
)
style_early_tag = ParagraphStyle(
    "early-tag",
    parent=styles["Normal"],
    fontName="Helvetica",
    fontSize=8,
    leading=10,
    textColor=colors.HexColor("#5B6474"),
    backColor=colors.HexColor("#EEF3FD"),
    borderColor=colors.HexColor("#CCD7EE"),
    borderWidth=0.4,
    borderPadding=2,
    alignment=0,
)
style_dn_header = ParagraphStyle(
    "dn-header",
    parent=styles["Normal"],
    fontName="STSong-Light",
    fontSize=15,
    leading=19,
    textColor=TITLE_TEXT_COLOR,
    spaceAfter=0,
    spaceBefore=0,
    alignment=0,
)

def _make_placeholder(text: str = "No Data", width: int = 80, height: int = 80) -> Drawing:
    drawing = Drawing(width, height)
    drawing.add(Rect(0, 0, width, height, strokeColor=colors.grey, fillColor=colors.whitesmoke))
    drawing.add(
        String(
            width / 2,
            height / 2,
            text,
            fontSize=8,
            textAnchor="middle",
            fillColor=colors.darkgrey,
        )
    )
    return drawing


def _format_value(value: Any, default: str = "-") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return default
        escaped = html.escape(normalized)
        escaped = escaped.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
        return escaped
    return html.escape(str(value))


def _format_datetime(value: Any) -> str:
    if not value or value == "-":
        return "-"
    text = str(value)
    sanitized = text
    if sanitized.endswith("Z"):
        sanitized = sanitized[:-1] + "+00:00"
    try:
        dt = datetime.datetime.fromisoformat(sanitized)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return html.escape(text)


def _image_from_bytes(data: bytes | None, width: int, height: int, placeholder_text: str) -> Image | Drawing:
    if data:
        try:
            stream = BytesIO(data)
            stream.seek(0)
            return Image(stream, width=width, height=height)
        except Exception:
            logger.warning("Failed to load image bytes into PDF, falling back to placeholder.")
    return _make_placeholder(placeholder_text, width, height)


def _fetch_url_bytes(url: str, timeout: int = 10) -> bytes | None:
    try:
        request = Request(url, headers={"User-Agent": "JakartaBackend/1.0"})
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (URLError, OSError, TimeoutError) as exc:
        logger.warning("Failed to fetch URL %s: %s", url, exc)
    except Exception:
        logger.exception("Unexpected error fetching URL %s", url)
    return None


def _fetch_map_image(
    lng: float,
    lat: float,
    token: str,
    width: int = 256,
    height: int = 256,
    *,
    scale: float = 2.0,
) -> bytes | None:
    coordinates = f"{lng:.6f},{lat:.6f}"
    marker = f"pin-s+ff0000({coordinates})"
    fetch_scale = 1.0 if scale is None else max(scale, 1.0)
    fetch_width = max(1, int(round(width * fetch_scale)))
    fetch_height = max(1, int(round(height * fetch_scale)))
    params = urlencode({"access_token": token})
    url = (
        "https://api.mapbox.com/styles/v1/mapbox/streets-v11/static/"
        f"{marker}/{coordinates},{MAP_ZOOM_LEVEL}/{fetch_width}x{fetch_height}?{params}"
    )
    return _fetch_url_bytes(url)


def _resolve_photo_bytes(photo_url: Any, storage_base_path: str) -> bytes | None:
    if not photo_url:
        return None
    url = str(photo_url)
    if url.startswith("http://") or url.startswith("https://"):
        return _fetch_url_bytes(url)

    if url.startswith("/uploads/"):
        relative = url[len("/uploads/") :].lstrip("/")
        path = os.path.join(storage_base_path, relative)
    elif url.startswith("/"):
        path = url
    else:
        path = os.path.join(storage_base_path, url)

    if os.path.exists(path):
        try:
            with open(path, "rb") as file_obj:
                return file_obj.read()
        except OSError as exc:
            logger.warning("Failed to read photo file %s: %s", path, exc)
    return None


def _parse_coordinates(lng: Any, lat: Any) -> Tuple[float, float] | None:
    try:
        if lng is None or lat is None:
            return None
        lng_val = float(lng)
        lat_val = float(lat)
        return lng_val, lat_val
    except (TypeError, ValueError):
        return None


def _format_date(value: Any) -> str:
    if value is None or value == "-":
        return "-"
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return _format_value(value)


def _build_early_bird_attribute_table(result: "EarlyBirdResult", *, column_width: float) -> Table:
    attrs = [
        ("Plan MOS Date", _format_date(result.plan_date)),
        ("Area", _format_value(result.dn.area)),
        ("LSP", _format_value(result.dn.lsp)),
        ("Arrival Status", _format_value(result.arrival_status)),
        ("Arrival Time", _format_datetime(result.arrival_time.isoformat())),
        ("Cut Off Time", _format_datetime(result.cutoff_time.isoformat())),
        ("Updated By", _format_value(result.record.updated_by)),
        ("Phone Number", _format_value(result.record.phone_number)),
    ]

    cell_inner_width = max(column_width - 8, 80)

    def _make_cell(label: str, value: str) -> Table:
        label_text = html.escape(label.upper()) if label else "&nbsp;"
        value_text = value if label else "&nbsp;"
        label_para = Paragraph(label_text, style_early_attr_label)
        value_para = Paragraph(value_text, style_early_attr_value)
        cell_table = Table([[label_para], [Spacer(1, 2)], [value_para]], colWidths=[cell_inner_width])
        cell_table.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return cell_table

    rows: list[list[Any]] = []
    for idx in range(0, len(attrs), 2):
        left_label, left_value = attrs[idx]
        left_cell = _make_cell(left_label, left_value)

        if idx + 1 < len(attrs):
            right_label, right_value = attrs[idx + 1]
            right_cell = _make_cell(right_label, right_value)
        else:
            right_cell = _make_cell("", "")

        rows.append([left_cell, right_cell])

    attr_table = Table(rows, colWidths=[column_width, column_width])
    attr_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return attr_table


def _build_early_bird_card(
    result: "EarlyBirdResult",
    map_fetcher: Callable[["EarlyBirdResult"], bytes | None],
    photo_loader: Callable[["EarlyBirdResult"], bytes | None],
    content_width: float,
) -> Table:
    map_padding_left = 8
    map_padding_right = 8
    map_column_width = EARLY_BIRD_MAP_WIDTH + map_padding_left + map_padding_right
    detail_padding_left = 14
    detail_padding_right = 14

    detail_column_width = content_width - (2 * map_column_width)
    if detail_column_width < 200:
        detail_column_width = 200
        total_width = detail_column_width + 2 * map_column_width
        if total_width > content_width:
            detail_column_width = max(content_width - (2 * map_column_width), 160)
    usable_detail_width = max(detail_column_width - detail_padding_left - detail_padding_right, 140)

    header_value = html.escape(result.dn.dn_number or "-")
    header = Paragraph(header_value, style_early_header)
    region_value = _format_value(result.dn.region)
    region_para = Paragraph(region_value, style_early_region)
    attr_column_width = max(usable_detail_width / 2, 130)
    attr_table = _build_early_bird_attribute_table(result, column_width=attr_column_width)

    chip_para = Paragraph("EARLY BIRD", style_early_tag)
    chip_width = min(70, max(usable_detail_width - 20, 40))
    chip_table = Table([[chip_para]], colWidths=[chip_width])
    chip_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ]
        )
    )

    region_col_width = min(120, max(usable_detail_width * 0.35, 60))
    header_col_width = max(usable_detail_width - region_col_width, 120)
    header_table = Table([[header, region_para]], colWidths=[header_col_width, region_col_width])
    header_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (1, 0), (1, 0), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )

    detail_rows = [[chip_table], [Spacer(1, 2)], [header_table], [Spacer(1, 6)], [attr_table]]
    detail_table = Table(detail_rows, colWidths=[usable_detail_width])
    detail_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("ALIGN", (0, 0), (0, 0), "LEFT"),
            ]
        )
    )

    map_bytes = map_fetcher(result)
    map_flowable = _image_from_bytes(map_bytes, EARLY_BIRD_MAP_WIDTH, EARLY_BIRD_MAP_HEIGHT, "No Map")

    photo_bytes = photo_loader(result)
    photo_flowable = _image_from_bytes(
        photo_bytes,
        EARLY_BIRD_PHOTO_WIDTH,
        EARLY_BIRD_PHOTO_HEIGHT,
        "No Photo",
    )

    card_table = Table(
        [[detail_table, map_flowable, photo_flowable]],
        colWidths=[detail_column_width, map_column_width, map_column_width],
    )
    card_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#F8FAFF")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D7DEEB")),
                ("LEFTPADDING", (0, 0), (0, 0), detail_padding_left),
                ("RIGHTPADDING", (0, 0), (0, 0), detail_padding_right),
                ("TOPPADDING", (0, 0), (0, 0), 16),
                ("BOTTOMPADDING", (0, 0), (0, 0), 16),
                ("LEFTPADDING", (1, 0), (2, 0), map_padding_left),
                ("RIGHTPADDING", (1, 0), (2, 0), map_padding_right),
                ("TOPPADDING", (1, 0), (2, 0), 12),
                ("BOTTOMPADDING", (1, 0), (2, 0), 12),
                ("ALIGN", (1, 0), (2, 0), "CENTER"),
                ("VALIGN", (1, 0), (2, 0), "MIDDLE"),
            ]
        )
    )
    return card_table


def generate_early_bird_pdf(
    results: Sequence[EarlyBirdResult],
    *,
    mapbox_token: str,
    storage_base_path: str,
    start_date: datetime.date,
    end_date: datetime.date,
) -> bytes:
    if not results:
        raise ValueError("No early-bird data to export")

    if not mapbox_token:
        raise ValueError("mapbox_token is required to generate the PDF.")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * mm,
        rightMargin=2 * mm,
        topMargin=5 * mm,
        bottomMargin=5 * mm,
    )

    map_cache: Dict[Tuple[float, float], bytes | None] = {}

    def map_fetcher(result: EarlyBirdResult) -> bytes | None:
        record_lng = getattr(result.record, "lng", None)
        record_lat = getattr(result.record, "lat", None)
        dn_lng = getattr(result.dn, "lng", None)
        dn_lat = getattr(result.dn, "lat", None)
        lng_value = record_lng if record_lng not in (None, "") else dn_lng
        lat_value = record_lat if record_lat not in (None, "") else dn_lat
        coords = _parse_coordinates(lng_value, lat_value)
        if coords is None:
            return None
        if coords not in map_cache:
            try:
                map_cache[coords] = _fetch_map_image(
                    coords[0],
                    coords[1],
                    mapbox_token,
                    width=EARLY_BIRD_MAP_WIDTH,
                    height=EARLY_BIRD_MAP_HEIGHT,
                    scale=2.0,
                )
            except Exception:
                logger.exception("Failed to fetch map image for coordinates %s", coords)
                map_cache[coords] = None
        return map_cache.get(coords)

    def photo_loader(result: EarlyBirdResult) -> bytes | None:
        photo_url = getattr(result.record, "photo_url", None) or getattr(result.dn, "photo_url", None)
        return _resolve_photo_bytes(photo_url, storage_base_path)

    elements: list = []
    elements.append(Paragraph("Early Bird DN Report", style_early_title))
    range_text = (
        f"Plan MOS Date Range: {start_date.strftime('%Y-%m-%d')} – {end_date.strftime('%Y-%m-%d')}"
    )
    elements.append(Paragraph(range_text, style_early_subtext))
    elements.append(Spacer(1, 18))

    available_width = doc.width

    for result in results:
        card = _build_early_bird_card(result, map_fetcher, photo_loader, available_width)
        elements.append(card)
        elements.append(Spacer(1, 12))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(A4[0] / 2.0, 1.0 * cm, "Generated by Jakarta Backend")
        canvas.restoreState()

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)

    buffer.seek(0)
    return buffer.read()


def _resolve_status_colors(value: Any) -> Tuple[colors.Color, colors.Color]:
    if value is None:
        return DEFAULT_STATUS_COLORS
    key = str(value).strip().lower()
    if not key:
        return DEFAULT_STATUS_COLORS
    return STATUS_COLOR_MAP.get(key, DEFAULT_STATUS_COLORS)


def _build_status_table(record: Mapping[str, Any]) -> Table:
    status_fields = [
        ("Status Delivery", record.get("status_delivery")),
        ("Status Site", record.get("status_site")),
    ]
    rows = [
        [
            Paragraph(f"<b>{html.escape(label)}:</b>", style_info),
            Paragraph(_format_value(value), style_info),
        ]
        for label, value in status_fields
    ]
    status_table = Table(rows, colWidths=[3.4 * cm, 4.0 * cm])
    commands: list[Tuple[str, Tuple[int, int], Tuple[int, int], Any]] = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, 0), (0, -1), LABEL_TEXT_COLOR),
    ]
    for idx, (_, value) in enumerate(status_fields):
        bg_color, text_color = _resolve_status_colors(value)
        commands.append(("BACKGROUND", (1, idx), (1, idx), bg_color))
        commands.append(("TEXTCOLOR", (1, idx), (1, idx), text_color))
        commands.append(("LEFTPADDING", (1, idx), (1, idx), 6))
        commands.append(("RIGHTPADDING", (1, idx), (1, idx), 14))
    status_table.setStyle(TableStyle(commands))
    return status_table


def _build_dn_header(dn_data: Mapping[str, Any], *, width: float) -> Table:
    dn_value = _format_value(dn_data.get("dn_number"))
    region_value = _format_value(dn_data.get("region"))
    header_text = (
        f"<font name='Helvetica-Bold'>DN Number:</font> <font name='{HEADER_VALUE_FONT}'>{dn_value}</font> &nbsp;&nbsp;&nbsp; "
        f"<font name='Helvetica-Bold'>Region:</font> <font name='{HEADER_VALUE_FONT}'>{region_value}</font>"
    )
    header_paragraph = Paragraph(header_text, style_dn_header)
    header_table = Table([[header_paragraph]], colWidths=[width])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), TITLE_BACKGROUND),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return header_table


def _build_record_row(
    record: Mapping[str, Any],
    map_fetcher: Callable[[Mapping[str, Any]], bytes | None],
    photo_loader: Callable[[Any], bytes | None],
) -> Table:
    created_at = _format_datetime(record.get("created_at"))
    status_table = _build_status_table(record)

    detail_lines = [
        f"<b>Remark:</b> {_format_value(record.get('remark'))}",
        f"<b>Phone Number:</b> {_format_value(record.get('phone_number'))}",
        f"<b>Updated by:</b> {_format_value(record.get('updated_by'))}",
        f"<b>Created at:</b> {created_at}",
    ]
    details_paragraph = Paragraph("<br/>".join(detail_lines), style_info)

    map_bytes = map_fetcher(record)
    map_flowable = _image_from_bytes(map_bytes, MAP_IMAGE_WIDTH, MAP_IMAGE_HEIGHT, "No Location")

    photo_bytes = photo_loader(record.get("photo_url"))
    photo_flowable = _image_from_bytes(photo_bytes, PHOTO_IMAGE_WIDTH, PHOTO_IMAGE_HEIGHT, "No Photo")

    table = Table(
        [
            [status_table, map_flowable, photo_flowable],
            [details_paragraph, "", ""],
        ],
        colWidths=[8 * cm, 3 * cm, 3.5 * cm],
    )
    table.setStyle(
        TableStyle(
            [
                ("SPAN", (1, 0), (1, 1)),
                ("SPAN", (2, 0), (2, 1)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (0, 1), CARD_BACKGROUND),
                ("BACKGROUND", (1, 0), (2, 1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.4, CARD_BORDER_COLOR),
                ("LINEABOVE", (0, 1), (-1, 1), 0.3, CARD_BORDER_COLOR),
                ("LEFTPADDING", (0, 0), (0, 1), 10),
                ("RIGHTPADDING", (0, 0), (0, 1), 6),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                ("TOPPADDING", (0, 1), (0, 1), 6),
                ("BOTTOMPADDING", (0, 1), (0, 1), 6),
                ("LEFTPADDING", (1, 0), (2, 1), 6),
                ("RIGHTPADDING", (1, 0), (2, 1), 6),
                ("TOPPADDING", (1, 0), (2, 1), 6),
                ("BOTTOMPADDING", (1, 0), (2, 1), 6),
                ("ALIGN", (1, 0), (1, 1), "CENTER"),
                ("VALIGN", (1, 0), (1, 1), "MIDDLE"),
                ("ALIGN", (2, 0), (2, 1), "CENTER"),
                ("VALIGN", (2, 0), (2, 1), "MIDDLE"),
            ]
        )
    )
    return table


def _build_not_found_page(dn_number: str) -> list:
    elements = [
        Paragraph(f"<b>DN Number:</b> {html.escape(dn_number)}", style_title),
        Spacer(1, 6),
        Paragraph("DN record not found.", style_info),
        PageBreak(),
    ]
    return elements


def generate_dn_details_pdf(
    entries: Sequence[Mapping[str, Any]],
    *,
    mapbox_token: str,
    storage_base_path: str,
) -> bytes:
    if not mapbox_token:
        raise ValueError("mapbox_token is required to generate the PDF.")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    map_cache: Dict[Tuple[float, float], bytes | None] = {}

    def map_fetcher(record: Mapping[str, Any]) -> bytes | None:
        coords = _parse_coordinates(record.get("lng"), record.get("lat"))
        if coords is None:
            return None
        if coords not in map_cache:
            try:
                map_cache[coords] = _fetch_map_image(
                    coords[0],
                    coords[1],
                    mapbox_token,
                    width=MAP_IMAGE_WIDTH,
                    height=MAP_IMAGE_HEIGHT,
                    scale=2.0,
                )
            except Exception:
                logger.exception("Failed to fetch map image for coordinates %s", coords)
                map_cache[coords] = None
        cached = map_cache.get(coords)
        return cached if cached else None

    def photo_loader(photo_url: Any) -> bytes | None:
        return _resolve_photo_bytes(photo_url, storage_base_path)

    elements: list = []

    for entry in entries:
        dn_number = entry.get("dn_number", "-")
        dn_data = entry.get("dn")
        records: Iterable[Mapping[str, Any]] = entry.get("records") or []

        if not dn_data:
            elements.extend(_build_not_found_page(str(dn_number)))
            continue

        elements.append(_build_dn_header(dn_data, width=doc.width))
        elements.append(Spacer(1, 6))

        def add_info_line(fields: Mapping[str, Any]) -> None:
            parts = []
            for label, value in fields.items():
                formatted = _format_value(value, default="")
                if formatted:
                    parts.append(f"<b>{html.escape(label)}:</b> {formatted}")
            if parts:
                elements.append(Paragraph(" &nbsp;&nbsp;&nbsp; ".join(parts), style_info))

        add_info_line(
            {
                "DU ID": dn_data.get("du_id"),
                "LSP": dn_data.get("lsp"),
                "Plan MOS Date": dn_data.get("plan_mos_date"),
            }
        )
        add_info_line(
            {
                "Delivery Type": dn_data.get("delivery_type_a_to_b"),
                "Project": dn_data.get("project_request"),
                "Status WH": dn_data.get("status_wh"),
            }
        )
        add_info_line(
            {
                "ETA": dn_data.get("estimate_arrive_sites_time_eta"),
                "ATD": dn_data.get("actual_depart_from_start_point_atd"),
                "ATA": dn_data.get("actual_arrive_time_ata"),
            }
        )
        driver_name = dn_data.get("driver_contact_name") or "-"
        driver_phone = dn_data.get("driver_contact_number") or "-"
        add_info_line(
            {
                "Driver": f"{driver_name} ({driver_phone})",
                "Subcon": dn_data.get("subcon"),
            }
        )

        remark = dn_data.get("remark")
        if remark:
            elements.append(Spacer(1, 4))
            elements.append(Paragraph(f"<b>Remark:</b> {_format_value(remark)}", style_info))

        elements.append(Spacer(1, 6))

        has_records = False
        for record in records:
            has_records = True
            elements.append(_build_record_row(record, map_fetcher, photo_loader))
            elements.append(Spacer(1, 6))

        if not has_records:
            elements.append(Paragraph("No Records", style_small))

        elements.append(PageBreak())

    if not elements:
        elements.append(Paragraph("No DN data available.", style_info))

    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawCentredString(A4[0] / 2.0, 1.0 * cm, "Generated by Jakarta Backend")
        canvas.restoreState()

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)

    buffer.seek(0)
    return buffer.read()
