from dataclasses import dataclass

# Where a later citation can point. Word and CSV have no pages, sheets or slides.
PAGE = "page"
SHEET = "sheet"
SLIDE = "slide"
BODY = "body"


@dataclass(frozen=True, slots=True)
class ExtractedFragment:
    """One locatable piece of text taken from an evidence file.

    ``reference`` is the 1-based page or slide number, or the sheet name.
    It is empty for a ``body`` fragment.
    """

    locator: str
    reference: str
    text: str
