from dataclasses import dataclass

from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import FILE_ID_MAX_CHARS, FILENAME_MAX_CHARS, MAX_FILES, MAX_TOTAL_BYTES
from ia_cumplify.domain.evidence_text import FileReadStatus

DOCUMENTARY_SITUATION_PROMPT_VERSION = "documentary-situation-v1"
MAX_CONTENT_CHARS = 4000

# Same request caps as an evidence review: the backend applies them before it sends the pack.
DOCUMENTARY_SITUATION_MAX_FILES = MAX_FILES
DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES = MAX_TOTAL_BYTES
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
