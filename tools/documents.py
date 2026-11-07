# tools/documents.py
"""非纯文本文档读取：``read_document``。

支持的格式：

- PDF（``pypdf``，可选依赖）
- Word ``.docx``（``python-docx``）
- Excel ``.xlsx``（``openpyxl``，只读 + 缓存值）
- PowerPoint ``.pptx``（``python-pptx``）
- 常见纯文本（``.txt`` / ``.md`` / ``.csv`` / ``.json`` / ``.log`` / ``.xml`` / ``.yaml``…）

统一抽成文本后按字符偏移分段返回，避免一次把整本书塞进上下文。
不支持旧版二进制 Office 格式（``.doc`` / ``.xls`` / ``.ppt``）与图片 OCR，
会返回明确错误与替代建议。
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from langchain.tools import tool

from tools.context import ToolContext
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
    """文档解析失败；带机读错误码。"""

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
    except ImportError as exc:  # pragma: no cover - 依赖已写入 pyproject
        raise DocumentError(
            "missing_dependency",
            "读取 PDF 需要 pypdf，但当前环境没有安装。",
            hint="在项目根目录运行：uv add pypdf",
        ) from exc

    try:
        reader = PdfReader(str(target))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise DocumentError("encrypted_document", "PDF 已加密，无法读取。") from exc
        parts: list[str] = []
        pages = list(reader.pages)
        for index, page in enumerate(pages[: _MAX_PDF_PAGES], 1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(f"--- 第 {index} 页 ---\n{text}")
        if len(pages) > _MAX_PDF_PAGES:
            parts.append(f"…[文档超过 {_MAX_PDF_PAGES} 页，后续页面未读取]")
        return "\n\n".join(parts)
    except DocumentError:
        raise
    except Exception as exc:
        raise DocumentError("document_error", f"PDF 解析失败：{exc}。") from exc


def _read_docx(target: Path) -> str:
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - 依赖已写入 pyproject
        raise DocumentError(
            "missing_dependency",
            "读取 .docx 需要 python-docx，但当前环境没有安装。",
            hint="在项目根目录运行：uv add python-docx",
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
                    parts.append("[表格]\n" + "\n".join(rows))
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"Word 文档解析失败：{exc}。") from exc


def _read_xlsx(target: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover - 依赖已写入 pyproject
        raise DocumentError(
            "missing_dependency",
            "读取 .xlsx 需要 openpyxl，但当前环境没有安装。",
            hint="在项目根目录运行：uv add openpyxl",
        ) from exc

    workbook = None
    try:
        workbook = load_workbook(str(target), read_only=True, data_only=True)
        parts: list[str] = []
        for sheet in workbook.worksheets:
            parts.append(f"--- 工作表：{sheet.title} ---")
            rows: list[str] = []
            for index, row in enumerate(sheet.iter_rows(values_only=True), 1):
                if index > _MAX_ROWS_PER_SHEET:
                    rows.append(f"…[本表超过 {_MAX_ROWS_PER_SHEET} 行，后续未读取]")
                    break
                cells = [_cell_text(value) for value in row]
                while cells and not cells[-1]:
                    cells.pop()
                if any(str(cell).strip() for cell in cells):
                    rows.append("\t".join(cells))
            parts.append("\n".join(rows) if rows else "(空表)")
        if not parts:
            return ""
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"Excel 解析失败：{exc}。") from exc
    finally:
        if workbook is not None:
            try:
                workbook.close()
            except Exception:
                pass


def _read_pptx(target: Path) -> str:
    try:
        from pptx import Presentation
    except ImportError as exc:  # pragma: no cover - 依赖已写入 pyproject
        raise DocumentError(
            "missing_dependency",
            "读取 .pptx 需要 python-pptx，但当前环境没有安装。",
            hint="在项目根目录运行：uv add python-pptx",
        ) from exc

    try:
        presentation = Presentation(str(target))
        parts: list[str] = []
        for index, slide in enumerate(presentation.slides, 1):
            if index > _MAX_SLIDES:
                parts.append(f"…[演示文稿超过 {_MAX_SLIDES} 页，后续未读取]")
                break
            blocks: list[str] = [f"--- 第 {index} 页 ---"]
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
                    blocks.append(f"[备注] {notes}")
            parts.append("\n".join(blocks))
        return "\n\n".join(parts)
    except Exception as exc:
        raise DocumentError("document_error", f"PowerPoint 解析失败：{exc}。") from exc


def _extract(target: Path) -> tuple[str, str]:
    """按后缀抽取全文，返回 ``(格式标签, 文本)``。"""
    suffix = target.suffix.lower()
    if suffix in _TEXT_SUFFIXES:
        raw = target.read_bytes()[:_MAX_TEXT_BYTES]
        if b"\x00" in raw:
            raise DocumentError("binary_file", f"{target.name} 看起来是二进制文件，无法按文本读取。")
        return "文本", _decode_text(raw)
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
            f"暂不支持旧版 Office 格式 {suffix}。",
            hint="请用 Office / LibreOffice 另存为 .docx / .xlsx / .pptx 后再读。",
        )
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}:
        raise DocumentError(
            "unsupported_image",
            "图片需要 OCR 或视觉模型，当前工具不能直接识别图片文字。",
            hint="可以先让用户描述图片内容，或把图片转成文字后再放入工作区。",
        )
    raise DocumentError(
        "unsupported_type",
        f"不支持读取 {suffix or '(无后缀)'} 文件。",
        hint="支持 PDF / DOCX / XLSX / PPTX 与常见纯文本格式。",
    )


def build_document_tools(ctx: ToolContext) -> list:
    """构造 ``read_document``：抽取文本并按字符偏移分段返回。"""

    @tool
    def read_document(path: str, offset: int = 0, limit: int = 0) -> str:
        """读取 PDF / Word / Excel / PowerPoint / 文本文件，返回抽取后的文字。

        适合读取用户放进工作区的文档；纯文本文件也可以继续用 read_file
        （带行号、更方便编辑）。文档很长时用 offset 分段读取。

        Args:
            path: 相对工作区的文件路径。
            offset: 从第几个字符开始返回，默认 0。
            limit: 本次最多返回多少字符；0 表示使用工具默认预算。

        Returns:
            首行为文档类型与总字符数，随后是正文；末尾提示下一段的 offset。
        """
        try:
            target = resolve_in_workspace(ctx.workspace, path)
        except WorkspacePathError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        if not target.exists():
            return tool_error("not_found", f"文件不存在：{path}。", hint="可以先用 list_dir 查看目录内容。")
        if target.is_dir():
            return tool_error("is_directory", f"{path} 是目录。", hint="请给出具体的文件路径。")
        try:
            if target.stat().st_size > _MAX_DOC_BYTES:
                return tool_error(
                    "file_too_large",
                    f"{path} 超过 {_MAX_DOC_BYTES // 1_000_000} MB，已拒绝解析。",
                    hint="请先拆分文件或只提取需要的部分。",
                )
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")

        try:
            label, full_text = _extract(target)
        except DocumentError as exc:
            return tool_error(exc.code, exc.message, hint=exc.hint)
        except OSError as exc:
            return tool_error("read_failed", f"读取 {path} 失败：{exc}。")

        if not full_text.strip():
            return tool_error(
                "empty_document",
                f"{path} 没有可提取的文字。",
                hint="如果这是扫描版 PDF 或图片，需要 OCR / 视觉模型才能识别。",
            )

        total = len(full_text)
        start = max(0, int(offset or 0))
        if start >= total:
            return tool_error(
                "offset_out_of_range",
                f"offset={start} 超过文档总长度 {total} 字符。",
                hint=f"请把 offset 设在 0..{max(0, total - 1)} 之间。",
            )
        budget = int(limit or 0)
        if budget <= 0:
            budget = max(1000, ctx.max_output_chars - 300)
        budget = min(budget, ctx.max_output_chars)
        end = min(total, start + budget)
        chunk = full_text[start:end]

        header = f"文档：{display_path(ctx.workspace, target)}（{label}；共 {total} 字符）"
        body = [header, chunk]
        if end < total:
            body.append(f"…还有 {total - end} 字符，用 offset={end} 继续。")
        return truncate("\n".join(body), ctx.max_output_chars)

    return [read_document]
