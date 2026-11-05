"""Extract one company documentary situation from uploaded files."""

from io import BytesIO

from docx import Document

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.use_cases.extract_documentary_situation import ExtractDocumentarySituationUseCase
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.documentary_situation import (
    DOCUMENTARY_SITUATION_PROMPT_VERSION,
    DocumentarySituationFile,
    DocumentarySituationJudgment,
    DocumentarySituationRequest,
    DocumentarySituationSubject,
)
from ia_cumplify.domain.evidence_text import READ, UNREADABLE


class RecordingExtractor:
    def __init__(self) -> None:
        self.subjects: list[DocumentarySituationSubject] = []

    @property
    def version(self) -> str:
        return f"{DOCUMENTARY_SITUATION_PROMPT_VERSION}@test"

    def extract(self, subject: DocumentarySituationSubject) -> DocumentarySituationJudgment:
        self.subjects.append(subject)
        readable = " ".join(
            fragment for item in subject.files for fragment in item.fragments if item.status == READ
        )
        return DocumentarySituationJudgment(
            content=f"Situación: {readable}"[:4000],
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15, llm_calls=1),
            model="test",
            prompt_version=self.version,
        )


def test_pdf_and_docx_are_read_into_one_situation() -> None:
    extractor = RecordingExtractor()
    request = DocumentarySituationRequest(
        files=(
            DocumentarySituationFile("pdf", "rca.pdf", _pdf(["RCA 12 autoriza 40 m3."])),
            DocumentarySituationFile("docx", "memoria.docx", _docx("Estanque de 40 m3.")),
        )
    )

    result = ExtractDocumentarySituationUseCase(extractor).execute(request)

    assert [item.status for item in result.files] == [READ, READ]
    assert "RCA 12 autoriza 40 m3." in result.content
    assert "Estanque de 40 m3." in result.content
    assert extractor.subjects


def test_an_unreadable_image_does_not_wipe_a_readable_pdf() -> None:
    extractor = RecordingExtractor()
    request = DocumentarySituationRequest(
        files=(
            DocumentarySituationFile("pdf", "rca.pdf", _pdf(["RCA 99 autoriza 12 m2."])),
            DocumentarySituationFile("img", "foto.png", b"\x89PNG\r\n\x1a\nnot-parsed"),
        )
    )

    result = ExtractDocumentarySituationUseCase(extractor).execute(request)

    assert [item.status for item in result.files] == [READ, UNREADABLE]
    assert "RCA 99 autoriza 12 m2." in result.content
    assert extract_text(".png", b"\x89PNG\r\n\x1a\nnot-parsed").status == UNREADABLE
    subject = extractor.subjects[0]
    assert subject.files[0].status == READ
    assert subject.files[1].status == UNREADABLE


def test_all_unreadable_skips_the_model() -> None:
    extractor = RecordingExtractor()
    request = DocumentarySituationRequest(
        files=(DocumentarySituationFile("img", "foto.jpg", b"\xff\xd8not-parsed"),)
    )

    result = ExtractDocumentarySituationUseCase(extractor).execute(request)

    assert result.content == ""
    assert result.files[0].status == UNREADABLE
    assert extractor.subjects == []
    assert result.usage.llm_calls == 0


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


def _docx(paragraph: str) -> bytes:
    document = Document()
    document.add_paragraph(paragraph)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()
