"""POST /api/v1/documents/extract.

Returns the text of one knowledge-base document so backend-cumplify can index it (sprint 3
plan, step 2B.7). It uses the evidence extractor, calls no model and reads no database.
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ia_cumplify.adapters.inbound.http.schemas.document_extraction import (
    DocumentExtractionResponse,
    file_too_large,
    upload_filename,
)
from ia_cumplify.application.use_cases.extract_document import ExtractDocumentUseCase
from ia_cumplify.domain.document_extraction import MAX_FILE_BYTES

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


def get_extract_document_use_case() -> ExtractDocumentUseCase:
    return ExtractDocumentUseCase()


@router.post("/extract", response_model=DocumentExtractionResponse)
async def extract_document(
    file: list[UploadFile] | None = File(default=None, description="The document, in one part named file"),
    use_case: ExtractDocumentUseCase = Depends(get_extract_document_use_case),
) -> DocumentExtractionResponse:
    """Return the document's status, its text and its pages, sheets or slides.

    Only a ``read`` document carries text. A document is never returned in part.
    """
    try:
        if not file or len(file) != 1:
            raise ValueError("Send exactly one file, in a part named file.")
        filename = upload_filename(file[0].filename)
        content = await _read_upload(file[0])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Parsing a 25 MB file blocks. On the event loop it would hold every other request, /health included.
    document = await run_in_threadpool(use_case.execute, filename, content)
    # Neither the filename nor the text: both can carry company data.
    logger.info(
        "Document extraction: %d bytes, status %s, %d fragments.",
        len(content),
        document.status,
        len(document.fragments),
    )
    return DocumentExtractionResponse.from_domain(document)


async def _read_upload(upload: UploadFile) -> bytes:
    """Read the part, and stop once the cap is passed. The bytes past the cap are not kept."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_FILE_BYTES:
            raise ValueError(file_too_large(MAX_FILE_BYTES))
        chunks.append(chunk)
    return b"".join(chunks)
