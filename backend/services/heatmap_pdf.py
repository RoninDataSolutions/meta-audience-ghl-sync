"""
heatmap_pdf.py — ReportLab PDF generator for the Geo ROAS heat map.

Renders a multi-page report:
  1. Cover — account, window, KPIs, reallocation callout
  2. US choropleth map — colored by selected metric (default: spend)
  3. Performance tiers — High / Medium / Low / Zero / Untapped tables
  4. Action items — numbered steps + Include / Exclude state lists
  5. Appendix — full per-state table

The map is drawn directly from us-atlas GeoJSON (Albers USA projection) using
ReportLab Drawing primitives — no matplotlib/cartopy required.
"""

import io
import json
import logging
import os
from datetime import datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Polygon, String
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, Color, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)

logger = logging.getLogger(__name__)

# ── Layout ───────────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = letter
MARGIN_L = 0.65 * inch
MARGIN_R = 0.65 * inch
MARGIN_T = 0.55 * inch
MARGIN_B = 0.65 * inch
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R

# ── Palette (matches the on-screen dashboard) ────────────────────────────────

BG_DARK = HexColor("#0a0a0c")
INK = HexColor("#0F172A")           # body text on white background
INK_MUTED = HexColor("#64748B")
INK_FAINT = HexColor("#94A3B8")
LINE = HexColor("#E2E8F0")
PANEL = HexColor("#F8FAFC")
PANEL_DARK = HexColor("#1a1a1f")
GOLD = HexColor("#C8972A")

# Tier colors
C_HIGH = HexColor("#10B981")        # high ROAS
C_HIGH_BG = HexColor("#ECFDF5")
C_MED = HexColor("#F59E0B")         # medium
C_MED_BG = HexColor("#FFFBEB")
C_LOW = HexColor("#EF4444")         # low ROAS
C_LOW_BG = HexColor("#FEF2F2")
C_NONE = HexColor("#94A3B8")        # zero ROAS
C_NONE_BG = HexColor("#F1F5F9")
C_UNT = HexColor("#6366F1")         # untapped
C_UNT_BG = HexColor("#EEF2FF")

# Map colormap stops (cold → hot)
MAP_STOPS = [
    (40, 50, 70),     # cold (no data / very low)
    (59, 130, 246),   # cool blue
    (16, 185, 129),   # warm green
    (245, 158, 11),   # hot amber
    (239, 68, 68),    # blazing red
]


TIER_META = {
    "high_roas":   {"label": "High ROAS",   "icon": "▲", "color": C_HIGH, "bg": C_HIGH_BG, "subtitle": "Scale 15-25%"},
    "medium_roas": {"label": "Medium ROAS", "icon": "—", "color": C_MED,  "bg": C_MED_BG,  "subtitle": "Hold steady"},
    "low_roas":    {"label": "Low ROAS",    "icon": "▼", "color": C_LOW,  "bg": C_LOW_BG,  "subtitle": "Cut - losing money"},
    "no_roas":     {"label": "Zero ROAS",   "icon": "X", "color": C_NONE, "bg": C_NONE_BG, "subtitle": "Exclude - no return"},
    "untapped":    {"label": "Untapped",    "icon": "♦", "color": C_UNT,  "bg": C_UNT_BG,  "subtitle": "Prospecting opportunity"},
}


# ── Paragraph styles ─────────────────────────────────────────────────────────

def _s(name: str, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


STYLES = {
    "h1":      _s("h1", fontName="Helvetica-Bold", fontSize=22, leading=28, textColor=INK, spaceAfter=2),
    "h1_dark": _s("h1_dark", fontName="Helvetica-Bold", fontSize=22, leading=28, textColor=white, spaceAfter=2),
    "h2":      _s("h2", fontName="Helvetica-Bold", fontSize=14, leading=18, textColor=INK, spaceAfter=4),
    "h3":      _s("h3", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK, spaceAfter=2),
    "tag":     _s("tag", fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=GOLD,
                  spaceAfter=0, alignment=TA_LEFT),
    "tag_dark":_s("tag_dark", fontName="Helvetica-Bold", fontSize=8, leading=11,
                  textColor=HexColor("#F59E0B"), spaceAfter=0, alignment=TA_LEFT),
    "body":    _s("body", fontName="Helvetica", fontSize=10, leading=14, textColor=INK, spaceAfter=4),
    "body_w":  _s("body_w", fontName="Helvetica", fontSize=10, leading=14, textColor=white, spaceAfter=4),
    "muted":   _s("muted", fontName="Helvetica", fontSize=9, leading=12, textColor=INK_MUTED),
    "muted_w": _s("muted_w", fontName="Helvetica", fontSize=9, leading=12, textColor=HexColor("#94A3B8")),
    "kpi_label": _s("kpi_label", fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                    textColor=INK_MUTED, alignment=TA_LEFT),
    "kpi_value": _s("kpi_value", fontName="Helvetica-Bold", fontSize=18, leading=22,
                    textColor=INK, alignment=TA_LEFT),
    "kpi_value_accent": _s("kpi_value_accent", fontName="Helvetica-Bold", fontSize=18, leading=22,
                           textColor=C_HIGH, alignment=TA_LEFT),
    "kpi_sub": _s("kpi_sub", fontName="Helvetica", fontSize=8, leading=10,
                  textColor=INK_FAINT, alignment=TA_LEFT),
    "cell":    _s("cell", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
    "cell_num":_s("cell_num", fontName="Helvetica", fontSize=8.5, leading=11,
                  textColor=INK, alignment=TA_RIGHT),
    "cell_head": _s("cell_head", fontName="Helvetica-Bold", fontSize=7.5, leading=10,
                    textColor=INK_MUTED),
    "mono":    _s("mono", fontName="Courier", fontSize=8.5, leading=12, textColor=INK),
    "mono_muted": _s("mono_muted", fontName="Courier", fontSize=8, leading=11, textColor=INK_MUTED),
    "footer":  _s("footer", fontName="Helvetica", fontSize=7, textColor=INK_FAINT, alignment=TA_CENTER),
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _money(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"${round(float(v)):,}"
    except Exception:
        return str(v)


def _roas(v: Any) -> str:
    if v is None or v == 0 or v == "":
        return "—"
    try:
        return f"{float(v):.2f}×"
    except Exception:
        return "—"


def _pct(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{float(v):.1f}%"
    except Exception:
        return "—"


def _num(v: Any) -> str:
    if v is None or v == "":
        return "—"
    try:
        return f"{int(float(v)):,}"
    except Exception:
        return str(v)


def _p(text: Any, style_key: str = "body") -> Paragraph:
    return Paragraph(str(text) if text is not None else "", STYLES[style_key])


def _window_label(days: int | None) -> str:
    if not days:
        return "selected window"
    if days >= 365 and days % 365 == 0:
        y = days // 365
        return f"{y} year" if y == 1 else f"{y} years"
    if days >= 60 and days % 30 == 0:
        return f"{days // 30} months"
    return f"{days} days"


# ── Map rendering ────────────────────────────────────────────────────────────

_GEO_CACHE: dict[str, Any] = {}

# FIPS → state postal code (us-atlas keys features by FIPS code)
FIPS_TO_STATE = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO", "09": "CT",
    "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI", "16": "ID", "17": "IL",
    "18": "IN", "19": "IA", "20": "KS", "21": "KY", "22": "LA", "23": "ME", "24": "MD",
    "25": "MA", "26": "MI", "27": "MN", "28": "MS", "29": "MO", "30": "MT", "31": "NE",
    "32": "NV", "33": "NH", "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND",
    "39": "OH", "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA", "54": "WV",
    "55": "WI", "56": "WY", "72": "PR",
}


def _load_states_geojson() -> dict:
    if "states" in _GEO_CACHE:
        return _GEO_CACHE["states"]
    path = os.path.join(os.path.dirname(__file__), "..", "data", "us_states_albers.geojson")
    with open(path) as f:
        data = json.load(f)
    _GEO_CACHE["states"] = data
    return data


def _lerp(c1: tuple, c2: tuple, t: float) -> tuple[int, int, int]:
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _metric_color(value: float, vmin: float, vmax: float, reverse: bool = False) -> Color:
    if vmax == vmin or value <= 0:
        return Color(40 / 255, 50 / 255, 70 / 255)
    t = (value - vmin) / (vmax - vmin)
    if reverse:
        t = 1 - t
    t = max(0.0, min(1.0, t))
    # Pick interval
    n_stops = len(MAP_STOPS) - 1
    seg = min(int(t * n_stops), n_stops - 1)
    local = (t * n_stops) - seg
    r, g, b = _lerp(MAP_STOPS[seg], MAP_STOPS[seg + 1], local)
    return Color(r / 255, g / 255, b / 255)


def _polygon_coords(geometry: dict) -> list[list[tuple[float, float]]]:
    """Return a list of rings (each a list of (x, y) tuples). For MultiPolygon, returns all outer rings."""
    out: list[list[tuple[float, float]]] = []
    gtype = geometry.get("type")
    coords = geometry.get("coordinates", [])
    if gtype == "Polygon":
        # coords: [outer_ring, hole1, hole2, ...]; we only take the outer ring
        if coords:
            out.append([(p[0], p[1]) for p in coords[0]])
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                out.append([(p[0], p[1]) for p in poly[0]])
    return out


def _build_map_drawing(
    states: list[dict],
    metric: str = "spend",
    metric_label: str = "Spend",
    target_w: float = 6.8 * inch,
    target_h: float = 4.3 * inch,
) -> Drawing:
    """
    Build a ReportLab Drawing of the US choropleth, colored by `metric`.
    Source projection is Albers USA in 0-975 × 0-610 coords; we scale to target_w × target_h.
    """
    geo = _load_states_geojson()

    # Compute color scale bounds
    values: list[float] = []
    by_code: dict[str, dict] = {s["state"]: s for s in states if s.get("state")}
    for s in states:
        v = s.get(metric)
        if isinstance(v, (int, float)) and v > 0:
            values.append(float(v))
    vmin = min(values) if values else 0.0
    vmax = max(values) if values else 1.0

    # Scaling: us-atlas albers is 975 × 610
    src_w, src_h = 975.0, 610.0
    sx = target_w / src_w
    sy = target_h / src_h
    scale = min(sx, sy)
    drawing_w = src_w * scale
    drawing_h = src_h * scale

    d = Drawing(drawing_w, drawing_h)
    # Background
    bg = Polygon(points=[0, 0, drawing_w, 0, drawing_w, drawing_h, 0, drawing_h])
    bg.fillColor = HexColor("#F8FAFC")
    bg.strokeColor = None
    d.add(bg)

    # State polygons
    for feat in geo.get("features", []):
        fips = str(feat.get("id")).zfill(2)
        code = FIPS_TO_STATE.get(fips)
        if not code:
            continue
        row = by_code.get(code)
        value = row.get(metric) if row and isinstance(row.get(metric), (int, float)) else 0
        fill = _metric_color(float(value or 0), vmin, vmax) if value and value > 0 else Color(0.94, 0.96, 0.98)

        # Flip y because ReportLab's origin is bottom-left, but Albers SVG uses top-left
        rings = _polygon_coords(feat.get("geometry", {}))
        for ring in rings:
            pts: list[float] = []
            for (x, y) in ring:
                pts.append(x * scale)
                pts.append(drawing_h - (y * scale))  # flip y
            if len(pts) >= 6:
                poly = Polygon(points=pts)
                poly.fillColor = fill
                poly.strokeColor = HexColor("#CBD5E1")
                poly.strokeWidth = 0.4
                d.add(poly)

        # Centroid label (rough centroid of the largest ring)
        if rings:
            biggest = max(rings, key=len)
            avg_x = sum(p[0] for p in biggest) / len(biggest)
            avg_y = sum(p[1] for p in biggest) / len(biggest)
            txt = String(avg_x * scale, drawing_h - (avg_y * scale) - 3, code)
            txt.fontName = "Helvetica-Bold"
            txt.fontSize = 6
            txt.fillColor = HexColor("#1E293B")
            txt.textAnchor = "middle"
            d.add(txt)

    return d


# ── Page templates ───────────────────────────────────────────────────────────

class HeatmapDoc(BaseDocTemplate):
    def __init__(self, buffer, account_name: str, window_label: str, **kw):
        super().__init__(buffer, pagesize=letter,
                         leftMargin=MARGIN_L, rightMargin=MARGIN_R,
                         topMargin=MARGIN_T, bottomMargin=MARGIN_B,
                         title=f"Geo ROAS — {account_name}", **kw)
        self.account_name = account_name
        self.window_label = window_label
        frame = Frame(MARGIN_L, MARGIN_B, CONTENT_W, PAGE_H - MARGIN_T - MARGIN_B,
                      id="body", showBoundary=0)
        self.addPageTemplates([
            PageTemplate(id="default", frames=[frame], onPage=self._draw_chrome),
        ])

    def _draw_chrome(self, canvas, doc):
        canvas.saveState()
        # Footer
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(INK_FAINT)
        canvas.drawString(MARGIN_L, 0.35 * inch, f"Geo ROAS — {self.account_name} · {self.window_label}")
        canvas.drawRightString(PAGE_W - MARGIN_R, 0.35 * inch, f"Page {doc.page}")
        canvas.restoreState()


# ── Page builders ────────────────────────────────────────────────────────────

def _kpi_card(label: str, value: str, sub: str, accent: bool = False) -> Table:
    """A single KPI cell. Returns a one-cell Table with styled content."""
    inner = [
        [_p(label, "kpi_label")],
        [_p(value, "kpi_value_accent" if accent else "kpi_value")],
        [_p(sub, "kpi_sub")],
    ]
    inner_t = Table(inner, colWidths=[CONTENT_W / 4 - 4],
                    style=TableStyle([
                        ("LEFTPADDING", (0, 0), (-1, -1), 0),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                        ("TOPPADDING", (0, 0), (0, 0), 0),
                        ("TOPPADDING", (0, 1), (0, 1), 4),
                        ("TOPPADDING", (0, 2), (0, 2), 2),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                    ]))

    outer = Table([[inner_t]], colWidths=[CONTENT_W / 4 - 4],
                  style=TableStyle([
                      ("BACKGROUND", (0, 0), (-1, -1), C_HIGH_BG if accent else PANEL),
                      ("BOX", (0, 0), (-1, -1), 0.5, C_HIGH if accent else LINE),
                      ("LEFTPADDING", (0, 0), (-1, -1), 12),
                      ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                      ("TOPPADDING", (0, 0), (-1, -1), 12),
                      ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                  ]))
    return outer


def _build_cover(summary: dict, narrative: dict, account_name: str, window_label: str) -> list:
    story: list = []
    story.append(_p("Geographic Performance · Last " + window_label, "tag"))
    story.append(Spacer(1, 4))
    story.append(_p("Geo ROAS Dashboard", "h1"))
    story.append(_p(account_name, "muted"))
    story.append(Spacer(1, 16))

    # KPI row (4 cards)
    avg_monthly = summary.get("avg_monthly_spend")
    total_ltv = summary.get("total_ltv") or 0
    ltv_roas = summary.get("ltv_roas")
    account_roas = summary.get("account_roas")
    reall = narrative.get("reallocation") or {}

    kpi_row = [[
        _kpi_card(
            "TOTAL SPEND",
            _money(summary.get("total_spend")),
            f"~{_money(avg_monthly)}/mo" if avg_monthly else "",
        ),
        _kpi_card(
            "LIFETIME VALUE",
            _money(total_ltv),
            "from paying geos",
        ),
        _kpi_card(
            "BLENDED ROAS",
            _roas(ltv_roas if ltv_roas else account_roas),
            "overall return",
        ),
        _kpi_card(
            "PROJECTED GAIN",
            f"+{_money(reall.get('projected_revenue_gain'))}" if reall.get("projected_revenue_gain") else "—",
            f"from {_money(reall.get('recoverable_spend'))} reallocated" if reall.get("recoverable_spend") else "after reallocation",
            accent=True,
        ),
    ]]
    kpi_table = Table(kpi_row, colWidths=[CONTENT_W / 4 - 4] * 4,
                      style=TableStyle([
                          ("LEFTPADDING", (0, 0), (-1, -1), 2),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                          ("VALIGN", (0, 0), (-1, -1), "TOP"),
                      ]))
    story.append(kpi_table)
    story.append(Spacer(1, 18))

    # Reallocation callout
    if reall.get("recoverable_spend", 0) > 0 and reall.get("projected_revenue_gain", 0) > 0:
        callout_inner = [
            [_p("⇄ REALLOCATION OPPORTUNITY", "tag")],
            [_p(
                f"Cutting <b>{_money(reall['recoverable_spend'])}</b> of Low + Zero ROAS spend and "
                f"redirecting to High ROAS geos (avg {reall.get('upside_avg_roas', 0):.1f}×) projects "
                f"<b>~{_money(reall['projected_revenue_gain'])}</b> in additional revenue.", "body",
            )],
        ]
        callout = Table(callout_inner, colWidths=[CONTENT_W - 12])
        callout.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_HIGH_BG),
            ("BOX", (0, 0), (-1, -1), 0.7, C_HIGH),
            ("LEFTPADDING", (0, 0), (-1, -1), 16),
            ("RIGHTPADDING", (0, 0), (-1, -1), 16),
            ("TOPPADDING", (0, 0), (0, 0), 12),
            ("TOPPADDING", (0, 1), (0, 1), 4),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
        ]))
        story.append(callout)
        story.append(Spacer(1, 14))

    # Tier-count summary (small inline)
    tt = narrative.get("tier_totals") or {}
    counts_text = (
        f"{tt.get('high_count', 0)} High ROAS · {tt.get('medium_count', 0)} Medium · "
        f"{tt.get('low_count', 0)} Low · {tt.get('no_roas_count', 0)} Zero · "
        f"{tt.get('untapped_count', 0)} Untapped"
    )
    story.append(_p(counts_text, "muted"))
    return story


def _build_map_page(states: list[dict], window_label: str) -> list:
    story = []
    story.append(_p("Spend Distribution", "h2"))
    story.append(_p(f"Heat map colored by spend in the last {window_label}. Darker = more spend.", "muted"))
    story.append(Spacer(1, 8))
    try:
        d = _build_map_drawing(states, metric="spend", metric_label="Spend",
                                target_w=CONTENT_W, target_h=4.3 * inch)
        story.append(d)
    except Exception as e:
        logger.warning(f"Map render failed: {e}", exc_info=True)
        story.append(_p("(Map rendering unavailable.)", "muted"))
    story.append(Spacer(1, 10))
    story.append(_p(
        "Color scale: cold blue = low, green = mid, amber/red = high. "
        "States with no spend appear in light gray.",
        "muted",
    ))
    return story


def _state_roas(row: dict) -> float:
    spend = row.get("spend", 0) or 0
    if spend <= 0:
        return 0.0
    return max(row.get("revenue_30d") or 0, row.get("total_ltv") or 0) / spend


def _tier_table(rows: list[dict], tier_key: str) -> Table | None:
    if not rows:
        return None
    meta = TIER_META[tier_key]

    if tier_key == "no_roas":
        # Just an inline list of state names — full table is overkill
        names = ", ".join(r.get("state_name") or r.get("state") for r in rows)
        block = Table([[_p(names, "mono_muted")]], colWidths=[CONTENT_W])
        block.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), meta["bg"]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        return block

    if tier_key == "untapped":
        data: list[list[Any]] = [[
            _p("State", "cell_head"), _p("LTV", "cell_head"), _p("Current Spend", "cell_head"),
        ]]
        for r in rows:
            data.append([
                _p(f"{r.get('state_name')} ({r.get('state')})", "cell"),
                _p(_money(r.get("total_ltv")), "cell_num"),
                _p(_money(r.get("spend")), "cell_num"),
            ])
        col_w = [CONTENT_W * 0.5, CONTENT_W * 0.25, CONTENT_W * 0.25]
    else:
        show_customers = tier_key == "high_roas"
        headers = ["State", "Spent", "LTV", "ROAS"]
        if show_customers:
            headers.append("Cust.")
        data = [[_p(h, "cell_head") for h in headers]]
        for r in rows:
            row_data = [
                _p(f"{r.get('state_name')} ({r.get('state')})", "cell"),
                _p(_money(r.get("spend")), "cell_num"),
                _p(_money(r.get("total_ltv")), "cell_num"),
                _p(f"{_state_roas(r):.2f}×", "cell_num"),
            ]
            if show_customers:
                row_data.append(_p(_num(r.get("paying_contacts")) if r.get("paying_contacts") else "—", "cell_num"))
            data.append(row_data)
        if show_customers:
            col_w = [CONTENT_W * 0.42, CONTENT_W * 0.16, CONTENT_W * 0.16, CONTENT_W * 0.14, CONTENT_W * 0.12]
        else:
            col_w = [CONTENT_W * 0.46, CONTENT_W * 0.18, CONTENT_W * 0.18, CONTENT_W * 0.18]

    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), meta["bg"]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, meta["color"]),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, PANEL]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t


def _tier_section_header(tier_key: str, summary: str) -> list:
    meta = TIER_META[tier_key]
    title_inner = [[
        _p(f'<font color="#{meta["color"].hexval()[2:]}"><b>{meta["icon"]}</b></font>  <b>{meta["label"]}</b>'
           f'   <font color="#94A3B8" size=8>{meta["subtitle"]}</font>',
           "body"),
    ]]
    title = Table(title_inner, colWidths=[CONTENT_W])
    title.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story = [title]
    if summary:
        story.append(_p(summary, "muted"))
    story.append(Spacer(1, 4))
    return story


def _build_tiers_pages(by_tier: dict[str, list[dict]]) -> list:
    story = []
    story.append(_p("Performance Tiers", "h2"))
    story.append(Spacer(1, 6))

    order = ["high_roas", "medium_roas", "low_roas", "no_roas", "untapped"]
    for tier in order:
        rows = by_tier.get(tier, [])
        if not rows:
            continue
        # Compute tier summary line
        if tier == "high_roas":
            spend = sum(r.get("spend", 0) for r in rows)
            ltv = sum(r.get("total_ltv", 0) for r in rows)
            avg_roas = ltv / spend if spend > 0 else 0
            summary_line = f"{_money(ltv)} LTV on {_money(spend)} spent · Avg {avg_roas:.1f}× ROAS · {len(rows)} states"
        elif tier == "medium_roas":
            spend = sum(r.get("spend", 0) for r in rows)
            ltv = sum(r.get("total_ltv", 0) for r in rows)
            avg_roas = ltv / spend if spend > 0 else 0
            summary_line = f"{_money(ltv)} LTV on {_money(spend)} spent · Avg {avg_roas:.1f}× ROAS · {len(rows)} states"
        elif tier == "low_roas":
            spend = sum(r.get("spend", 0) for r in rows)
            ltv = sum(r.get("total_ltv", 0) for r in rows)
            loss = max(spend - ltv, 0)
            summary_line = f"{_money(ltv)} LTV on {_money(spend)} spent · Losing {_money(loss)} below 1× return · {len(rows)} states"
        elif tier == "no_roas":
            spend = sum(r.get("spend", 0) for r in rows)
            summary_line = f"{len(rows)} states · {_money(spend)} spent · No paying customers"
        else:
            summary_line = "Existing customers, minimal current spend"

        section: list = []
        section.extend(_tier_section_header(tier, summary_line))
        tbl = _tier_table(rows, tier)
        if tbl:
            section.append(tbl)
        section.append(Spacer(1, 10))
        story.append(KeepTogether(section))

    return story


def _build_actions_page(narrative: dict, by_tier: dict) -> list:
    story = []
    story.append(_p("Action Items", "h2"))
    story.append(Spacer(1, 6))

    reall = narrative.get("reallocation") or {}
    tt = narrative.get("tier_totals") or {}

    # Numbered steps
    steps = []
    if tt.get("high_count", 0) > 0:
        steps.append(("01", C_HIGH,
                      f"Scale High ROAS — these {tt['high_count']} states return ≥2× on spend. Lift daily budgets 15-25%."))
    if tt.get("medium_count", 0) > 0:
        steps.append(("02", C_MED,
                      f"Hold Medium ROAS — {tt['medium_count']} states profitable but not yet ready to scale."))
    if (tt.get("low_count", 0) + tt.get("no_roas_count", 0)) > 0:
        total_excl = tt.get("low_count", 0) + tt.get("no_roas_count", 0)
        wasted = (tt.get("low_spend", 0) or 0) + (tt.get("no_roas_spend", 0) or 0)
        steps.append(("03", C_LOW,
                      f"Exclude Low + Zero ROAS — {total_excl} states wasting {_money(wasted)}."))
    if tt.get("untapped_count", 0) > 0:
        steps.append(("04", C_UNT,
                      f"Launch prospecting in {tt['untapped_count']} untapped state(s) — state-targeted campaign + Lookalike 1%."))

    for num, color, text in steps:
        row = Table([[
            _p(f'<font color="#{color.hexval()[2:]}"><b>{num}</b></font>', "body"),
            _p(text, "body"),
        ]], colWidths=[0.35 * inch, CONTENT_W - 0.35 * inch])
        row.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(row)
        story.append(Spacer(1, 6))

    story.append(Spacer(1, 8))

    # Include / Exclude lists
    incl = narrative.get("inclusion_csv") or ""
    incl_count = len(narrative.get("inclusion_state_names") or [])
    excl = narrative.get("exclusion_csv") or ""
    excl_count = len(narrative.get("exclusion_state_names") or [])

    if incl:
        story.append(_render_list_block(
            f"✓ INCLUDE IN ADS — {incl_count} states",
            incl,
            "Paste into Ad Set → Audience → Locations → Include",
            C_HIGH, C_HIGH_BG,
        ))
        story.append(Spacer(1, 10))

    if excl:
        story.append(_render_list_block(
            f"✗ EXCLUDE FROM ADS — {excl_count} states",
            excl,
            "Paste into Ad Set → Audience → Locations → Exclude",
            C_LOW, C_LOW_BG,
        ))

    return story


def _render_list_block(label: str, text: str, hint: str, accent: Color, bg: Color) -> Table:
    inner = [
        [_p(f'<font color="#{accent.hexval()[2:]}"><b>{label}</b></font>', "tag")],
        [_p(text, "mono")],
        [_p(f"<i>{hint}</i>", "muted")],
    ]
    t = Table(inner, colWidths=[CONTENT_W - 4])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 14),
        ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (0, 0), 10),
        ("TOPPADDING", (0, 1), (0, 1), 6),
        ("TOPPADDING", (0, 2), (0, 2), 6),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    return t


def _build_appendix(states: list[dict]) -> list:
    story = []
    story.append(_p("Appendix: Full State Table", "h2"))
    story.append(_p("All 50 states + DC. Sorted by spend, descending.", "muted"))
    story.append(Spacer(1, 6))

    headers = ["State", "Spend", "Impr", "Contacts", "Paying", "Conv", "Revenue", "LTV", "CPA", "ROAS"]
    data: list[list[Any]] = [[_p(h, "cell_head") for h in headers]]

    sorted_rows = sorted(states, key=lambda r: r.get("spend", 0) or 0, reverse=True)
    for r in sorted_rows:
        data.append([
            _p(f"{r.get('state_name')} ({r.get('state')})", "cell"),
            _p(_money(r.get("spend")), "cell_num"),
            _p(_num(r.get("impressions")), "cell_num"),
            _p(_num(r.get("contacts")), "cell_num"),
            _p(_num(r.get("paying_contacts")), "cell_num"),
            _p(_num(r.get("conversions")), "cell_num"),
            _p(_money(r.get("revenue_30d")), "cell_num"),
            _p(_money(r.get("total_ltv")), "cell_num"),
            _p(_money(r.get("cpa")), "cell_num"),
            _p(f"{_state_roas(r):.2f}×" if r.get("spend", 0) > 0 else "—", "cell_num"),
        ])

    col_w = [
        CONTENT_W * 0.18,  # State
        CONTENT_W * 0.09,  # Spend
        CONTENT_W * 0.09,  # Impr
        CONTENT_W * 0.08,  # Contacts
        CONTENT_W * 0.07,  # Paying
        CONTENT_W * 0.07,  # Conv
        CONTENT_W * 0.10,  # Revenue
        CONTENT_W * 0.10,  # LTV
        CONTENT_W * 0.09,  # CPA
        CONTENT_W * 0.13,  # ROAS
    ]
    t = Table(data, colWidths=col_w, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PANEL),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK_MUTED),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, PANEL]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
    ]))
    story.append(t)
    return story


# ── Main entry point ────────────────────────────────────────────────────────

def generate_heatmap_pdf(
    breakdown: dict,
    account_name: str,
    days: int,
) -> bytes:
    """
    Build the PDF for a heat map breakdown payload and return the raw bytes.

    breakdown should be the dict produced by build_geographic_breakdown.
    """
    buffer = io.BytesIO()
    win_label = _window_label(days)
    doc = HeatmapDoc(buffer, account_name=account_name, window_label=win_label)

    states = breakdown.get("states") or []
    summary = breakdown.get("summary") or {}
    narrative = breakdown.get("narrative") or {}

    # Bucket states by tier
    by_tier: dict[str, list[dict]] = {
        "high_roas": [], "medium_roas": [], "low_roas": [], "no_roas": [], "untapped": [],
    }
    for s in states:
        cls = s.get("classification")
        if cls and cls in by_tier:
            by_tier[cls].append(s)

    # Sort each tier sensibly
    by_tier["high_roas"].sort(key=lambda r: _state_roas(r), reverse=True)
    by_tier["medium_roas"].sort(key=lambda r: r.get("total_ltv", 0), reverse=True)
    by_tier["low_roas"].sort(key=lambda r: r.get("spend", 0), reverse=True)
    by_tier["no_roas"].sort(key=lambda r: r.get("spend", 0), reverse=True)
    by_tier["untapped"].sort(key=lambda r: r.get("total_ltv", 0), reverse=True)

    story: list = []
    story.extend(_build_cover(summary, narrative, account_name, win_label))
    story.append(PageBreak())
    story.extend(_build_map_page(states, win_label))
    story.append(PageBreak())
    story.extend(_build_tiers_pages(by_tier))
    story.append(PageBreak())
    story.extend(_build_actions_page(narrative, by_tier))
    story.append(PageBreak())
    story.extend(_build_appendix(states))

    doc.build(story)
    return buffer.getvalue()
