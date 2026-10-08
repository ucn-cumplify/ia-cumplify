import re

_IMAGE_PLACEHOLDER = "[imagen omitida]"
# Any other type, such as a PDF the BCN attached to the article.
_FILE_PLACEHOLDER = "[archivo omitido]"

# The top-level MIME type ("image", "application", ...). The placeholder depends on it.
_MIME_TYPE = r"(?P<type>[a-z][a-z0-9.+-]*)"

# Markdown images whose target is a data URI of any type: from "![" to the first "]", then
# "](data:<type>/..." up to the next ")". The backend writes BCN attachments this way.
_MARKDOWN_IMAGE_START = re.compile(r"!\[")
_MARKDOWN_DATA_TARGET = re.compile(rf"\]\(\s*data:{_MIME_TYPE}\/[^)]+\)", re.IGNORECASE)

# HTML <img> whose src is a data URI (quoted or not): from "<img" to the first ">".
_IMG_TAG_START = re.compile(r"<img\b", re.IGNORECASE)
_IMG_SRC_DATA_URI = re.compile(rf"\bsrc\s*=\s*(?:[\"']\s*)?data:{_MIME_TYPE}\/[^>\s\"']", re.IGNORECASE)

# Leftover data URIs (src leftovers, CSS, raw blobs, a Markdown target the first pass did not take).
# Base64 wrapped over several lines continues only with long lines (40+ characters, longer than any real
# word), and a short last line only if it ends with "=" padding, so the text that follows is never consumed.
_DATA_URI = re.compile(
    rf"data:{_MIME_TYPE}\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/]+=*"
    r"(?:\s+[A-Za-z0-9+/]{40,}=*)*"
    r"(?:\s+[A-Za-z0-9+/]{1,39}={1,2}(?![A-Za-z0-9+/=]))?",
    re.IGNORECASE,
)


def strip_base64_images(text: str) -> str:
    """Drop embedded images and other data URIs (attachments) so they do not consume model tokens.

    An image becomes "[imagen omitida]" and any other type "[archivo omitido]". The Markdown and <img>
    passes scan the text once: a single regex for each was quadratic on text with many "![" or "<img" and
    no closing character, about a second within the chat limits.
    """
    if not text:
        return text
    # Every pattern needs "data:".
    if "data:" not in text.casefold():
        return text

    cleaned = _strip_markdown_images(text)
    cleaned = _strip_img_tags(cleaned)
    cleaned = _DATA_URI.sub(lambda match: _placeholder(match["type"]), cleaned)
    return cleaned


def _placeholder(mime_type: str) -> str:
    return _IMAGE_PLACEHOLDER if mime_type.lower() == "image" else _FILE_PLACEHOLDER


def _strip_markdown_images(text: str) -> str:
    """Linear: when the target after the first "]" is not a data URI, no "![" before that "]" has one."""
    parts: list[str] = []
    kept_from = 0
    position = 0
    last_paren = text.rfind(")")
    while (start := _MARKDOWN_IMAGE_START.search(text, position)) is not None:
        close = text.find("]", start.end())
        if close == -1 or close > last_paren:
            break
        target = _MARKDOWN_DATA_TARGET.match(text, close)
        if target is not None:
            parts.append(text[kept_from : start.start()])
            parts.append(_placeholder(target["type"]))
            kept_from = position = target.end()
        else:
            position = close + 1
    parts.append(text[kept_from:])
    return "".join(parts)


def _strip_img_tags(text: str) -> str:
    """Linear: when a tag has no data URI src, no "<img" before its ">" has one either."""
    parts: list[str] = []
    kept_from = 0
    position = 0
    while (start := _IMG_TAG_START.search(text, position)) is not None:
        end = text.find(">", start.end())
        if end == -1:
            break
        source = _IMG_SRC_DATA_URI.search(text, start.end(), end)
        if source is not None:
            parts.append(text[kept_from : start.start()])
            parts.append(_placeholder(source["type"]))
            kept_from = end + 1
        position = end + 1
    parts.append(text[kept_from:])
    return "".join(parts)
