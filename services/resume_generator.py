import io
from html import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Inches, Pt
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (CondPageBreak, KeepTogether, Paragraph,
                                 SimpleDocTemplate, Spacer)


# ── Public API ────────────────────────────────────────────────────────────────

def generate_docx(resume_text: str, original_bytes: bytes | None = None,
                  original_filename: str = "") -> bytes:
    if original_bytes and original_filename.lower().endswith(".docx"):
        try:
            return _docx_from_template(resume_text, original_bytes)
        except Exception:
            pass
    return _docx_basic(resume_text)


def generate_pdf(resume_text: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=inch, rightMargin=inch,
    )
    doc.build(_build_pdf_story(resume_text))
    return buf.getvalue()


# ── DOCX: template-based (matches original style) ────────────────────────────

def _docx_from_template(resume_text: str, template_bytes: bytes) -> bytes:
    template = Document(io.BytesIO(template_bytes))
    styles = _extract_docx_styles(template)

    doc = Document(io.BytesIO(template_bytes))

    # Clear body content but keep section properties (page size, margins)
    body = doc.element.body
    sect_pr = body.find(qn("w:sectPr"))
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)

    _populate_docx(doc, resume_text, styles)

    # Re-attach sectPr at the end so page layout is preserved
    if sect_pr is not None and body.find(qn("w:sectPr")) is None:
        body.append(sect_pr)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _extract_docx_styles(doc: Document) -> dict:
    styles = {
        "name":    {"font": "Calibri", "size": 16.0, "bold": True,  "align": WD_ALIGN_PARAGRAPH.CENTER},
        "section": {"font": "Calibri", "size": 11.0, "bold": True,  "align": WD_ALIGN_PARAGRAPH.LEFT},
        "body":    {"font": "Calibri", "size": 10.5, "bold": False, "align": WD_ALIGN_PARAGRAPH.LEFT},
        "bullet":  {"font": "Calibri", "size": 10.5, "bold": False, "align": WD_ALIGN_PARAGRAPH.LEFT,
                    "indent": Inches(0.25)},
        "margins": {
            "top":    Inches(0.75), "bottom": Inches(0.75),
            "left":   Inches(1.0),  "right":  Inches(1.0),
        },
    }

    # Page margins
    if doc.sections:
        s = doc.sections[0]
        styles["margins"] = {
            "top": s.top_margin, "bottom": s.bottom_margin,
            "left": s.left_margin, "right": s.right_margin,
        }

    non_empty = [p for p in doc.paragraphs if p.text.strip()]
    found = {"name": False, "section": False, "body": False, "bullet": False}

    for i, para in enumerate(non_empty):
        text = para.text.strip()
        runs = [r for r in para.runs if r.text.strip()]
        if not runs:
            continue
        r = runs[0]

        font_name = r.font.name
        if not font_name and r.style:
            font_name = r.style.font.name

        font_size = None
        if r.font.size:
            font_size = r.font.size.pt
        elif r.style and r.style.font.size:
            font_size = r.style.font.size.pt

        info = {}
        if font_name:
            info["font"] = font_name
        if font_size:
            info["size"] = font_size
        if r.font.bold is not None:
            info["bold"] = r.font.bold
        if para.alignment is not None:
            info["align"] = para.alignment
        if para.paragraph_format.left_indent:
            info["indent"] = para.paragraph_format.left_indent

        if i == 0 and not found["name"]:
            styles["name"].update(info)
            found["name"] = True
        elif text.isupper() and len(text) > 2 and not found["section"]:
            styles["section"].update(info)
            found["section"] = True
        elif (text.startswith("•") or text.startswith("-")) and not found["bullet"]:
            styles["bullet"].update(info)
            found["bullet"] = True
        elif not text.isupper() and not text.startswith("•") and i > 0 and not found["body"]:
            styles["body"].update(info)
            found["body"] = True

        if all(found.values()):
            break

    return styles


def _populate_docx(doc: Document, resume_text: str, styles: dict):
    lines = resume_text.strip().split("\n")
    first_line = True

    for line in lines:
        stripped = line.strip()

        if not stripped:
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(2)
            continue

        if first_line:
            _add_styled_para(doc, stripped, styles["name"])
            first_line = False
        elif stripped.isupper() and len(stripped) > 2:
            _add_styled_para(doc, stripped, styles["section"])
        elif stripped.startswith("•") or stripped.startswith("-"):
            _add_styled_para(doc, stripped.lstrip("•- "), styles["bullet"])
        else:
            _add_styled_para(doc, stripped, styles["body"])


def _add_styled_para(doc: Document, text: str, style: dict):
    p = doc.add_paragraph()
    if style.get("align") is not None:
        p.alignment = style["align"]
    fmt = p.paragraph_format
    fmt.space_before = Pt(style.get("space_before", 0))
    fmt.space_after = Pt(style.get("space_after", 2))
    if style.get("indent"):
        fmt.left_indent = style["indent"]

    run = p.add_run(text)
    if style.get("font"):
        run.font.name = style["font"]
    if style.get("size"):
        run.font.size = Pt(style["size"])
    if style.get("bold") is not None:
        run.font.bold = style["bold"]
    try:
        if style.get("color"):
            run.font.color.rgb = style["color"]
    except Exception:
        pass


# ── DOCX: basic fallback ──────────────────────────────────────────────────────

def _docx_basic(resume_text: str) -> bytes:
    doc = Document()
    for section in doc.sections:
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)

    default_styles = {
        "name":    {"font": "Calibri", "size": 16.0, "bold": True,  "align": WD_ALIGN_PARAGRAPH.CENTER},
        "section": {"font": "Calibri", "size": 11.0, "bold": True,  "align": WD_ALIGN_PARAGRAPH.LEFT},
        "body":    {"font": "Calibri", "size": 10.5, "bold": False, "align": WD_ALIGN_PARAGRAPH.LEFT},
        "bullet":  {"font": "Calibri", "size": 10.5, "bold": False, "align": WD_ALIGN_PARAGRAPH.LEFT,
                    "indent": Inches(0.25)},
    }
    _populate_docx(doc, resume_text, default_styles)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── PDF: section-aware with KeepTogether ─────────────────────────────────────

def _build_pdf_story(resume_text: str) -> list:
    styles = getSampleStyleSheet()

    name_style = ParagraphStyle(
        "RName", parent=styles["Normal"],
        fontSize=16, fontName="Helvetica-Bold",
        alignment=1, spaceAfter=4,
    )
    section_style = ParagraphStyle(
        "RSection", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        spaceBefore=8, spaceAfter=3,
        borderPadding=(0, 0, 2, 0),
    )
    body_style = ParagraphStyle(
        "RBody", parent=styles["Normal"],
        fontSize=10, spaceAfter=2,
    )
    bullet_style = ParagraphStyle(
        "RBullet", parent=styles["Normal"],
        fontSize=10, leftIndent=16, spaceAfter=2,
    )

    # Split into sections (each starting at an ALL CAPS header)
    raw_sections: list[list[str]] = []
    current: list[str] = []

    for line in resume_text.strip().split("\n"):
        stripped = line.strip()
        if stripped and stripped.isupper() and len(stripped) > 2 and not stripped.startswith("•"):
            if current:
                raw_sections.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        raw_sections.append(current)

    story: list = []
    first_section = True

    for section_lines in raw_sections:
        block: list = []
        first_line_in_section = True

        for line in section_lines:
            stripped = line.strip()
            if not stripped:
                block.append(Spacer(1, 3))
                continue

            safe = escape(stripped)

            if first_section and first_line_in_section:
                block.append(Paragraph(safe, name_style))
            elif stripped.isupper() and len(stripped) > 2:
                block.append(Paragraph(safe, section_style))
            elif stripped.startswith("•") or stripped.startswith("-"):
                block.append(Paragraph(f"- {escape(stripped.lstrip('•- '))}", bullet_style))
            else:
                block.append(Paragraph(safe, body_style))

            first_line_in_section = False

        if not first_section:
            # Insert a conditional page break before this section if less than
            # 1.5 inches remain — keeps the header with its first few items
            story.append(CondPageBreak(1.5 * inch))

        story.append(KeepTogether(block))
        first_section = False

    return story
