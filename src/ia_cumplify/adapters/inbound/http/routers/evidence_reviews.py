"""POST /api/v1/evidence-reviews.

The caller sends the article, its regulation, the obligation and the files. This endpoint
reads no database, the same way the chat answers from the passages of the request.
"""

import logging
from collections.abc import Sequence

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from ia_cumplify.adapters.inbound.http.dependencies import get_evidence_reviewer
from ia_cumplify.adapters.inbound.http.schemas.evidence_review import (
    EvidenceReviewResponse,
    files_too_large,
    parse_evidence_review,
    too_many_files,
)
from ia_cumplify.application.ports.evidence_reviewer import EvidenceReviewerPort
from ia_cumplify.application.use_cases.review_evidence import ReviewEvidenceUseCase
from ia_cumplify.domain.evidence_review import MAX_FILES, MAX_TOTAL_BYTES
from ia_cumplify.domain.exceptions import EvidenceReviewError

router = APIRouter(prefix="/evidence-reviews", tags=["evidence-reviews"])
logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


def get_review_evidence_use_case(
    reviewer: EvidenceReviewerPort = Depends(get_evidence_reviewer),
) -> ReviewEvidenceUseCase:
    return ReviewEvidenceUseCase(reviewer)


@router.post("", response_model=EvidenceReviewResponse)
async def create_evidence_review(
    review: str = Form(..., description="JSON: the article, its regulation, the obligation if any, and the files"),
    files: list[UploadFile] | None = File(default=None),
    use_case: ReviewEvidenceUseCase = Depends(get_review_evidence_use_case),
) -> EvidenceReviewResponse:
    """Return each file's status and the model's judgment of the article.

    ``review`` is one form field. Each file is a part named ``files``, and its filename
    matches one ``files[].filename`` in the JSON. Nothing is read from the database.
    The extracted text is sent to the model and is not part of the response.
    """
    try:
        uploads = await _read_uploads(files or [])
        accepted = parse_evidence_review(review, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        # Reading the files and the model call block for up to the review timeout. On the event
        # loop they would hold every other request of this single process, /health included; in
        # the thread pool they run the way FastAPI runs a sync endpoint.
        result = await run_in_threadpool(use_case.execute, accepted)
    except EvidenceReviewError as exc:
        logger.error("Evidence review failed for %d files: %s", len(accepted.files), type(exc).__name__)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    logger.info(
        "Evidence review response: %d files (%s), reference date %s, review_version %s.",
        len(result.files),
        ", ".join(item.status for item in result.files) or "none",
        accepted.reference_date.isoformat(),
        result.review_version,
    )
    return EvidenceReviewResponse.from_domain(result)


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
