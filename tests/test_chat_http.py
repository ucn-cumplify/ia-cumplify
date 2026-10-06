"""POST /api/v1/chat through FastAPI: the checks before the stream, the schema and the event stream.

A scripted responder or the fake one stands in for OpenAI; test_openai_chat_responder.py covers the
real adapter against a fake OpenAI server.
"""

import asyncio
import json
import logging
from dataclasses import asdict
from time import perf_counter

import pytest
from chat_doubles import LIMITS, TEST_VERSION, USAGE, ScriptedResponder, ScriptedStream, completed
from chat_http import HEADERS, SECRET, ZERO_USAGE, chat_body, configure, passage, read_reply
from fake_openai import running
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import get_chat_responder
from ia_cumplify.adapters.inbound.http.routers.chat import chat_stream_state, get_answer_chat_use_case
from ia_cumplify.application.use_cases.answer_chat import NO_PASSAGES_TEXT, AnswerChatUseCase
from ia_cumplify.domain.chat import (
    ChatCitation,
    ChatDeltaEvent,
    ChatDoneEvent,
    ProviderFinishEvent,
    ProviderTextEvent,
)
from ia_cumplify.domain.exceptions import ChatProviderError

CHAT = "/api/v1/chat"


def post(*, responder=None, use_case=None, headers=HEADERS, **kwargs):
    """One request to a new app, its lifespan included. Variables must be set before calling it."""
    app = create_app()
    if responder is not None:
        app.dependency_overrides[get_chat_responder] = lambda: responder
    if use_case is not None:
        app.dependency_overrides[get_answer_chat_use_case] = lambda: use_case
    with TestClient(app) as client:
        return client.post(CHAT, headers=headers, **kwargs)


def rejection(response) -> list[dict]:
    """The 422 list of FastAPI, which must never repeat the request."""
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert all(list(error) == ["type", "loc", "msg"] for error in detail), detail
    assert SECRET not in response.text
    return detail


class TestBeforeTheStream:
    def test_missing_or_wrong_api_key_is_401_without_calling_the_provider(self, monkeypatch) -> None:
        configure(monkeypatch)
        responder = ScriptedResponder(completed("Texto [P1]."))
        for headers in ({}, {"X-API-Key": "otra-clave"}):
            response = post(json=chat_body(), headers=headers, responder=responder)
            assert response.status_code == 401
            assert response.json() == {"detail": "Missing or invalid X-API-Key."}
        assert responder.prompts == []

    def test_missing_service_api_key_is_503_before_the_schema(self) -> None:
        response = post(json={"question": SECRET})
        assert response.status_code == 503
        assert response.json() == {"detail": "SERVICE_API_KEY is not configured."}

    def test_401_comes_before_a_missing_openai_key(self, monkeypatch) -> None:
        configure(monkeypatch)
        assert post(json=chat_body(), headers={}).status_code == 401

    @pytest.mark.parametrize(
        "body",
        [
            chat_body(passages=[]),
            # The question is over its limit: 503 all the same, the limits come after the key.
            chat_body(question="x" * 2001),
            # And the schema too.
            {"question": SECRET},
            {**chat_body(), "passage": SECRET},
        ],
        ids=["without-passages", "long-question", "missing-field", "extra-field"],
    )
    def test_missing_openai_key_is_503_before_any_422(self, monkeypatch, body: dict) -> None:
        configure(monkeypatch)
        with running() as fake:
            monkeypatch.setenv("OPENAI_BASE_URL", fake.base_url)
            response = post(json=body)
        assert response.status_code == 503
        assert response.json() == {"detail": "OPENAI_API_KEY is not configured."}
        assert fake.requests() == []

    def test_malformed_json_is_422_even_without_api_key(self, monkeypatch) -> None:
        configure(monkeypatch)
        raw = json.dumps(chat_body(question=SECRET))[:-5].encode()
        response = post(content=raw, headers={"Content-Type": "application/json"})
        detail = rejection(response)
        assert detail[0]["type"] == "json_invalid"

    def test_body_that_is_not_utf8_is_400_even_without_api_key(self, monkeypatch) -> None:
        configure(monkeypatch)
        response = post(content=b'{"question": "\xff"}', headers={"Content-Type": "application/json"})
        assert response.status_code == 400

    def test_the_openapi_schema_documents_the_request(self) -> None:
        operation = create_app().openapi()["paths"]["/api/v1/chat"]["post"]
        # The body lives in a dependency, and it is still the whole body, not a field of it.
        schema = operation["requestBody"]["content"]["application/json"]["schema"]
        assert schema == {"$ref": "#/components/schemas/ChatRequestBody"}

    def test_works_without_a_database(self, monkeypatch) -> None:
        configure(monkeypatch, database_url="")
        app = create_app()
        app.dependency_overrides[get_chat_responder] = lambda: ScriptedResponder(completed("Texto [P1]."))
        with TestClient(app) as client:
            assert app.state.db_pool is None
            reply = read_reply(client.post(CHAT, json=chat_body(), headers=HEADERS))
        assert reply.done["coverage"] == "answered"


class TestSchema:
    @pytest.fixture(autouse=True)
    def _fake_responder(self, monkeypatch) -> None:
        configure(monkeypatch, chat_fake_responder="true")

    @pytest.mark.parametrize(
        ("body", "error_type", "loc"),
        [
            ({"question": SECRET}, "missing", ["body", "passages"]),
            ({"passages": [passage(1, text=SECRET)]}, "missing", ["body", "question"]),
            (
                chat_body(question=SECRET, passages=[{"id": "a", "kind": "article", "reference": "r"}]),
                "missing",
                ["body", "passages", 0, "text"],
            ),
            (chat_body(passages=[passage(1, text=SECRET), passage(1)]), "value_error", ["body", "passages"]),
            (chat_body(passages=[passage(1, reference=f"{SECRET}\nart. 2")]), "string_pattern_mismatch", None),
            (chat_body(passages=[passage(1, reference=f"{SECRET}\u2028art. 2")]), "string_pattern_mismatch", None),
            (chat_body(passages=[passage(1, reference=f"{SECRET}\x1c")]), "string_pattern_mismatch", None),
            (chat_body(passages=[passage(1, reference=SECRET * 10)]), "string_too_long", None),
            (chat_body(passages=[passage(1, id=f"{SECRET}\n")]), "string_pattern_mismatch", ["body", "passages", 0, "id"]),
            (chat_body(passages=[passage(1, id=SECRET * 5)]), "string_too_long", ["body", "passages", 0, "id"]),
            (chat_body(passages=[passage(1, id="")]), "string_too_short", ["body", "passages", 0, "id"]),
            (chat_body(passages=[passage(1, kind="law", text=SECRET)]), "literal_error", ["body", "passages", 0, "kind"]),
            (chat_body(context={"app_name": f"{SECRET}\nPlanta"}), "string_pattern_mismatch", ["body", "context", "app_name"]),
            (chat_body(context={"app_name": f"{SECRET}\u2028Planta"}), "string_pattern_mismatch", ["body", "context", "app_name"]),
            (chat_body(context={"app_name": f"{SECRET}\u2029Planta"}), "string_pattern_mismatch", ["body", "context", "app_name"]),
            (chat_body(context={"app_name": f"{SECRET}\x1c"}), "string_pattern_mismatch", ["body", "context", "app_name"]),
            (chat_body(context={"app_name": f"{SECRET}\x1e"}), "string_pattern_mismatch", ["body", "context", "app_name"]),
            (chat_body(context={"app_name": " \n "}), "string_too_short", ["body", "context", "app_name"]),
            (chat_body(history=[{"role": "system", "content": SECRET}]), "literal_error", ["body", "history", 0, "role"]),
            ({**chat_body(), "passage": [SECRET]}, "extra_forbidden", ["body", "passage"]),
            (chat_body(passages=[passage(1, extra=SECRET)]), "extra_forbidden", ["body", "passages", 0, "extra"]),
            (chat_body(history=[{"role": "user", "content": "a", "name": SECRET}]), "extra_forbidden", None),
            (chat_body(context={"app_name": "Planta", "app_id": SECRET}), "extra_forbidden", None),
            (chat_body(question=[SECRET]), "string_type", ["body", "question"]),
            (chat_body(passages={"0": passage(1, text=SECRET)}), "list_type", ["body", "passages"]),
            ([SECRET], "model_attributes_type", ["body"]),
        ],
    )
    def test_rejections_never_repeat_the_request(self, caplog, body, error_type: str, loc: list | None) -> None:
        caplog.set_level(logging.DEBUG)
        detail = rejection(post(json=body))
        assert error_type in [error["type"] for error in detail], detail
        if loc is not None:
            assert loc in [error["loc"] for error in detail], detail
        assert SECRET not in caplog.text
        # The handler logs the type and the location of each error.
        assert error_type in caplog.text

    def test_repeated_ids_have_a_fixed_message(self) -> None:
        detail = rejection(post(json=chat_body(passages=[passage(1), passage(2, id="id-1")])))
        assert detail == [
            {"type": "value_error", "loc": ["body", "passages"], "msg": "Value error, Passage ids must be unique."}
        ]

    def test_lone_surrogate_in_an_id_is_rejected(self) -> None:
        # Escaped in the JSON, as a client would send it. Echoed back in done, System.Text.Json could not
        # read it; the one-line pattern rejects it before that.
        raw = json.dumps(chat_body(passages=[passage(1, id="id-\ud800")])).encode()
        detail = rejection(post(content=raw, headers={**HEADERS, "Content-Type": "application/json"}))
        assert detail[0]["type"] == "string_unicode"
        assert detail[0]["loc"] == ["body", "passages", 0, "id"]

    @pytest.mark.parametrize("ending", ["\n", "\u2028", "\r\n", "\u0085"])
    def test_reference_and_app_name_with_a_line_break_at_the_end_are_trimmed(self, ending: str) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        body = chat_body(
            passages=[passage(1, reference=f" Ley 16.744, art. 66{ending}")],
            context={"app_name": f"Planta Quilicura{ending}"},
        )
        reply = read_reply(post(json=body, responder=responder))
        assert reply.done["coverage"] == "answered"
        prompt = responder.prompts[0]
        assert prompt.passages[0].reference == "Ley 16.744, art. 66"
        assert prompt.app_name == "Planta Quilicura"

    def test_the_id_is_never_trimmed(self) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        reply = read_reply(post(json=chat_body(passages=[passage(1, id=" id 1 ")]), responder=responder))
        assert reply.done["citations"] == [{"n": 1, "id": " id 1 "}]

    def test_lone_surrogates_in_free_text_reach_the_model_as_replacement_characters(self) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        body = chat_body(
            question="¿Qué\ud800?",
            history=[{"role": "user", "content": "a\udfff"}],
            passages=[passage(1, text="t\udc00")],
        )
        raw = json.dumps(body).encode()
        read_reply(post(content=raw, headers={**HEADERS, "Content-Type": "application/json"}, responder=responder))
        prompt = responder.prompts[0]
        assert (prompt.question, prompt.history[0].content, prompt.passages[0].text) == ("¿Qué�?", "a�", "t�")

    def test_lone_surrogates_in_reference_and_app_name_reach_the_model_as_replacement_characters(self) -> None:
        # Like the free text: they only reach the model, so U+FFFD instead of a 422 (string_unicode).
        responder = ScriptedResponder(completed("Texto [P1]."))
        body = chat_body(passages=[passage(1, reference=" Ley\ud83d\n")], context={"app_name": "Planta\udfff"})
        raw = json.dumps(body).encode()
        reply = read_reply(post(content=raw, headers={**HEADERS, "Content-Type": "application/json"}, responder=responder))
        assert reply.done["coverage"] == "answered"
        prompt = responder.prompts[0]
        assert (prompt.passages[0].reference, prompt.app_name) == ("Ley\ufffd", "Planta\ufffd")

    @pytest.mark.parametrize(
        "changes",
        [
            {"history": None},
            {"context": None},
            {"context": {}},
            {"context": {"app_name": None}},
            # What System.Text.Json sends by default for a request without them.
            {"history": None, "context": {"app_name": None}},
        ],
    )
    def test_null_optional_fields_are_left_out(self, changes: dict) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        reply = read_reply(post(json=chat_body(**changes), responder=responder))
        assert reply.done["coverage"] == "answered"
        assert (responder.prompts[0].history, responder.prompts[0].app_name) == ((), None)

    @pytest.mark.parametrize(
        ("changes", "loc"),
        [
            ({"question": None}, ["body", "question"]),
            ({"passages": None}, ["body", "passages"]),
            ({"history": [{"role": None, "content": "a"}]}, ["body", "history", 0, "role"]),
            ({"history": [{"role": "user", "content": None}]}, ["body", "history", 0, "content"]),
            ({"passages": [passage(1, id=None)]}, ["body", "passages", 0, "id"]),
            ({"passages": [passage(1, kind=None)]}, ["body", "passages", 0, "kind"]),
            ({"passages": [passage(1, reference=None)]}, ["body", "passages", 0, "reference"]),
            ({"passages": [passage(1, text=None)]}, ["body", "passages", 0, "text"]),
        ],
    )
    def test_null_in_a_required_field_is_422(self, changes: dict, loc: list) -> None:
        detail = rejection(post(json=chat_body(**changes)))
        assert loc in [error["loc"] for error in detail]


class TestLimits:
    def test_default_limit_message_gives_only_lengths(self, monkeypatch) -> None:
        configure(monkeypatch, chat_fake_responder="true")
        response = post(json=chat_body(question=SECRET * 100))
        assert response.status_code == 422
        assert response.json() == {"detail": f"The question has {len(SECRET) * 100} characters; the limit is 2000."}

    @pytest.mark.parametrize(
        ("env", "body", "message"),
        [
            (
                {"chat_question_max_chars": "10"},
                chat_body(question=SECRET),
                f"The question has {len(SECRET)} characters; the limit is 10.",
            ),
            (
                {"chat_history_max_messages": "1"},
                chat_body(history=[{"role": "user", "content": SECRET}] * 2),
                "The history has 2 messages; the limit is 1.",
            ),
            (
                {"chat_history_max_chars": "10"},
                chat_body(history=[{"role": "user", "content": SECRET}]),
                f"The history has {len(SECRET)} characters; the limit is 10.",
            ),
            ({"chat_max_passages": "1"}, chat_body(), "The request has 2 passages; the limit is 1."),
            (
                {"chat_passage_max_chars": "10"},
                chat_body(passages=[passage(1, text=SECRET)]),
                f"passages[0].text has {len(SECRET)} characters; the limit is 10.",
            ),
            (
                {"chat_passages_max_total_chars": "30"},
                chat_body(passages=[passage(1, text=SECRET), passage(2, text=SECRET)]),
                f"The passages have {len(SECRET) * 2} characters in total; the limit is 30.",
            ),
        ],
    )
    def test_each_limit_comes_from_its_variable(self, monkeypatch, caplog, env: dict, body: dict, message: str) -> None:
        configure(monkeypatch, chat_fake_responder="true", **env)
        response = post(json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": message}
        assert SECRET not in caplog.text


class TestStream:
    @pytest.fixture(autouse=True)
    def _service_key(self, monkeypatch) -> None:
        configure(monkeypatch)

    def test_deltas_then_one_done_with_citations_and_coverage(self) -> None:
        responder = ScriptedResponder(completed("La ley exige un comité [P", "2]. También [P1][P2]."))
        reply = read_reply(post(json=chat_body(), responder=responder))
        assert reply.deltas == ["La ley exige un comité ", "[1]. También [2][1]."]
        done = reply.done
        assert done["citations"] == [{"n": 1, "id": "id-2"}, {"n": 2, "id": "id-1"}]
        assert (done["coverage"], done["citations_dropped"], done["finish_reason"]) == ("answered", 0, "stop")
        assert done["usage"] == asdict(USAGE)
        assert done["chat_version"] == TEST_VERSION
        metrics = done["dev_metrics"]
        assert list(metrics) == ["elapsed_ms", "first_delta_ms"]
        assert 0 <= metrics["first_delta_ms"] <= metrics["elapsed_ms"]
        # Each data field is JSON in one line; no ping in an answer this fast.
        assert reply.comments == []
        assert len(responder.prompts) == 1

    def test_a_nested_span_split_between_fragments_never_becomes_a_citation(self) -> None:
        responder = ScriptedResponder(completed("Según el artículo [P1]. Ver nota [3[P9", "]] al final."))
        reply = read_reply(post(json=chat_body(passages=[passage(1)]), responder=responder))
        assert reply.deltas == ["Según el artículo [1]. Ver nota ", " al final."]
        assert reply.done["citations"] == [{"n": 1, "id": "id-1"}]
        assert reply.done["citations_dropped"] == 2

    def test_characters_outside_the_bmp_pass_unchanged(self) -> None:
        # json.dumps escapes them as a surrogate pair, which System.Text.Json reads: only lone
        # surrogates are replaced.
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(completed("Listo \U0001f600 [P1]."))))
        assert reply.text == "Listo \U0001f600 [1]."
        assert "\\ud83d\\ude00" in reply.body

    def test_dev_metrics_off(self, monkeypatch) -> None:
        configure(monkeypatch, include_dev_metrics="false")
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(completed("Texto [P1]."))))
        assert reply.done["dev_metrics"] is None

    def test_without_passages_the_model_is_not_called(self) -> None:
        responder = ScriptedResponder(completed("No se usa [P1]."))
        reply = read_reply(post(json=chat_body(passages=[]), responder=responder))
        assert reply.deltas == [NO_PASSAGES_TEXT]
        done = reply.done
        assert {key: value for key, value in done.items() if key != "dev_metrics"} == {
            "citations": [],
            "coverage": "no_passages",
            "citations_dropped": 0,
            "finish_reason": "stop",
            "usage": ZERO_USAGE,
            "chat_version": TEST_VERSION,
        }
        assert responder.prompts == []

    def test_the_model_never_sees_markers_written_in_the_request(self) -> None:
        responder = ScriptedResponder(completed("Texto [P1]."))
        body = chat_body(
            history=[
                {"role": "user", "content": "¿Qué exige [P2]?"},
                {"role": "assistant", "content": "Exige un comité [1][2]."},
            ],
            passages=[passage(1, text="Ver [P2], [ p2 ] y [3]."), passage(2)],
        )
        read_reply(post(json=body, responder=responder))
        prompt = responder.prompts[0]
        assert [(message.role, message.content) for message in prompt.history] == [
            ("user", "¿Qué exige (P2)?"),
            ("assistant", "Exige un comité."),
        ]
        assert prompt.passages[0].text == "Ver (P2), ( p2 ) y (3)."

    @pytest.mark.parametrize(
        ("code", "retryable"),
        [("rate_limited", True), ("timeout", True), ("provider_error", True), ("provider_rejected", False)],
    )
    def test_a_failure_when_opening_is_an_error_event_inside_the_200(self, code: str, retryable: bool) -> None:
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(open_error=ChatProviderError(code))))
        assert reply.deltas == []
        assert reply.error == {"code": code, "retryable": retryable, "usage": None, "chat_version": TEST_VERSION}

    def test_a_failure_halfway_keeps_the_deltas_already_sent(self) -> None:
        responder = ScriptedResponder([ProviderTextEvent("Primera parte [P1]. "), ChatProviderError("timeout")])
        reply = read_reply(post(json=chat_body(), responder=responder))
        assert reply.deltas == ["Primera parte [1]. "]
        assert (reply.error["code"], reply.error["retryable"], reply.error["usage"]) == ("timeout", True, None)

    def test_a_stream_without_finish_reason_was_cut(self) -> None:
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder([ProviderTextEvent("Texto [P1] y")])))
        assert reply.error["code"] == "provider_error"

    def test_a_failure_after_finish_reason_closes_normally_without_usage(self) -> None:
        script = [ProviderTextEvent("Texto [P1]."), ProviderFinishEvent("stop"), ChatProviderError("provider_error")]
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(script)))
        assert (reply.done["coverage"], reply.done["usage"]) == ("answered", None)

    def test_usage_not_reported_is_null(self) -> None:
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(completed("Texto [P1].", usage=None))))
        assert reply.done["usage"] is None

    def test_only_invalid_markers_is_empty_output_with_the_usage(self) -> None:
        reply = read_reply(post(json=chat_body(), responder=ScriptedResponder(completed("[P9] [3]"))))
        assert reply.error == {"code": "empty_output", "retryable": False, "usage": asdict(USAGE), "chat_version": TEST_VERSION}

    def test_an_unexpected_exception_in_the_provider_is_internal(self, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        responder = ScriptedResponder([ProviderTextEvent("Texto [P1]. "), RuntimeError(SECRET)])
        reply = read_reply(post(json=chat_body(), responder=responder))
        assert reply.deltas == ["Texto [1]. "]
        assert (reply.error["code"], reply.error["retryable"]) == ("internal", False)
        assert "RuntimeError" in caplog.text
        assert SECRET not in caplog.text and SECRET not in reply.body

    def test_the_log_has_counts_and_times_but_no_content(self, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        body = chat_body(
            question=f"¿{SECRET}?",
            history=[{"role": "user", "content": SECRET}],
            passages=[passage(1, reference=SECRET, text=SECRET), passage(2, kind="obligation")],
            context={"app_name": SECRET},
        )
        read_reply(post(json=body, responder=ScriptedResponder(completed(f"{SECRET} [P1]."))))
        assert "Chat answer done: 2 passages (article 1, obligation 1)" in caplog.text
        assert SECRET not in caplog.text


class _BrokenUseCase:
    """The real prepare, and a stream scripted on purpose to break the rules of the use case."""

    def __init__(self, events: list) -> None:
        self._real = AnswerChatUseCase(ScriptedResponder(), LIMITS)
        self._events = events

    @property
    def version(self) -> str:
        return TEST_VERSION

    def prepare(self, request):
        return self._real.prepare(request)

    async def stream(self, prepared, slot):
        for event in self._events:
            if isinstance(event, BaseException):
                raise event
            yield event


class TestRouterSafetyNet:
    @pytest.fixture(autouse=True)
    def _service_key(self, monkeypatch) -> None:
        configure(monkeypatch)

    def test_an_exception_outside_the_use_case_still_ends_with_one_error(self, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        use_case = _BrokenUseCase([ChatDeltaEvent("Texto"), RuntimeError(SECRET)])
        reply = read_reply(post(json=chat_body(), use_case=use_case))
        assert reply.deltas == ["Texto"]
        assert reply.error == {"code": "internal", "retryable": False, "usage": None, "chat_version": TEST_VERSION}
        assert SECRET not in caplog.text

    def test_an_answer_without_its_final_event_gets_one(self, caplog) -> None:
        reply = read_reply(post(json=chat_body(), use_case=_BrokenUseCase([ChatDeltaEvent("Texto")])))
        assert reply.deltas == ["Texto"]
        assert reply.error["code"] == "internal"
        assert "RuntimeError" in caplog.text

    def test_nothing_follows_the_final_event(self) -> None:
        done = ChatDoneEvent(
            citations=(ChatCitation(n=1, id="id-1"),),
            coverage="answered",
            citations_dropped=0,
            finish_reason="stop",
            usage=None,
            chat_version=TEST_VERSION,
        )
        use_case = _BrokenUseCase([ChatDeltaEvent("Texto [1]."), done, ChatDeltaEvent("más"), done, RuntimeError(SECRET)])
        reply = read_reply(post(json=chat_body(), use_case=use_case))
        assert [event.name for event in reply.events] == ["delta", "done"]


class TestRequestScope:
    """chat_stream_state, as FastAPI drives it when the response ends or the client disconnects."""

    def _disconnect(self, stream: ScriptedStream, *, started: bool = True) -> None:
        async def scenario() -> None:
            dependency = chat_stream_state()
            state = await anext(dependency)
            state.slot.hold(stream)
            if started:
                state.started_at = perf_counter()
            # The exception FastAPI throws in when the response is cut must come out again.
            with pytest.raises(RuntimeError, match="cut"):
                await dependency.athrow(RuntimeError("cut"))

        asyncio.run(scenario())

    def test_closes_the_stream_and_lets_the_exception_through(self, caplog) -> None:
        caplog.set_level(logging.INFO)
        stream = ScriptedStream(())
        self._disconnect(stream)
        assert stream.closed
        assert "Chat answer stopped before its final event after 0 deltas" in caplog.text

    def test_a_failing_close_is_logged_without_hiding_the_exception(self, caplog) -> None:
        stream = ScriptedStream((), close_error=OSError(SECRET))
        self._disconnect(stream)
        assert stream.close_calls == 1
        assert "OSError" in caplog.text and SECRET not in caplog.text

    def test_a_request_rejected_before_the_stream_logs_no_stop(self, caplog) -> None:
        caplog.set_level(logging.INFO)
        self._disconnect(ScriptedStream(()), started=False)
        assert "stopped" not in caplog.text


class TestFakeResponder:
    def test_cites_the_first_passage_without_an_openai_key(self, monkeypatch, caplog) -> None:
        configure(monkeypatch, chat_fake_responder="true")
        caplog.set_level(logging.WARNING)
        reply = read_reply(post(json=chat_body()))
        assert len(reply.deltas) > 1
        assert reply.text.endswith("[1].")
        done = reply.done
        assert done["citations"] == [{"n": 1, "id": "id-1"}]
        assert (done["coverage"], done["citations_dropped"], done["finish_reason"]) == ("answered", 0, "stop")
        assert (done["usage"], done["chat_version"]) == (ZERO_USAGE, "fake-v1@fake")
        # The startup warning, from the lifespan.
        assert "CHAT_FAKE_RESPONDER is on" in caplog.text

    def test_without_passages_it_gives_the_no_passages_refusal(self, monkeypatch) -> None:
        configure(monkeypatch, chat_fake_responder="true")
        reply = read_reply(post(json=chat_body(passages=[])))
        assert reply.deltas == [NO_PASSAGES_TEXT]
        assert (reply.done["coverage"], reply.done["usage"], reply.done["chat_version"]) == (
            "no_passages",
            ZERO_USAGE,
            "fake-v1@fake",
        )

    def test_no_startup_warning_when_it_is_off(self, monkeypatch, caplog) -> None:
        configure(monkeypatch)
        caplog.set_level(logging.WARNING)
        with TestClient(create_app()):
            pass
        assert "CHAT_FAKE_RESPONDER" not in caplog.text
