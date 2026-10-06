"""Citation markers of the chat.

The model cites passages by key ("[P3]"). The client receives numbered markers ("[1]", "[2]"... by
order of first appearance) and, in the done event, the list that maps each number to a passage id.
Changing any rule here, or the not_covered mark of domain/chat.py, bumps CHAT_PROMPT_VERSION
(adapters/outbound/openai/chat_prompts.py).
"""

import re

from ia_cumplify.domain.chat import NOT_COVERED_WORD

# Longest bracketed span held back while waiting for its "]", counting both brackets.
MARKER_SPAN_MAX_CHARS = 64
# Spans cut by a later "[" that wait, all together, for the span after them to resolve.
_PENDING_MAX_CHARS = 2 * MARKER_SPAN_MAX_CHARS

# A key is "P" or "p", optional spaces and digits, written as a word of its own: "P3", "p3" and "P 3"
# are keys, "P2O5" is not. The output filter and the input neutralization share this definition.
# Digits are any Unicode decimal digit, so "[３]" is treated as a number too.
_KEY = re.compile(r"(?<!\w)[Pp]\s*(\d+)(?!\w)")
# One key, or a list separated by "," ";" or "y": "[P1]", "[P1, P3]", "[p1; P3]", "[P1 y P3]".
_KEY_LIST = re.compile(r"\[\s*[Pp]\s*\d+(?:(?:\s*[,;]\s*|\s+y\s+)[Pp]\s*\d+)*\s*\]")
_NUMBER = re.compile(r"\[\s*\d+\s*\]")
# A span cut before its "]" that can still become a bracketed number. Group 1: it has digits.
_NUMBER_START = re.compile(r"\[\s*(\d+\s*)?")
# Starts only where a run of spaces starts: unanchored, each position inside a long run rescans it.
_ASSISTANT_NUMBER = re.compile(r"(?<![ \t])[ \t]*\[\s*\d+\s*\]")
# The mark is matched ignoring case and spaces inside the brackets, here and in _could_become_mark.
_MARK = re.compile(rf"\[\s*{re.escape(NOT_COVERED_WORD)}\s*\]", re.IGNORECASE)
_MARK_WORD = NOT_COVERED_WORD.casefold()
_SPAN = re.compile(r"\[[^\[\]]*\]")
_MAX_ROUNDS = 32
_NO_BRACKETS = str.maketrans("[]", "()")


def neutralize_markers(text: str, *, assistant: bool = False) -> str:
    """Keeps a marker written in the input from becoming a citation.

    Removes the not_covered mark (and, for an assistant, earlier "[n]" markers), then parenthesizes
    every bracketed span with a key or only a number ("[P2]" to "(P2)"). Both steps repeat until nothing
    changes: a removal can join a new span ("[P[1]1]"), and "[a [P1] b]" keeps a key after its inner
    span changes. _MAX_ROUNDS caps the cost of crafted input, which then loses all its brackets.
    """
    for _ in range(_MAX_ROUNDS):
        cleaned = _ASSISTANT_NUMBER.sub("", text) if assistant else text
        cleaned = _MARK.sub("", cleaned)
        if cleaned == text:
            break
        text = cleaned
    else:
        return text.translate(_NO_BRACKETS)
    # Parentheses never create a mark or a bracketed number, so nothing is left to remove after this.
    for _ in range(_MAX_ROUNDS):
        cleaned = _SPAN.sub(_parenthesize, text)
        if cleaned == text:
            return text
        text = cleaned
    return text.translate(_NO_BRACKETS)


def _parenthesize(match: re.Match[str]) -> str:
    span = match.group(0)
    if _NUMBER.fullmatch(span) or _KEY.search(span):
        return f"({span[1:-1]})"
    return span


def _could_become_mark(held: str) -> bool:
    """Whether a span cut before its "]" is the start of the not_covered mark.

    Completed with the rest of the word and "]", it has to match _MARK itself: str.casefold() and
    re.IGNORECASE disagree on "ı" and "İ".
    """
    body = held[1:].lstrip()
    return _MARK.fullmatch(held + _MARK_WORD[len(body) :] + "]") is not None


class CitationFilter:
    """Turns the keys the model writes into numbered markers while the answer streams.

    feed() returns the text that can be emitted now and finish() what is still held when the stream
    ends. A span from "[" to "]" is held until it closes, so a marker split between fragments comes
    out whole, in a single piece of text. Whitespace before the first visible text is dropped.

    A span cut by another "[" waits for the span after it. If that one is removed, it counts as never
    written and the cut span goes on: "[3[P9]]" is the number "[3]", removed too. Otherwise removing
    could join a number or the not_covered mark in the emitted text.
    """

    def __init__(self, passage_count: int) -> None:
        self._passage_count = passage_count
        self._numbers: dict[int, int] = {}  # key number -> marker number
        self._held: str | None = None
        # Spans cut by a later "[", outermost first, not emitted yet. Empty whenever _held is None.
        self._pending: list[str] = []
        self._visible = False
        self._not_covered = False
        self._dropped = 0

    @property
    def visible(self) -> bool:
        """Whether some visible text was emitted."""
        return self._visible

    @property
    def not_covered(self) -> bool:
        """Whether the answer opened with the not_covered mark."""
        return self._not_covered

    @property
    def dropped(self) -> int:
        """Markers the model wrote and the filter removed as invalid."""
        return self._dropped

    @property
    def cited_keys(self) -> tuple[int, ...]:
        """Key numbers in marker order: the first one is [1]."""
        return tuple(self._numbers)

    def feed(self, text: str) -> str:
        out: list[str] = []
        i = 0
        while i < len(text):
            if self._held is None:
                start = text.find("[", i)
                if start < 0:
                    out.append(self._plain(text[i:]))
                    break
                out.append(self._plain(text[i:start]))
                self._held = "["
                i = start + 1
                continue
            char = text[i]
            i += 1
            if char == "[":
                # Another "[" before the "]": what was held is a span without its end.
                held, self._held = self._held, "["
                if _KEY.search(held):
                    self._dropped += 1
                else:
                    out.append(self._keep_pending(held))
            elif char == "]":
                span, self._held = self._held + "]", None
                out.append(self._settle(self._resolve_span(span)))
            else:
                self._held += char
                if len(self._held) >= MARKER_SPAN_MAX_CHARS:
                    # Not a marker: the rest is plain text up to the next "[", unless a span it cut resumes.
                    held, self._held = self._held, None
                    out.append(self._settle(self._resolve_capped(held)))
        return "".join(out)

    def finish(self) -> str:
        out = ""
        while self._held is not None and not out:
            held, self._held = self._held, None
            # The start of the not_covered mark is dropped as well.
            out = self._settle("" if _could_become_mark(held) else self._resolve_unclosed(held))
        return out

    def discard(self) -> None:
        """Drops what is held. After a refusal nothing else is emitted."""
        self._held = None
        self._pending.clear()

    def _plain(self, text: str) -> str:
        if not self._visible:
            text = text.lstrip()
            self._visible = bool(text)
        return text

    def _show(self, text: str) -> str:
        self._visible = True
        return text

    def _keep_pending(self, held: str) -> str:
        self._pending.append(held)
        out: list[str] = []
        while sum(map(len, self._pending)) > _PENDING_MAX_CHARS:
            # A removal could still close the oldest one: it goes out without its "[".
            out.append(self._show("(" + self._pending.pop(0)[1:]))
        return "".join(out)

    def _settle(self, released: str) -> str:
        """Emits what a span became, after the spans waiting for it; "" means it was removed."""
        if released:
            pending = "".join(self._pending)
            self._pending.clear()
            return self._show(pending + released)
        if self._pending:
            self._held = self._pending.pop()
        return ""

    def _resolve_span(self, span: str) -> str:
        if _MARK.fullmatch(span):
            # It counts only before any visible text; anywhere else it is just removed.
            if not self._visible and not self._pending:
                self._not_covered = True
            return ""
        if _NUMBER.fullmatch(span):
            # In the emitted text a bracketed number is always a valid marker: the model's are dropped.
            self._dropped += 1
            return ""
        if _KEY_LIST.fullmatch(span):
            return self._markers(span)
        if _KEY.search(span):
            # A range ("[P1-P3]") or any other span around a key.
            self._dropped += 1
            return ""
        return span

    def _resolve_unclosed(self, held: str) -> str:
        if _KEY.search(held):
            self._dropped += 1
            return ""
        return held

    def _resolve_capped(self, held: str) -> str:
        """64 characters without "]". Text follows, so the start of a number or of the mark is dropped:
        "[" + 63 digits + "]" would come out as a bracketed number."""
        number = _NUMBER_START.fullmatch(held)
        if number is not None:
            if number.group(1) is not None:
                self._dropped += 1
            return ""
        if _could_become_mark(held):
            return ""
        return self._resolve_unclosed(held)

    def _markers(self, span: str) -> str:
        markers: list[str] = []
        seen: set[int] = set()
        for match in _KEY.finditer(span):
            key = int(match.group(1))
            if not 1 <= key <= self._passage_count:
                self._dropped += 1
            elif key not in seen:
                seen.add(key)
                number = self._numbers.setdefault(key, len(self._numbers) + 1)
                markers.append(f"[{number}]")
        return "".join(markers)
