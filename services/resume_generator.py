import io
from html import escape

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


def generate_docx(resume_text: str) -> bytes:
    doc = Document()

    section = doc.sections[0]
    section.top_margin = Inches(0.75)
    section.bottom_margin = Inches(0.75)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)

    lines = resume_text.strip().split("\n")
    first_line = True

    for line in lines:
        line = line.rstrip()

        if not line:
            doc.add_paragraph()
            continue

        if first_line:
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = p.add_run(line)
            run.bold = True
            run.font.size = Pt(18)
            first_line = False
        elif line.isupper() and len(line) > 2 and not line.startswith("•"):
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            p.paragraph_format.space_after = Pt(2)
            run = p.add_run(line)
            run.bold = True
            run.font.size = Pt(11)
            # Underline as section divider
            run.underline = True
        elif line.startswith("•") or line.startswith("-"):
            p = doc.add_paragraph(style="List Bullet")
            p.paragraph_format.left_indent = Inches(0.25)
            p.add_run(line.lstrip("•- "))
        else:
            doc.add_paragraph(line)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_pdf(resume_text: str) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=inch,
        rightMargin=inch,
    )

    styles = getSampleStyleSheet()
    name_style = ParagraphStyle(
        "Name", parent=styles["Normal"],
        fontSize=18, fontName="Helvetica-Bold",
        spaceAfter=6, alignment=1,
    )
    section_style = ParagraphStyle(
        "Section", parent=styles["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        spaceBefore=12, spaceAfter=3,
        underlineProportion=0.05,
    )
    body_style = ParagraphStyle(
        "Body", parent=styles["Normal"],
        fontSize=10, spaceAfter=3,
    )
    bullet_style = ParagraphStyle(
        "Bullet", parent=styles["Normal"],
        fontSize=10, leftIndent=20, spaceAfter=2,
    )

    story = []
    lines = resume_text.strip().split("\n")
    first_line = True

    for line in lines:
        line = line.rstrip()

        if not line:
            story.append(Spacer(1, 4))
            continue

        safe = escape(line)

        if first_line:
            story.append(Paragraph(safe, name_style))
            first_line = False
        elif line.isupper() and len(line) > 2 and not line.startswith("•"):
            story.append(Paragraph(f"<u>{safe}</u>", section_style))
        elif line.startswith("•") or line.startswith("-"):
            cleaned = escape(line.lstrip("•- "))
            story.append(Paragraph(f"- {cleaned}", bullet_style))
        else:
            story.append(Paragraph(safe, body_style))

    doc.build(story)
    return buf.getvalue()
