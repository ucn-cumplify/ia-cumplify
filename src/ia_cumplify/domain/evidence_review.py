from dataclasses import dataclass
from datetime import date

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
