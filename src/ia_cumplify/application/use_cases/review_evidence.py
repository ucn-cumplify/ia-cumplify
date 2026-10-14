"""Builds the body of a review from the files that were read and, when present, the model judgment.

The model call itself is separate. Without it the response still reports each file, and the
judgment fields stay empty.
"""

from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import (
    REVIEW_PROMPT_VERSION,
    EvidenceReview,
    EvidenceReviewRequest,
    ReviewJudgment,
    ReviewedFile,
)
from ia_cumplify.domain.evidence_text import ExtractedFile


def assemble_evidence_review(
    request: EvidenceReviewRequest,
    extracted: tuple[ExtractedFile, ...],
    judgment: ReviewJudgment | None,
) -> EvidenceReview:
    """Pair each uploaded file with the text that was read from it, in the same order."""
    if len(extracted) != len(request.files):
        raise ValueError("Each uploaded file needs a read result.")
    files = tuple(
        ReviewedFile(
            id=item.id,
            filename=item.filename,
            status=reading.status,
            truncated=reading.truncated,
            detail=reading.detail,
        )
        for item, reading in zip(request.files, extracted, strict=True)
    )
    if judgment is None:
        return EvidenceReview(
            files=files,
            nature=None,
            requirements=(),
            verdict=None,
            summary=None,
            usage=TokenUsage(),
            model=None,
            review_version=REVIEW_PROMPT_VERSION,
        )
    return EvidenceReview(
        files=files,
        nature=judgment.nature,
        requirements=judgment.requirements,
        verdict=judgment.verdict,
        summary=judgment.summary,
        usage=judgment.usage,
        model=judgment.model,
        review_version=judgment.review_version,
    )
