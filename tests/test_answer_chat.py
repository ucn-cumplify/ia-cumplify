import asyncio
import logging
from dataclasses import replace

import pytest
from chat_doubles import (
    LIMITS,
    TEST_VERSION,
    USAGE,
    ScriptedResponder,
    chunked,
    completed,
    make_passages,
    run_chat,
)

from ia_cumplify.application.ports.chat_responder import ProviderStreamSlot
from ia_cumplify.application.use_cases.answer_chat import (
    NO_PASSAGES_TEXT,
    NOT_COVERED_TEXT,
    REFUSED_TEXT,
    AnswerChatUseCase,
    ChatLimits,
)
from ia_cumplify.domain.chat import (
    ChatCitation,
    ChatDeltaEvent,
    ChatMessage,
    ChatPassage,
    ChatRequest,
    ChatUsage,
    ProviderFinishEvent,
    ProviderRefusalEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
)
from ia_cumplify.domain.exceptions import ChatProviderError

SECRET = "SECRETO-de-la-empresa"


def prepare(request: ChatRequest, limits: ChatLimits = LIMITS):
    return AnswerChatUseCase(ScriptedResponder(), limits).prepare(request)


def rejection(request: ChatRequest, limits: ChatLimits = LIMITS) -> str:
    with pytest.raises(ValueError) as caught:
        prepare(request, limits)
    message = str(caught.value)
    # Errors give lengths, limits and positions, never what the request says.
    assert SECRET not in message
    return message


def passage(**changes) -> ChatPassage:
    return replace(ChatPassage(id="id-1", kind="article", reference="Ley 16.744, art. 66", text="Texto."), **changes)


class TestValidation:
    def test_question_is_trimmed_with_unicode_white_space_only(self) -> None:
        prepared = prepare(ChatRequest(question="\u3000\u2028 ¿Qué exige? \u00a0\t"))
        assert prepared.prompt.question == "¿Qué exige?"
        # U+001C is a line break for str.splitlines but not White_Space: .NET Trim() keeps it.
        assert prepare(ChatRequest(question="\x1c")).prompt.question == "\x1c"

    @pytest.mark.parametrize("question", ["", "   ", "\u3000\n\u2029"])
    def test_empty_question(self, question: str) -> None:
        assert rejection(ChatRequest(question=question)) == "The question is empty."

    def test_question_over_its_limit(self) -> None:
        limits = replace(LIMITS, question_max_chars=10)
        assert prepare(ChatRequest(question="  " + "a" * 10 + "  "), limits).prompt.question == "a" * 10
        message = rejection(ChatRequest(question=SECRET * 2), limits)
        assert message == f"The question has {len(SECRET) * 2} characters; the limit is 10."

    def test_history_drops_empty_messages_before_counting(self) -> None:
        history = [ChatMessage("user", "hola")] * 6 + [ChatMessage("assistant", " \n ")]
        prepared = prepare(ChatRequest(question="q", history=tuple(history)))
        assert len(prepared.prompt.history) == 6

        too_many = tuple([ChatMessage("user", SECRET)] * 7)
        assert rejection(ChatRequest(question="q", history=too_many)) == "The history has 7 messages; the limit is 6."

    def test_history_characters(self) -> None:
        limits = replace(LIMITS, history_max_chars=30)
        history = (ChatMessage("user", f"  {SECRET}  "), ChatMessage("assistant", "x" * 10))
        assert len(SECRET) + 10 > 30
        message = rejection(ChatRequest(question="q", history=history), limits)
        assert message == f"The history has {len(SECRET) + 10} characters; the limit is 30."

    def test_history_roles_do_not_need_to_alternate(self) -> None:
        history = (ChatMessage("assistant", "a"), ChatMessage("assistant", "b"), ChatMessage("user", "c"))
        prepared = prepare(ChatRequest(question="q", history=history))
        assert [message.role for message in prepared.prompt.history] == ["assistant", "assistant", "user"]

    def test_history_role_outside_the_list(self) -> None:
        history = (ChatMessage("user", "a"), ChatMessage("system", SECRET))  # type: ignore[arg-type]
        assert rejection(ChatRequest(question="q", history=history)) == "history[1].role must be user or assistant."

    def test_passage_count(self) -> None:
        message = rejection(ChatRequest(question="q", passages=make_passages(13)))
        assert message == "The request has 13 passages; the limit is 12."

    @pytest.mark.parametrize(
        ("change", "expected"),
        [
            ({"id": ""}, "passages[1].id has 0 characters; it must have between 1 and 100."),
            ({"id": "x" * 101}, "passages[1].id has 101 characters; it must have between 1 and 100."),
            ({"id": "id-2\n"}, "passages[1].id must be a single line."),
            ({"id": "id\u20282"}, "passages[1].id must be a single line."),
            ({"id": "id-1"}, "Passage ids must be unique."),
            ({"kind": "law"}, "passages[1].kind is not one of the accepted kinds."),
            ({"reference": " \n "}, "passages[1].reference has 0 characters; it must have between 1 and 200."),
            ({"reference": "x" * 201}, "passages[1].reference has 201 characters; it must have between 1 and 200."),
            ({"reference": f"{SECRET}\nart. 2"}, "passages[1].reference must be a single line."),
            ({"reference": f"{SECRET}\u2028art. 2"}, "passages[1].reference must be a single line."),
            ({"reference": f"{SECRET}\x1c"}, "passages[1].reference must be a single line."),
            ({"text": " \u3000 "}, "passages[1].text is empty."),
            ({"text": SECRET * 300}, f"passages[1].text has {len(SECRET) * 300} characters; the limit is 6000."),
        ],
    )
    def test_passage_rules(self, change: dict, expected: str) -> None:
        passages = (passage(), replace(passage(id="id-2"), **change))
        assert rejection(ChatRequest(question="q", passages=passages)) == expected

    def test_passage_id_is_never_trimmed(self) -> None:
        prepared = prepare(ChatRequest(question="q", passages=(passage(id=" id 1 "),)))
        assert prepared.passage_ids == (" id 1 ",)

    @pytest.mark.parametrize("ending", ["\n", "\u2028", "\r\n", "\u0085"])
    def test_reference_and_app_name_with_a_line_break_at_the_end_are_trimmed(self, ending: str) -> None:
        request = ChatRequest(
            question="q", passages=(passage(reference=f"Ley 16.744, art. 66{ending}"),), app_name=f"Planta{ending}"
        )
        prepared = prepare(request)
        assert prepared.prompt.passages[0].reference == "Ley 16.744, art. 66"
        assert prepared.prompt.app_name == "Planta"

    def test_passages_total(self) -> None:
        limits = replace(LIMITS, passage_max_chars=100, passages_max_total_chars=150)
        passages = tuple(passage(id=f"id-{i}", text=SECRET * 4) for i in range(3))
        message = rejection(ChatRequest(question="q", passages=passages), limits)
        assert message == f"The passages have {len(SECRET) * 12} characters in total; the limit is 150."

    def test_lengths_are_measured_before_removing_images(self) -> None:
        image = "data:image/png;base64," + "A" * 6000
        message = rejection(ChatRequest(question="q", passages=(passage(text=f"Ver {image}"),)))
        assert message == f"passages[0].text has {len(image) + 4} characters; the limit is 6000."

    @pytest.mark.parametrize(
        ("app_name", "expected"),
        [
            ("  ", "context.app_name has 0 characters; it must have between 1 and 200."),
            ("x" * 201, "context.app_name has 201 characters; it must have between 1 and 200."),
            (f"{SECRET}\nPlanta", "context.app_name must be a single line."),
            (f"{SECRET}\u2029Planta", "context.app_name must be a single line."),
            (f"{SECRET}\x1e", "context.app_name must be a single line."),
        ],
    )
    def test_app_name(self, app_name: str, expected: str) -> None:
        assert rejection(ChatRequest(question="q", app_name=app_name)) == expected

    def test_without_app_name(self) -> None:
        assert prepare(ChatRequest(question="q")).prompt.app_name is None

    def test_prompt_has_keys_in_order_and_no_ids(self) -> None:
        prepared = prepare(ChatRequest(question="q", passages=make_passages(3)))
        assert [p.key for p in prepared.prompt.passages] == ["P1", "P2", "P3"]
        assert prepared.passage_ids == ("id-1", "id-2", "id-3")
        assert "id-1" not in repr(prepared.prompt)

    def test_lone_surrogates_in_the_input_become_replacement_characters(self) -> None:
        request = ChatRequest(
            question="¿Qué\ud800?",
            history=(ChatMessage("user", "a\udfff"),),
            passages=(passage(reference="Ley\ud801", text="t\udc00"),),
            app_name="P\ud83d",
        )
        prompt = prepare(request).prompt
        assert prompt.question == "¿Qué\ufffd?"
        assert prompt.history[0].content == "a\ufffd"
        assert (prompt.passages[0].reference, prompt.passages[0].text) == ("Ley\ufffd", "t\ufffd")
        assert prompt.app_name == "P\ufffd"


class TestNoPassages:
    def test_fixed_refusal_without_calling_the_model(self) -> None:
        run = run_chat(passages=())
        assert run.deltas == [NO_PASSAGES_TEXT]
        done = run.done
        assert (done.coverage, done.citations, done.citations_dropped, done.finish_reason) == (
            "no_passages",
            (),
            0,
            "stop",
        )
        assert done.usage == ChatUsage(llm_calls=0)
        assert done.chat_version == TEST_VERSION
        assert run.responder.prompts == []


class TestAnswer:
    def test_answered_with_markers_split_between_fragments(self) -> None:
        run = run_chat(completed("Debe constituirse un comité [", "P1] en toda faena [P", "3", "].", "\n\nYa está [P1]."))
        assert run.text == "Debe constituirse un comité [1] en toda faena [2].\n\nYa está [1]."
        done = run.done
        assert done.citations == (ChatCitation(n=1, id="id-1"), ChatCitation(n=2, id="id-3"))
        assert (done.coverage, done.citations_dropped, done.finish_reason) == ("answered", 0, "stop")
        assert done.usage == USAGE
        assert done.chat_version == TEST_VERSION
        assert len(run.responder.prompts) == 1

    @pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
    def test_lists_split_between_fragments(self, size: int) -> None:
        raw = "Uno [P1, P2]. Dos [p3; P1]. Tres [P2 y P9]. Cuatro [P1, P1]."
        run = run_chat(completed(*chunked(raw, size)))
        assert run.text == "Uno [1][2]. Dos [3][1]. Tres [2]. Cuatro [1]."
        assert run.done.citations_dropped == 1
        assert [c.id for c in run.done.citations] == ["id-1", "id-2", "id-3"]

    def test_uncited(self) -> None:
        run = run_chat(completed("Respuesta sin citas [sic] [P2O5]."))
        assert run.text == "Respuesta sin citas [sic] [P2O5]."
        assert (run.done.coverage, run.done.citations) == ("uncited", ())

    def test_uncited_when_every_marker_was_invalid_but_text_remains(self) -> None:
        run = run_chat(completed("Texto [P9] y [4] y [P1-P2]."))
        assert run.text == "Texto  y  y ."
        assert (run.done.coverage, run.done.citations_dropped) == ("uncited", 3)

    def test_only_invalid_markers_is_empty_output(self) -> None:
        run = run_chat(completed(" [P9]", "[3] ", "[P1-P2]\n"))
        assert run.deltas == []
        error = run.error
        assert (error.code, error.retryable, error.usage, error.chat_version) == (
            "empty_output",
            False,
            USAGE,
            TEST_VERSION,
        )

    def test_no_text_at_all_is_empty_output(self) -> None:
        run = run_chat(completed(finish="length"))
        assert (run.error.code, run.error.retryable) == ("empty_output", False)

    @pytest.mark.parametrize("reason", ["length", "content_filter"])
    def test_cut_finish_reasons_keep_valid_citations(self, reason: str) -> None:
        run = run_chat(completed("Texto incompleto [P2] y", finish=reason))
        assert (run.done.finish_reason, run.done.coverage) == (reason, "answered")
        assert run.done.citations == (ChatCitation(n=1, id="id-2"),)

    def test_unknown_finish_reason_is_reported_as_stop(self, caplog: pytest.LogCaptureFixture) -> None:
        run = run_chat(completed("Texto [P1].", finish="tool_calls"))
        assert run.done.finish_reason == "stop"
        assert "finish_reason" in caplog.text

    def test_usage_missing_is_null(self) -> None:
        run = run_chat(completed("Texto [P1].", usage=None))
        assert run.done.usage is None

    def test_provider_lone_surrogate_becomes_replacement_character(self) -> None:
        run = run_chat(completed("Seg\ud800n [P1]."))
        assert run.text == "Seg\ufffdn [1]."
        assert run.done.coverage == "answered"

    def test_a_claim_the_model_did_not_cite_never_gets_a_citation(self) -> None:
        run = run_chat(
            completed("La ley exige un comité [P2]. Además exige capacitar [1[P9]]."), passages=make_passages(2)
        )
        assert run.text == "La ley exige un comité [1]. Además exige capacitar ."
        assert run.done.citations == (ChatCitation(n=1, id="id-2"),)
        assert run.done.citations_dropped == 2

    @pytest.mark.parametrize(
        "fragments",
        [("Respaldado [P2]. Inventado [[0]1].",), ("Respaldado [P2]. Inventado [", "[0]", "1].")],
    )
    def test_removed_span_inside_an_unclosed_bracket_never_becomes_a_marker(self, fragments: tuple[str, ...]) -> None:
        run = run_chat(completed(*fragments))
        assert run.text == "Respaldado [1]. Inventado ."
        assert run.done.citations == (ChatCitation(n=1, id="id-2"),)
        assert run.done.citations_dropped == 2

    def test_trailing_held_span_is_emitted_before_done(self) -> None:
        run = run_chat(completed("Texto [P1] y [nota"))
        assert run.deltas == ["Texto [1] y ", "[nota"]
        assert run.done.coverage == "answered"


class TestNotCovered:
    @pytest.mark.parametrize(
        "fragments",
        [
            ("[SIN_RESPALDO] Falta saber la dotación.",),
            ("[SIN_", "RESP", "ALDO]", " Falta saber la dotación."),
            ("\n\n  ", "[SIN_RESPALDO]\n\n", "Falta saber la dotación."),
        ],
    )
    def test_mark_at_the_start(self, fragments: tuple[str, ...]) -> None:
        run = run_chat(completed(*fragments))
        assert run.text == "Falta saber la dotación."
        assert (run.done.coverage, run.done.citations, run.done.citations_dropped) == ("not_covered", (), 0)

    def test_mark_with_citations_keeps_them(self) -> None:
        run = run_chat(completed("[SIN_RESPALDO] Solo se sabe que hay un comité [P2]."))
        assert run.done.coverage == "not_covered"
        assert run.done.citations == (ChatCitation(n=1, id="id-2"),)

    def test_mark_without_text_gets_the_fixed_text(self) -> None:
        run = run_chat(completed("[SIN_RESPALDO]", " \n", "[P9]"))
        assert run.deltas == [NOT_COVERED_TEXT]
        assert (run.done.coverage, run.done.citations_dropped) == ("not_covered", 1)

    def test_mark_later_is_removed_without_changing_coverage(self) -> None:
        run = run_chat(completed("Respuesta [P1]. [SIN_", "RESPALDO] Fin."))
        assert "SIN_RESPALDO" not in run.text
        assert run.done.coverage == "answered"

    def test_mark_rebuilt_by_a_removal_at_the_start(self) -> None:
        run = run_chat(completed("[[P9]SIN_RESPALDO] Faltan datos."))
        assert (run.text, run.done.coverage, run.done.citations_dropped) == ("Faltan datos.", "not_covered", 1)

    def test_stream_ending_inside_the_mark_is_empty_output(self) -> None:
        run = run_chat(completed("  [SIN_RES"))
        assert run.deltas == []
        assert run.error.code == "empty_output"

    def test_stream_ending_inside_a_later_mark_drops_it(self) -> None:
        run = run_chat(completed("Texto [P1]. [SIN_RESPAL"))
        assert run.text == "Texto [1]. "
        assert run.done.coverage == "answered"


class TestRefusal:
    def test_refusal_before_any_text(self) -> None:
        script = [
            ProviderTextEvent("[SIN_RES"),
            ProviderRefusalEvent(),
            ProviderTextEvent("texto que no se emite [P1]"),
            *completed(),
        ]
        run = run_chat(script)
        assert run.deltas == [REFUSED_TEXT]
        done = run.done
        assert (done.coverage, done.citations, done.finish_reason, done.usage) == ("refused", (), "stop", USAGE)
        # The stream was read to the end to report finish_reason and usage.
        assert run.responder.streams[0].delivered == len(script)

    def test_refusal_after_text_keeps_the_citations_already_sent(self) -> None:
        script = [
            ProviderTextEvent("Dice X [P2]. Y [P"),
            ProviderRefusalEvent(),
            ProviderTextEvent("1]. Más texto."),
            ProviderRefusalEvent(),
            *completed(finish="content_filter"),
        ]
        run = run_chat(script)
        assert run.deltas == ["Dice X [1]. Y ", f"\n\n{REFUSED_TEXT}"]
        done = run.done
        assert (done.coverage, done.finish_reason, done.usage) == ("refused", "content_filter", USAGE)
        assert done.citations == (ChatCitation(n=1, id="id-2"),)

    def test_refusal_wins_over_not_covered(self) -> None:
        run = run_chat([ProviderTextEvent("[SIN_RESPALDO] Falta X."), ProviderRefusalEvent(), *completed()])
        assert run.deltas == ["Falta X.", f"\n\n{REFUSED_TEXT}"]
        assert run.done.coverage == "refused"


class TestProviderFailures:
    @pytest.mark.parametrize(
        ("code", "retryable"),
        [("rate_limited", True), ("timeout", True), ("provider_error", True), ("provider_rejected", False)],
    )
    def test_failure_when_opening(self, code: str, retryable: bool) -> None:
        run = run_chat(responder=ScriptedResponder(open_error=ChatProviderError(code)))  # type: ignore[arg-type]
        assert run.deltas == []
        assert (run.error.code, run.error.retryable, run.error.usage) == (code, retryable, None)
        assert run.error.chat_version == TEST_VERSION

    def test_failure_halfway_after_some_deltas(self) -> None:
        run = run_chat([ProviderTextEvent("Primera parte [P1]. "), ChatProviderError("timeout")])
        assert run.deltas == ["Primera parte [1]. "]
        assert (run.error.code, run.error.retryable, run.error.usage) == ("timeout", True, None)

    def test_stream_without_finish_reason_was_cut(self) -> None:
        run = run_chat([ProviderTextEvent("Texto [P1] cortado")])
        assert run.deltas == ["Texto [1] cortado"]
        assert (run.error.code, run.error.retryable) == ("provider_error", True)

    def test_failure_after_finish_reason_closes_normally_without_usage(self) -> None:
        script = [ProviderTextEvent("Texto [P1] y [P"), ProviderFinishEvent("stop"), ChatProviderError("provider_error")]
        run = run_chat(script)
        # "[P" was held when the stream ended: without digits it is not a key, so it goes out as written.
        assert run.deltas == ["Texto [1] y ", "[P"]
        assert (run.done.coverage, run.done.finish_reason, run.done.usage) == ("answered", "stop", None)
        assert run.done.citations_dropped == 0

    def test_failure_after_finish_reason_without_visible_text(self) -> None:
        run = run_chat([ProviderFinishEvent("length"), ChatProviderError("timeout")])
        assert (run.error.code, run.error.usage) == ("empty_output", None)

    def test_failure_after_the_usage_chunk_keeps_the_usage(self) -> None:
        run = run_chat([*completed("Texto [P1]."), ChatProviderError("provider_error")])
        assert run.done.usage == USAGE

    def test_unexpected_exception_is_internal_and_logs_only_its_type(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG)
        run = run_chat([ProviderTextEvent("Texto [P1]."), RuntimeError(SECRET)])
        assert run.deltas == ["Texto [1]."]
        assert (run.error.code, run.error.retryable) == ("internal", False)
        assert "RuntimeError" in caplog.text
        assert SECRET not in caplog.text

    def test_unexpected_exception_when_opening(self, caplog: pytest.LogCaptureFixture) -> None:
        run = run_chat(responder=ScriptedResponder(open_error=UnicodeEncodeError("utf-8", SECRET, 0, 1, "x")))
        assert (run.error.code, run.error.retryable) == ("internal", False)
        assert SECRET not in caplog.text

    def test_unknown_provider_event_is_internal(self) -> None:
        run = run_chat([object()])  # type: ignore[list-item]
        assert run.error.code == "internal"

    def test_provider_error_codes_are_closed(self) -> None:
        with pytest.raises(ValueError):
            ChatProviderError("empty_output")  # type: ignore[arg-type]


class TestClosing:
    def test_stream_is_closed_after_a_normal_answer(self) -> None:
        run = run_chat(completed("Texto [P1]."))
        assert run.responder.streams[0].closed

    def test_stream_is_closed_after_a_failure(self) -> None:
        run = run_chat([ProviderTextEvent("Texto"), ChatProviderError("timeout")])
        assert run.responder.streams[0].closed

    def test_request_scope_closes_the_stream_while_the_answer_is_suspended(self) -> None:
        responder = ScriptedResponder(completed("Uno [P1]. ", "Dos."))
        use_case = AnswerChatUseCase(responder, LIMITS)
        prepared = use_case.prepare(ChatRequest(question="q", passages=make_passages(2)))
        slot = ProviderStreamSlot()

        async def scenario() -> None:
            answer = use_case.stream(prepared, slot)
            first = await anext(answer)
            assert isinstance(first, ChatDeltaEvent)
            # What the dependency with yield does when the client disconnects.
            await slot.aclose()
            assert responder.streams[0].closed
            await answer.aclose()

        asyncio.run(scenario())
        assert responder.streams[0].close_calls >= 1

    def test_consumer_stopping_early_closes_the_stream(self) -> None:
        responder = ScriptedResponder(completed("Uno [P1]. ", "Dos.", "Tres."))
        use_case = AnswerChatUseCase(responder, LIMITS)
        prepared = use_case.prepare(ChatRequest(question="q", passages=make_passages(2)))

        async def scenario() -> None:
            answer = use_case.stream(prepared, ProviderStreamSlot())
            await anext(answer)
            await answer.aclose()

        asyncio.run(scenario())
        assert responder.streams[0].closed

    def test_slot_close_is_idempotent(self) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        slot = ProviderStreamSlot()

        async def scenario() -> None:
            stream = await responder.open(None)  # type: ignore[arg-type]
            slot.hold(stream)
            await slot.aclose()
            await slot.aclose()

        asyncio.run(scenario())
        assert responder.streams[0].close_calls == 1

    def test_failing_close_does_not_add_a_second_final_event(self, caplog: pytest.LogCaptureFixture) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."), close_error=OSError(SECRET))
        run = run_chat(responder=responder)
        assert run.done.coverage == "answered"
        assert "OSError" in caplog.text
        assert SECRET not in caplog.text


def test_usage_reports_cache_and_reasoning_tokens() -> None:
    usage = ChatUsage(
        prompt_tokens=3480, completion_tokens=410, total_tokens=3890, cached_tokens=1024, reasoning_tokens=96, llm_calls=1
    )
    run = run_chat([ProviderTextEvent("Texto [P1]."), ProviderFinishEvent("stop"), ProviderUsageEvent(usage)])
    assert run.done.usage == usage
