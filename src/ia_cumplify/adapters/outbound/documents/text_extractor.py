"""Reads the text out of an evidence file.

The file comes back as locatable fragments: one per PDF page, per Excel sheet and per
PowerPoint slide, and a single body for Word (paragraphs and tables in document order)
and for CSV. Each file also comes back with a status: read, unreadable (a scanned PDF
or an image), unsupported or error.

A file past the page, row or uncompressed-size limit is an error and is not returned
in part. Cutting text that passes the character cap is not applied here.
"""

from __future__ import annotations

import csv
import zipfile
from collections.abc import Callable
from datetime import date, datetime
from io import BytesIO, StringIO

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from openpyxl import load_workbook
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pypdf import PdfReader

from ia_cumplify.domain.evidence_text import (
    BODY,
    ERROR,
    PAGE,
    READ,
    SHEET,
    SLIDE,
    UNREADABLE,
    UNSUPPORTED,
    ExtractedFile,
    ExtractedFragment,
    ReadLimits,
)

# Raster images have no text layer. They are not parsed.
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"})

_READERS: dict[str, Callable[[bytes, ReadLimits], tuple[ExtractedFragment, ...]]] = {}

DEFAULT_READ_LIMITS = ReadLimits()


class ReadLimitExceeded(Exception):
    """The file is larger than ReadLimits allows. It becomes status ``error``."""


def extract_text(
    extension: str,
    content: bytes,
    limits: ReadLimits = DEFAULT_READ_LIMITS,
) -> ExtractedFile:
    """Read ``content``. ``extension`` includes the dot, as in ``.pdf``.

    A reader that raises, including a limit, becomes ``error`` and does not
    propagate. An extension with no reader is ``unsupported``. An image, or a
    file whose fragments are all blank, is ``unreadable``.
    """
    normalized = extension.strip().lower()
    if normalized and not normalized.startswith("."):
        normalized = f".{normalized}"

    if normalized in _IMAGE_EXTENSIONS:
        return ExtractedFile(UNREADABLE, ())

    reader = _READERS.get(normalized)
    if reader is None:
        return ExtractedFile(UNSUPPORTED, ())

    try:
        fragments = reader(content, limits)
    except Exception as exc:
        return ExtractedFile(ERROR, (), _error_detail(exc))

    if not any(fragment.text.strip() for fragment in fragments):
        return ExtractedFile(UNREADABLE, fragments)
    return ExtractedFile(READ, fragments)


def _error_detail(exc: Exception) -> str:
    message = " ".join(str(exc).split())
    if not message:
        return type(exc).__name__
    return message[:500]


def extract_pdf(content: bytes, limits: ReadLimits) -> tuple[ExtractedFragment, ...]:
    reader = PdfReader(BytesIO(content))
    if len(reader.pages) > limits.max_pages:
        raise ReadLimitExceeded(f"The PDF has more than {limits.max_pages} pages.")
    return tuple(
        ExtractedFragment(PAGE, str(number), (page.extract_text() or "").strip())
        for number, page in enumerate(reader.pages, start=1)
    )


def extract_docx(content: bytes, limits: ReadLimits) -> tuple[ExtractedFragment, ...]:
    _guard_zip(content, limits)
    document = Document(BytesIO(content))
    parts: list[str] = []
    seen_rows = 0
    for block in _iter_blocks(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                parts.append(text)
            continue
        seen_rows = _count_rows(len(block.rows), seen_rows, limits)
        rendered = _render_table(block)
        if rendered:
            parts.append(rendered)
    return (ExtractedFragment(BODY, "", "\n".join(parts)),)


def extract_xlsx(content: bytes, limits: ReadLimits) -> tuple[ExtractedFragment, ...]:
    # read_only walks rows without building the full cell graph. data_only keeps the
    # stored value; a formula without a cached result comes back empty.
    _guard_zip(content, limits)
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        fragments: list[ExtractedFragment] = []
        seen_rows = 0
        for sheet in workbook.worksheets:
            lines: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                seen_rows = _count_rows(1, seen_rows, limits)
                cells = [_cell_text(value) for value in row]
                if any(cells):
                    lines.append(" | ".join(cells))
            fragments.append(ExtractedFragment(SHEET, sheet.title, "\n".join(lines)))
        return tuple(fragments)
    finally:
        workbook.close()


def extract_csv(content: bytes, limits: ReadLimits) -> tuple[ExtractedFragment, ...]:
    text = _decode_csv(content)
    delimiter = _detect_delimiter(text)
    rendered_rows: list[str] = []
    seen_rows = 0
    for row in csv.reader(StringIO(text), delimiter=delimiter):
        seen_rows = _count_rows(1, seen_rows, limits)
        if any(cell.strip() for cell in row):
            rendered_rows.append(delimiter.join(cell.strip() for cell in row))
    return (ExtractedFragment(BODY, "", "\n".join(rendered_rows)),)


def extract_pptx(content: bytes, limits: ReadLimits) -> tuple[ExtractedFragment, ...]:
    _guard_zip(content, limits)
    presentation = Presentation(BytesIO(content))
    if len(presentation.slides) > limits.max_pages:
        raise ReadLimitExceeded(f"Slide count is over the limit of {limits.max_pages}.")
    fragments: list[ExtractedFragment] = []
    for number, slide in enumerate(presentation.slides, start=1):
        chunks: list[str] = []
        for shape in slide.shapes:
            chunks.extend(_shape_chunks(shape))
        fragments.append(ExtractedFragment(SLIDE, str(number), "\n".join(chunks)))
    return tuple(fragments)


def _guard_zip(content: bytes, limits: ReadLimits) -> None:
    """Refuse a ZIP whose central directory declares too much uncompressed data.

    A hostile Word, Excel or PowerPoint file can be tiny on disk and huge once
    opened. The sizes are read from the directory, and the package is not parsed
    when they pass the limit. A file that is not a ZIP is left to the reader.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile:
        return
    with archive:
        infos = archive.infolist()
        if len(infos) > limits.max_zip_entries:
            raise ReadLimitExceeded(f"The archive has more than {limits.max_zip_entries} entries.")
        total = 0
        for info in infos:
            size = info.file_size
            total += size
            if size > limits.max_uncompressed_bytes or total > limits.max_uncompressed_bytes:
                raise ReadLimitExceeded(
                    f"Uncompressed size is over the limit of {limits.max_uncompressed_bytes} bytes."
                )


def _count_rows(count: int, seen: int, limits: ReadLimits) -> int:
    seen += count
    if seen > limits.max_rows:
        raise ReadLimitExceeded(f"The file has more than {limits.max_rows} rows.")
    return seen


def _iter_blocks(document: Document):
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _render_table(table: Table) -> str:
    lines: list[str] = []
    for row in table.rows:
        cells = [" ".join(cell.text.split()) for cell in row.cells]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _cell_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        if value.hour == value.minute == value.second == value.microsecond == 0:
            return value.date().isoformat()
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _decode_csv(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    if content.startswith(b"\xef\xbb\xbf"):
        return content.decode("utf-8-sig")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252")


# Semicolon before comma: Chilean spreadsheets exported as CSV use ";".
_DELIMITERS = (";", ",", "\t", "|")


def _detect_delimiter(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()][:20]
    if not lines:
        return ","
    best: tuple[int, int, str] | None = None
    for delimiter in _DELIMITERS:
        counts = [line.count(delimiter) for line in lines]
        present = sum(count > 0 for count in counts)
        if present == 0:
            continue
        score = (present, sum(counts))
        if best is None or score > best[:2]:
            best = (*score, delimiter)
    return best[2] if best is not None else ","


def _shape_chunks(shape) -> list[str]:
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        chunks: list[str] = []
        for child in shape.shapes:
            chunks.extend(_shape_chunks(child))
        return chunks
    if getattr(shape, "has_table", False) and shape.has_table:
        lines: list[str] = []
        for row in shape.table.rows:
            cells = [" ".join(cell.text.split()) for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
        return ["\n".join(lines)] if lines else []
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        text = shape.text_frame.text.strip()
        return [text] if text else []
    return []


_READERS[".pdf"] = extract_pdf
_READERS[".docx"] = extract_docx
_READERS[".xlsx"] = extract_xlsx
_READERS[".csv"] = extract_csv
_READERS[".pptx"] = extract_pptx
