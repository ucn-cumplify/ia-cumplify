"""Text extraction for evidence files: PDF pages, Word with tables, Excel sheets,
CSV encoding and delimiter, and PowerPoint slides."""

import zipfile
from datetime import datetime
from io import BytesIO

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.domain.evidence_text import (
    BODY,
    ERROR,
    PAGE,
    READ,
    SHEET,
    SLIDE,
    UNREADABLE,
    UNSUPPORTED,
    ReadLimits,
)


def test_pdf_text_comes_back_per_page():
    content = _pdf(["Acta de constitucion del Comite Paritario.", "En la planta no funciona el Comite Paritario."])

    fragments = _fragments(".pdf", content)

    assert [(f.locator, f.reference, f.text) for f in fragments] == [
        (PAGE, "1", "Acta de constitucion del Comite Paritario."),
        (PAGE, "2", "En la planta no funciona el Comite Paritario."),
    ]


def test_docx_keeps_paragraphs_and_tables_in_order():
    document = Document()
    document.add_paragraph("Fecha: 3 de marzo de 2026.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Asistente"
    table.cell(0, 1).text = "Representación"
    table.cell(1, 0).text = "Representante de la empresa 1"
    table.cell(1, 1).text = "empresa"
    document.add_paragraph("No hay actas de otros meses de 2026.")
    content = _save(document.save)

    fragment = _fragments(".docx", content)[0]

    assert fragment.locator == BODY
    assert fragment.reference == ""
    assert fragment.text == "\n".join(
        [
            "Fecha: 3 de marzo de 2026.",
            "Asistente | Representación",
            "Representante de la empresa 1 | empresa",
            "No hay actas de otros meses de 2026.",
        ]
    )


def test_minimal_docx_package_is_readable():
    content = _minimal_docx("Fecha: 3 de marzo de 2026.")

    fragment = _fragments(".DOCX", content)[0]

    assert fragment.text == "Fecha: 3 de marzo de 2026."


def test_xlsx_is_read_per_sheet_with_values():
    workbook = Workbook()
    trucks = workbook.active
    trucks.title = "Camiones"
    trucks.append(["Patente", "Kilometraje", "Trabajo"])
    trucks.append(["BBCD-11", 120430, "Cambio de aceite"])
    dates = workbook.create_sheet("Fechas")
    dates["A1"] = datetime(2026, 4, 2)
    workbook.create_sheet("Vacia")
    content = _save(workbook.save)

    fragments = _fragments(".xlsx", content)

    assert [(f.locator, f.reference, f.text) for f in fragments] == [
        (SHEET, "Camiones", "Patente | Kilometraje | Trabajo\nBBCD-11 | 120430 | Cambio de aceite"),
        (SHEET, "Fechas", "2026-04-02"),
        (SHEET, "Vacia", ""),
    ]


def test_csv_detects_utf8_comma_cp1252_semicolon_and_tab():
    utf8 = "Nombre,RUT,Fecha de la última capacitación\nRepresentante de la empresa 1,15.111.111-1,2026-03-15\n".encode()
    cp1252 = "Nombre;Ciudad\nAna;Concepción\n".encode("cp1252")
    bom = b"\xef\xbb\xbf" + "Nombre;Nota\nAna;\"tiene coma, y punto\"\n".encode()
    tabs = "Patente\tTrabajo\nBBCD-11\tCambio de aceite\n".encode()

    assert _fragments(".csv", utf8)[0].text == (
        "Nombre,RUT,Fecha de la última capacitación\nRepresentante de la empresa 1,15.111.111-1,2026-03-15"
    )
    assert _fragments(".csv", cp1252)[0].text == "Nombre;Ciudad\nAna;Concepción"
    assert _fragments(".csv", bom)[0].text == "Nombre;Nota\nAna;tiene coma, y punto"
    assert _fragments(".csv", tabs)[0].text == "Patente\tTrabajo\nBBCD-11\tCambio de aceite"


def test_pptx_text_and_tables_come_back_per_slide():
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    first = presentation.slides.add_slide(blank)
    box = first.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    box.text_frame.text = "Comité Paritario"
    table_shape = first.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1))
    table_shape.table.cell(0, 0).text = "Nombre"
    table_shape.table.cell(0, 1).text = "RUT"
    table_shape.table.cell(1, 0).text = "Representante de la empresa 1"
    table_shape.table.cell(1, 1).text = "15.111.111-1"
    second = presentation.slides.add_slide(blank)
    note = second.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1))
    note.text_frame.text = "Segunda diapositiva"
    content = _save(presentation.save)

    fragments = _fragments(".pptx", content)

    assert [(f.locator, f.reference) for f in fragments] == [(SLIDE, "1"), (SLIDE, "2")]
    assert fragments[0].text == "\n".join(
        [
            "Comité Paritario",
            "Nombre | RUT",
            "Representante de la empresa 1 | 15.111.111-1",
        ]
    )
    assert fragments[1].text == "Segunda diapositiva"


def test_a_scanned_pdf_and_an_image_are_unreadable():
    scanned = extract_text(".pdf", _pdf(["", "   "]))
    image = extract_text(".png", b"\x89PNG\r\n\x1a\nnot-parsed")

    assert scanned.status == UNREADABLE
    assert scanned.detail is None
    assert [(fragment.reference, fragment.text) for fragment in scanned.fragments] == [("1", ""), ("2", "")]
    assert image.status == UNREADABLE
    assert image.fragments == ()
    assert extract_text(".jpg", b"not-a-jpeg").status == UNREADABLE


def test_an_unknown_extension_is_unsupported():
    result = extract_text(".txt", b"esto es texto, pero el formato no se lee")

    assert result.status == UNSUPPORTED
    assert result.fragments == ()
    assert result.detail is None
    assert extract_text(".doc", b"PK").status == UNSUPPORTED


def test_a_broken_file_is_an_error_and_keeps_no_fragments():
    result = extract_text(".pdf", b"this is not a pdf")

    assert result.status == ERROR
    assert result.fragments == ()
    assert result.detail
    assert extract_text(".docx", b"not a word file").status == ERROR


def test_too_many_pages_or_slides_is_an_error():
    pages = extract_text(".pdf", _pdf(["uno", "dos", "tres"]), ReadLimits(max_pages=2))
    slides = extract_text(".pptx", _slides(2), ReadLimits(max_pages=1))

    assert pages.status == ERROR
    assert pages.fragments == ()
    assert pages.detail == "The PDF has more than 2 pages."
    assert slides.status == ERROR
    assert slides.fragments == ()
    assert slides.detail == "Slide count is over the limit of 1."


def test_too_many_rows_is_an_error():
    csv_file = extract_text(".csv", b"a\nb\nc\nd\n", ReadLimits(max_rows=3))
    sheet = Workbook()
    sheet.active.append(["uno"])
    sheet.active.append(["dos"])
    sheet.active.append(["tres"])
    sheet.active.append(["cuatro"])
    excel = extract_text(".xlsx", _save(sheet.save), ReadLimits(max_rows=3))
    document = Document()
    table = document.add_table(rows=4, cols=1)
    for index, label in enumerate(("uno", "dos", "tres", "cuatro")):
        table.cell(index, 0).text = label
    word = extract_text(".docx", _save(document.save), ReadLimits(max_rows=3))

    assert csv_file.detail == "The file has more than 3 rows."
    assert excel.detail == "The file has more than 3 rows."
    assert word.detail == "The file has more than 3 rows."
    assert csv_file.fragments == excel.fragments == word.fragments == ()


def test_a_zip_that_declares_too_much_uncompressed_data_is_not_opened():
    content = _declare_uncompressed(_minimal_docx("Acta firmada."), 50_000_000)
    limits = ReadLimits(max_uncompressed_bytes=1024)

    for extension in (".docx", ".xlsx", ".pptx"):
        result = extract_text(extension, content, limits)
        assert result.status == ERROR
        assert result.fragments == ()
        assert result.detail == "Uncompressed size is over the limit of 1024 bytes."


def test_a_zip_with_too_many_entries_is_not_opened():
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in ("a.xml", "b.xml", "c.xml"):
            archive.writestr(name, b"<x/>")
    result = extract_text(".docx", buffer.getvalue(), ReadLimits(max_zip_entries=2))

    assert result.status == ERROR
    assert result.fragments == ()
    assert result.detail == "The archive has more than 2 entries."


def _fragments(extension: str, content: bytes):
    result = extract_text(extension, content)
    assert result.status == READ
    assert result.detail is None
    return result.fragments


def _save(save) -> bytes:
    buffer = BytesIO()
    save(buffer)
    return buffer.getvalue()


def _minimal_docx(paragraph: str) -> bytes:
    """Same package shape as the development seeder: document.xml and the two relationships."""
    import zipfile

    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f'<w:p><w:r><w:t xml:space="preserve">{paragraph}</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/>'
        "</Relationships>"
    )
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", rels)
        package.writestr("word/document.xml", document)
    return buffer.getvalue()


def _pdf(pages: list[str]) -> bytes:
    streams = []
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        streams.append(f"BT /F1 12 Tf 72 700 Td ({escaped}) Tj ET".encode("ascii"))

    objects: dict[int, bytes] = {}
    kids = " ".join(f"{4 + index * 2} 0 R" for index in range(len(streams)))
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(streams)} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    for index, stream in enumerate(streams):
        page_id = 4 + index * 2
        content_id = page_id + 1
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_id} 0 R /Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objects[content_id] = b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"

    chunks = [b"%PDF-1.4\n"]
    offsets = [0]
    for number in range(1, max(objects) + 1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n")
    xref_at = sum(len(chunk) for chunk in chunks)
    size = max(objects) + 1
    xref = [f"xref\n0 {size}\n".encode(), b"0000000000 65535 f \n"]
    xref.extend(f"{offsets[number]:010d} 00000 n \n".encode() for number in range(1, size))
    trailer = f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return b"".join([*chunks, *xref, trailer])


def _slides(count: int) -> bytes:
    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    for number in range(count):
        slide = presentation.slides.add_slide(blank)
        box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
        box.text_frame.text = f"Diapositiva {number + 1}"
    return _save(presentation.save)


def _declare_uncompressed(content: bytes, size: int) -> bytes:
    """Rewrite the ZIP directory so each entry claims ``size`` bytes uncompressed."""
    blob = bytearray(content)
    encoded = size.to_bytes(4, "little")
    for signature, offset in ((b"PK\x03\x04", 22), (b"PK\x01\x02", 24)):
        start = 0
        while True:
            index = blob.find(signature, start)
            if index < 0:
                break
            blob[index + offset : index + offset + 4] = encoded
            start = index + 4
    return bytes(blob)
