"""Render the payroll policy source (markdown) into a PDF for the corpus.

Why this exists: the project asks the ingestion pipeline to handle at least two
source formats. The corpus already has Markdown, HTML and TXT files; this script
produces one PDF so the PDF parser is exercised too. The generated PDF is
committed, so you only need to run this if you edit the source text.

Usage:
    pip install reportlab
    python scripts/make_pdf.py
"""

import re
from pathlib import Path

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "source" / "POL-014-payroll-and-compensation.md"
OUT = ROOT / "corpus" / "POL-014-payroll-and-compensation.pdf"


def main() -> None:
    text = SRC.read_text(encoding="utf-8")

    # Split the YAML-style front matter (between the two '---' lines) from the body.
    _, front, body = text.split("---", 2)
    meta = dict(line.split(":", 1) for line in front.strip().splitlines())
    meta = {k.strip(): v.strip() for k, v in meta.items()}

    styles = getSampleStyleSheet()
    story = [
        # The DOC-ID line lets the PDF parser recover the document ID after text extraction.
        Paragraph(f"DOC-ID: {meta['doc_id']}", styles["Normal"]),
        Paragraph(f"OWNER: {meta['owner']} | EFFECTIVE-DATE: {meta['effective_date']}", styles["Normal"]),
        Spacer(1, 12),
    ]
    for block in body.strip().split("\n\n"):
        block = block.strip()
        if block.startswith("# "):
            story.append(Paragraph(block[2:], styles["Title"]))
        elif block.startswith("## "):
            story.append(Paragraph(block[3:], styles["Heading2"]))
        else:
            # Convert simple **bold** markers so reportlab renders them.
            html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", block)
            for line in html.splitlines():
                story.append(Paragraph(line.lstrip("- "), styles["BodyText"]))
        story.append(Spacer(1, 6))

    doc = SimpleDocTemplate(str(OUT), pagesize=LETTER, title=meta["title"], subject=meta["doc_id"])
    doc.build(story)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
