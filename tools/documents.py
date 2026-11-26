# tools/documents.py
"""Read non-plain-text documents: ``read_document``.

Supported formats:

- PDF (``pypdf``, optional dependency)
- Word ``.docx`` (``python-docx``)
- Excel ``.xlsx`` (``openpyxl``, read-only with cached values)
- PowerPoint ``.pptx`` (``python-pptx``)
- Common plain text (``.txt`` / ``.md`` / ``.csv`` / ``.json`` / ``.log`` / ``.xml`` / ``.yaml`` ...)

Every format is extracted to text and returned in segments by character offset, so an
entire book is never placed into the context at once. Legacy binary Office formats
(``.doc`` / ``.xls`` / ``.ppt``) and image OCR are not supported; they produce explicit
errors with alternative suggestions.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
from tools.spec import ToolSpec
from tools.errors import tool_error, truncate
from tools.paths import WorkspacePathError, display_path, resolve_in_workspace

_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".log",
    ".xml", ".yaml", ".yml", ".ini", ".toml", ".cfg", ".conf", ".rst",
    ".py", ".js", ".ts", ".vue", ".css", ".html", ".htm", ".sql", ".sh",
}
_MAX_TEXT_BYTES = 2_000_000
_MAX_DOC_BYTES = 50_000_000
_MAX_ROWS_PER_SHEET = 300
_MAX_SLIDES = 300
_MAX_PDF_PAGES = 300


class DocumentError(ValueError):
    """Document parsing failure; carries a machine-readable error code."""

    def __init__(self, code: str, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        if isinstance(value, _dt.datetime) and value.time() == _dt.time(0, 0):
            return value.date().isoformat()
        return value.isoformat(sep=" ") if isinstance(value, _dt.datetime) else value.isoformat()
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _read_pdf(target: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise DocumentError(
            "missing_dependency",
            "Reading PDF requires pypdf, which is not installed in this environment.",
            hint="Run from the project root: uv add pypdf",
        ) from exc

    try:
        reader = PdfReader(str(target))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise DocumentError("encrypted_document", "The PDF is encrypted and cannot be read.") from exc
        parts: list[str] = []
        pages = list(reader.pages)
        for index, page in enumerate(pages[: _MAX_PDF_PAGES], 1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(f"--- Page {index} ---\n{text}")
        if len(pages) > _MAX_PDF_PAGES:
            parts.append(f"…[document exceeds {_MAX_PDF_PAGES} pages; remaining pages were not read]")
        return "\n\n".join(parts)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("document_error", f"PDF parsing failed: {exc}.") from exc


def _read_docx(target: Path) -> str:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise DocumentError(
            "missing_dependency",
            "Reading .docx requires python-docx, which is not installed in this environment.",
            hint="Run from the project root: uv add python-docx",
        ) from exc

    try:
        document = Document(str(target))
        parts: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag == qn("w:p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                style = (paragraph.style.name if paragraph.style is not None else "") or ""
                if style.lower().startswith("heading"):
                    level = "".join(char for char in style if char.isdigit()) or "1"
                    parts.append(f"{'#' * min(int(level), 6)} {text}")
                else:
                    parts.append(text)
            elif child.tag == qn("w:tbl"):
                table = Table(child, document)
                rows: list[str] = []
                for row in table.rows:
                    cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    parts.append("[Table]\n" + "\n".join(rows))
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"Word document parsing failed: {exc}.") from exc


def _read_xlsx(target: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise DocumentError(
            "missing_dependency",
            "Reading .xlsx requires openpyxl, which is not installed in this environment.",
            hint="Run from the project root: uv add openpyxl",
        ) from exc

    workbook = None
    try:
        workbook = load_workbook(str(target), read_only=True, data_only=True)
        parts: list[str] = []
        for sheet in workbook.worksheets:
            parts.append(f"--- Sheet: {sheet.title} ---")
            rows: list[str] = []
            for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if index > _MAX_ROWS_PER_SHEET:
                    rows.append(f"…[sheet exceeds {_MAX_ROWS_PER_SHEET} rows; remaining rows were not read]")
                    break
                cells = [_cell_text(value) for value in row]
                while cells and not cells[-1]:
                    cells.pop()
                if any(str(cell).strip() for cell in cells):
                    rows.append("\t".join(cells))
            parts.append("\n".join(rows) if rows else "(empty sheet)")
        if not parts:
            return ""
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"Excel parsing failed: {exc}.") from exc
    finally:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass


def _read_pptx(target: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover - dependency is declared in pyproject
        raise DocumentError(
            "missing_dependency",
            "Reading .pptx requires python-pptx, which is not installed in this environment.",
            hint="Run from the project root: uv add python-pptx",
        ) from exc

    try:
        presentation = Presentation(str(target))
        parts: list[str] = []
        for index, slide in enumerate(presentation.slides, 1):
            if index > _MAX_SLIDES:
                parts.append(f"…[presentation exceeds {_MAX_SLIDES} slides; remaining slides were not read]")
                break
            blocks: list[str] = [f"--- Page {index} ---"]
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False):
                    text = shape.text_frame.text.strip()
                    if text:
                        blocks.append(text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                        if any(cells):
                            blocks.append(" | ".join(cells))
            if slide.has_notes_slide:
                notes = slide.notes_slide.notes_text_frame.text.strip()
                if notes:
                    blocks.append(f"[Notes] {notes}")
            parts.append("\n".join(blocks))
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"PowerPoint parsing failed: {exc}.") from exc


def _extract(target: Path) -> tuple[str, str]:
    """Extract the full text by suffix and return ``(format label, text)``."""
    suffix = target.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        raw = target.read_bytes()[:_MAX_TEXT_BYTES]
        if b"\x00" in raw:
            raise DocumentError("binary_file", f"{target.name} appears to be a binary file and cannot be read as text.")
        return "Text", _decode_text(raw)
    if suffix == ".pdf":
        return "PDF", _read_pdf(target)
    if suffix == ".docx":
        return "Word", _read_docx(target)
    if suffix == ".xlsx":
        return "Excel", _read_xlsx(target)
    if suffix == ".pptx":
        return "PowerPoint", _read_pptx(target)
    if suffix in {".doc", ".xls", ".ppt"}:
        raise DocumentError(
            "legacy_format",
            f"Legacy Office format {suffix} is not supported.",
            hint="Save it as .docx / .xlsx / .pptx with Office / LibreOffice, then read it again.",
        )
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}:
        raise DocumentError(
            "unsupported_image",
            "Images require OCR or a vision model; this tool cannot recognize text in images directly.",
            hint="Ask the user to describe the image, or convert the image to text before placing it in the workspace.",
        )
    raise DocumentError(
        "unsupported_type",
        f"Reading {suffix or '(no suffix)'} files is not supported.",
        hint="Supported: PDF / DOCX / XLSX / PPTX and common plain-text formats.",
    )


def build_document_tools(ctx: ToolContext) -> list:
    """Build ``read_document``: extracts text and returns it in segments by character offset."""

    @tool
    def read_document(path: str, offset: int = 0, limit: int = 0) -> str:
        """Read PDF / Word / Excel / PowerPoint / text files and return the extracted text.

        Suitable for reading documents that the user has placed in the workspace; for plain text files,
        read_file can still be used (it includes line numbers and is more convenient for editing).
        For very long documents, use offset to read in segments.

        Args:
            path: File path relative to the workspace.
            offset: The character position to start returning from; defaults to 0.
            limit: The maximum number of characters to return this time; 0 means use the tool's default budget.

        Returns:
            The first line contains the document type and total character count, followed by the body text;
            the end includes a hint for the next segment's offset.
        """
        try:
            target = resolve_in_workspace(ctx.workspace, path)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not target.exists():
            return tool_error("not_found", f"File does not exist: {path}.", hint="Use list_dir first to see the directory contents.")
        if target.is_dir():
            return tool_error("is_directory", f"{path} is a directory.", hint="Provide a concrete file path.")
        try:
            if target.stat().st_size > _MAX_DOC_BYTES:
                return tool_error(
                    "file_too_large",
                    f"{path} exceeds {_MAX_DOC_BYTES // 1_000_000} MB and was not parsed.",
                    hint="Split the file first or extract only the needed parts.",
                )
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}.")

        try:
            label, full_text = _extract(target)
        except DocumentError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        except OSError as exc:
            return tool_error("read_failed", f"Failed to read {path}: {exc}.")

        if not full_text.strip():
            return tool_error(
                "empty_document",
                f"{path} contains no extractable text.",
                hint="Scanned PDFs or images need OCR / a vision model to recognize text.",
            )

        total = len(full_text)
        start = max(0, int(offset or 0))
        if start >= total:
            return tool_error(
                "offset_out_of_range",
                f"offset={start} exceeds the document length of {total} characters.",
                hint=f"Set offset within 0..{max(0, total - 1)}.",
            )
        budget = int(limit or 0)
        if budget <= 0:
            budget = max(1000, ctx.max_output_chars - 300)
        budget = min(budget, ctx.max_output_chars)
        end = min(total, start + budget)
        chunk = full_text[start:end]

        header = f"Document: {display_path(ctx.workspace, target)} ({label}; {total} characters total)"
        body = [header, chunk]
        if end < total:
            body.append(f"…{total - end} characters remain; continue with offset={end}.")
        return truncate("\n".join(body), ctx.max_output_chars)

    return [read_document]


TOOL_ENTRIES: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read_document",
        label="文档读取",
        description="读取 PDF、Office 与文本文件",
        tool_names=("read_document",),
        builder=lambda _config, ctx: build_document_tools(ctx),
        defer=True,
        search_hint="pdf docx xlsx pptx word excel document office 文档 表格 幻灯片 解析",
    ),
)
