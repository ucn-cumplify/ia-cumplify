"""One document of the knowledge base after extraction (sprint 3 plan, step 2B.7).

The contract is provisional until backend-cumplify loads the base (step 2B.6).
"""

from dataclasses import dataclass

from ia_cumplify.domain.evidence_text import ExtractedFragment, FileReadStatus, ReadLimits

MAX_FILE_BYTES = 25 * 1024 * 1024
FILENAME_MAX_CHARS = 255
# Counted in UTF-16 code units, the way the backend measures a string (string.Length). Its embedding
# planner sends every chunk of a document in one request of up to 200,000 characters.
MAX_TEXT_UTF16_UNITS = 200_000

# text is every fragment of a read document, in order, joined with this. A blank line is the paragraph
# break the backend chunker splits on, so a page, sheet or slide never runs into the next one.
FRAGMENT_SEPARATOR = "\n\n"

# The evidence review limits. Provisional for the base: its format is not known yet.
DOCUMENT_READ_LIMITS = ReadLimits(max_chars=MAX_TEXT_UTF16_UNITS)

# The extractor's own detail can quote the file (a parser message), so it never leaves the service.
UNREADABLE_FILE_DETAIL = "The file is damaged or encrypted, or it passes a page, row or archive limit."
TEXT_TOO_LONG_DETAIL = f"The text is longer than {MAX_TEXT_UTF16_UNITS} characters."


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """``text`` is set only when ``status`` is ``read``; otherwise there is no text and no fragment.

    ``fragments`` keeps only the pieces with text, and ``text`` is exactly their texts joined with
    FRAGMENT_SEPARATOR.
    """

    filename: str
    status: FileReadStatus
    detail: str | None
    text: str | None
    fragments: tuple[ExtractedFragment, ...]
