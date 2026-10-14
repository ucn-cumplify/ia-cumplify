"""Reviews one request: read the files, ask the model, return the review.

The model call goes through EvidenceReviewerPort. This use case does not read the database.
"""

from dataclasses import replace

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.ports.evidence_reviewer import EvidenceReviewerPort
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import (
    REVIEW_PROMPT_VERSION,
    EvidenceReview,
    EvidenceReviewRequest,
    EvidenceReviewSubject,
    ReviewRequirement,
    ReviewSourceFile,
    ReviewedFile,
)
from ia_cumplify.domain.evidence_text import ExtractedFile, ReadLimits


class ReviewEvidenceUseCase:
    """Reads each file and asks the reviewer to judge the article. It keeps no state and no database."""

    def __init__(self, reviewer: EvidenceReviewerPort, limits: ReadLimits | None = None) -> None:
        self._reviewer = reviewer
        self._limits = limits if limits is not None else ReadLimits()

    def execute(self, request: EvidenceReviewRequest) -> EvidenceReview:
        extracted = tuple(
            extract_text(_extension(item.filename), item.content, self._limits) for item in request.files
        )
        judgment = self._reviewer.review(_subject(request, extracted))
        known = {item.id for item in request.files}
        judgment = replace(
            judgment,
            review_version=self._reviewer.version,
            requirements=_known_requirements(judgment.requirements, known),
        )
        return assemble_evidence_review(request, extracted, judgment)


def assemble_evidence_review(
    request: EvidenceReviewRequest,
    extracted: tuple[ExtractedFile, ...],
    judgment,
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


def _subject(request: EvidenceReviewRequest, extracted: tuple[ExtractedFile, ...]) -> EvidenceReviewSubject:
    return EvidenceReviewSubject(
        reference_date=request.reference_date,
        article=request.article,
        legal_body=request.legal_body,
        obligation=request.obligation,
        files=tuple(
            ReviewSourceFile(
                id=item.id,
                filename=item.filename,
                status=reading.status,
                truncated=reading.truncated,
                fragments=reading.fragments,
            )
            for item, reading in zip(request.files, extracted, strict=True)
        ),
    )


def _known_requirements(
    requirements: tuple[ReviewRequirement, ...], known: set[str]
) -> tuple[ReviewRequirement, ...]:
    kept: list[ReviewRequirement] = []
    for item in requirements:
        text = item.text.strip()
        if not text:
            continue
        citations = tuple(
            citation for citation in item.citations if citation.file_id in known and citation.quote.strip()
        )
        kept.append(replace(item, text=text, citations=citations))
    return tuple(kept)


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:] if dot > 0 else ""
