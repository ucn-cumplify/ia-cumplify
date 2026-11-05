"""JSON part of POST /api/v1/company-profiles/documentary-situation."""

import json
from collections.abc import Sequence

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ia_cumplify.adapters.inbound.http.schemas.classification import TokenUsageResponse
from ia_cumplify.adapters.inbound.http.schemas.evidence_review import OneLine, _upload_filename, _without_lone_surrogates
from ia_cumplify.domain.documentary_situation import (
    DOCUMENTARY_SITUATION_FILE_ID_MAX_CHARS,
    DOCUMENTARY_SITUATION_FILENAME_MAX_CHARS,
    DOCUMENTARY_SITUATION_MAX_FILES,
    DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES,
    DocumentarySituation,
    DocumentarySituationFile,
    DocumentarySituationRequest,
)


def too_many_files(count: int) -> str:
    return f"The request has {count} files; the limit is {DOCUMENTARY_SITUATION_MAX_FILES}."


def files_too_large(limit: int) -> str:
    return f"The files exceed {limit} bytes."


class DocumentarySituationFileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: OneLine = Field(..., max_length=DOCUMENTARY_SITUATION_FILE_ID_MAX_CHARS)
    filename: OneLine = Field(..., max_length=DOCUMENTARY_SITUATION_FILENAME_MAX_CHARS)

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


class DocumentarySituationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[DocumentarySituationFileBody] = Field(..., min_length=1, max_length=DOCUMENTARY_SITUATION_MAX_FILES)

    @field_validator("files")
    @classmethod
    def _unique_names(cls, files: list[DocumentarySituationFileBody]) -> list[DocumentarySituationFileBody]:
        names = [item.filename for item in files]
        if len(names) != len(set(names)):
            raise ValueError("Each filename must be unique.")
        ids = [item.id for item in files]
        if len(ids) != len(set(ids)):
            raise ValueError("Each file id must be unique.")
        return files

    def to_request(self, uploads: Sequence[tuple[str, bytes]]) -> DocumentarySituationRequest:
        contents = dict(uploads)
        return DocumentarySituationRequest(
            files=tuple(
                DocumentarySituationFile(id=item.id, filename=item.filename, content=contents[item.filename])
                for item in self.files
            )
        )


class ReviewedDocumentaryFileResponse(BaseModel):
    filename: str
    status: str


class DocumentarySituationResponse(BaseModel):
    content: str
    prompt_version: str
    usage: TokenUsageResponse
    files: list[ReviewedDocumentaryFileResponse]

    @classmethod
    def from_domain(cls, result: DocumentarySituation) -> "DocumentarySituationResponse":
        return cls(
            content=result.content,
            prompt_version=result.prompt_version,
            usage=TokenUsageResponse.from_domain(result.usage),
            files=[
                ReviewedDocumentaryFileResponse(filename=item.filename, status=item.status)
                for item in result.files
            ],
        )


def parse_documentary_situation(
    situation: str,
    uploads: Sequence[tuple[str | None, bytes]],
) -> DocumentarySituationRequest:
    body = _situation_body(situation)
    if len(uploads) > DOCUMENTARY_SITUATION_MAX_FILES:
        raise ValueError(too_many_files(len(uploads)))
    if sum(len(content) for _, content in uploads) > DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES:
        raise ValueError(files_too_large(DOCUMENTARY_SITUATION_MAX_TOTAL_BYTES))

    received: dict[str, bytes] = {}
    declared = {item.filename for item in body.files}
    for raw_name, content in uploads:
        filename = _upload_filename(raw_name)
        if filename not in declared:
            raise ValueError("An uploaded file is not listed in the situation.")
        if filename in received:
            raise ValueError("The same filename was uploaded more than once.")
        received[filename] = content
    if any(item.filename not in received for item in body.files):
        raise ValueError("A file listed in the situation was not uploaded.")
    return body.to_request([(item.filename, received[item.filename]) for item in body.files])


def _situation_body(situation: str) -> DocumentarySituationBody:
    try:
        parsed = json.loads(situation)
    except json.JSONDecodeError:
        raise RequestValidationError(
            [{"type": "json_invalid", "loc": ("body", "situation"), "msg": "The situation JSON is not valid."}]
        ) from None
    try:
        return DocumentarySituationBody.model_validate(parsed)
    except ValidationError as exc:
        raise RequestValidationError(
            [{**error, "loc": ("body", "situation", *error["loc"])} for error in exc.errors()]
        ) from None
