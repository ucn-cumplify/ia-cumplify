"""POST /api/v1/evidence-reviews.

The caller sends the article, its regulation, the obligation and the files. This endpoint
reads no database, the same way the chat answers from the passages of the request.
The review result is not produced here.
"""

import logging
from collections.abc import Sequence

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from ia_cumplify.adapters.inbound.http.schemas.evidence_review import (
    files_too_large,
    parse_evidence_review,
    too_many_files,
)
from ia_cumplify.domain.evidence_review import MAX_FILES, MAX_TOTAL_BYTES

router = APIRouter(prefix="/evidence-reviews", tags=["evidence-reviews"])
logger = logging.getLogger(__name__)

_READ_CHUNK = 64 * 1024


@router.post("", status_code=204)
async def create_evidence_review(
    review: str = Form(..., description="JSON: the article, its regulation, the obligation if any, and the files"),
    files: list[UploadFile] | None = File(default=None),
) -> Response:
    """Accept what the review will judge, and the files that should accredit it.

    ``review`` is one form field. Each file is a part named ``files``, and its filename
    matches one ``files[].filename`` in the JSON. Nothing is read from the database.
    """
    try:
        uploads = await _read_uploads(files or [])
        accepted = parse_evidence_review(review, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "Evidence review request accepted: %d files, %d bytes, reference date %s.",
        len(accepted.files),
        sum(len(item.content) for item in accepted.files),
        accepted.reference_date.isoformat(),
    )
    return Response(status_code=204)


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
