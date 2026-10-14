"""Text extraction for evidence files: PDF pages, Word with tables, Excel sheets,
CSV encoding and delimiter, and PowerPoint slides."""

from datetime import datetime
from io import BytesIO

from docx import Document
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.domain.evidence_text import BODY, PAGE, SHEET, SLIDE


def test_pdf_text_comes_back_per_page():
    content = _pdf(["Acta de constitucion del Comite Paritario.", "En la planta no funciona el Comite Paritario."])

    fragments = extract_text(".pdf", content)

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

    fragment = extract_text(".docx", content)[0]

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

    fragment = extract_text(".DOCX", content)[0]

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

    fragments = extract_text(".xlsx", content)

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

    assert extract_text(".csv", utf8)[0].text == (
        "Nombre,RUT,Fecha de la última capacitación\nRepresentante de la empresa 1,15.111.111-1,2026-03-15"
    )
    assert extract_text(".csv", cp1252)[0].text == "Nombre;Ciudad\nAna;Concepción"
    assert extract_text(".csv", bom)[0].text == "Nombre;Nota\nAna;tiene coma, y punto"
    assert extract_text(".csv", tabs)[0].text == "Patente\tTrabajo\nBBCD-11\tCambio de aceite"


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

    fragments = extract_text(".pptx", content)

    assert [(f.locator, f.reference) for f in fragments] == [(SLIDE, "1"), (SLIDE, "2")]
    assert fragments[0].text == "\n".join(
        [
            "Comité Paritario",
            "Nombre | RUT",
            "Representante de la empresa 1 | 15.111.111-1",
        ]
    )
    assert fragments[1].text == "Segunda diapositiva"


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
