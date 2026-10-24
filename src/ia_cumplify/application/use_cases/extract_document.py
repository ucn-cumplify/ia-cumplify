"""Extracts the text of one knowledge-base document with the evidence extractor.

A document is never returned in part: one whose text was cut, or whose joined text passes the
backend's limit, is an error with no text. This use case does not read the database.
"""

from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.domain.chat import replace_lone_surrogates
from ia_cumplify.domain.document_extraction import (
    DOCUMENT_READ_LIMITS,
    FRAGMENT_SEPARATOR,
    MAX_TEXT_UTF16_UNITS,
    TEXT_TOO_LONG_DETAIL,
    UNREADABLE_FILE_DETAIL,
    ExtractedDocument,
)
from ia_cumplify.domain.evidence_text import ERROR, READ, ExtractedFragment, FileReadStatus, ReadLimits


class ExtractDocumentUseCase:
    def __init__(self, limits: ReadLimits | None = None) -> None:
        self._limits = limits if limits is not None else DOCUMENT_READ_LIMITS

    def execute(self, filename: str, content: bytes) -> ExtractedDocument:
        reading = extract_text(_extension(filename), content, self._limits)
        if reading.status == ERROR:
            return _without_text(filename, ERROR, UNREADABLE_FILE_DETAIL)
        if reading.status != READ:
            return _without_text(filename, reading.status, None)
        if reading.truncated:
            return _without_text(filename, ERROR, TEXT_TOO_LONG_DETAIL)

        # System.Text.Json cannot read a lone surrogate, and UTF-16 cannot count one: U+FFFD keeps every length.
        fragments = tuple(
            ExtractedFragment(
                fragment.locator,
                replace_lone_surrogates(fragment.reference),
                replace_lone_surrogates(fragment.text),
            )
            for fragment in reading.fragments
            if fragment.text.strip()
        )
        text = FRAGMENT_SEPARATOR.join(fragment.text for fragment in fragments)
        if utf16_length(text) > MAX_TEXT_UTF16_UNITS:
            return _without_text(filename, ERROR, TEXT_TOO_LONG_DETAIL)
        return ExtractedDocument(filename, READ, None, text, fragments)


def utf16_length(text: str) -> int:
    """Length as C# string.Length counts it: a character outside the BMP is two units."""
    return len(text.encode("utf-16-le")) // 2


def _without_text(filename: str, status: FileReadStatus, detail: str | None) -> ExtractedDocument:
    return ExtractedDocument(filename, status, detail, None, ())


def _extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:] if dot > 0 else ""
