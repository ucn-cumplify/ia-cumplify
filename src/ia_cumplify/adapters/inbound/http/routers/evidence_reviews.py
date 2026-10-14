"""POST /api/v1/evidence-reviews.

The caller sends the article, its regulation, the obligation and the files. This endpoint
reads no database, the same way the chat answers from the passages of the request.
"""

import logging
from collections.abc import Sequence

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ia_cumplify.adapters.inbound.http.schemas.evidence_review import (
    EvidenceReviewResponse,
    files_too_large,
    parse_evidence_review,
    too_many_files,
)
from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.use_cases.review_evidence import assemble_evidence_review
from ia_cumplify.domain.evidence_review import (
    MAX_FILES,
    MAX_TOTAL_BYTES,
    EvidenceReviewRequest,
    ReviewJudgment,
)
from ia_cumplify.domain.evidence_text import ExtractedFile

router = APIRouter(prefix="/evidence-reviews", tags=["evidence-reviews"])
logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


def judgment_for(request: EvidenceReviewRequest) -> ReviewJudgment | None:
    """What the model concluded. None until that call is connected; the file statuses are still returned."""
    return None


@router.post("", response_model=EvidenceReviewResponse)
async def create_evidence_review(
    review: str = Form(..., description="JSON: the article, its regulation, the obligation if any, and the files"),
    files: list[UploadFile] | None = File(default=None),
) -> EvidenceReviewResponse:
    """Return each file's status and, when the model has run, the judgment of the article.

    ``review`` is one form field. Each file is a part named ``files``, and its filename
    matches one ``files[].filename`` in the JSON. Nothing is read from the database.
    The extracted text stays here: the response keeps the status, not the text.
    """
    try:
        uploads = await _read_uploads(files or [])
        accepted = parse_evidence_review(review, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    extracted = tuple(_read_file(item.filename, item.content) for item in accepted.files)
    result = assemble_evidence_review(accepted, extracted, judgment_for(accepted))
    logger.info(
        "Evidence review response: %d files (%s), reference date %s, review_version %s.",
        len(result.files),
        ", ".join(item.status for item in result.files) or "none",
        accepted.reference_date.isoformat(),
        result.review_version,
    )
    return EvidenceReviewResponse.from_domain(result)


def _read_file(filename: str, content: bytes) -> ExtractedFile:
    dot = filename.rfind(".")
    extension = filename[dot:] if dot > 0 else ""
    return extract_text(extension, content)


async def _read_uploads(files: Sequence[UploadFile]) -> list[tuple[str | None, bytes]]:
    """Read every part, and stop once the review caps are passed. The bytes past the cap are not kept."""
    if len(files) > MAX_FILES:
        raise ValueError(too_many_files(len(files)))
    uploads: list[tuple[str | None, bytes]] = []
    total = 0
    for upload in files:
        chunks: list[bytes] = []
        while True:
            chunk = await upload.read(_READ_CHUNK)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TOTAL_BYTES:
                raise ValueError(files_too_large(MAX_TOTAL_BYTES))
            chunks.append(chunk)
        uploads.append((upload.filename, b"".join(chunks)))
    return uploads
