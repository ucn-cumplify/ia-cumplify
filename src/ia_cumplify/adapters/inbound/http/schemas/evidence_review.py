"""JSON part of POST /api/v1/evidence-reviews.

The caller sends what to judge. This service does not read the article, the regulation
or the obligation from the database.
"""

import json
import re
from collections.abc import Sequence
from datetime import date
from typing import Annotated

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from ia_cumplify.adapters.inbound.http.schemas.classification import TokenUsageResponse
from ia_cumplify.domain.chat import replace_lone_surrogates
from ia_cumplify.domain.evidence_review import (
    ARTICLE_NUMBER_MAX_CHARS,
    ARTICLE_TEXT_MAX_CHARS,
    FILE_ID_MAX_CHARS,
    FILENAME_MAX_CHARS,
    LEGAL_BODY_NUMBER_MAX_CHARS,
    LEGAL_BODY_TYPE_MAX_CHARS,
    MAX_FILES,
    MAX_TOTAL_BYTES,
    OBLIGATION_DESCRIPTION_MAX_CHARS,
    OBLIGATION_NAME_MAX_CHARS,
    TITLE_MAX_CHARS,
    ArticleNature,
    CitationLocator,
    EvidenceReview,
    EvidenceReviewRequest,
    RequirementStatus,
    ReviewArticle,
    ReviewFile,
    ReviewLegalBody,
    ReviewObligation,
)

# A single line: no line break of str.splitlines anywhere. A pattern keeps the value out of the 422.
_ONE_LINE = "^[^\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]+$"
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

OneLine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=_ONE_LINE)]
# Article text and an obligation description keep their line breaks.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _without_lone_surrogates(value: object) -> object:
    return replace_lone_surrogates(value) if isinstance(value, str) else value


def _blank_as_missing(value: object) -> object:
    """Null, or a string that is empty after trimming, means the optional field was not sent."""
    value = _without_lone_surrogates(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


class ReviewFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: OneLine = Field(..., max_length=FILE_ID_MAX_CHARS, description="Caller id of the file. Not a path")
    filename: OneLine = Field(..., max_length=FILENAME_MAX_CHARS)

    @field_validator("id", "filename", mode="before")
    @classmethod
    def _ids_without_lone_surrogates(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("filename")
    @classmethod
    def _single_segment(cls, filename: str) -> str:
        if "/" in filename or "\\" in filename or filename in {".", ".."}:
            raise ValueError("The filename must be a single path segment.")
        return filename


class ReviewArticleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: OneLine = Field(..., max_length=ARTICLE_NUMBER_MAX_CHARS)
    text: Text = Field(..., max_length=ARTICLE_TEXT_MAX_CHARS)
    title: OneLine | None = Field(default=None, max_length=TITLE_MAX_CHARS)
    section: OneLine | None = Field(default=None, max_length=TITLE_MAX_CHARS)

    @field_validator("number", "text", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("title", "section", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> object:
        return _blank_as_missing(value)


class ReviewLegalBodyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: OneLine = Field(..., max_length=TITLE_MAX_CHARS)
    type: OneLine | None = Field(default=None, max_length=LEGAL_BODY_TYPE_MAX_CHARS)
    number: OneLine | None = Field(default=None, max_length=LEGAL_BODY_NUMBER_MAX_CHARS)

    @field_validator("title", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("type", "number", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> object:
        return _blank_as_missing(value)


class ReviewObligationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: OneLine = Field(..., max_length=OBLIGATION_NAME_MAX_CHARS)
    description: Text | None = Field(default=None, max_length=OBLIGATION_DESCRIPTION_MAX_CHARS)

    @field_validator("name", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("description", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> object:
        return _blank_as_missing(value)


class EvidenceReviewBody(BaseModel):
    """The ``review`` form field. Optional fields accept null, as System.Text.Json sends them."""

    model_config = ConfigDict(extra="forbid")

    reference_date: date = Field(..., description="Calendar day, YYYY-MM-DD, for vigencia and deadlines")
    article: ReviewArticleBody
    legal_body: ReviewLegalBodyBody
    obligation: ReviewObligationBody | None = None
    files: list[ReviewFileBody] = Field(default_factory=list, max_length=MAX_FILES)

    @field_validator("reference_date", mode="before")
    @classmethod
    def _calendar_date(cls, value: object) -> object:
        if isinstance(value, str) and _DATE.fullmatch(value) is None:
            raise ValueError("reference_date must be a calendar date, YYYY-MM-DD.")
        return value

    @field_validator("files", mode="before")
    @classmethod
    def _null_files(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("files")
    @classmethod
    def _unique_files(cls, files: list[ReviewFileBody]) -> list[ReviewFileBody]:
        ids = [item.id for item in files]
        if len(ids) != len(set(ids)):
            raise ValueError("File ids must be unique.")
        names = [item.filename for item in files]
        if len(names) != len(set(names)):
            raise ValueError("File names must be unique.")
        return files

    def to_request(self, uploads: Sequence[tuple[str, bytes]]) -> EvidenceReviewRequest:
        by_name = {filename: content for filename, content in uploads}
        return EvidenceReviewRequest(
            reference_date=self.reference_date,
            article=ReviewArticle(
                number=self.article.number,
                text=self.article.text,
                title=self.article.title,
                section=self.article.section,
            ),
            legal_body=ReviewLegalBody(
                title=self.legal_body.title,
                type=self.legal_body.type,
                number=self.legal_body.number,
            ),
            obligation=(
                None
                if self.obligation is None
                else ReviewObligation(name=self.obligation.name, description=self.obligation.description)
            ),
            files=tuple(
                ReviewFile(id=item.id, filename=item.filename, content=by_name[item.filename]) for item in self.files
            ),
        )


def files_too_large(limit: int = MAX_TOTAL_BYTES) -> str:
    return f"The files are over the limit of {limit} bytes."


def too_many_files(count: int) -> str:
    return f"The review has {count} files; the limit is {MAX_FILES}."


def parse_evidence_review(review: str, uploads: Sequence[tuple[str | None, bytes]]) -> EvidenceReviewRequest:
    """Validate the JSON and pair each declared file with the upload of the same filename.

    ``uploads`` are ``(filename, content)`` as received. A path in the upload filename is
    reduced to its last segment. The JSON filename is already a single segment.
    """
    body = _review_body(review)
    if len(uploads) > MAX_FILES:
        raise ValueError(too_many_files(len(uploads)))
    if sum(len(content) for _, content in uploads) > MAX_TOTAL_BYTES:
        raise ValueError(files_too_large(MAX_TOTAL_BYTES))

    received: dict[str, bytes] = {}
    for raw_name, content in uploads:
        filename = _upload_filename(raw_name)
        if filename not in {item.filename for item in body.files}:
            raise ValueError("An uploaded file is not listed in the review.")
        if filename in received:
            raise ValueError("The same filename was uploaded more than once.")
        received[filename] = content
    if any(item.filename not in received for item in body.files):
        raise ValueError("A file listed in the review was not uploaded.")
    return body.to_request([(item.filename, received[item.filename]) for item in body.files])


def _review_body(review: str) -> EvidenceReviewBody:
    try:
        parsed = json.loads(review)
    except json.JSONDecodeError:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body", "review"), "msg": "The review JSON is not valid."}]
        ) from None
    try:
        return EvidenceReviewBody.model_validate(parsed)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", "review", *error["loc"])} for error in exc.errors()]
        ) from None


def _upload_filename(filename: str | None) -> str:
    if not filename or not str(filename).strip():
        raise ValueError("Each uploaded file needs a filename.")
    name = str(filename).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise ValueError("Each uploaded file needs a filename.")
    return name


class ReviewCitationResponse(BaseModel):
    file_id: str
    locator: CitationLocator
    reference: str = Field(..., description="1-based page or slide, sheet name, or empty for a body")
    quote: str


class ReviewRequirementResponse(BaseModel):
    text: str
    evidence: str = Field(..., description="The respaldo that accredits the exigencia. Without one, the exigencia is dropped")
    status: RequirementStatus
    citations: list[ReviewCitationResponse]
    missing: str = Field(
        ...,
        description=(
            "What does not appear in the files. Empty when nothing is missing. "
            "It does not recommend an action. «cita no encontrada» marks a quote that was not in the file"
        ),
    )


class ReviewedFileResponse(BaseModel):
    id: str
    filename: str
    status: str
    truncated: bool
    detail: str | None = None


class EvidenceReviewResponse(BaseModel):
    """What POST /api/v1/evidence-reviews returns. The extracted text of a file is not included."""

    files: list[ReviewedFileResponse]
    nature: ArticleNature | None = Field(
        default=None,
        description="explicit_obligations, informative, referral or procedural. Null before the model runs",
    )
    procedure_for: str | None = Field(
        default=None,
        description="For a procedural article, the authorization it serves. Null for the other natures",
    )
    requirements: list[ReviewRequirementResponse]
    verdict: RequirementStatus | None = None
    summary: str | None = None
    usage: TokenUsageResponse
    model: str | None = None
    review_version: str

    @classmethod
    def from_domain(cls, result: EvidenceReview) -> "EvidenceReviewResponse":
        return cls(
            files=[
                ReviewedFileResponse(
                    id=item.id,
                    filename=item.filename,
                    status=item.status,
                    truncated=item.truncated,
                    detail=item.detail,
                )
                for item in result.files
            ],
            nature=result.nature,
            procedure_for=result.procedure_for,
            requirements=[
                ReviewRequirementResponse(
                    text=item.text,
                    evidence=item.evidence,
                    status=item.status,
                    citations=[
                        ReviewCitationResponse(
                            file_id=citation.file_id,
                            locator=citation.locator,
                            reference=citation.reference,
                            quote=citation.quote,
                        )
                        for citation in item.citations
                    ],
                    missing=item.missing,
                )
                for item in result.requirements
            ],
            verdict=result.verdict,
            summary=result.summary,
            usage=TokenUsageResponse.from_domain(result.usage),
            model=result.model,
            review_version=result.review_version,
        )
