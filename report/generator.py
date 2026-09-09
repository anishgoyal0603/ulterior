"""Generates a CCPA-format PDF audit report — the artifact a compliance
officer or regulator would actually review, with evidence linked per
violation, not a bare score."""

from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Default Helvetica has no glyph for ₹ (renders as a black box). DejaVu Sans
# does, and ships on most Linux systems (including Docker python:slim base
# images with fonts-dejavu-core installed) -- register once at import time.
_FONT_REGISTERED = False


def _ensure_font():
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
        _FONT_REGISTERED = True
    except Exception:
        pass  # falls back to Helvetica (₹ will render as a box) if font truly unavailable


def generate_audit_report(site_name: str, violations: list, output_path: str):
    """violations: list of dicts matching schemas.ViolationOut shape."""
    _ensure_font()
    font_name = "DejaVuSans" if _FONT_REGISTERED else "Helvetica"
    font_bold = "DejaVuSans-Bold" if _FONT_REGISTERED else "Helvetica-Bold"

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Title"], fontSize=16, fontName=font_bold)
    small = ParagraphStyle("S", parent=styles["Normal"], fontSize=8, leading=10, fontName=font_name)
    small_bold = ParagraphStyle("SB", parent=small, fontName=font_bold)
    normal = ParagraphStyle("N", parent=styles["Normal"], fontName=font_name)

    doc = SimpleDocTemplate(output_path, pagesize=A4,
                             leftMargin=15*mm, rightMargin=15*mm,
                             topMargin=15*mm, bottomMargin=15*mm)
    story = []

    story.append(Paragraph(f"Dark Pattern Observation Report — {site_name}", title_style))
    story.append(Paragraph(
        f"Generated {datetime.now().strftime('%d %b %Y, %H:%M')} &nbsp;|&nbsp; "
        f"Assessed against India's Guidelines for Prevention and Regulation of "
        f"Dark Patterns, 2023 (13 patterns)",
        normal
    ))
    story.append(Spacer(1, 10))

    # LEGAL FRAMING — the single most important paragraph in this document.
    #
    # This tool is not the CCPA, is unaffiliated with any regulator, and has
    # no authority to determine that anyone has violated anything. Stating
    # "Company X violates CCPA" in an automated report about a named real
    # business is a defamation exposure, and it is also simply untrue: what
    # the engine observed is that a page exhibits characteristics matching a
    # defined pattern. That is a strong, useful, defensible claim. The
    # stronger-sounding one is neither.
    #
    # The word "violation" is therefore confined to describing the CATEGORY
    # DEFINITIONS, never the audited site's conduct.
    story.append(Paragraph(
        "<b>Status of this document.</b> This is an automated observation report, not a "
        "legal determination. It records characteristics of the pages listed below that "
        "match the definitions in the 2023 Guidelines. It is produced by an unaffiliated "
        "tool with no regulatory authority, was generated without the operator's "
        "involvement, and reflects only the pages captured at the time and date above — "
        "which may since have changed. Nothing here should be read as a finding that any "
        "person or business has breached any law. Each observation below carries the "
        "evidence supporting it so that a reader can verify it independently.",
        small
    ))
    story.append(Spacer(1, 8))

    violations = sorted(violations, key=lambda v: v["confidence"], reverse=True)

    tier_counts = {"provable": 0, "corroborated": 0, "indicative": 0, "unstated": 0}
    for v in violations:
        tier = (v.get("evidence") or {}).get("evidence_tier", "unstated")
        tier_counts[tier if tier in tier_counts else "unstated"] += 1

    story.append(Paragraph(
        f"<b>{len(violations)}</b> observation(s), ranked by evidence strength: "
        f"<b>{tier_counts['provable']}</b> provable "
        f"(directly observed in the page's own DOM state or arithmetic), "
        f"<b>{tier_counts['corroborated']}</b> corroborated "
        f"(two or more independent signals had to agree), "
        f"<b>{tier_counts['indicative']}</b> indicative "
        f"(consistent with the pattern; requires human confirmation). "
        f"Layer indicates how each was produced: 1 = deterministic rule, "
        f"2 = rendered geometry and WCAG contrast, 3 = language analysis.",
        normal
    ))
    story.append(Spacer(1, 10))

    header = [Paragraph(h, small_bold) for h in
              ["Pattern", "Step", "Layer", "Evidence tier", "Conf.", "Observation"]]
    table_data = [header]
    for v in violations:
        tier = (v.get("evidence") or {}).get("evidence_tier", "unstated")
        table_data.append([
            Paragraph(f"{v['pattern_code']}<br/>{v['pattern_name']}", small),
            Paragraph(v["step_name"], small),
            Paragraph(str(v["layer"]), small),
            Paragraph(tier, small),
            Paragraph(f"{v['confidence']*100:.0f}%", small),
            Paragraph(v["explanation"], small),
        ])

    # Column widths sum to 508pt, just inside the 510pt of usable width an A4
    # page leaves after 15mm margins. Adding the evidence-tier column without
    # re-budgeting the others made "Layer" render as "La/ye/r" down three
    # lines and split "Evidence tier" mid-word -- a table too cramped to read
    # undermines the whole point of a report a non-technical official is
    # supposed to verify by eye.
    table = Table(table_data, colWidths=[88, 58, 38, 62, 38, 224])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f8")]),
        # ReportLab's 6pt default side padding costs 12pt of every column.
        # On the narrow numeric columns that was more than half the usable
        # width, which is what pushed "Layer" onto two lines. 3pt keeps the
        # cells legible and gives the text room to sit on one line.
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(table)

    # LIMITATIONS — printed on every report, not kept in a README nobody
    # reading the PDF will ever see. A report that lists only what it found,
    # with no account of what it could not look at, invites the reader to
    # treat silence as a clean bill of health.
    story.append(Spacer(1, 14))
    story.append(Paragraph("Scope and limitations of this assessment", small_bold))
    story.append(Spacer(1, 4))
    for limitation in [
        "Only the pages captured in this run were assessed. Flows behind "
        "authentication, payment, or region/device gating were not traversed.",
        "Disguised Advertisement (DP-09) is assessed by whether a disclosure label is "
        "readable, not by whether an advertisement visually mimics surrounding content; "
        "the latter requires computer vision this tool does not perform.",
        "Rogue Malware (DP-13) describes presentation only. No file was downloaded, "
        "inspected, or analysed, and no claim is made about the contents of any file.",
        "Absence-based observations (for example, no cancellation route found) report "
        "what was not present on the pages visited, which is not the same as proof that "
        "it does not exist elsewhere on the site.",
        "Prices, availability, and page content change continuously. This report "
        "describes one capture at one moment.",
    ]:
        story.append(Paragraph(f"• {limitation}", small))
        story.append(Spacer(1, 3))

    doc.build(story)
    return output_path
