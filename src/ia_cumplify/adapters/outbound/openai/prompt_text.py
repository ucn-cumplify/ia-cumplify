"""Lines of the request data that could pass for a delimiter of the prompt.

A prompt sends the request data in blocks that open and close with a delimiter line, such as
"--- PASAJE P1 ---" in the chat. A line of the data that imitates one, with three dashes of any kind at its
start, gets the "> " prefix, so it can neither close its block nor open another one. The rules are those of
CHT-007 in docs/Chat; each prompt module says which of its texts go through here.
"""

import unicodedata

from ia_cumplify.adapters.outbound.openai.strip_images import strip_base64_images

# Opening of every language-model system prompt except the chat. The chat already says it works for
# Cumplify in its own first sentence (chat-v4) and is not rebuilt from this constant.
CUMPLIFY_ROLE = "You work for Cumplify, a Chilean legal-compliance platform."


def neutralize_delimiter_lines(text: str) -> str:
    """Prefixes "> " to every line that could pass for a passage delimiter."""
    lines = text.splitlines(keepends=True)
    return "".join(f"> {line}" if _looks_like_delimiter(line) else line for line in lines)


def block_text(text: str) -> str:
    """A multi-line text of the request as it goes inside a block: without its data URIs, and with its
    delimiter lines neutralized."""
    return neutralize_delimiter_lines(strip_base64_images(text))


def classification_block(text: str) -> str:
    """Law text inside a classification block.

    Delimiter lines are neutralized like any other block, and a line that could pass for an
    "Article ID:" header is neutralized too: the classifier separates articles with that header.
    """
    return neutralize_article_id_lines(block_text(text))


def neutralize_article_id_lines(text: str) -> str:
    """Prefixes "> " to every line that could pass for an Article ID header."""
    lines = text.splitlines(keepends=True)
    return "".join(f"> {line}" if _looks_like_article_id(line) else line for line in lines)


# Invisible characters outside the C, Mn and Me categories: the Hangul fillers, which are
# Default_Ignorable_Code_Point (a property unicodedata does not expose), and two blank symbols.
_BLANK_CHARACTERS = frozenset("\u115f\u1160\u3164\uffa0\u2800\U0001d159")


def _is_invisible(char: str) -> bool:
    category = unicodedata.category(char)
    return category[0] == "C" or category in ("Mn", "Me") or char in _BLANK_CHARACTERS


def _looks_like_article_id(line: str) -> bool:
    """An "Article ID:" header, once invisibles and a leading quote marker are set aside."""
    if line.startswith("> "):
        return False
    probe = _probe(line)
    return probe.startswith("Article ID:")


def _probe(line: str) -> str:
    probe = "".join(char for char in line if not _is_invisible(char))
    return unicodedata.normalize("NFKC", probe).lstrip()


def _looks_like_delimiter(line: str) -> bool:
    """Three or more dashes of any kind at the start, even with spaces between them.

    Invisible characters (control, format, unassigned and private-use code points, combining marks,
    Hangul fillers and blank symbols) are removed before NFKC, so a visible prefix such as "´" keeps
    the line as content. The probe only decides: the line is sent as written, since NFKC would also
    turn "Nº" into "No".
    """
    probe = _probe(line)
    dashes = 0
    for char in probe:
        if char == "\u2212" or unicodedata.category(char) == "Pd":
            dashes += 1
            if dashes == 3:
                return True
        elif not char.isspace():
            return False
    return False
