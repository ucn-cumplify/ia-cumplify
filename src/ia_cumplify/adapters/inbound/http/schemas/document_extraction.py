"""Request checks and response of POST /api/v1/documents/extract (provisional contract)."""

from typing import Literal

from pydantic import BaseModel, Field

from ia_cumplify.domain.chat import replace_lone_surrogates
from ia_cumplify.domain.document_extraction import FILENAME_MAX_CHARS, MAX_FILE_BYTES, ExtractedDocument


class ExtractedFragmentResponse(BaseModel):
    locator: Literal["page", "sheet", "slide", "body"]
    reference: str = Field(..., description="1-based page or slide, sheet name, or empty for a body")
    text: str


class DocumentExtractionResponse(BaseModel):
    """``text`` is the fragments' texts joined with a blank line ("\\n\\n"). It is null, and
    ``fragments`` is empty, when ``status`` is not ``read``."""

    filename: str
    status: Literal["read", "unreadable", "unsupported", "error"]
    detail: str | None = None
    text: str | None = None
    fragments: list[ExtractedFragmentResponse]

    @classmethod
    def from_domain(cls, document: ExtractedDocument) -> "DocumentExtractionResponse":
        return cls(
            filename=document.filename,
            status=document.status,
            detail=document.detail,
            text=document.text,
            fragments=[
                ExtractedFragmentResponse(locator=item.locator, reference=item.reference, text=item.text)
                for item in document.fragments
            ],
        )


def file_too_large(limit: int = MAX_FILE_BYTES) -> str:
    return f"The file is larger than {limit // (1024 * 1024)} MB."


def upload_filename(filename: str | None) -> str:
    """The last path segment of the uploaded filename. A browser can send ``C:\\fakepath\\name``."""
    name = replace_lone_surrogates(str(filename or "")).replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in {".", ".."}:
        raise ValueError("The file needs a filename.")
    if len(name) > FILENAME_MAX_CHARS:
        raise ValueError(f"The filename is longer than {FILENAME_MAX_CHARS} characters.")
    if any(not character.isprintable() for character in name):
        raise ValueError("The filename must be a single line of printable characters.")
    return name
