"""The OpenAI chat adapter against a fake OpenAI on 127.0.0.1: the call it makes, how it reads the
chunks and how every provider failure reaches the stream (CHT-009, CHT-010, CHT-012, CHT-013)."""

import asyncio
import json
import logging
import socket
import time
from collections.abc import Iterator
from dataclasses import asdict, replace
from types import SimpleNamespace

import httpx2
import pytest
from chat_doubles import LIMITS
from chat_http import HEADERS, SECRET, chat_body, configure, read_reply
from fake_openai import FakeOpenAI, Scenario, answer, chunk, running, usage_chunk
from fastapi.testclient import TestClient
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError
from openai.resources.chat.completions import AsyncCompletions
from openai.types.chat import ChatCompletionChunk

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.outbound.openai.chat_prompts import CHAT_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.chat_responder import (
    OpenAIChatResponderAdapter,
    OpenAIChatStream,
    _chunk_events,
    _opening_error_code,
    _streaming_error_code,
)
from ia_cumplify.application.use_cases.answer_chat import REFUSED_TEXT, AnswerChatUseCase
from ia_cumplify.domain.chat import (
    ChatRequest,
    ChatUsage,
    ProviderFinishEvent,
    ProviderRefusalEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
)
from ia_cumplify.domain.exceptions import ChatProviderError

OPENAI_KEY = "sk-test-fake"
ROLE = chunk(role="assistant", content="")


def _status(status: int, code: str | None = None, *headers: tuple[str, str]) -> Scenario:
    error = {"message": f"{SECRET} rejected", "type": "invalid_request_error", "param": None, "code": code}
    return Scenario(status=status, error={"error": error}, headers=headers)


SCENARIOS: dict[str, Scenario | list[Scenario]] = {
    "answer": Scenario(events=answer("Debe constituirse un comité [P", "2] según el artículo [P1].")),
    "no_usage": Scenario(events=answer("Texto [P1].", usage=False)),
    "length": Scenario(events=answer("Texto incompleto [P1] y", finish="length")),
    "rate_limited": _status(429, "rate_limit_exceeded"),
    "quota": _status(429, "insufficient_quota"),
    "bad_request": _status(400),
    "unauthorized": _status(401, "invalid_api_key"),
    "forbidden": _status(403),
    "not_found": _status(404, "model_not_found"),
    "too_large": _status(413),
    "unprocessable": _status(422),
    "request_timeout": _status(408),
    "conflict": _status(409),
    "server_error": _status(500),
    "unavailable": _status(503),
    # An error status whose body is cut or never ends: the SDK reads it outside its own error handling.
    "server_error_cut": replace(_status(500), ending="abort"),
    "server_error_stall": replace(_status(500), ending="hang"),
    "bad_request_cut": replace(_status(400), ending="abort"),
    "rate_limited_cut": replace(_status(429, "rate_limit_exceeded"), ending="abort"),
    "late_headers": Scenario(events=answer("Texto [P1]."), headers_delay=3.0),
    "stall": Scenario(events=(ROLE, chunk("Primera parte [P1]. ")), ending="hang"),
    "abort": Scenario(events=(ROLE, chunk("Primera parte [P1]. ")), ending="abort"),
    "error_inside": Scenario(events=(ROLE, chunk("Texto [P1]. "), {"error": {"message": SECRET, "type": "server_error"}})),
    "no_finish": Scenario(events=(ROLE, chunk("Texto [P1] cortado")), ending="eof"),
    "no_finish_done": Scenario(events=(ROLE, chunk("Texto [P1] cortado")), ending="done"),
    "cut_after_finish": Scenario(events=(ROLE, chunk("Texto [P1]."), chunk(finish="stop")), ending="abort"),
    "refusal": Scenario(events=(ROLE, chunk(refusal=SECRET), chunk(refusal=" y más"), chunk(finish="stop"), usage_chunk())),
    "refusal_after_text": Scenario(
        events=(ROLE, chunk("Dice X [P2]. Y [P"), chunk(refusal=SECRET), chunk("1]."), chunk(finish="stop"), usage_chunk())
    ),
    "empty_refusal": Scenario(
        events=(chunk(role="assistant", content="", refusal=""), chunk("Texto [P1]."), chunk(finish="stop"), usage_chunk())
    ),
    "surrogate": Scenario(events=answer("Seg\ud800n [P1].")),
    "malformed": Scenario(events=(ROLE, "{no es JSON")),
    "retry": [_status(500, None, ("retry-after-ms", "1")), Scenario(events=answer("Texto [P1]."))],
    "burst": Scenario(events=answer(*(f"t{i} " for i in range(5000))), per_write=5, delay=0.002),
}


@pytest.fixture
def fake(monkeypatch) -> Iterator[FakeOpenAI]:
    with running(SCENARIOS) as server:
        configure(monkeypatch, openai_api_key=OPENAI_KEY)
        monkeypatch.setenv("OPENAI_BASE_URL", server.base_url)
        yield server


def ask(scenario: str, **changes):
    """The fake OpenAI picks the scenario by the first word of the question."""
    with TestClient(create_app()) as client:
        return read_reply(client.post("/api/v1/chat", json=chat_body(question=scenario, **changes), headers=HEADERS))


class TestTheCall:
    def test_carries_the_contract_parameters_and_nothing_else(self, fake: FakeOpenAI) -> None:
        reply = ask("answer", history=[{"role": "user", "content": "¿Y el comité?"}], context={"app_name": "Planta"})
        assert reply.done["coverage"] == "answered"
        [request] = fake.requests("answer")
        assert (request["method"], request["path"]) == ("POST", "/v1/chat/completions")
        assert request["headers"]["authorization"] == f"Bearer {OPENAI_KEY}"
        body = request["body"]
        # Exactly these: no prompt_cache_options (the cache mode is still to be agreed), no user and
        # no safety_identifier.
        assert sorted(body) == sorted(
            ["model", "messages", "stream", "stream_options", "store", "reasoning_effort", "max_completion_tokens"]
        )
        assert (body["model"], body["reasoning_effort"], body["max_completion_tokens"]) == ("gpt-5.6-luna", "low", 4000)
        assert (body["stream"], body["stream_options"], body["store"]) == (True, {"include_usage": True}, False)
        messages = body["messages"]
        assert [message["role"] for message in messages] == ["system", "user", "user", "user"]
        assert messages[0]["content"] == CHAT_SYSTEM_PROMPT
        assert messages[1]["content"] == "¿Y el comité?"
        assert messages[2]["content"].startswith("App: Planta\n\n--- PASAJE P1 ---\n")
        assert messages[3]["content"] == "answer"
        # The model never sees a passage id.
        assert "id-1" not in json.dumps(messages)

    def test_chat_settings_take_precedence_over_the_openai_ones(self, fake: FakeOpenAI, monkeypatch) -> None:
        configure(
            monkeypatch,
            openai_model="modelo-general",
            chat_model="modelo-chat",
            chat_reasoning_effort="medium",
            chat_max_completion_tokens="1234",
        )
        reply = ask("answer")
        body = fake.requests("answer")[0]["body"]
        assert (body["model"], body["reasoning_effort"], body["max_completion_tokens"]) == ("modelo-chat", "medium", 1234)
        assert reply.done["chat_version"] == "chat-v3@modelo-chat"

    def test_empty_chat_model_and_effort_use_the_openai_ones(self, fake: FakeOpenAI, monkeypatch) -> None:
        configure(monkeypatch, openai_model="modelo-general", openai_reasoning_effort="high", chat_model="")
        reply = ask("answer")
        body = fake.requests("answer")[0]["body"]
        assert (body["model"], body["reasoning_effort"]) == ("modelo-general", "high")
        assert reply.done["chat_version"] == "chat-v3@modelo-general"

    def test_the_constructor_loads_the_chat_resources(self) -> None:
        adapter = OpenAIChatResponderAdapter(
            api_key=OPENAI_KEY, model="m", reasoning_effort="low", max_completion_tokens=10, timeout_seconds=60, max_retries=0
        )
        # Resolved here, in the threadpool that builds the dependency, and not inside the stream.
        assert isinstance(adapter._completions, AsyncCompletions)
        timeout = adapter._completions._client.timeout
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (5.0, 60, 60, 60)
        assert adapter._completions._client.max_retries == 0


class TestTheAnswer:
    def test_a_full_answer(self, fake: FakeOpenAI) -> None:
        reply = ask("answer")
        assert reply.text == "Debe constituirse un comité [1] según el artículo [2]."
        done = reply.done
        assert done["citations"] == [{"n": 1, "id": "id-2"}, {"n": 2, "id": "id-1"}]
        assert (done["coverage"], done["citations_dropped"], done["finish_reason"]) == ("answered", 0, "stop")
        assert done["usage"] == {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "cached_tokens": 640,
            "reasoning_tokens": 96,
            "llm_calls": 1,
        }
        assert done["chat_version"] == "chat-v3@gpt-5.6-luna"

    def test_usage_not_reported_is_null(self, fake: FakeOpenAI) -> None:
        assert ask("no_usage").done["usage"] is None

    def test_length_keeps_the_valid_citations(self, fake: FakeOpenAI) -> None:
        done = ask("length").done
        assert (done["finish_reason"], done["coverage"], done["citations"]) == ("length", "answered", [{"n": 1, "id": "id-1"}])

    def test_refusal_before_any_text(self, fake: FakeOpenAI) -> None:
        reply = ask("refusal")
        assert reply.deltas == [REFUSED_TEXT]
        assert (reply.done["coverage"], reply.done["citations"], reply.done["usage"]["llm_calls"]) == ("refused", [], 1)
        assert SECRET not in reply.body

    def test_refusal_after_text(self, fake: FakeOpenAI) -> None:
        reply = ask("refusal_after_text")
        assert reply.deltas == ["Dice X [1]. Y ", f"\n\n{REFUSED_TEXT}"]
        assert (reply.done["coverage"], reply.done["citations"]) == ("refused", [{"n": 1, "id": "id-2"}])
        assert SECRET not in reply.body

    def test_an_empty_refusal_field_is_not_a_refusal(self, fake: FakeOpenAI) -> None:
        assert ask("empty_refusal").done["coverage"] == "answered"

    def test_a_lone_surrogate_from_the_provider_becomes_a_replacement_character(self, fake: FakeOpenAI) -> None:
        reply = ask("surrogate")
        # read_reply already checked that no escaped surrogate reached the body and that done is the
        # only final event.
        assert reply.text == "Seg�n [1]."
        assert reply.done["coverage"] == "answered"


class TestProviderFailures:
    @pytest.mark.parametrize(
        ("scenario", "code"),
        [
            ("rate_limited", "rate_limited"),
            ("quota", "provider_rejected"),
            ("bad_request", "provider_rejected"),
            ("unauthorized", "provider_rejected"),
            ("forbidden", "provider_rejected"),
            ("not_found", "provider_rejected"),
            ("too_large", "provider_rejected"),
            ("unprocessable", "provider_rejected"),
            ("request_timeout", "provider_error"),
            ("conflict", "provider_error"),
            ("server_error", "provider_error"),
            ("unavailable", "provider_error"),
        ],
    )
    def test_a_rejection_when_opening(self, fake: FakeOpenAI, caplog, scenario: str, code: str) -> None:
        caplog.set_level(logging.DEBUG)
        reply = ask(scenario)
        assert reply.deltas == []
        assert reply.error == {
            "code": code,
            "retryable": code != "provider_rejected",
            "usage": None,
            "chat_version": "chat-v3@gpt-5.6-luna",
        }
        # CHAT_MAX_RETRIES is 0 by default: one request, even for the retryable statuses.
        assert len(fake.requests(scenario)) == 1
        # The provider's message never reaches the stream nor the log; its status does reach the log.
        assert SECRET not in reply.body and SECRET not in caplog.text
        assert "The chat provider answered HTTP" in caplog.text

    @pytest.mark.parametrize(
        ("scenario", "code"),
        [
            ("server_error_cut", "provider_error"),
            ("bad_request_cut", "provider_error"),
            ("rate_limited_cut", "provider_error"),
            ("server_error_stall", "timeout"),
        ],
    )
    def test_an_error_status_whose_body_is_cut_or_stalls(
        self, fake: FakeOpenAI, monkeypatch, caplog, scenario: str, code: str
    ) -> None:
        # Without its body there is no code (insufficient_quota...): a cut connection or a timeout.
        configure(monkeypatch, chat_timeout_seconds="0.3")
        caplog.set_level(logging.DEBUG)
        reply = ask(scenario)
        assert reply.deltas == []
        assert (reply.error["code"], reply.error["retryable"]) == (code, True)
        assert len(fake.requests(scenario)) == 1
        assert "failed unexpectedly" not in caplog.text
        assert "Reading the chat provider's error answer failed" in caplog.text
        assert SECRET not in reply.body and SECRET not in caplog.text

    def test_connection_refused(self, monkeypatch) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = sock.getsockname()[1]
        configure(monkeypatch, openai_api_key=OPENAI_KEY)
        monkeypatch.setenv("OPENAI_BASE_URL", f"http://127.0.0.1:{closed_port}/v1")
        assert ask("answer").error["code"] == "provider_error"

    def test_timeout_before_the_headers(self, fake: FakeOpenAI, monkeypatch) -> None:
        configure(monkeypatch, chat_timeout_seconds="0.3")
        started = time.monotonic()
        reply = ask("late_headers")
        assert time.monotonic() - started < 2.5
        assert reply.deltas == []
        assert (reply.error["code"], reply.error["retryable"]) == ("timeout", True)

    def test_timeout_between_chunks(self, fake: FakeOpenAI, monkeypatch) -> None:
        configure(monkeypatch, chat_timeout_seconds="0.3")
        reply = ask("stall")
        assert reply.deltas == ["Primera parte [1]. "]
        assert reply.error["code"] == "timeout"

    def test_connection_cut_halfway(self, fake: FakeOpenAI) -> None:
        reply = ask("abort")
        assert reply.deltas == ["Primera parte [1]. "]
        assert (reply.error["code"], reply.error["usage"]) == ("provider_error", None)

    def test_error_inside_the_accepted_stream(self, fake: FakeOpenAI, caplog) -> None:
        caplog.set_level(logging.DEBUG)
        reply = ask("error_inside")
        assert reply.deltas == ["Texto [1]. "]
        assert reply.error["code"] == "provider_error"
        assert SECRET not in reply.body and SECRET not in caplog.text

    @pytest.mark.parametrize("scenario", ["no_finish", "no_finish_done"])
    def test_a_stream_that_ends_without_finish_reason_was_cut(self, fake: FakeOpenAI, scenario: str) -> None:
        reply = ask(scenario)
        assert reply.deltas == ["Texto [1] cortado"]
        assert reply.error["code"] == "provider_error"

    def test_a_cut_after_finish_reason_closes_normally_without_usage(self, fake: FakeOpenAI) -> None:
        reply = ask("cut_after_finish")
        assert (reply.done["coverage"], reply.done["finish_reason"], reply.done["usage"]) == ("answered", "stop", None)

    def test_a_chunk_the_sdk_cannot_decode_is_internal(self, fake: FakeOpenAI) -> None:
        # CHT-010: any exception other than the SDK's errors is internal. The httpx2 errors the SDK lets
        # escape when opening are mapped as its own stream maps them (test above).
        assert ask("malformed").error["code"] == "internal"

    def test_retries_happen_only_before_the_provider_answers(self, fake: FakeOpenAI, monkeypatch) -> None:
        configure(monkeypatch, chat_max_retries="1")
        assert ask("retry").done["coverage"] == "answered"
        assert len(fake.requests("retry")) == 2
        # After the 200, a failure is never retried.
        assert ask("abort").error["code"] == "provider_error"
        assert len(fake.requests("abort")) == 1


def _wait_for(fake: FakeOpenAI, cid: int, event: str, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [record for record in fake.events_of(cid) if record["ev"] == event]
        if found:
            return found[0]
        time.sleep(0.01)
    return None


def test_closing_the_stream_closes_the_provider_connection(fake: FakeOpenAI) -> None:
    adapter = OpenAIChatResponderAdapter(
        api_key=OPENAI_KEY, model="m", reasoning_effort="low", max_completion_tokens=10, timeout_seconds=5, max_retries=0
    )
    prepared = AnswerChatUseCase(adapter, LIMITS).prepare(ChatRequest(question="burst"))

    async def scenario() -> float:
        stream = await adapter.open(prepared.prompt)
        events = aiter(stream)
        assert isinstance(await anext(events), ProviderTextEvent)
        closed_at = time.time()
        await stream.aclose()
        await events.aclose()
        return closed_at

    closed_at = asyncio.run(scenario())
    [request] = fake.requests("burst")
    eof = _wait_for(fake, request["cid"], "eof")
    assert eof is not None
    assert (eof["t"] - closed_at) * 1000 < 500
    stopped = _wait_for(fake, request["cid"], "stopped_after_eof")
    assert stopped is not None and stopped["sent"] < 5004


_REQUEST = httpx2.Request("POST", "http://127.0.0.1:9/v1/chat/completions")


def _status_error(status: int, code: str | None = None) -> APIStatusError:
    body = {"message": SECRET, "type": "x", "param": None, "code": code}
    return APIStatusError(SECRET, response=httpx2.Response(status, request=_REQUEST, json={"error": body}), body=body)


class TestErrorCodes:
    @pytest.mark.parametrize(
        ("status", "code", "expected"),
        [
            (400, None, "provider_rejected"),
            (401, "invalid_api_key", "provider_rejected"),
            (403, None, "provider_rejected"),
            (404, "model_not_found", "provider_rejected"),
            (413, None, "provider_rejected"),
            (422, None, "provider_rejected"),
            (429, "rate_limit_exceeded", "rate_limited"),
            (429, None, "rate_limited"),
            (429, "insufficient_quota", "provider_rejected"),
            (408, None, "provider_error"),
            (409, None, "provider_error"),
            (500, None, "provider_error"),
            (502, None, "provider_error"),
            (503, None, "provider_error"),
            (504, None, "provider_error"),
            # No row in the contract: an unexpected answer of the provider.
            (302, None, "provider_error"),
        ],
    )
    def test_status_when_opening(self, status: int, code: str | None, expected: str) -> None:
        assert _opening_error_code(_status_error(status, code)) == expected

    def test_timeout_is_checked_before_connection(self) -> None:
        # APITimeoutError is an APIConnectionError: in the other order every timeout is provider_error.
        assert issubclass(APITimeoutError, APIConnectionError)
        assert _opening_error_code(APITimeoutError(request=_REQUEST)) == "timeout"
        assert _opening_error_code(APIConnectionError(request=_REQUEST)) == "provider_error"
        assert _opening_error_code(APIError(SECRET, _REQUEST, body=None)) == "provider_error"

    @pytest.mark.parametrize(
        ("error", "expected"),
        [
            (APITimeoutError(request=_REQUEST), "timeout"),
            (APIConnectionError(request=_REQUEST), "provider_error"),
            (APIError(SECRET, _REQUEST, body={"message": SECRET}), "provider_error"),
            # Inside the stream a status never makes rate_limited nor provider_rejected.
            (_status_error(429, "insufficient_quota"), "provider_error"),
            (_status_error(400), "provider_error"),
        ],
    )
    def test_while_streaming(self, error: APIError, expected: str) -> None:
        assert _streaming_error_code(error) == expected

    def test_the_stream_maps_its_failures_as_streaming_ones(self) -> None:
        # The SDK raises no status error once the stream is open; if it ever did, it must not come out
        # as rate_limited or provider_rejected, which only describe a rejection when opening.
        class FailingSdkStream:
            async def __anext__(self):
                raise _status_error(429, "insufficient_quota")

        async def scenario() -> str:
            with pytest.raises(ChatProviderError) as caught:
                async for _ in OpenAIChatStream(FailingSdkStream()):  # type: ignore[arg-type]
                    pass
            return caught.value.code

        assert asyncio.run(scenario()) == "provider_error"

    def test_the_error_carries_no_provider_message(self) -> None:
        error = ChatProviderError(_opening_error_code(_status_error(400)))
        assert SECRET not in str(error) and SECRET not in repr(error)


class TestChunks:
    def test_refusal_comes_before_the_text_of_the_same_chunk(self) -> None:
        events = _chunk_events(ChatCompletionChunk.model_validate(chunk("Texto", refusal="no")))
        assert events == [ProviderRefusalEvent(), ProviderTextEvent("Texto")]

    def test_finish_reason_goes_as_the_provider_sent_it(self) -> None:
        events = _chunk_events(ChatCompletionChunk.model_validate(chunk(finish="tool_calls")))
        assert events == [ProviderFinishEvent("tool_calls")]

    def test_usage_with_and_without_details(self) -> None:
        [event] = _chunk_events(ChatCompletionChunk.model_validate(usage_chunk(10, 5, 3, 2)))
        assert event == ProviderUsageEvent(ChatUsage(10, 5, 15, 3, 2, 1))
        bare = {**usage_chunk(), "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        [event] = _chunk_events(ChatCompletionChunk.model_validate(bare))
        assert asdict(event.usage) == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
            "llm_calls": 1,
        }

    def test_a_chunk_with_missing_fields_gives_nothing(self) -> None:
        assert _chunk_events(SimpleNamespace()) == []  # type: ignore[arg-type]
        assert _chunk_events(SimpleNamespace(choices=[SimpleNamespace(delta=None)], usage=None)) == []  # type: ignore[arg-type]
