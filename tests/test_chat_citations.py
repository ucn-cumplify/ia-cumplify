import re
from time import perf_counter

import pytest
from chat_doubles import chunked

from ia_cumplify.application.use_cases.chat_citations import (
    MARKER_SPAN_MAX_CHARS,
    CitationFilter,
    neutralize_markers,
)
from ia_cumplify.domain.chat import NOT_COVERED_MARK

_MARKER = re.compile(r"\[\d+\]")
# What the filter treats as a bracketed number.
_ANY_NUMBER = re.compile(r"\[\s*\d+\s*\]")


def filter_text(chunks: list[str], passages: int = 3) -> tuple[str, CitationFilter]:
    citation_filter = CitationFilter(passages)
    out = "".join(citation_filter.feed(chunk) for chunk in chunks)
    return out + citation_filter.finish(), citation_filter


def filter_whole(text: str, passages: int = 3) -> tuple[str, CitationFilter]:
    return filter_text([text], passages)


@pytest.mark.parametrize(
    ("raw", "expected", "dropped", "cited"),
    [
        ("Dice X [P1].", "Dice X [1].", 0, (1,)),
        ("A [P1][P3].", "A [1][2].", 0, (1, 3)),
        # Numbered by first appearance; a passage cited again keeps its number.
        ("A [P3]. B [P1]. C [P3].", "A [1]. B [2]. C [1].", 0, (3, 1)),
        # Lists come out as consecutive markers, in the written order and without repeating a passage.
        ("A [P1, P2].", "A [1][2].", 0, (1, 2)),
        ("A [P1; P2].", "A [1][2].", 0, (1, 2)),
        ("A [P1 y P2].", "A [1][2].", 0, (1, 2)),
        ("A [P2,P1;P3 y P1].", "A [1][2][3].", 0, (2, 1, 3)),
        ("A [P1, P1].", "A [1].", 0, (1,)),
        ("A [P1]. B [P2, P1].", "A [1]. B [2][1].", 0, (1, 2)),
        # Keys not assigned in this request: each one is removed and counted.
        ("A [P1, P9].", "A [1].", 1, (1,)),
        ("A [P9].", "A .", 1, ()),
        ("A [P0].", "A .", 1, ()),
        ("A [P9, P10].", "A .", 2, ()),
        # Lowercase keys, spaces around and inside a key.
        ("A [p1].", "A [1].", 0, (1,)),
        ("A [ P1 ].", "A [1].", 0, (1,)),
        ("A [P 2].", "A [1].", 0, (2,)),
        ("A [P01].", "A [1].", 0, (1,)),
        # Any other span around a key is removed whole and counts 1.
        ("A [P1-P3].", "A .", 1, ()),
        ("A [ver P2].", "A .", 1, ()),
        ("A [P1 P2].", "A .", 1, ()),
        ("A [P1, ].", "A .", 1, ()),
        ("A [P1 Y P2].", "A .", 1, ()),
        ("A [(P2)].", "A .", 1, ()),
        # A bracketed number the model wrote is always removed: in the output it means a citation.
        ("A [3].", "A .", 1, ()),
        ("A [ 12 ].", "A .", 1, ()),
        ("A [\uff13].", "A .", 1, ()),
        # Spans without a key pass as written.
        ("P2O5 [P2O5] [sic] [imagen omitida] [] [3.] [x1]", "P2O5 [P2O5] [sic] [imagen omitida] [] [3.] [x1]", 0, ()),
        ("A [P1yP2].", "A [P1yP2].", 0, ()),
        # A key outside brackets is plain text.
        ("El pasaje P1 dice algo.", "El pasaje P1 dice algo.", 0, ()),
        ("Un ] suelto.", "Un ] suelto.", 0, ()),
        # A removed span counts as never written: the span it cut goes on with what follows. Emitting
        # "[1" would turn "[1[P9]]" into a citation the model never made.
        ("A [P2]. B [1[P9]].", "A [1]. B .", 2, (2,)),
        ("A [3[P9]].", "A .", 2, ()),
        ("A [3[5]].", "A .", 2, ()),
        ("A [3[P1-P3]].", "A .", 2, ()),
        ("A [[P9]3].", "A .", 2, ()),
        ("A [ 3 [P9] ].", "A .", 2, ()),
        ("A [1[2[P9]]].", "A .", 3, ()),
        ("A [3[P9][P8]].", "A .", 3, ()),
        ("A [2[SIN_RESPALDO]].", "A .", 1, ()),
        ("A [SIN_[P9]RESPALDO].", "A .", 1, ()),
        ("A [ver [P9] P2].", "A .", 2, ()),
        ("A [P[P9]1].", "A [1].", 1, (1,)),
        ("A [3[P1" + " " * 61 + "]].", "A ].", 2, ()),
        ("A [P2]. B [[0]1].", "A [1]. B .", 2, (2,)),
        ("A [P1] y [[SIN_RESPALDO]2].", "A [1] y .", 1, (1,)),
        ("A [P1][P2] y [[P1-P3]2].", "A [1][2] y .", 2, (1, 2)),
        ("A [P1]. B [S\u0131N_RESPALDO[P9]] fin.", "A [1]. B  fin.", 1, (1,)),
        # When the inner span stays, the cut one can no longer become a number or the mark: it goes out
        # as written (api.md, "[nota [P1]").
        ("A [3[P1]].", "A [3[1]].", 0, (1,)),
        ("A [3[sic]].", "A [3[sic]].", 0, ()),
        ("Ver [12 [P1].", "Ver [12 [1].", 0, (1,)),
        ("A [1 2 [P1].", "A [1 2 [1].", 0, (1,)),
        ("[SIN_RESPALDO[x] Texto [P1].", "[SIN_RESPALDO[x] Texto [1].", 0, (1,)),
    ],
)
def test_markers(raw: str, expected: str, dropped: int, cited: tuple[int, ...]) -> None:
    out, citation_filter = filter_whole(raw)
    assert out == expected
    assert citation_filter.dropped == dropped
    assert citation_filter.cited_keys == cited


def test_another_bracket_before_the_close_releases_the_held_span() -> None:
    out, citation_filter = filter_whole("[nota [P1]")
    assert out == "[nota [1]"
    assert citation_filter.dropped == 0

    out, citation_filter = filter_whole("Ver [ver P2 [P1] aquí.")
    assert out == "Ver [1] aquí."
    assert citation_filter.dropped == 1


def test_span_of_64_characters_counting_both_brackets_still_closes() -> None:
    marker = "[P1" + " " * 60 + "]"
    assert len(marker) == MARKER_SPAN_MAX_CHARS
    out, citation_filter = filter_whole(f"A {marker}.")
    assert (out, citation_filter.dropped) == ("A [1].", 0)


def test_64_characters_without_close_are_not_a_marker() -> None:
    long_span = "[" + "a" * 70 + "]"
    out, citation_filter = filter_whole(f"x {long_span} y")
    # Emitted as written; the later "]" is plain text too.
    assert out == f"x {long_span} y"
    assert citation_filter.dropped == 0


def test_64_characters_without_close_and_a_key_are_dropped() -> None:
    held = "[P1 " + "a" * 60
    assert len(held) == MARKER_SPAN_MAX_CHARS
    out, citation_filter = filter_whole(f"x {held}bbbb] y")
    # The held 64 characters go away; the rest, up to the next "[", is plain text.
    assert out == "x bbbb] y"
    assert citation_filter.dropped == 1

    out, citation_filter = filter_whole("[P1" + " " * 61 + "]")
    assert (out, citation_filter.dropped) == ("]", 1)


@pytest.mark.parametrize(
    ("raw", "expected", "dropped"),
    [
        # Shown, it would join the text after it into a bracketed number or into the mark.
        ("Texto [" + "1" * 63 + "] fin.", "Texto ] fin.", 1),
        ("Texto [" + " " * 63 + "1] fin.", "Texto 1] fin.", 0),
        ("Texto [P1]. [SIN_RESPALDO" + " " * 51 + "] fin.", "Texto [1]. ] fin.", 0),
        # Dropped, it counts as never written, like any removed span: "[3" goes on into "[35]".
        ("[3[" + " " * 63 + "5]]", "]", 1),
    ],
)
def test_64_characters_that_can_still_become_a_number_or_the_mark_are_dropped(
    raw: str, expected: str, dropped: int
) -> None:
    out, citation_filter = filter_whole(raw)
    assert (out, citation_filter.dropped) == (expected, dropped)


@pytest.mark.parametrize(
    ("raw", "expected", "dropped"),
    [
        ("Texto [nota", "Texto [nota", 0),
        ("Texto [ver P1", "Texto ", 1),
        ("Texto [SIN_RES", "Texto ", 0),
        ("Texto [sin_respaldo ", "Texto ", 0),
        ("Texto [", "Texto ", 0),
        ("Texto [SIN_ RES", "Texto [SIN_ RES", 0),
        # Completed, it would match the mark: re.IGNORECASE takes "\u0131" for "i".
        ("Texto [S\u0131N_RES", "Texto ", 0),
        # Nothing follows, so the start of a number goes out as written.
        ("Texto [12", "Texto [12", 0),
        # The span the removed one cut is the one held when the stream ends.
        ("Texto [SIN_[P9", "Texto ", 1),
        ("Texto [3[P9", "Texto [3", 1),
    ],
)
def test_span_held_when_the_stream_ends(raw: str, expected: str, dropped: int) -> None:
    out, citation_filter = filter_whole(raw)
    assert out == expected
    assert citation_filter.dropped == dropped


@pytest.mark.parametrize(
    "raw",
    [
        "[SIN_RESPALDO]Faltan datos.",
        "  \n\n [SIN_RESPALDO]\nFaltan datos.",
        "[ sin_respaldo ] Faltan datos.",
        "[P9][SIN_RESPALDO] Faltan datos.",
    ],
)
def test_not_covered_mark_at_the_start(raw: str) -> None:
    out, citation_filter = filter_whole(raw)
    assert out == "Faltan datos."
    assert citation_filter.not_covered


def test_mark_rebuilt_by_a_removal_counts_only_at_the_start() -> None:
    out, citation_filter = filter_whole("[[P9]SIN_RESPALDO] Faltan datos.")
    assert (out, citation_filter.not_covered, citation_filter.dropped) == ("Faltan datos.", True, 1)
    # Inside another span the mark is not at the start: a "[" came before it.
    out, citation_filter = filter_whole("[[SIN_RESPALDO] texto")
    assert (out, citation_filter.not_covered) == ("[ texto", False)
    out, citation_filter = filter_whole("[[SIN_RESPALDO]1] Faltan datos.")
    assert (out, citation_filter.not_covered, citation_filter.dropped) == ("Faltan datos.", False, 1)


def test_not_covered_mark_later_is_removed_without_counting() -> None:
    out, citation_filter = filter_whole("Respuesta [P1]. [SIN_RESPALDO] Fin.")
    assert out == "Respuesta [1].  Fin."
    assert not citation_filter.not_covered
    assert citation_filter.dropped == 0


def test_whitespace_before_the_first_visible_text_is_dropped() -> None:
    out, citation_filter = filter_text(["\n", "  ", " Hola [P1]"])
    assert out == "Hola [1]"
    assert citation_filter.visible

    out, citation_filter = filter_whole("   \n ")
    assert out == ""
    assert not citation_filter.visible


def test_discard_drops_the_held_span() -> None:
    citation_filter = CitationFilter(3)
    assert citation_filter.feed("Dice [P") == "Dice "
    citation_filter.discard()
    assert citation_filter.finish() == ""
    assert citation_filter.dropped == 0

    # Also the spans a later "[" cut.
    citation_filter = CitationFilter(3)
    assert citation_filter.feed("Dice [nota [ver ") == "Dice "
    citation_filter.discard()
    assert citation_filter.finish() == ""


def test_retention_is_bounded_and_a_cascade_never_closes_a_number() -> None:
    citation_filter = CitationFilter(3)
    raw = "[a" * 400
    emitted = "".join(citation_filter.feed(char) for char in raw)
    # The cut spans waiting (128 characters at most) plus the one being held.
    assert len(raw) - len(emitted) <= 3 * MARKER_SPAN_MAX_CHARS
    out, _ = filter_whole("A [3" + "[5" * 80 + "[P9]" + "]" * 81 + " fin.")
    assert not _ANY_NUMBER.search(out)


TRICKY_OUTPUTS = [
    "La Ley 16.744 exige un comité [P1, P2]. Además [P3][p1] y [ P 2 ] [P9].",
    "[SIN_RESPALDO]\n\nFalta saber [ver P1] y [P2O5] [sic] [3].",
    "[nota [P1] [P1-P3] [" + "x" * 80 + "] fin [P2",
    "   [P1] ya [" + "P1 " + "y" * 70 + "] y [SIN_RESPALDO] otra [SIN_RE",
    "Inicio [P1 y P3; P2] [ 7 ] [[P2]] ]] [[",
    "Inicio [P2]. B [1[P9]] y [[P9]3] y [SIN_[P9]RESPALDO] y [1[2[P8]]] y [ver [P9] P2] fin [3[P9",
    "B [[0]1] [[P9]2] [SIN_[P9]RESPALDO] [1 [P9]] [SIN_RESPALDO[x] [12 [P1] [" + "4" * 63 + "]",
]


@pytest.mark.parametrize("raw", TRICKY_OUTPUTS)
def test_output_does_not_depend_on_how_the_provider_splits_the_text(raw: str) -> None:
    expected, reference = filter_whole(raw)
    for size in range(1, len(raw) + 1):
        out, citation_filter = filter_text(chunked(raw, size))
        assert out == expected, size
        assert citation_filter.dropped == reference.dropped
        assert citation_filter.cited_keys == reference.cited_keys
        assert citation_filter.not_covered == reference.not_covered


@pytest.mark.parametrize("raw", TRICKY_OUTPUTS)
def test_markers_come_out_whole_in_a_single_piece(raw: str) -> None:
    citation_filter = CitationFilter(3)
    pieces = [citation_filter.feed(char) for char in raw] + [citation_filter.finish()]
    joined = "".join(pieces)
    # A marker split between two pieces would be found in the joined text but in no piece.
    assert sum(len(_MARKER.findall(piece)) for piece in pieces) == len(_MARKER.findall(joined))
    assert NOT_COVERED_MARK not in joined
    # No bracketed number at all spans two pieces, and every one left is a valid marker.
    assert sum(len(_ANY_NUMBER.findall(piece)) for piece in pieces) == len(_ANY_NUMBER.findall(joined))
    valid = {f"[{n}]" for n in range(1, len(citation_filter.cited_keys) + 1)}
    assert set(_ANY_NUMBER.findall(joined)) <= valid


class TestNeutralizeMarkers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Ver [P2] y [ p2 ].", "Ver (P2) y ( p2 )."),
            ("Ver [P1, P2] y [P1-P3] y [ver P2].", "Ver (P1, P2) y (P1-P3) y (ver P2)."),
            ("Nota [3] y [ 4 ].", "Nota (3) y ( 4 )."),
            ("[sic] [P2O5] [imagen omitida] [3.]", "[sic] [P2O5] [imagen omitida] [3.]"),
            ("Texto [SIN_RESPALDO] y [ sin_respaldo ].", "Texto  y ."),
            # Removing the mark joins a new span, which is neutralized too.
            ("[P[SIN_RESPALDO]1]", "(P1)"),
            ("[SIN_[SIN_RESPALDO]RESPALDO]", ""),
            # Once the inner span is parenthesized, the outer one holds a key too.
            ("[a[P1]b]", "(a(P1)b)"),
            ("[[3]]", "[(3)]"),
            ("Abierto [P1 sin cierre", "Abierto [P1 sin cierre"),
        ],
    )
    def test_user_content(self, raw: str, expected: str) -> None:
        assert neutralize_markers(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("La app registra la obligación [1].", "La app registra la obligación."),
            ("Dice [1][2] y [ 3 ].", "Dice y."),
            ("[[1]2] fin", " fin"),
            ("[P[1]1]", "(P1)"),
            ("[SIN_[1]RESPALDO] fin", " fin"),
            ("Ver [P2] y [sic] [1]", "Ver (P2) y [sic]"),
        ],
    )
    def test_assistant_content_loses_its_markers(self, raw: str, expected: str) -> None:
        assert neutralize_markers(raw, assistant=True) == expected

    def test_nesting_converges(self) -> None:
        assert neutralize_markers("[x [y [P1] z] w]") == "(x (y (P1) z) w)"
        assert neutralize_markers("[x [y] P1]") == "[x [y] P1]"  # never innermost: the filter sees no key span

    @pytest.mark.parametrize(
        ("raw", "assistant"),
        [
            ("[" * 2999 + "P1" + "]" * 2999, False),
            ("[" * 2666 + "1" + "]1" * 2666, True),
            ("[SIN_" * 260 + "RESPALDO]" * 260, False),
        ],
        ids=["nested-keys", "nested-assistant-numbers", "nested-marks"],
    )
    def test_text_built_to_need_many_rounds_loses_its_brackets(self, raw: str, assistant: bool) -> None:
        out = neutralize_markers(raw, assistant=assistant)
        assert "[" not in out and "]" not in out

    def test_a_long_run_of_spaces_in_an_assistant_message_stays_linear(self) -> None:
        # Unanchored, each round rescanned the run from every one of its positions: seconds of CPU.
        nested = "[" * 31 + "1" + "]1" * 30 + "]"
        raw = "a" + " " * (8000 - len(nested) - 2) + "a" + nested
        started = perf_counter()
        out = neutralize_markers(raw, assistant=True)
        assert perf_counter() - started < 0.5
        assert out == raw[: len(raw) - len(nested)]


def test_the_mark_the_prompt_asks_for_is_the_one_removed_everywhere() -> None:
    from ia_cumplify.adapters.outbound.openai.chat_prompts import CHAT_SYSTEM_PROMPT

    # The derived patterns only support "[" + one word + "]", short enough to be held whole.
    assert re.fullmatch(r"\[[A-Za-z_]+\]", NOT_COVERED_MARK)
    assert len(NOT_COVERED_MARK) < MARKER_SPAN_MAX_CHARS
    assert NOT_COVERED_MARK in CHAT_SYSTEM_PROMPT
    for size in range(1, len(NOT_COVERED_MARK) + 2):
        out, citation_filter = filter_text(chunked(f" {NOT_COVERED_MARK} Faltan datos.", size))
        assert (out, citation_filter.not_covered) == ("Faltan datos.", True), size
    for cut in range(1, len(NOT_COVERED_MARK)):
        assert filter_whole(f"Texto {NOT_COVERED_MARK[:cut]}")[0] == "Texto ", cut
    assert neutralize_markers(f"Hola {NOT_COVERED_MARK.lower()} chao") == "Hola  chao"
