"""
audit_pdf.py — ReportLab PDF generator for Meta ad account audit reports.

Key design rules (see implementation guide):
- Every table cell is a Paragraph, never a raw string
- Column widths sized by content type, always sum <= CONTENT_W
- 7.5pt font in all table cells
- VALIGN TOP on all tables
- Explicit 4-5pt padding on all tables
"""

import io
from datetime import date

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from reportlab.platypus import (
    BaseDocTemplate,
    PageTemplate,
    Frame,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    PageBreak,
    KeepTogether,
)
from reportlab.pdfgen import canvas as rl_canvas


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------
PAGE_W, PAGE_H = letter
MARGIN_L = 0.65 * inch
MARGIN_R = 0.65 * inch
MARGIN_T = 0.55 * inch
MARGIN_B = 0.65 * inch
CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R   # ~7.2 inches / 518 pt


# ---------------------------------------------------------------------------
# Color palette — RDS brand
# ---------------------------------------------------------------------------
NAVY       = HexColor("#0D1F3C")   # primary dark bg
INDIGO     = HexColor("#4F46E5")   # primary accent
GOLD       = HexColor("#C8972A")   # secondary accent
GRAY       = HexColor("#64748B")   # muted / footer text
GRAY_LIGHT = HexColor("#F8FAFC")   # alt table row bg
GRAY_MID   = HexColor("#E2E8F0")   # grid lines
TEXT_DARK  = HexColor("#0F172A")   # body text
AMBER      = HexColor("#D97706")   # warnings
AMBER_BG   = HexColor("#FEF3C7")
AMBER_TEXT = HexColor("#92400E")
GREEN_DARK = HexColor("#065f46")
GREEN_BG   = HexColor("#ecfdf5")
RED_DARK   = HexColor("#991b1b")
RED_BG     = HexColor("#fef2f2")
BLUE_DARK  = HexColor("#1e40af")
BLUE_BG    = HexColor("#eff6ff")


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------
def _s(name, **kw) -> ParagraphStyle:
    return ParagraphStyle(name, **kw)


STYLES = {
    "title": _s("title", fontName="Helvetica-Bold", fontSize=20, leading=26,
                 textColor=white, spaceAfter=4),
    "subtitle": _s("subtitle", fontName="Helvetica", fontSize=10, leading=13,
                    textColor=HexColor("#94A3B8"), spaceAfter=10),
    "cover_label": _s("cover_label", fontName="Helvetica-Bold", fontSize=8,
                       leading=11, textColor=GOLD, spaceAfter=0),
    "h1": _s("h1", fontName="Helvetica-Bold", fontSize=13, leading=16,
              textColor=INDIGO, spaceAfter=4),
    "h2": _s("h2", fontName="Helvetica-Bold", fontSize=11, leading=14,
              textColor=NAVY, spaceAfter=3),
    "h3": _s("h3", fontName="Helvetica-Bold", fontSize=9, leading=12,
              textColor=TEXT_DARK, spaceAfter=2),
    "body": _s("body", fontName="Helvetica", fontSize=9, leading=13,
                textColor=TEXT_DARK, spaceAfter=6),
    "body_sm": _s("body_sm", fontName="Helvetica", fontSize=8, leading=11,
                   textColor=TEXT_DARK),
    "gray": _s("gray", fontName="Helvetica", fontSize=8.5, leading=12,
                textColor=GRAY),
    # Table cells
    "cell": _s("cell", fontName="Helvetica", fontSize=7.5, leading=10,
                textColor=TEXT_DARK),
    "cell_bold": _s("cell_bold", fontName="Helvetica-Bold", fontSize=7.5,
                     leading=10, textColor=TEXT_DARK),
    "cell_hdr": _s("cell_hdr", fontName="Helvetica-Bold", fontSize=7.5,
                    leading=10, textColor=white),
    "cell_num": _s("cell_num", fontName="Helvetica", fontSize=7.5, leading=10,
                    textColor=TEXT_DARK, alignment=TA_RIGHT),
    "cell_num_hdr": _s("cell_num_hdr", fontName="Helvetica-Bold", fontSize=7.5,
                        leading=10, textColor=white, alignment=TA_RIGHT),
    # Finding cards
    "finding_title": _s("finding_title", fontName="Helvetica-Bold", fontSize=9,
                         leading=12, textColor=TEXT_DARK, spaceAfter=2),
    "finding_body": _s("finding_body", fontName="Helvetica", fontSize=8.5,
                        leading=12, textColor=TEXT_DARK),
    "risk_text": _s("risk_text", fontName="Helvetica", fontSize=8.5, leading=12,
                     textColor=AMBER_TEXT),
    # Action items
    "action_num": _s("action_num", fontName="Helvetica-Bold", fontSize=9,
                      leading=13, textColor=INDIGO),
    "action_body": _s("action_body", fontName="Helvetica", fontSize=9,
                       leading=13, textColor=TEXT_DARK, leftIndent=14,
                       firstLineIndent=-14),
    "exec_body": _s("exec_body", fontName="Helvetica", fontSize=9, leading=14,
                     textColor=TEXT_DARK, spaceAfter=6),
    "badge": _s("badge", fontName="Helvetica-Bold", fontSize=10, leading=13,
                 textColor=white),
    "model_label": _s("model_label", fontName="Helvetica-Bold", fontSize=7,
                       leading=9, textColor=white, alignment=TA_RIGHT),
}


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------
def _money(v) -> str:
    try:
        return f"${float(v):,.2f}" if v else "—"
    except (TypeError, ValueError):
        return "—"


def _pct(v) -> str:
    try:
        return f"{float(v):.1f}%" if v else "—"
    except (TypeError, ValueError):
        return "—"


def _num(v) -> str:
    try:
        return f"{int(float(v)):,}" if v else "—"
    except (TypeError, ValueError):
        return "—"


def _f(v, default=0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _trunc(s, n=35) -> str:
    try:
        s = str(s)
        return s[:n] + "…" if len(s) > n else s
    except Exception:
        return "—"


def _safe_xml(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text, style="body") -> Paragraph:
    st = STYLES[style] if isinstance(style, str) else style
    return Paragraph(_safe_xml(str(text) if text else "—"), st)


def _pct_change(new_val, old_val) -> str:
    try:
        n, o = float(new_val), float(old_val)
        if o == 0:
            return "—"
        change = (n - o) / abs(o) * 100
        return f"{'+'if change>=0 else ''}{change:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _delta_color(s: str, higher_is_better=True) -> HexColor:
    if s in ("—", ""):
        return GRAY
    try:
        val = float(s.replace("%", "").replace("+", ""))
        if val > 0:
            return HexColor("#059669") if higher_is_better else RED_DARK
        elif val < 0:
            return RED_DARK if higher_is_better else HexColor("#059669")
        return GRAY
    except (TypeError, ValueError):
        return GRAY


# ---------------------------------------------------------------------------
# Numbered canvas — "Page X of Y" footer
# ---------------------------------------------------------------------------
class NumberedCanvas(rl_canvas.Canvas):
    def __init__(self, *args, **kwargs):
        rl_canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        n = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_footer(n)
            rl_canvas.Canvas.showPage(self)
        rl_canvas.Canvas.save(self)

    def _draw_footer(self, page_count):
        self.saveState()
        self.setFont("Helvetica", 6.5)
        self.setFillColor(GRAY)
        y = MARGIN_B - 18
        self.setStrokeColor(GRAY_MID)
        self.setLineWidth(0.5)
        self.line(MARGIN_L, MARGIN_B - 6, PAGE_W - MARGIN_R, MARGIN_B - 6)
        self.drawString(MARGIN_L, y, "Ronin Data Solutions — Confidential")
        self.drawRightString(PAGE_W - MARGIN_R, y,
                             f"Page {self._pageNumber} of {page_count}")
        self.restoreState()


# ---------------------------------------------------------------------------
# Core table builder
# ---------------------------------------------------------------------------
def make_table(headers: list, rows: list, col_widths: list,
               extra_styles: list = None) -> Table:
    """
    headers: list of strings (will be wrapped in cell_hdr Paragraph)
    rows:    list of lists of Paragraph objects (or plain strings, auto-wrapped)
    col_widths: must sum <= CONTENT_W
    """
    def _wrap(cell, style="cell"):
        if isinstance(cell, Paragraph):
            return cell
        return _p(str(cell) if cell is not None else "—", style)

    hdr_row = [_wrap(h, "cell_hdr") for h in headers]
    data = [hdr_row]
    for row in rows:
        data.append([_wrap(c) for c in row])

    row_bgs = []
    for i in range(1, len(data)):
        bg = white if i % 2 == 1 else GRAY_LIGHT
        row_bgs.append(("BACKGROUND", (0, i), (-1, i), bg))

    cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("LINEBELOW", (0, 0), (-1, 0), 1, INDIGO),
        ("GRID", (0, 0), (-1, -1), 0.3, GRAY_MID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ] + row_bgs + (extra_styles or [])

    t = Table(data, colWidths=col_widths, repeatRows=1)
    t.setStyle(TableStyle(cmds))
    return t


# ---------------------------------------------------------------------------
# Finding card builder (left-colored-border cards)
# ---------------------------------------------------------------------------
def finding_card(item: dict, border_color, bg_color) -> Table:
    parts = []
    title_key = next((k for k in ("finding", "opportunity", "title") if item.get(k)), None)
    if title_key:
        parts.append(_p(item[title_key], "finding_title"))

    if item.get("evidence"):
        parts.append(_p(f"<i>Evidence:</i> {_safe_xml(item['evidence'])}", "finding_body"))

    for key in ("recommendation", "action"):
        if item.get(key):
            parts.append(_p(f"<i>Action:</i> {_safe_xml(item[key])}", "finding_body"))

    for key in ("rationale",):
        if item.get(key):
            parts.append(_p(f"<i>Rationale:</i> {_safe_xml(item[key])}", "finding_body"))

    for key in ("expected_impact", "impact"):
        if item.get(key):
            parts.append(_p(f"<i>Impact:</i> {_safe_xml(item[key])}", "finding_body"))

    if not parts:
        parts.append(_p(str(item), "finding_body"))

    inner = Table([[p] for p in parts], colWidths=[CONTENT_W - 30])
    inner.setStyle(TableStyle([
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))

    content = Table([[inner]], colWidths=[CONTENT_W - 14])
    content.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg_color),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))

    outer = Table([[" ", content]], colWidths=[4, CONTENT_W - 10])
    outer.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), border_color),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return outer


# ---------------------------------------------------------------------------
# Section heading
# ---------------------------------------------------------------------------
def section_header(text: str) -> list:
    return [
        Spacer(1, 0.1 * inch),
        _p(text, "h1"),
        HRFlowable(width="100%", thickness=1, color=INDIGO),
        Spacer(1, 0.05 * inch),
    ]


def sub_header(text: str) -> list:
    return [Spacer(1, 6), _p(text, "h2"), Spacer(1, 4)]


# ---------------------------------------------------------------------------
# Page 1 — Cover + Account Snapshot
# ---------------------------------------------------------------------------
def _page_cover(account_name: str, metrics: dict, prev_report) -> list:
    story = []

    # Dark navy cover block
    cover = Table(
        [
            [_p("META AD ACCOUNT INTELLIGENCE AUDIT", "title")],
            [_p(f"{account_name}  ·  Generated {date.today()}", "subtitle")],
            [_p("POWERED BY RONIN DATA SOLUTIONS", "cover_label")],
        ],
        colWidths=[CONTENT_W],
    )
    cover.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), NAVY),
        ("TOPPADDING", (0, 0), (0, 0), 18),
        ("TOPPADDING", (0, 1), (0, 1), 2),
        ("TOPPADDING", (0, 2), (0, 2), 6),
        ("BOTTOMPADDING", (0, 2), (0, 2), 14),
        ("LEFTPADDING", (0, 0), (-1, -1), 16),
        ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("LINEBELOW", (0, 2), (0, 2), 3, GOLD),
    ]))
    story.append(cover)
    story.append(Spacer(1, 0.15 * inch))

    story += section_header("ACCOUNT SNAPSHOT")

    m = metrics or {}
    imp_7d = _f(m.get("total_impressions_7d"))
    clk_7d = _f(m.get("total_clicks_7d"))
    imp_30d = _f(m.get("total_impressions_30d"))
    clk_30d = _f(m.get("total_clicks_30d"))
    ctr_7d = (clk_7d / imp_7d * 100) if imp_7d else None
    ctr_30d = _f(m.get("avg_ctr_30d")) or ((clk_30d / imp_30d * 100) if imp_30d else None)
    roas_30d = _f(m.get("avg_roas_30d"))

    rows_def = [
        ("Total Spend",      _money(m.get("total_spend_7d")),   _money(m.get("total_spend_30d")),   True,  m.get("total_spend_30d"),  m.get("total_spend_30d")),
        ("Impressions",      _num(imp_7d or None),               _num(imp_30d or None),              True,  imp_30d,                   m.get("total_impressions_30d")),
        ("Clicks",           _num(clk_7d or None),               _num(clk_30d or None),              True,  clk_30d,                   m.get("total_clicks_30d")),
        ("CTR",              _pct(ctr_7d),                       _pct(ctr_30d),                      True,  ctr_30d,                   m.get("avg_ctr_30d")),
        ("Conversions",      _num(m.get("total_conversions_7d")),_num(m.get("total_conversions_30d")),True, m.get("total_conversions_30d"), m.get("total_conversions_30d")),
        ("Avg CPA",          _money(m.get("avg_cpa_30d")),       _money(m.get("avg_cpa_30d")),       False, m.get("avg_cpa_30d"),      m.get("avg_cpa_30d")),
        ("ROAS",             "—" if not roas_30d else f"{roas_30d:.2f}x","—" if not roas_30d else f"{roas_30d:.2f}x", True, roas_30d, m.get("avg_roas_30d")),
        ("Active Campaigns", _num(m.get("campaign_count")),      _num(m.get("campaign_count")),      True,  m.get("campaign_count"),   m.get("campaign_count")),
        ("Active Audiences", _num(m.get("audience_count")),      _num(m.get("audience_count")),      True,  m.get("audience_count"),   m.get("audience_count")),
    ]

    has_prev = prev_report is not None
    pm = {}
    if has_prev:
        try:
            pm = prev_report.metrics or {}
        except Exception:
            has_prev = False

    prev_30d_map = {
        "Total Spend": pm.get("total_spend_30d"),
        "Impressions": pm.get("total_impressions_30d"),
        "Clicks": pm.get("total_clicks_30d"),
        "CTR": pm.get("avg_ctr_30d"),
        "Conversions": pm.get("total_conversions_30d"),
        "Avg CPA": pm.get("avg_cpa_30d"),
        "ROAS": pm.get("avg_roas_30d"),
        "Active Campaigns": pm.get("campaign_count"),
        "Active Audiences": pm.get("audience_count"),
    }

    if has_prev:
        headers = ["Metric", "Last 7 Days", "Last 30 Days", "Δ vs Prev"]
        col_w = [CONTENT_W * 0.33, CONTENT_W * 0.21, CONTENT_W * 0.21, CONTENT_W * 0.25]
    else:
        headers = ["Metric", "Last 7 Days", "Last 30 Days"]
        col_w = [CONTENT_W * 0.38, CONTENT_W * 0.31, CONTENT_W * 0.31]

    rows = []
    for label, v7d, v30d, hib, cur_raw, _ in rows_def:
        if has_prev:
            delta_str = _pct_change(cur_raw, prev_30d_map.get(label))
            dc = _delta_color(delta_str, higher_is_better=hib)
            delta_p = Paragraph(
                _safe_xml(delta_str),
                ParagraphStyle("dlt", parent=STYLES["cell"], textColor=dc,
                               fontName="Helvetica-Bold"),
            )
            rows.append([_p(label, "cell_bold"), _p(v7d, "cell"), _p(v30d, "cell"), delta_p])
        else:
            rows.append([_p(label, "cell_bold"), _p(v7d, "cell"), _p(v30d, "cell")])

    story.append(make_table(headers, rows, col_w))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Page 2 — Executive Brief
# ---------------------------------------------------------------------------
def _page_executive_brief(analyses: dict, metrics: dict) -> list:
    story = []

    analysis = None
    model_label = ""
    for model_name, ana in (analyses or {}).items():
        if isinstance(ana, dict) and "error" not in ana:
            analysis = ana
            model_label = model_name
            break

    if not analysis:
        return story

    # Model color chip
    model_color = INDIGO
    if "openai" in model_label.lower() or "gpt" in model_label.lower():
        model_color = HexColor("#059669")
    elif "opus" in model_label.lower():
        model_color = HexColor("#7c3aed")

    badge = Table(
        [[_p(f"{model_label.upper()} — EXECUTIVE BRIEF", "badge")]],
        colWidths=[CONTENT_W],
    )
    badge.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), model_color),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, 0), (0, 0), 8),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (0, 0), (0, 0), 12),
    ]))
    story.append(badge)
    story.append(Spacer(1, 0.1 * inch))

    # Executive Summary
    summary = analysis.get("executive_summary", "")
    if summary:
        story += section_header("EXECUTIVE SUMMARY")
        story.append(_p(summary, "exec_body"))

    # Priority Actions
    priority = analysis.get("priority_actions", [])
    if priority:
        story += section_header("PRIORITY ACTIONS")
        for i, action in enumerate(priority, start=1):
            action_text = str(action) if isinstance(action, str) else (action.get("action") or str(action))
            story.append(Paragraph(
                f"<b>{i}.</b>  {_safe_xml(action_text)}",
                STYLES["action_body"],
            ))
            story.append(Spacer(1, 4))

    # Risk Flags
    risks = analysis.get("risk_flags", [])
    if risks:
        story += section_header("RISK FLAGS")
        for risk in risks:
            risk_text = str(risk) if isinstance(risk, str) else (risk.get("flag") or risk.get("risk") or str(risk))
            card = Table(
                [[_p(f"⚠  {_safe_xml(risk_text)}", "risk_text")]],
                colWidths=[CONTENT_W],
            )
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), AMBER_BG),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (0, 0), (0, 0), 10),
            ]))
            story.append(card)
            story.append(Spacer(1, 4))

    # Campaign Verdicts (compact)
    verdicts = analysis.get("campaign_by_campaign", [])
    if verdicts:
        story += section_header("CAMPAIGN VERDICTS")
        verdict_colors = {
            "strong":        (HexColor("#dcfce7"), HexColor("#166534")),
            "decent":        (BLUE_BG,             BLUE_DARK),
            "underperforming":(AMBER_BG,            AMBER_TEXT),
            "critical":      (RED_BG,               RED_DARK),
        }
        headers = ["Campaign", "Verdict", "Key Recommendation"]
        col_w = [CONTENT_W * 0.32, CONTENT_W * 0.13, CONTENT_W * 0.55]
        rows = []
        extra = []
        for ri, v in enumerate(verdicts, start=1):
            vstr = str(v.get("verdict", "")).lower()
            bg, tc = verdict_colors.get(vstr, (white, TEXT_DARK))
            verdict_p = Paragraph(
                _safe_xml(_trunc(v.get("verdict", "—"), 18)),
                ParagraphStyle("vp", parent=STYLES["cell"], textColor=tc,
                               fontName="Helvetica-Bold"),
            )
            rows.append([
                _p(_trunc(v.get("campaign", "—"), 40), "cell_bold"),
                verdict_p,
                _p(_trunc(v.get("recommendation", "—"), 80), "cell"),
            ])
            extra.append(("BACKGROUND", (1, ri), (1, ri), bg))
        story.append(make_table(headers, rows, col_w, extra))

    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Campaign Performance (30d)
# ---------------------------------------------------------------------------
def _page_campaigns(raw_metrics: dict) -> list:
    story = []
    story += section_header("CAMPAIGN PERFORMANCE — LAST 30 DAYS")

    try:
        campaigns = raw_metrics["windows"]["30d"]["campaigns"]
    except (KeyError, TypeError):
        campaigns = []

    if not campaigns:
        story.append(_p("No campaign data available.", "gray"))
        story.append(PageBreak())
        return story

    campaigns = sorted(campaigns, key=lambda c: _f(c.get("spend")), reverse=True)

    headers = ["Campaign", "Objective", "Spend", "Impr", "Clicks", "CTR",
               "Conv", "CPA", "ROAS", "Freq", "F.7d"]
    col_w = [
        1.50 * inch,  # campaign name
        0.85 * inch,  # objective
        0.58 * inch,  # spend
        0.55 * inch,  # impressions
        0.46 * inch,  # clicks
        0.40 * inch,  # CTR
        0.40 * inch,  # conv
        0.48 * inch,  # CPA
        0.42 * inch,  # ROAS
        0.36 * inch,  # freq
        0.36 * inch,  # freq 7d
    ]
    # sanity: total = ~6.36 inches < CONTENT_W ~7.2 inches — good

    rows = []
    extra = []
    for ri, c in enumerate(campaigns, start=1):
        freq_30 = _f(c.get("frequency"))
        freq_7  = _f(c.get("frequency_7d"))
        roas    = _f(c.get("roas"))
        impr    = _f(c.get("impressions"))
        clks    = _f(c.get("clicks"))
        ctr     = (clks / impr * 100) if impr else None
        conv    = c.get("primary_action_count") or c.get("conversions")
        cpa     = c.get("primary_action_cost") or c.get("cpa")

        f7_str  = f"{freq_7:.1f}" if freq_7 else "—"
        roas_str = f"{roas:.2f}x" if roas else "—"

        rows.append([
            _p(_trunc(c.get("name", "—"), 38), "cell_bold"),
            _p(_trunc(c.get("objective", "—"), 18), "cell"),
            _p(_money(c.get("spend")), "cell_num"),
            _p(_num(impr or None), "cell_num"),
            _p(_num(clks or None), "cell_num"),
            _p(_pct(ctr), "cell_num"),
            _p(_num(conv), "cell_num"),
            _p(_money(cpa), "cell_num"),
            _p(roas_str, "cell_num"),
            _p(f"{freq_30:.1f}" if freq_30 else "—", "cell_num"),
            _p(f7_str, "cell_num"),
        ])
        if freq_7 > 5.0:
            extra.append(("BACKGROUND", (10, ri), (10, ri), RED_BG))
        elif freq_7 > 3.0:
            extra.append(("BACKGROUND", (10, ri), (10, ri), AMBER_BG))

    story.append(make_table(headers, rows, col_w, extra))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Ad Set Performance (top 20, 30d)
# ---------------------------------------------------------------------------
def _page_adsets(raw_metrics: dict) -> list:
    story = []
    story += section_header("TOP AD SETS — LAST 30 DAYS")

    try:
        adsets = raw_metrics["windows"]["30d"]["adsets"]
    except (KeyError, TypeError):
        adsets = []

    if not adsets:
        story.append(_p("No ad set data available.", "gray"))
        story.append(PageBreak())
        return story

    adsets = sorted(adsets, key=lambda a: _f(a.get("spend")), reverse=True)[:20]

    headers = ["Ad Set", "Campaign", "Spend", "Clicks", "CTR", "Conv", "CPA", "Freq", "F.7d"]
    col_w = [
        1.55 * inch,  # ad set name
        1.45 * inch,  # campaign name
        0.58 * inch,  # spend
        0.46 * inch,  # clicks
        0.40 * inch,  # CTR
        0.40 * inch,  # conv
        0.48 * inch,  # CPA
        0.36 * inch,  # freq
        0.36 * inch,  # freq 7d
    ]

    rows = []
    extra = []
    for ri, a in enumerate(adsets, start=1):
        freq_7  = _f(a.get("frequency_7d"))
        freq_30 = _f(a.get("frequency"))
        impr    = _f(a.get("impressions"))
        clks    = _f(a.get("clicks"))
        ctr     = (clks / impr * 100) if impr else None
        conv    = a.get("primary_action_count") or a.get("conversions")
        cpa     = a.get("primary_action_cost") or a.get("cpa")

        rows.append([
            _p(_trunc(a.get("name", "—"), 38), "cell_bold"),
            _p(_trunc(a.get("campaign_name", "—"), 35), "cell"),
            _p(_money(a.get("spend")), "cell_num"),
            _p(_num(clks or None), "cell_num"),
            _p(_pct(ctr), "cell_num"),
            _p(_num(conv), "cell_num"),
            _p(_money(cpa), "cell_num"),
            _p(f"{freq_30:.1f}" if freq_30 else "—", "cell_num"),
            _p(f"{freq_7:.1f}" if freq_7 else "—", "cell_num"),
        ])
        if freq_7 > 5.0:
            extra.append(("BACKGROUND", (8, ri), (8, ri), RED_BG))
        elif freq_7 > 3.0:
            extra.append(("BACKGROUND", (8, ri), (8, ri), AMBER_BG))

    story.append(make_table(headers, rows, col_w, extra))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Ad Creative Performance (top 20, 30d)
# ---------------------------------------------------------------------------
def _page_ads(raw_metrics: dict) -> list:
    story = []
    story += section_header("TOP ADS — LAST 30 DAYS")

    try:
        ads = raw_metrics["windows"]["30d"]["ads"]
    except (KeyError, TypeError):
        ads = []

    if not ads:
        story.append(_p("No ad data available.", "gray"))
        story.append(PageBreak())
        return story

    ads = sorted(ads, key=lambda a: _f(a.get("spend")), reverse=True)[:20]

    headers = ["Ad Name", "Format", "Spend", "Impr", "CTR", "Conv", "CPA", "Freq"]
    col_w = [
        2.00 * inch,  # ad name
        0.75 * inch,  # format
        0.58 * inch,  # spend
        0.58 * inch,  # impressions
        0.42 * inch,  # CTR
        0.42 * inch,  # conv
        0.52 * inch,  # CPA
        0.38 * inch,  # freq
    ]

    rows = []
    for a in ads:
        impr  = _f(a.get("impressions"))
        clks  = _f(a.get("clicks"))
        ctr   = (clks / impr * 100) if impr else None
        conv  = a.get("primary_action_count") or a.get("conversions")
        cpa   = a.get("primary_action_cost") or a.get("cpa")
        freq  = _f(a.get("frequency"))
        try:
            fmt = a.get("creative", {}).get("format") or "—"
        except Exception:
            fmt = "—"

        rows.append([
            _p(_trunc(a.get("name", "—"), 42), "cell_bold"),
            _p(_trunc(fmt, 18), "cell"),
            _p(_money(a.get("spend")), "cell_num"),
            _p(_num(impr or None), "cell_num"),
            _p(_pct(ctr), "cell_num"),
            _p(_num(conv), "cell_num"),
            _p(_money(cpa), "cell_num"),
            _p(f"{freq:.1f}" if freq else "—", "cell_num"),
        ])

    story.append(make_table(headers, rows, col_w))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Placement Breakdown (30d)
# ---------------------------------------------------------------------------
def _page_placements(raw_metrics: dict) -> list:
    story = []
    story += section_header("PERFORMANCE BY PLACEMENT")

    try:
        placements = raw_metrics["breakdowns_30d"]["by_placement"]
    except (KeyError, TypeError):
        placements = []

    if not placements:
        story.append(_p("No placement breakdown available.", "gray"))
        story.append(PageBreak())
        return story

    total_spend = sum(_f(p.get("spend")) for p in placements) or 1.0

    cpas = [((_f(p.get("cpa") or p.get("primary_action_cost"))), i + 1)
            for i, p in enumerate(placements)
            if _f(p.get("cpa") or p.get("primary_action_cost")) > 0]
    best_row  = min(cpas, key=lambda x: x[0])[1] if cpas else None
    worst_row = max(cpas, key=lambda x: x[0])[1] if cpas else None

    headers = ["Platform", "Position", "Spend", "% Spd", "Impr", "Clicks", "CTR", "Conv", "CPA"]
    col_w = [
        0.90 * inch,  # platform
        1.10 * inch,  # position
        0.58 * inch,  # spend
        0.50 * inch,  # % spend
        0.58 * inch,  # impressions
        0.50 * inch,  # clicks
        0.42 * inch,  # CTR
        0.42 * inch,  # conv
        0.60 * inch,  # CPA
    ]

    rows = []
    extra = []
    for ri, p in enumerate(placements, start=1):
        spend = _f(p.get("spend"))
        impr  = _f(p.get("impressions"))
        clks  = _f(p.get("clicks"))
        ctr   = (clks / impr * 100) if impr else None
        conv  = p.get("conversions") or p.get("primary_action_count")
        cpa   = p.get("cpa") or p.get("primary_action_cost")
        pct   = spend / total_spend * 100

        rows.append([
            _p(_trunc(p.get("publisher_platform", "—"), 20), "cell_bold"),
            _p(_trunc(p.get("platform_position", "—"), 28), "cell"),
            _p(_money(spend or None), "cell_num"),
            _p(_pct(pct), "cell_num"),
            _p(_num(impr or None), "cell_num"),
            _p(_num(clks or None), "cell_num"),
            _p(_pct(ctr), "cell_num"),
            _p(_num(conv), "cell_num"),
            _p(_money(cpa), "cell_num"),
        ])
        if ri == best_row:
            extra.append(("BACKGROUND", (0, ri), (-1, ri), GREEN_BG))
        elif ri == worst_row:
            extra.append(("BACKGROUND", (0, ri), (-1, ri), RED_BG))

    story.append(make_table(headers, rows, col_w, extra))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Demographic Breakdown (30d)
# ---------------------------------------------------------------------------
def _page_demographics(raw_metrics: dict) -> list:
    story = []
    story += section_header("PERFORMANCE BY DEMOGRAPHIC")

    try:
        demos = raw_metrics["breakdowns_30d"]["by_demographic"]
    except (KeyError, TypeError):
        demos = []

    if not demos:
        story.append(_p("No demographic breakdown available.", "gray"))
        story.append(PageBreak())
        return story

    demos = sorted(demos, key=lambda d: _f(d.get("spend")), reverse=True)[:15]
    total_spend = sum(_f(d.get("spend")) for d in demos) or 1.0

    headers = ["Age", "Gender", "Spend", "% Spd", "Clicks", "Conv", "CPA"]
    col_w = [
        0.70 * inch,  # age
        0.70 * inch,  # gender
        0.75 * inch,  # spend
        0.60 * inch,  # % spend
        0.70 * inch,  # clicks
        0.60 * inch,  # conv
        0.85 * inch,  # CPA
    ]

    rows = []
    for d in demos:
        spend = _f(d.get("spend"))
        pct   = spend / total_spend * 100
        conv  = d.get("conversions") or d.get("primary_action_count")
        cpa   = d.get("cpa") or d.get("primary_action_cost")

        rows.append([
            _p(str(d.get("age", "—")), "cell"),
            _p(str(d.get("gender", "—")), "cell"),
            _p(_money(spend or None), "cell_num"),
            _p(_pct(pct), "cell_num"),
            _p(_num(d.get("clicks")), "cell_num"),
            _p(_num(conv), "cell_num"),
            _p(_money(cpa), "cell_num"),
        ])

    story.append(make_table(headers, rows, col_w))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# Geographic Breakdown (US states, 30d)
# ---------------------------------------------------------------------------
def _page_geographic(raw_metrics: dict) -> list:
    story = []
    story += section_header("PERFORMANCE BY US STATE")

    geo = None
    try:
        geo = raw_metrics.get("business_context", {}).get("geographic_breakdown")
    except (KeyError, TypeError, AttributeError):
        geo = None

    if not geo or not geo.get("states"):
        story.append(_p("No geographic breakdown available. Requires GHL contacts with state data and Meta US-region insights.", "gray"))
        story.append(PageBreak())
        return story

    states = geo["states"]
    summary = geo.get("summary", {})

    # Summary line
    avg_cpa = summary.get("account_avg_cpa")
    summary_text = (
        f"30d spend across {summary.get('states_with_spend', 0)} states · "
        f"{summary.get('states_with_contacts', 0)} states with GHL contacts · "
        f"{summary.get('states_with_conversions', 0)} states with matched conversions"
    )
    if avg_cpa:
        summary_text += f" · account avg CPA ${avg_cpa:,.2f}"
    story.append(_p(summary_text, "gray"))
    story.append(Spacer(1, 8))

    # Top wasted / opportunity callouts as side-by-side cards
    wasted = summary.get("wasted", [])
    opportunity = summary.get("opportunity", [])

    if wasted or opportunity:
        if wasted:
            story.append(_p("<b>Wasted spend (high spend, low conversion):</b>", "body"))
            for w in wasted:
                line = (
                    f"&nbsp;&nbsp;• {w['state_name']} ({w['state']}): "
                    f"${w['spend']:,.0f} spent → {w['conversions']} conversion(s)"
                )
                if w.get("cpa"):
                    line += f", CPA ${w['cpa']:,.0f}"
                story.append(_p(line, "body"))
            story.append(Spacer(1, 6))

        if opportunity:
            story.append(_p("<b>Underspent opportunities (low spend, high conversion rate):</b>", "body"))
            for o in opportunity:
                line = (
                    f"&nbsp;&nbsp;• {o['state_name']} ({o['state']}): "
                    f"${o['spend']:,.0f} spent → {o['conversions']} conversion(s), "
                    f"{o['conversion_rate_pct']}% conv rate"
                )
                story.append(_p(line, "body"))
            story.append(Spacer(1, 8))

    # Top-N table — show top 20 by spend
    top_states = states[:20]

    headers = ["State", "Spend", "Impr", "Clicks", "Contacts", "Conv", "Rev", "CPA", "Class"]
    col_w = [
        0.95 * inch,  # state
        0.75 * inch,  # spend
        0.70 * inch,  # impressions
        0.65 * inch,  # clicks
        0.70 * inch,  # contacts
        0.55 * inch,  # conv
        0.70 * inch,  # revenue
        0.70 * inch,  # cpa
        0.80 * inch,  # classification
    ]

    rows = []
    class_label = {
        "wasted": "Wasted",
        "opportunity": "Opportunity",
        "working": "Working",
    }
    for s in top_states:
        cls = class_label.get(s.get("classification") or "", "—")
        rows.append([
            _p(f"{s['state_name'] or '—'} ({s['state']})", "cell"),
            _p(_money(s.get("spend")), "cell_num"),
            _p(_num(s.get("impressions")), "cell_num"),
            _p(_num(s.get("clicks")), "cell_num"),
            _p(_num(s.get("contacts")), "cell_num"),
            _p(_num(s.get("conversions")), "cell_num"),
            _p(_money(s.get("revenue")), "cell_num"),
            _p(_money(s.get("cpa")), "cell_num"),
            _p(cls, "cell"),
        ])

    story.append(make_table(headers, rows, col_w))
    story.append(PageBreak())
    return story


# ---------------------------------------------------------------------------
# AI Analysis — full detail
# ---------------------------------------------------------------------------
def _render_analysis_section(analysis: dict) -> list:
    story = []

    def _items_as_cards(items, border_color, bg_color):
        out = []
        for item in items:
            if isinstance(item, str):
                item = {"finding": item}
            out.append(finding_card(item, border_color, bg_color))
            out.append(Spacer(1, 6))
        return out

    # Executive Summary (repeated for reference)
    summary = analysis.get("executive_summary", "")
    if summary:
        story += section_header("EXECUTIVE SUMMARY")
        story.append(_p(summary, "exec_body"))

    # Campaign Verdicts
    verdicts = analysis.get("campaign_by_campaign", [])
    if verdicts:
        story += section_header("CAMPAIGN VERDICTS")
        verdict_colors = {
            "strong":         (HexColor("#dcfce7"), HexColor("#166534")),
            "decent":         (BLUE_BG,             BLUE_DARK),
            "underperforming":(AMBER_BG,             AMBER_TEXT),
            "critical":       (RED_BG,               RED_DARK),
        }
        headers = ["Campaign", "Objective", "Verdict", "Recommendation"]
        col_w = [CONTENT_W * 0.28, CONTENT_W * 0.14, CONTENT_W * 0.13, CONTENT_W * 0.45]
        rows = []
        extra = []
        for ri, v in enumerate(verdicts, start=1):
            vstr = str(v.get("verdict", "")).lower()
            bg, tc = verdict_colors.get(vstr, (white, TEXT_DARK))
            verdict_p = Paragraph(
                _safe_xml(_trunc(v.get("verdict", "—"), 20)),
                ParagraphStyle("vp2", parent=STYLES["cell"], textColor=tc,
                               fontName="Helvetica-Bold"),
            )
            rows.append([
                _p(_trunc(v.get("campaign", "—"), 38), "cell_bold"),
                _p(_trunc(v.get("objective", "—"), 20), "cell"),
                verdict_p,
                _p(_trunc(v.get("recommendation", "—"), 80), "cell"),
            ])
            extra.append(("BACKGROUND", (2, ri), (2, ri), bg))
        story.append(make_table(headers, rows, col_w, extra))
        story.append(Spacer(1, 0.08 * inch))

    # What's Working
    working = analysis.get("whats_working", [])
    if working:
        story += section_header("WHAT'S WORKING")
        story += _items_as_cards(working, GREEN_DARK, GREEN_BG)

    # What's Not Working
    not_working = analysis.get("whats_not_working", [])
    if not_working:
        story += section_header("WHAT'S NOT WORKING")
        story += _items_as_cards(not_working, RED_DARK, RED_BG)

    # Opportunities
    opps = analysis.get("opportunities", [])
    if opps:
        story += section_header("OPPORTUNITIES")
        for item in opps:
            if isinstance(item, str):
                item = {"opportunity": item}
            story.append(finding_card(item, BLUE_DARK, BLUE_BG))
            story.append(Spacer(1, 6))

    # Creative Analysis
    creative = analysis.get("creative_analysis", {})
    if creative:
        story += section_header("CREATIVE ANALYSIS")
        if creative.get("summary"):
            story.append(_p(creative["summary"], "exec_body"))

        by_format = creative.get("performance_by_format", [])
        if by_format:
            story += sub_header("Performance by Format")
            headers = ["Format", "Ads", "Spend", "Avg CTR", "Avg CPA", "Assessment"]
            col_w = [0.90*inch, 0.38*inch, 0.60*inch, 0.60*inch, 0.60*inch, 3.52*inch]
            rows = [[
                _p(_trunc(f.get("format", "—"), 20), "cell_bold"),
                _p(_num(f.get("ads") or f.get("count")), "cell_num"),
                _p(_money(f.get("spend")), "cell_num"),
                _p(_pct(f.get("avg_ctr")), "cell_num"),
                _p(_money(f.get("avg_cpa")), "cell_num"),
                _p(_trunc(f.get("assessment", "—"), 80), "cell"),
            ] for f in by_format]
            story.append(make_table(headers, rows, col_w))
            story.append(Spacer(1, 6))

        fatigue = creative.get("fatigue_signals", [])
        if fatigue:
            story += sub_header("Fatigue Signals")
            for sig in fatigue:
                sig_text = str(sig) if isinstance(sig, str) else (sig.get("signal") or sig.get("description") or str(sig))
                card = Table([[_p(f"⚠  {_safe_xml(sig_text)}", "risk_text")]],
                             colWidths=[CONTENT_W])
                card.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (0, 0), AMBER_BG),
                    ("TOPPADDING", (0, 0), (0, 0), 5),
                    ("BOTTOMPADDING", (0, 0), (0, 0), 5),
                    ("LEFTPADDING", (0, 0), (0, 0), 10),
                    ("RIGHTPADDING", (0, 0), (0, 0), 10),
                ]))
                story.append(card)
                story.append(Spacer(1, 3))

        recs = creative.get("recommendations", [])
        if recs:
            story += sub_header("Recommendations")
            for i, rec in enumerate(recs, start=1):
                rec_text = str(rec) if isinstance(rec, str) else (rec.get("recommendation") or str(rec))
                story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(rec_text)}", STYLES["action_body"]))
                story.append(Spacer(1, 3))

    # Placement Analysis
    placement = analysis.get("placement_analysis", {})
    if placement:
        story += section_header("PLACEMENT ANALYSIS")
        if placement.get("summary"):
            story.append(_p(placement["summary"], "exec_body"))
        for label, key in [("Top Performers", "top_performers"), ("Underperformers", "underperformers")]:
            items = placement.get(key, [])
            if items:
                story += sub_header(label)
                for tp in items:
                    if isinstance(tp, str):
                        tp = {"placement": tp}
                    text = tp.get("placement") or tp.get("platform") or str(tp)
                    detail = tp.get("detail", "") if isinstance(tp, dict) else ""
                    body = f"{_safe_xml(text)}"
                    if detail:
                        body += f" — {_safe_xml(detail)}"
                    story.append(_p(body, "body_sm"))
                    story.append(Spacer(1, 3))
        pl_recs = placement.get("recommendations", [])
        if pl_recs:
            story += sub_header("Recommendations")
            for i, rec in enumerate(pl_recs, start=1):
                rec_text = str(rec) if isinstance(rec, str) else (rec.get("recommendation") or str(rec))
                story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(rec_text)}", STYLES["action_body"]))
                story.append(Spacer(1, 3))

    # Demographic Analysis
    demo_analysis = analysis.get("demographic_analysis", {})
    if demo_analysis:
        story += section_header("DEMOGRAPHIC ANALYSIS")
        if demo_analysis.get("summary"):
            story.append(_p(demo_analysis["summary"], "exec_body"))
        for label, key in [("Top Segments", "top_segments"), ("Wasted Spend Segments", "wasted_spend_segments")]:
            items = demo_analysis.get(key, [])
            if items:
                story += sub_header(label)
                for seg in items:
                    if isinstance(seg, str):
                        seg = {"segment": seg}
                    text = seg.get("segment") or str(seg)
                    detail = seg.get("detail", "") if isinstance(seg, dict) else ""
                    body = _safe_xml(text)
                    if detail:
                        body += f" — {_safe_xml(detail)}"
                    story.append(_p(body, "body_sm"))
                    story.append(Spacer(1, 3))
        da_recs = demo_analysis.get("recommendations", [])
        if da_recs:
            story += sub_header("Recommendations")
            for i, rec in enumerate(da_recs, start=1):
                rec_text = str(rec) if isinstance(rec, str) else (rec.get("recommendation") or str(rec))
                story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(rec_text)}", STYLES["action_body"]))
                story.append(Spacer(1, 3))

    # Audience Analysis
    audience = analysis.get("audience_analysis", "")
    if audience:
        story += section_header("AUDIENCE ANALYSIS")
        text = audience if isinstance(audience, str) else audience.get("summary", str(audience))
        story.append(_p(text, "exec_body"))

    # Budget Allocation
    budget = analysis.get("budget_allocation", {})
    if budget:
        story += section_header("BUDGET ALLOCATION")
        if budget.get("summary"):
            story.append(_p(budget["summary"], "exec_body"))
        if budget.get("current_split"):
            story.append(_p(f"Current Split: {budget['current_split']}", "body_sm"))
            story.append(Spacer(1, 4))
        for i, change in enumerate(budget.get("recommended_changes", []), start=1):
            ct = str(change) if isinstance(change, str) else (change.get("change") or str(change))
            story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(ct)}", STYLES["action_body"]))
            story.append(Spacer(1, 3))
        if budget.get("estimated_impact"):
            story.append(_p(f"Estimated Impact: {budget['estimated_impact']}", "body_sm"))

    # Trend Analysis
    trends = analysis.get("trend_analysis", {})
    if trends:
        story += section_header("TREND ANALYSIS")
        for label, key in [("7-Day vs 30-Day", "seven_vs_thirty"),
                           ("30 vs 60 vs 90 Days", "thirty_vs_sixty_vs_ninety"),
                           ("Frequency Trends", "frequency_trends")]:
            val = trends.get(key, "")
            if val:
                story += sub_header(label)
                story.append(_p(str(val), "body_sm"))
                story.append(Spacer(1, 4))

    # Risk Flags
    risks = analysis.get("risk_flags", [])
    if risks:
        story += section_header("RISK FLAGS")
        for risk in risks:
            risk_text = str(risk) if isinstance(risk, str) else (risk.get("flag") or risk.get("risk") or str(risk))
            card = Table([[_p(f"⚠  {_safe_xml(risk_text)}", "risk_text")]],
                         colWidths=[CONTENT_W])
            card.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), AMBER_BG),
                ("TOPPADDING", (0, 0), (0, 0), 6),
                ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("RIGHTPADDING", (0, 0), (0, 0), 10),
            ]))
            story.append(card)
            story.append(Spacer(1, 4))

    # Priority Actions
    priority = analysis.get("priority_actions", [])
    if priority:
        story += section_header("PRIORITY ACTIONS")
        for i, action in enumerate(priority, start=1):
            action_text = str(action) if isinstance(action, str) else (action.get("action") or str(action))
            story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(action_text)}", STYLES["action_body"]))
            story.append(Spacer(1, 4))

    # 30-Day Projection
    projection = analysis.get("projection_30d", {})
    if projection and isinstance(projection, dict):
        story += section_header("30-DAY PROJECTION")

        trajectory = str(projection.get("trajectory", "")).lower()
        if trajectory:
            traj_colors = {
                "improving": (GREEN_BG, GREEN_DARK),
                "declining":  (RED_BG,   RED_DARK),
                "stable":     (BLUE_BG,  BLUE_DARK),
                "volatile":   (AMBER_BG, AMBER_TEXT),
            }
            bg, tc = traj_colors.get(trajectory, (GRAY_LIGHT, GRAY))
            traj_p = Paragraph(
                trajectory.upper(),
                ParagraphStyle("traj", parent=STYLES["cell_bold"], textColor=tc),
            )
            traj_badge = Table([[traj_p]], colWidths=[1.2 * inch])
            traj_badge.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), bg),
                ("TOPPADDING", (0, 0), (0, 0), 4),
                ("BOTTOMPADDING", (0, 0), (0, 0), 4),
                ("LEFTPADDING", (0, 0), (0, 0), 8),
                ("RIGHTPADDING", (0, 0), (0, 0), 8),
            ]))
            story.append(traj_badge)
            story.append(Spacer(1, 6))

        if projection.get("summary"):
            story.append(_p(projection["summary"], "exec_body"))

        proj_rows = []
        for label, key, fmt_fn in [
            ("Projected Spend (30d)",       "projected_spend",       _money),
            ("Projected Conversions (30d)", "projected_conversions", _num),
            ("Projected CPA",               "projected_cpa",         _money),
            ("Projected ROAS",              "projected_roas",        lambda v: f"{_f(v):.2f}x" if v else "—"),
        ]:
            val = projection.get(key)
            if val is not None:
                proj_rows.append([_p(label, "cell_bold"), _p(fmt_fn(val), "cell_num")])

        if proj_rows:
            tbl = Table(proj_rows, colWidths=[CONTENT_W * 0.55, CONTENT_W * 0.45])
            tbl.setStyle(TableStyle([
                ("GRID",         (0, 0), (-1, -1), 0.3, GRAY_MID),
                ("BACKGROUND",   (0, 0), (-1, -1), GRAY_LIGHT),
                ("TOPPADDING",   (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
                ("LEFTPADDING",  (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN",       (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(tbl)
            story.append(Spacer(1, 6))

        drivers = projection.get("key_drivers", [])
        if drivers:
            story += sub_header("Key Drivers")
            for d in drivers:
                story.append(_p(f"• {_safe_xml(str(d))}", "body_sm"))
                story.append(Spacer(1, 2))

        for label, key in [("Upside Scenario", "upside_scenario"), ("Downside Scenario", "downside_scenario")]:
            val = projection.get(key, "")
            if val:
                story += sub_header(label)
                story.append(_p(str(val), "body_sm"))
                story.append(Spacer(1, 4))

        conf = projection.get("confidence", "")
        conf_note = projection.get("confidence_note", "")
        if conf:
            conf_text = f"Confidence: {conf.upper()}"
            if conf_note:
                conf_text += f" — {conf_note}"
            story.append(_p(conf_text, "gray"))

    # Implementation Plan — Next 30 Days
    action_plan = analysis.get("action_plan", {})
    if action_plan and isinstance(action_plan, dict):
        story += section_header("IMPLEMENTATION PLAN — NEXT 30 DAYS")

        if action_plan.get("executive_brief"):
            story.append(_p(action_plan["executive_brief"], "exec_body"))

        campaigns_to_create = action_plan.get("campaigns_to_create", [])
        if campaigns_to_create:
            story += sub_header("Campaigns to Create")
            headers = ["#", "Campaign", "Audience", "Budget", "Expected Result"]
            col_w = [0.25 * inch, 1.50 * inch, 1.80 * inch, 0.70 * inch, 2.75 * inch]
            rows = []
            for c in campaigns_to_create:
                rows.append([
                    _p(str(c.get("priority", "")), "cell_bold"),
                    _p(_trunc(str(c.get("name", "—")), 30), "cell_bold"),
                    _p(_trunc(str(c.get("audience", "—")), 45), "cell"),
                    _p(_trunc(str(c.get("daily_budget", "—")), 12), "cell"),
                    _p(_trunc(str(c.get("expected_result", "—")), 65), "cell"),
                ])
            story.append(make_table(headers, rows, col_w))
            story.append(Spacer(1, 6))

            for c in campaigns_to_create:
                cd = c.get("creative_direction", "")
                if cd:
                    name = c.get("name", "")
                    prefix = f"<b>{_safe_xml(str(name))}:</b> " if name else ""
                    card = Table([[_p(f"{prefix}{_safe_xml(str(cd))}", "body_sm")]],
                                 colWidths=[CONTENT_W])
                    card.setStyle(TableStyle([
                        ("BACKGROUND",    (0, 0), (0, 0), BLUE_BG),
                        ("TOPPADDING",    (0, 0), (0, 0), 6),
                        ("BOTTOMPADDING", (0, 0), (0, 0), 6),
                        ("LEFTPADDING",   (0, 0), (0, 0), 10),
                        ("RIGHTPADDING",  (0, 0), (0, 0), 10),
                    ]))
                    story.append(card)
                    story.append(Spacer(1, 3))

        for section_label, key in [
            ("Campaigns to Cut",    "campaigns_to_cut"),
            ("Audiences to Build",  "audiences_to_build"),
            ("Budget Moves",        "budget_moves"),
        ]:
            items = action_plan.get(key, [])
            if items:
                story += sub_header(section_label)
                for i, item in enumerate(items, start=1):
                    text = str(item)
                    story.append(Paragraph(f"<b>{i}.</b>  {_safe_xml(text)}", STYLES["action_body"]))
                    story.append(Spacer(1, 3))

        week_by_week = action_plan.get("week_by_week", [])
        if week_by_week:
            story += sub_header("Week-by-Week Roadmap")
            headers = ["Week", "Actions"]
            col_w = [0.80 * inch, CONTENT_W - 0.80 * inch]
            rows = []
            for week_data in week_by_week:
                week_label = week_data.get("week", "")
                actions = week_data.get("actions", [])
                actions_text = "  •  ".join(str(a) for a in actions) if actions else "—"
                rows.append([_p(str(week_label), "cell_bold"), _p(actions_text, "cell")])
            story.append(make_table(headers, rows, col_w))

    return story


def _page_ai_analyses(analyses: dict) -> list:
    story = []
    if not analyses:
        return story

    model_colors = {"opus": "#7c3aed", "claude": "#4F46E5", "openai": "#059669", "gpt": "#059669"}
    first = True

    for model_name, analysis in analyses.items():
        try:
            if not isinstance(analysis, dict) or "error" in analysis:
                continue
            if not first:
                story.append(PageBreak())
            first = False

            color_hex = "#4F46E5"
            for key, color in model_colors.items():
                if key in model_name.lower():
                    color_hex = color
                    break

            badge = Table(
                [[_p(f"{model_name.upper()} ANALYSIS", "badge")]],
                colWidths=[CONTENT_W],
            )
            badge.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), HexColor(color_hex)),
                ("TOPPADDING", (0, 0), (0, 0), 8),
                ("BOTTOMPADDING", (0, 0), (0, 0), 8),
                ("LEFTPADDING", (0, 0), (0, 0), 12),
                ("RIGHTPADDING", (0, 0), (0, 0), 12),
            ]))
            story.append(badge)
            story.append(Spacer(1, 0.08 * inch))
            story += _render_analysis_section(analysis)
        except Exception:
            continue

    if not first:
        story.append(PageBreak())

    return story


# ---------------------------------------------------------------------------
# Historical Comparison
# ---------------------------------------------------------------------------
def _page_historical(metrics: dict, prev_report) -> list:
    story = []
    if prev_report is None:
        return story

    try:
        pm = prev_report.metrics or {}
        try:
            prev_date = prev_report.created_at.strftime("%Y-%m-%d")
        except Exception:
            prev_date = "previous report"

        story += section_header("COMPARISON TO PREVIOUS REPORT")
        story.append(_p(f"Comparing to report dated: {prev_date}", "gray"))
        story.append(Spacer(1, 0.08 * inch))

        m = metrics or {}
        comparison_rows = [
            ("Spend (7d)",        m.get("total_spend_7d"),       pm.get("total_spend_7d"),       _money, True),
            ("Spend (30d)",       m.get("total_spend_30d"),      pm.get("total_spend_30d"),      _money, True),
            ("Conversions (7d)",  m.get("total_conversions_7d"), pm.get("total_conversions_7d"), _num,   True),
            ("Conversions (30d)", m.get("total_conversions_30d"),pm.get("total_conversions_30d"),_num,   True),
            ("CPA (30d)",         m.get("avg_cpa_30d"),          pm.get("avg_cpa_30d"),          _money, False),
            ("CTR (30d)",         m.get("avg_ctr_30d"),          pm.get("avg_ctr_30d"),          _pct,   True),
            ("ROAS (30d)",        m.get("avg_roas_30d"),         pm.get("avg_roas_30d"),         lambda v: f"{_f(v):.2f}x" if v else "—", True),
            ("Impressions (30d)", m.get("total_impressions_30d"),pm.get("total_impressions_30d"),_num,   True),
        ]

        headers = ["Metric", "Previous", "Current", "Change %"]
        col_w = [CONTENT_W * 0.35, CONTENT_W * 0.20, CONTENT_W * 0.20, CONTENT_W * 0.25]
        rows = []
        extra = []

        for ri, (label, cur, prev, fmt_fn, hib) in enumerate(comparison_rows, start=1):
            delta_str = _pct_change(cur, prev)
            dc = _delta_color(delta_str, higher_is_better=hib)
            delta_p = Paragraph(
                _safe_xml(delta_str),
                ParagraphStyle("dh", parent=STYLES["cell"], textColor=dc,
                               fontName="Helvetica-Bold"),
            )
            rows.append([
                _p(label, "cell_bold"),
                _p(fmt_fn(prev), "cell_num"),
                _p(fmt_fn(cur), "cell_num"),
                delta_p,
            ])

        story.append(make_table(headers, rows, col_w, extra))
    except Exception:
        story.append(_p("Historical comparison data unavailable.", "gray"))

    return story


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def generate_pdf(
    account_name: str,
    metrics: dict,
    raw_metrics: dict,
    analyses: dict,
    prev_report=None,
) -> bytes:
    buf = io.BytesIO()

    frame = Frame(
        MARGIN_L, MARGIN_B,
        CONTENT_W, PAGE_H - MARGIN_T - MARGIN_B,
        id="main_frame",
        leftPadding=0, rightPadding=0,
        topPadding=0, bottomPadding=0,
    )
    template = PageTemplate(id="main", frames=[frame])
    doc = BaseDocTemplate(
        buf,
        pagesize=letter,
        pageTemplates=[template],
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title=f"Meta Ad Account Audit — {account_name}",
        author="Ronin Data Solutions",
    )

    story = []

    # Page 1 — Cover + Snapshot
    try:
        story += _page_cover(account_name, metrics or {}, prev_report)
    except Exception:
        story.append(PageBreak())

    # Page 2 — Executive Brief (AI summary first)
    if analyses:
        try:
            story += _page_executive_brief(analyses, metrics or {})
        except Exception:
            story.append(PageBreak())

    # Pages 3+ — Data tables
    if raw_metrics:
        for fn in [_page_campaigns, _page_adsets, _page_ads,
                   _page_placements, _page_demographics, _page_geographic]:
            try:
                story += fn(raw_metrics)
            except Exception:
                story.append(PageBreak())

    # Full AI Analyses
    if analyses:
        try:
            story += _page_ai_analyses(analyses)
        except Exception:
            story.append(PageBreak())

    # Historical Comparison
    if prev_report is not None:
        try:
            hist = _page_historical(metrics or {}, prev_report)
            if hist:
                story += hist
        except Exception:
            pass

    if not story:
        story.append(Paragraph("No data available.", STYLES["body"]))

    doc.build(story, canvasmaker=NumberedCanvas)
    buf.seek(0)
    return buf.read()
