# tests/documents_test.py
"""Document reading tool tests: Office extraction, chunking, and error contracts."""

import datetime
import shutil
import unittest
from pathlib import Path
from uuid import uuid4

from tools.context import ToolContext
from tools.documents import build_document_tools


def make_root() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"docs_{uuid4().hex[:12]}"
    path.mkdir()
    return path


def write_simple_pdf(path: Path, text: str = "Hello PDF") -> None:
    """Writes a minimal parseable PDF to validate text extraction (without reportlab)."""
    content = f"BT /F1 24 Tf 72 100 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


class DocumentsTest(unittest.TestCase):
    def setUp(self):
        self.root = make_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        ctx = ToolContext(workspace=self.root, max_output_chars=5000)
        self.tool = next(t for t in build_document_tools(ctx) if t.name == "read_document")

    def test_docx_extracts_headings_paragraphs_and_tables(self):
        from docx import Document

        document = Document()
        document.add_heading("项目说明", level=1)
        document.add_paragraph("这是正文。")
        table = document.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "名称"
        table.cell(0, 1).text = "数量"
        table.cell(1, 0).text = "苹果"
        table.cell(1, 1).text = "3"
        document.save(self.root / "a.docx")

        text = self.tool.invoke({"path": "a.docx"})
        self.assertIn("Word", text)
        self.assertIn("# 项目说明", text)
        self.assertIn("这是正文。", text)
        self.assertIn("名称 | 数量", text)
        self.assertIn("苹果 | 3", text)

    def test_xlsx_extracts_sheets_and_values(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "数据"
        sheet.append(["姓名", "数量", "日期", "启用"])
        sheet.append(["苹果", 3, datetime.date(2026, 11, 7), True])
        workbook.save(self.root / "a.xlsx")
        workbook.close()

        text = self.tool.invoke({"path": "a.xlsx"})
        self.assertIn("Sheet: 数据", text)
        self.assertIn("姓名\t数量\t日期\t启用", text)
        self.assertIn("苹果\t3\t2026-11-07\tTRUE", text)

    def test_pptx_extracts_slides_and_notes(self):
        from pptx import Presentation

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[1])
        slide.shapes.title.text = "封面"
        slide.placeholders[1].text = "第一页内容"
        slide.notes_slide.notes_text_frame.text = "演讲备注"
        presentation.save(self.root / "a.pptx")

        text = self.tool.invoke({"path": "a.pptx"})
        self.assertIn("PowerPoint", text)
        self.assertIn("Page 1", text)
        self.assertIn("封面", text)
        self.assertIn("第一页内容", text)
        self.assertIn("[Notes] 演讲备注", text)

    def test_text_offset_and_limit(self):
        (self.root / "notes.txt").write_text("0123456789" * 10, encoding="utf-8")
        text = self.tool.invoke({"path": "notes.txt", "offset": 10, "limit": 20})
        self.assertIn("100 characters total", text)
        self.assertIn("01234567890123456789", text)
        self.assertIn("offset=30", text)

    def test_offset_out_of_range(self):
        (self.root / "a.txt").write_text("abc", encoding="utf-8")
        text = self.tool.invoke({"path": "a.txt", "offset": 99})
        self.assertTrue(text.startswith("Error: [offset_out_of_range]"), text)

    def test_pdf_extracts_text(self):
        write_simple_pdf(self.root / "hello.pdf", "Hello PDF")
        text = self.tool.invoke({"path": "hello.pdf"})
        self.assertIn("PDF", text)
        self.assertIn("Page 1", text)
        self.assertIn("Hello PDF", text)

    def test_blank_pdf_is_reported_as_empty(self):
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with open(self.root / "blank.pdf", "wb") as handle:
            writer.write(handle)

        text = self.tool.invoke({"path": "blank.pdf"})
        self.assertTrue(text.startswith("Error: [empty_document]"), text)

    def test_unsupported_and_legacy_formats(self):
        (self.root / "a.bin").write_bytes(b"\x00\x01")
        text = self.tool.invoke({"path": "a.bin"})
        self.assertTrue(text.startswith("Error: [unsupported_type]"), text)

        (self.root / "old.doc").write_bytes(b"legacy")
        legacy = self.tool.invoke({"path": "old.doc"})
        self.assertTrue(legacy.startswith("Error: [legacy_format]"), legacy)

        (self.root / "photo.png").write_bytes(b"png")
        image = self.tool.invoke({"path": "photo.png"})
        self.assertTrue(image.startswith("Error: [unsupported_image]"), image)

    def test_missing_file_and_directory(self):
        missing = self.tool.invoke({"path": "nope.docx"})
        self.assertTrue(missing.startswith("Error: [not_found]"), missing)

        (self.root / "dir").mkdir()
        directory = self.tool.invoke({"path": "dir"})
        self.assertTrue(directory.startswith("Error: [is_directory]"), directory)


if __name__ == "__main__":
    unittest.main()
