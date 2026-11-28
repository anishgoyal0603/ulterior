"""Generates a CCPA-format PDF audit report — the artifact a compliance
officer or regulator would actually review, with evidence linked per
violation, not a bare score."""

import os
from datetime import datetime, timezone
from xml.sax.saxutils import escape as _esc
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, CondPageBreak
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
    # The bundled copy first (report/fonts, DejaVu licence alongside), so a
    # report made on Windows or macOS prints ₹ instead of a black box; then
    # system fonts that have the rupee sign.
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
    candidates = [
        (os.path.join(here, "DejaVuSans.ttf"), os.path.join(here, "DejaVuSans-Bold.ttf")),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        (r"C:\Windows\Fonts\Nirmala.ttf", r"C:\Windows\Fonts\NirmalaB.ttf"),
        (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    ]
    for regular, bold in candidates:
        if not (os.path.isfile(regular) and os.path.isfile(bold)):
            continue
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            _FONT_REGISTERED = True
            return
        except Exception:
            continue
    # Otherwise Helvetica, which has no ₹ glyph.


_AUTHORITY_TEXT = {
    "owner": "the store owner",
    "written_permission": "written permission from the store owner",
    "public_test_site": "a public test site that invites automated testing",
}


def generate_audit_report(site_name: str, violations: list, output_path: str, *,
                          job_id=None, authorization=None, manifest=None, comparison=None,
                          coverage=None, review=None):
    """violations: list of dicts matching schemas.ViolationOut shape.

    Optional extras, each printed only when given:
      authorization -- the permission record stored with the audit
      manifest      -- SHA-256 evidence fingerprints (app/integrity.py)
      comparison    -- resolved / still present / new against an earlier audit
      coverage      -- per-pattern Observed / Not observed / Partially / Not assessed
      review        -- the person who checked the findings against the screenshots
    """
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

    # ReportLab's Paragraph reads its text as markup. Site names, step names
    # and explanations all quote audited pages, and a "<" or "&" on a page
    # either breaks the PDF ("paraparser: syntax error") or, worse, formats
    # it. Escape everything that came from outside.
    story.append(Paragraph(f"Dark Pattern Observation Report — {_esc(str(site_name))}", title_style))
    reference = f"Audit #{job_id}" if job_id else "Audit"
    if manifest and manifest.get("fingerprint"):
        reference += f" &nbsp;·&nbsp; evidence fingerprint {manifest['fingerprint'][:16]}"
    story.append(Paragraph(f"Produced by Ulterior by The Odyssey &nbsp;·&nbsp; {reference}",
                           styles["Italic"]))
    story.append(Paragraph(
        # Labelled, and in the same clock as every other time in the report
        # (the permission and evidence times are UTC).
        f"Generated {datetime.now(timezone.utc).strftime('%d %b %Y, %H:%M')} UTC &nbsp;|&nbsp; "
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
        "tool with no regulatory authority, and reflects only the pages captured at the "
        "time and date above — "
        "which may since have changed. Nothing here should be read as a finding that any "
        "person or business has breached any law. Each observation below carries the "
        "evidence supporting it so that a reader can verify it independently.",
        small
    ))
    story.append(Spacer(1, 8))

    # PERMISSION — who allowed this audit, as recorded before it ran.
    if authorization:
        auth_text = _AUTHORITY_TEXT.get(authorization.get("authority"), "permission recorded")
        line = (f"<b>Permission to assess.</b> Recorded {_esc(str(authorization.get('recorded_at', '')))}: "
                f"{_esc(str(authorization.get('owner_name', '')))} of "
                f"{_esc(str(authorization.get('organisation', '')))}, on the basis of {auth_text}.")
        if authorization.get("reference"):
            line += f" Reference: {_esc(str(authorization['reference']))}."
        story.append(Paragraph(line, small))
        story.append(Spacer(1, 8))

    # HUMAN REVIEW -- whether a person checked the findings before this went out.
    if review:
        outcome = {"confirmed": "confirmed every observation against its screenshot",
                   "edited": "checked the observations and corrected some",
                   "rejected_some": "checked the observations and rejected some"}.get(review.get("outcome"), "checked")
        line = (f"<b>Human review.</b> {_esc(str(review.get('reviewer', '')))} {outcome} on "
                f"{_esc(str(review.get('reviewed_at', '')))}.")
        if review.get("note"):
            line += f" Note: {_esc(str(review['note']))}"
        story.append(Paragraph(line, small))
    else:
        story.append(Paragraph("<b>Human review.</b> Not yet reviewed by a person. Treat every "
                               "observation as unconfirmed until it is.", small))
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
            Paragraph(f"{_esc(str(v['pattern_code']))}<br/>{_esc(str(v['pattern_name']))}", small),
            Paragraph(_esc(str(v["step_name"])), small),
            Paragraph(str(v["layer"]), small),
            Paragraph(tier, small),
            Paragraph(f"{v['confidence']*100:.0f}%", small),
            Paragraph(_esc(str(v["explanation"])), small),
        ])

    # Column widths sum to 508pt, just inside the 510pt of usable width an A4
    # page leaves after 15mm margins. Adding the evidence-tier column without
    # re-budgeting the others made "Layer" render as "La/ye/r" down three
    # lines and split "Evidence tier" mid-word -- a table too cramped to read
    # undermines the whole point of a report a non-technical official is
    # supposed to verify by eye.
    table = Table(table_data, colWidths=[88, 58, 38, 62, 38, 224], repeatRows=1)
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

    # COVERAGE -- what each of the 13 patterns could and could not be checked on,
    # so silence is never read as a clean result.
    if coverage:
        from detectors.coverage import STATUS_LABEL
        story.append(Spacer(1, 14))
        story.append(CondPageBreak(160))
        story.append(Paragraph("Coverage of the 13 patterns in this audit", small_bold))
        story.append(Paragraph(
            "\"Not observed\" means the pages the pattern needs were inspected and nothing was found. "
            "\"Not assessed\" means those pages were not in this audit, so no conclusion is drawn.", small))
        story.append(Spacer(1, 4))
        rows = [[Paragraph(h, small_bold) for h in ["Pattern", "Status", "Why"]]]
        for c in coverage:
            rows.append([Paragraph(f"{_esc(str(c.get('code')))} {_esc(str(c.get('name')))}", small),
                         Paragraph(STATUS_LABEL.get(c.get("status"), str(c.get("status"))), small_bold),
                         Paragraph(_esc(str(c.get("note") or "")), small)])
        cov = Table(rows, colWidths=[150, 90, 268], repeatRows=1)
        cov.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(cov)

    # RE-CHECK — what changed since an earlier audit of the same pages.
    if comparison:
        story.append(Spacer(1, 14))
        story.append(CondPageBreak(80))
        story.append(Paragraph(f"Changes since audit #{comparison.get('baseline_id')}", small_bold))
        story.append(Spacer(1, 4))
        for label, key in (("No longer observed", "resolved"),
                           ("Still observed", "still_present"),
                           ("Newly observed", "new")):
            items = comparison.get(key) or []
            names = "; ".join(f"{_esc(str(i.get('pattern_code')))} {_esc(str(i.get('pattern_name') or ''))} "
                              f"({_esc(str(i.get('step_name')))})" for i in items) or "none"
            story.append(Paragraph(f"<b>{label} ({len(items)}):</b> {names}", small))
            story.append(Spacer(1, 3))

    # FIX CHECKLIST — one entry per pattern observed, for the store's developers.
    from report.remediation import checklist_for
    checklist = checklist_for([v["pattern_code"] for v in violations])
    if checklist:
        story.append(Spacer(1, 14))
        story.append(CondPageBreak(140))   # never strand the heading at a page foot
        story.append(Paragraph("Suggested fixes for developers", small_bold))
        story.append(Paragraph(
            "Suggestions for the business to consider, one per pattern observed above. "
            "They are not legal advice; the business decides what its pages must do.", small))
        story.append(Spacer(1, 4))
        rows = [[Paragraph(h, small_bold) for h in ["Pattern", "What to change", "How to check"]]]
        for code, name, fix, check in checklist:
            rows.append([Paragraph(f"{code}<br/>{_esc(name)}", small),
                         Paragraph(_esc(fix), small), Paragraph(_esc(check), small)])
        fixes = Table(rows, colWidths=[78, 250, 180], repeatRows=1)
        fixes.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(fixes)

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

    # EVIDENCE INTEGRITY — the fingerprints a reader can check this report against.
    if manifest:
        story.append(Spacer(1, 14))
        story.append(CondPageBreak(110))
        story.append(Paragraph("Evidence integrity", small_bold))
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"When this audit finished, each screenshot and the list of observations were "
            f"fingerprinted with SHA-256 (captured {_esc(str(manifest.get('captured_at', '')))}). "
            f"Anyone holding this report can ask the operator to re-check them; any change to the "
            f"evidence changes the fingerprint. This shows the evidence has not been altered since "
            f"capture. It is not a digital signature.", small))
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"<b>Report fingerprint:</b> <font face='Courier' size='7'>"
                               f"{_esc(str(manifest.get('fingerprint', '')))}</font>", small))
        story.append(Paragraph(f"<b>Observations ({manifest.get('findings_count', 0)}):</b> "
                               f"<font face='Courier' size='7'>{_esc(str(manifest.get('findings_sha256', '')))}"
                               f"</font>", small))
        for shot in manifest.get("screenshots", []):
            story.append(Paragraph(
                f"<b>{_esc(str(shot.get('step')))}</b> ({_esc(str(shot.get('file')))}): "
                f"<font face='Courier' size='7'>{_esc(str(shot.get('sha256')))}</font>", small))

    doc.build(story)
    return output_path
