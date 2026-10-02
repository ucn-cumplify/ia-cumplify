import re

_PLACEHOLDER = "[imagen omitida]"

# Markdown images whose target is a data URI.
_MARKDOWN_DATA_IMAGE = re.compile(
    r"!\[[^\]]*\]\(\s*data:image\/[^)]+\)",
    re.IGNORECASE,
)

# HTML <img> whose src is a data URI (quoted or not).
_IMG_TAG_DATA_URI = re.compile(
    r"<img\b[^>]*?\bsrc\s*=\s*(?:[\"']\s*)?data:image\/[^>\s\"']+[^>]*>",
    re.IGNORECASE | re.DOTALL,
)

# Leftover data URIs (src leftovers, CSS, raw blobs). Base64 wrapped over several lines continues only
# with long lines (40+ characters, longer than any real word), and a short last line only if it ends with
# "=" padding, so the text that follows the image is never consumed.
_DATA_IMAGE_URI = re.compile(
    r"data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+=*"
    r"(?:\s+[A-Za-z0-9+/]{40,}=*)*"
    r"(?:\s+[A-Za-z0-9+/]{1,39}={1,2}(?![A-Za-z0-9+/=]))?",
    re.IGNORECASE,
)


def strip_base64_images(text: str) -> str:
    """Drop embedded images so they do not consume model tokens."""
    if not text:
        return text
    if "base64" not in text.casefold() and "data:image" not in text.casefold():
        return text

    cleaned = _MARKDOWN_DATA_IMAGE.sub(_PLACEHOLDER, text)
    cleaned = _IMG_TAG_DATA_URI.sub(_PLACEHOLDER, cleaned)
    cleaned = _DATA_IMAGE_URI.sub(_PLACEHOLDER, cleaned)
    return cleaned
