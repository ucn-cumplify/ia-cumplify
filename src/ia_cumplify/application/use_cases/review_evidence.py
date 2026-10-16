"""Reviews one request: read the files, ask the model, return the review.

The model call goes through EvidenceReviewerPort. This use case does not read the database.
"""

from dataclasses import replace

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.ports.evidence_reviewer import EvidenceReviewerPort
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import (
    MAX_PROCEDURE_FOR_CHARS,
    MAX_REQUIREMENT_EVIDENCE_CHARS,
    REVIEW_PROMPT_VERSION,
    EvidenceReview,
    EvidenceReviewRequest,
    EvidenceReviewSubject,
    RequirementStatus,
    ReviewCitation,
    ReviewJudgment,
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
        judgment = _closed_when_nothing_to_verify(self._reviewer.review(_subject(request, extracted)))
        if judgment.nature == "explicit_obligations":
            requirements = _verified_requirements(judgment.requirements, _file_text(request, extracted))
            judgment = replace(judgment, requirements=requirements, verdict=_verdict(requirements))
        judgment = replace(judgment, review_version=self._reviewer.version)
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
        procedure_for=judgment.procedure_for,
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


# The mark the plan gives an exigencia whose quotes were not in the file that was read.
CITATION_NOT_FOUND = "cita no encontrada"


def _closed_when_nothing_to_verify(judgment: ReviewJudgment) -> ReviewJudgment:
    """Informative, referral and procedural articles have no exigencia to check.

    Only a procedural article keeps the authorization it serves. The other natures drop it.
    """
    procedure_for = None
    if judgment.nature == "procedural":
        procedure_for = _short_line(judgment.procedure_for, MAX_PROCEDURE_FOR_CHARS)
    if judgment.nature == "explicit_obligations":
        return replace(judgment, procedure_for=None)
    return replace(judgment, requirements=(), verdict="not_evaluable", procedure_for=procedure_for)


def _verified_requirements(
    requirements: tuple[ReviewRequirement, ...], texts: dict[str, str]
) -> tuple[ReviewRequirement, ...]:
    """Keep exigencia that name a respaldo, and only the quotes that appear in their file.

    Case and whitespace do not count. An accredited or partial exigencia with no quote left
    becomes not accredited and carries the mark.
    """
    folded = {file_id: _compact(text) for file_id, text in texts.items()}
    kept: list[ReviewRequirement] = []
    for item in requirements:
        text = " ".join(item.text.split())
        evidence = _short_line(item.evidence, MAX_REQUIREMENT_EVIDENCE_CHARS)
        if not text or evidence is None:
            continue
        citations = tuple(_found(citation, folded) for citation in item.citations)
        citations = tuple(citation for citation in citations if citation is not None)
        status: RequirementStatus = item.status
        missing = " ".join(item.missing.split())
        if status in {"accredited", "partial"} and not citations:
            status = "not_accredited"
            if CITATION_NOT_FOUND not in missing.casefold():
                missing = f"{missing} {CITATION_NOT_FOUND}".strip()
        kept.append(replace(item, text=text, evidence=evidence, status=status, citations=citations, missing=missing))
    return tuple(kept)


def _found(citation: ReviewCitation, folded: dict[str, str]) -> ReviewCitation | None:
    quote = _compact(citation.quote)
    file_text = folded.get(citation.file_id)
    if not quote or file_text is None or quote not in file_text:
        return None
    return replace(
        citation,
        file_id=citation.file_id.strip(),
        reference=citation.reference.strip(),
        quote=citation.quote.strip(),
    )


def _verdict(requirements: tuple[ReviewRequirement, ...]) -> RequirementStatus:
    """The review follows its exigencia. A mix that is not all one closed status is partial."""
    if not requirements:
        return "not_evaluable"
    statuses = {item.status for item in requirements}
    if statuses == {"accredited"}:
        return "accredited"
    if statuses == {"not_evaluable"}:
        return "not_evaluable"
    if statuses <= {"not_accredited", "not_evaluable"}:
        return "not_accredited"
    return "partial"


def _file_text(request: EvidenceReviewRequest, extracted: tuple[ExtractedFile, ...]) -> dict[str, str]:
    return {
        item.id: "\n".join(fragment.text for fragment in reading.fragments)
        for item, reading in zip(request.files, extracted, strict=True)
    }


def _short_line(text: str | None, max_chars: int) -> str | None:
    line = " ".join((text or "").split())
    if not line or len(line) > max_chars:
        return None
    return line


def _compact(text: str) -> str:
    """Letters and marks, without case or whitespace, so a quote can be found in the file."""
    return "".join(text.casefold().split())


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:] if dot > 0 else ""
