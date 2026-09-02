"""One-time fixture generator for Document Intelligence tests.

Not a runtime or CI dependency: `reportlab` and `pypdf` are used here only to
author static binary fixtures once; neither is added to `backend/pyproject.toml`.
Regenerate by running `python scripts/gen_document_intelligence_fixtures.py`
if fixtures ever need to change.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle

FIXTURES = (
    Path(__file__).resolve().parent.parent
    / "backend"
    / "tests"
    / "fixtures"
    / "document_intelligence"
)


def _normal_text_pdf() -> None:
    path = FIXTURES / "normal_text.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("KORTEX Fixture Document")
    c.setAuthor("KORTEX Test Suite")
    c.drawString(1 * inch, 10 * inch, "KORTEX Document Intelligence Fixture")
    c.drawString(
        1 * inch, 9.7 * inch, "This is a deterministic text extraction fixture."
    )
    c.save()


def _multipage_pdf() -> None:
    path = FIXTURES / "multipage.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Multipage Fixture")
    c.setAuthor("KORTEX Test Suite")
    c.setSubject("multipage")
    for i in range(1, 4):
        c.drawString(1 * inch, 10 * inch, f"Page {i} of 3")
        c.showPage()
    c.save()


def _table_pdf() -> None:
    path = FIXTURES / "table.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=letter)
    data = [
        ["Item", "Quantity", "Price"],
        ["Widget", "10", "5.00"],
        ["Gadget", "3", "12.50"],
    ]
    table = Table(data)
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
            ]
        )
    )
    doc.build([table])


def _empty_pdf() -> None:
    path = FIXTURES / "empty.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.showPage()
    c.save()


def _malformed_pdf() -> None:
    path = FIXTURES / "malformed.pdf"
    path.write_bytes(b"%PDF-1.4\nnot a real pdf structure\n%%EOF-truncated")


def _encrypted_pdf() -> None:
    source = FIXTURES / "normal_text.pdf"
    path = FIXTURES / "encrypted.pdf"
    writer = PdfWriter(clone_from=str(source))
    writer.encrypt(
        user_password="kortex-test-password", owner_password="kortex-test-owner"
    )
    with path.open("wb") as fh:
        writer.write(fh)


def _clear_text_image() -> None:
    path = FIXTURES / "clear_text.png"
    img = Image.new("RGB", (300, 80), color="white")
    d = ImageDraw.Draw(img)
    d.text((10, 25), "Hello KORTEX", fill="black")
    img.save(path)


def _non_text_image() -> None:
    path = FIXTURES / "non_text.png"
    img = Image.new("RGB", (200, 200), color="blue")
    img.save(path)


def _malformed_image() -> None:
    path = FIXTURES / "malformed.png"
    path.write_bytes(b"not a real png file")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    _normal_text_pdf()
    _multipage_pdf()
    _table_pdf()
    _empty_pdf()
    _malformed_pdf()
    _encrypted_pdf()
    _clear_text_image()
    _non_text_image()
    _malformed_image()
    print(f"Generated fixtures in {FIXTURES}")


if __name__ == "__main__":
    main()
