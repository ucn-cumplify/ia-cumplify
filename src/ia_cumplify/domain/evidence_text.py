from dataclasses import dataclass
from typing import Literal

# Where a later citation can point. Word and CSV have no pages, sheets or slides.
PAGE = "page"
SHEET = "sheet"
SLIDE = "slide"
BODY = "body"

# leído, sin texto legible, no soportado, con error.
READ = "read"
UNREADABLE = "unreadable"
UNSUPPORTED = "unsupported"
ERROR = "error"

FileReadStatus = Literal["read", "unreadable", "unsupported", "error"]


@dataclass(frozen=True, slots=True)
class ExtractedFragment:
    """One locatable piece of text taken from an evidence file.

    ``reference`` is the 1-based page or slide number, or the sheet name.
    It is empty for a ``body`` fragment.
    """

    locator: str
    reference: str
    text: str


@dataclass(frozen=True, slots=True)
class ExtractedFile:
    """One evidence file after reading.

    ``fragments`` keeps the pages, sheets or slides even when every one is empty,
    so a scanned PDF still reports how many pages it has. ``detail`` is set only
    when ``status`` is ``error``. ``truncated`` is set when the extracted text
    passed the character cap and was cut.
    """

    status: FileReadStatus
    fragments: tuple[ExtractedFragment, ...]
    detail: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ReadLimits:
    """How far a single file may be opened.

    Word, Excel and PowerPoint are ZIP files: the uncompressed size is the sum
    declared in the central directory, checked before the package is parsed.
    Slides count toward ``max_pages``. Past ``max_chars``, the extracted text is
    cut and the file is marked; that is not an error.
    """

    max_pages: int = 100
    max_rows: int = 10_000
    max_uncompressed_bytes: int = 64 * 1024 * 1024
    max_zip_entries: int = 4_096
    max_chars: int = 200_000
