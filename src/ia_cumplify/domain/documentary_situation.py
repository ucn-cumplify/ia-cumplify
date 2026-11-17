from dataclasses import dataclass

from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import FILE_ID_MAX_CHARS, FILENAME_MAX_CHARS
from ia_cumplify.domain.evidence_text import FileReadStatus

DOCUMENTARY_SITUATION_PROMPT_VERSION = "documentary-situation-v2"
MAX_CONTENT_CHARS = 4000

# The documentary pack is not an evidence review: 50 files, 20 MB each, not 10 files and 25 MB total.
DOCUMENTARY_SITUATION_MAX_FILES = 50
DOCUMENTARY_SITUATION_MAX_FILE_BYTES = 20 * 1024 * 1024
DOCUMENTARY_SITUATION_FILE_ID_MAX_CHARS = FILE_ID_MAX_CHARS
DOCUMENTARY_SITUATION_FILENAME_MAX_CHARS = FILENAME_MAX_CHARS


@dataclass(frozen=True, slots=True)
class DocumentarySituationFile:
    """One uploaded file. The service does not look it up."""

    id: str
    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DocumentarySituationRequest:
    files: tuple[DocumentarySituationFile, ...]


@dataclass(frozen=True, slots=True)
class DocumentarySituationSubjectFile:
    """One file after reading, as it goes to the model."""

    id: str
    filename: str
    status: FileReadStatus
    fragments: tuple[str, ...]
    truncated: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DocumentarySituationSubject:
    files: tuple[DocumentarySituationSubjectFile, ...]


@dataclass(frozen=True, slots=True)
class DocumentarySituationJudgment:
    content: str
    usage: TokenUsage
    model: str | None
    prompt_version: str


@dataclass(frozen=True, slots=True)
class ReviewedDocumentaryFile:
    filename: str
    status: FileReadStatus


@dataclass(frozen=True, slots=True)
class DocumentarySituation:
    content: str
    prompt_version: str
    usage: TokenUsage
    model: str | None
    files: tuple[ReviewedDocumentaryFile, ...]
