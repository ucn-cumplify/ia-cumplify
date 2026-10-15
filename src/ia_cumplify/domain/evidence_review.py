from dataclasses import dataclass
from datetime import date
from typing import Literal

from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_text import ExtractedFragment, FileReadStatus

# Per review, the caps agreed for one call (plan O1). The backend applies the same ones
# before it sends the request. The character cap of extracted file text is separate.
MAX_FILES = 10
MAX_TOTAL_BYTES = 25 * 1024 * 1024

# Request caps. Lengths and positions may appear in a 422; the text itself must not.
FILE_ID_MAX_CHARS = 100
FILENAME_MAX_CHARS = 255
ARTICLE_NUMBER_MAX_CHARS = 50
ARTICLE_TEXT_MAX_CHARS = 200_000
TITLE_MAX_CHARS = 500
LEGAL_BODY_TYPE_MAX_CHARS = 200
LEGAL_BODY_NUMBER_MAX_CHARS = 50
OBLIGATION_NAME_MAX_CHARS = 500
OBLIGATION_DESCRIPTION_MAX_CHARS = 20_000


@dataclass(frozen=True, slots=True)
class ReviewArticle:
    """The article under review. The service does not look it up."""

    number: str
    text: str
    title: str | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewLegalBody:
    """The regulation the article belongs to, as the caller describes it."""

    title: str
    type: str | None = None
    number: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewObligation:
    """The row's obligation, when the row has one."""

    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewFile:
    """One uploaded file. ``id`` is the caller's id, trimmed."""

    id: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class EvidenceReviewRequest:
    """What a review is asked to judge, plus the files that should accredit it.

    ``reference_date`` is the calendar day used later for vigencia and deadlines.
    ``obligation`` is absent when the row has none.
    """

    reference_date: date
    article: ReviewArticle
    legal_body: ReviewLegalBody
    obligation: ReviewObligation | None
    files: tuple[ReviewFile, ...]


# obligaciones explícitas, informativo, remisión, información procedimental (C9, decisiones 7 y 21).
ArticleNature = Literal["explicit_obligations", "informative", "referral", "procedural"]
# acreditada, parcial, no acreditada, no evaluable.
RequirementStatus = Literal["accredited", "partial", "not_accredited", "not_evaluable"]
# página, hoja, diapositiva, o el cuerpo cuando el formato no tiene esas partes.
CitationLocator = Literal["page", "sheet", "slide", "body"]

# The respaldo of one exigencia, and the authorization a procedural article serves. Past the cap the
# text is dropped instead of failing the whole review.
MAX_REQUIREMENT_EVIDENCE_CHARS = 300
MAX_PROCEDURE_FOR_CHARS = 300

# Prompt id. The model name is appended once a review is actually run: "evidence-review-v2@<model>".
# v2 adopts decision 21 (procedural nature and the respaldo of each exigencia) and checks citations.
REVIEW_PROMPT_VERSION = "evidence-review-v2"


@dataclass(frozen=True, slots=True)
class ReviewedFile:
    """How one uploaded file came back from reading. The extracted text is not part of the response."""

    id: str
    filename: str
    status: FileReadStatus
    truncated: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewCitation:
    """A literal excerpt and where it sits in the file the caller sent."""

    file_id: str
    locator: CitationLocator
    reference: str
    quote: str


@dataclass(frozen=True, slots=True)
class ReviewRequirement:
    """One exigencia of the article, with the respaldo that accredits it.

    ``evidence`` is that respaldo, as a short name of the document or record. Without one it is not
    an exigencia (decision 21). The citations support the status, and ``missing`` says what does not
    appear.
    """

    text: str
    status: RequirementStatus
    citations: tuple[ReviewCitation, ...]
    missing: str
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class ReviewJudgment:
    """What the model concludes. File statuses are not part of it: reading the files decides those."""

    nature: ArticleNature
    requirements: tuple[ReviewRequirement, ...]
    verdict: RequirementStatus
    summary: str
    usage: TokenUsage
    model: str
    review_version: str
    procedure_for: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceReview:
    """The body of POST /api/v1/evidence-reviews."""

    files: tuple[ReviewedFile, ...]
    nature: ArticleNature | None
    requirements: tuple[ReviewRequirement, ...]
    verdict: RequirementStatus | None
    summary: str | None
    usage: TokenUsage
    model: str | None
    review_version: str
    procedure_for: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewSourceFile:
    """One file as the model sees it: the status and the text that was read, never the bytes."""

    id: str
    filename: str
    status: FileReadStatus
    truncated: bool
    fragments: tuple[ExtractedFragment, ...]


@dataclass(frozen=True, slots=True)
class EvidenceReviewSubject:
    """What a review judges. It is built from the request, not loaded from the database."""

    reference_date: date
    article: ReviewArticle
    legal_body: ReviewLegalBody
    obligation: ReviewObligation | None
    files: tuple[ReviewSourceFile, ...]
