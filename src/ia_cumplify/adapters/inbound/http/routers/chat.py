import logging
from collections import Counter
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from ia_cumplify.adapters.inbound.http.dependencies import get_chat_responder
from ia_cumplify.adapters.inbound.http.schemas.chat import ChatRequestBody
from ia_cumplify.application.ports.chat_responder import ChatResponderPort, ProviderStreamSlot
from ia_cumplify.application.use_cases.answer_chat import AnswerChatUseCase, ChatLimits, PreparedChat
from ia_cumplify.config.settings import Settings, get_settings
from ia_cumplify.domain.chat import ChatDeltaEvent, ChatDoneEvent, ChatErrorEvent, ChatUsage

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


def chat_limits(settings: Settings) -> ChatLimits:
    return ChatLimits(
        question_max_chars=settings.chat_question_max_chars,
        history_max_messages=settings.chat_history_max_messages,
        history_max_chars=settings.chat_history_max_chars,
        max_passages=settings.chat_max_passages,
        passage_max_chars=settings.chat_passage_max_chars,
        passages_max_total_chars=settings.chat_passages_max_total_chars,
    )


def get_answer_chat_use_case(
    responder: ChatResponderPort = Depends(get_chat_responder),
) -> AnswerChatUseCase:
    return AnswerChatUseCase(responder, chat_limits(get_settings()))


def prepare_chat(
    body: ChatRequestBody,
    use_case: AnswerChatUseCase = Depends(get_answer_chat_use_case),
) -> PreparedChat:
    """Validates the whole request before the stream opens: inside the stream, a failure would be a
    200 without events instead of a 422.

    FastAPI solves use_case before validating the body, whatever their order here, so a missing
    OPENAI_API_KEY answers 503 before any 422 (CHT-006).
    """
    try:
        return use_case.prepare(body.to_domain())
    except ValueError as exc:
        # Lengths, limits and positions only: never what the request says.
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@dataclass(slots=True)
class ChatStreamState:
    """One answer, as the request scope sees it: the provider stream to close and how far it got."""

    slot: ProviderStreamSlot = field(default_factory=ProviderStreamSlot)
    started_at: float | None = None
    deltas: int = 0
    ended: bool = False


async def chat_stream_state() -> AsyncIterator[ChatStreamState]:
    """Closes the provider stream as soon as the response ends or the client disconnects (CHT-011).

    When the client disconnects in the middle of a burst, the answer generator stays suspended and its
    own finally runs only on garbage collection; this request-scoped teardown always runs. It lets the
    exception of a disconnect go on: swallowing it makes FastAPI fail with "Response not awaited".
    """
    state = ChatStreamState()
    try:
        yield state
    finally:
        if state.started_at is not None and not state.ended:
            logger.info(
                "Chat answer stopped before its final event after %d deltas and %.1f ms.",
                state.deltas,
                _ms_since(state.started_at),
            )
        try:
            await state.slot.aclose()
        except Exception as exc:
            logger.warning("Closing the chat provider stream failed: %s", type(exc).__name__)


@router.post("", response_class=EventSourceResponse)
async def answer_chat(
    prepared: PreparedChat = Depends(prepare_chat),
    use_case: AnswerChatUseCase = Depends(get_answer_chat_use_case),
    state: ChatStreamState = Depends(chat_stream_state, scope="request"),
) -> AsyncIterator[ServerSentEvent]:
    """Streams the answer as server-sent events: delta events, then exactly one done or error event.
    Provider failures arrive as that error event, inside a 200."""
    state.started_at = perf_counter()
    first_delta_at: float | None = None
    include_dev_metrics = get_settings().include_dev_metrics
    # Native JSON types only: FastAPI serializes each event after the yield, outside this try.
    try:
        async with aclosing(use_case.stream(prepared, state.slot)) as events:
            async for event in events:
                if isinstance(event, ChatDeltaEvent):
                    if first_delta_at is None:
                        first_delta_at = perf_counter()
                    state.deltas += 1
                    yield ServerSentEvent(event="delta", data={"text": event.text})
                    continue
                elapsed_ms = _ms_since(state.started_at)
                first_delta_ms = _ms_between(state.started_at, first_delta_at)
                if isinstance(event, ChatDoneEvent):
                    dev_metrics = (
                        {"elapsed_ms": elapsed_ms, "first_delta_ms": first_delta_ms} if include_dev_metrics else None
                    )
                    final = ServerSentEvent(event="done", data=_done_data(event, dev_metrics))
                else:
                    final = ServerSentEvent(event="error", data=_error_data(event))
                _log_final(prepared, event, state.deltas, elapsed_ms, first_delta_ms)
                state.ended = True
                yield final
                # The use case ends with its final event; leaving here makes a second one impossible.
                return
        # Nor can the answer end without one.
        raise RuntimeError("The chat answer ended without its final event.")
    # Exception, not BaseException: cancellation (CancelledError, GeneratorExit) must reach FastAPI.
    except Exception as exc:
        logger.error("The chat stream failed unexpectedly: %s", type(exc).__name__)
        if not state.ended:
            state.ended = True
            error = ChatErrorEvent(code="internal", retryable=False, usage=None, chat_version=use_case.version)
            yield ServerSentEvent(event="error", data=_error_data(error))


def _done_data(event: ChatDoneEvent, dev_metrics: dict[str, float | None] | None) -> dict[str, Any]:
    return {
        "citations": [{"n": citation.n, "id": citation.id} for citation in event.citations],
        "coverage": event.coverage,
        "citations_dropped": event.citations_dropped,
        "finish_reason": event.finish_reason,
        "usage": _usage_data(event.usage),
        "chat_version": event.chat_version,
        "dev_metrics": dev_metrics,  # DEV-ONLY
    }


def _error_data(event: ChatErrorEvent) -> dict[str, Any]:
    return {
        "code": event.code,
        "retryable": event.retryable,
        "usage": _usage_data(event.usage),
        "chat_version": event.chat_version,
    }


def _usage_data(usage: ChatUsage | None) -> dict[str, int] | None:
    if usage is None:
        return None
    return {
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cached_tokens": usage.cached_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "llm_calls": usage.llm_calls,
    }


def _ms_since(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 1)


def _ms_between(started_at: float, at: float | None) -> float | None:
    return None if at is None else round((at - started_at) * 1000, 1)


def _log_final(
    prepared: PreparedChat,
    event: ChatDoneEvent | ChatErrorEvent,
    deltas: int,
    elapsed_ms: float,
    first_delta_ms: float | None,
) -> None:
    """Counts, kinds, tokens and times only: the conversation never reaches the log."""
    prompt = prepared.prompt
    kinds = ", ".join(f"{kind} {count}" for kind, count in Counter(p.kind for p in prompt.passages).items())
    request = (
        f"{len(prompt.passages)} passages ({kinds or 'none'}), question of {len(prompt.question)} characters, "
        f"{len(prompt.history)} history messages"
    )
    usage = event.usage
    tokens = (
        "usage not reported"
        if usage is None
        else (
            f"tokens {usage.prompt_tokens} in ({usage.cached_tokens} cached), "
            f"{usage.completion_tokens} out ({usage.reasoning_tokens} reasoning), {usage.llm_calls} calls"
        )
    )
    times = f"first delta {first_delta_ms} ms, total {elapsed_ms} ms"
    if isinstance(event, ChatDoneEvent):
        logger.info(
            "Chat answer done: %s; coverage %s, finish %s, %d citations, %d dropped, %d deltas; %s; %s; %s.",
            request,
            event.coverage,
            event.finish_reason,
            len(event.citations),
            event.citations_dropped,
            deltas,
            tokens,
            times,
            event.chat_version,
        )
    else:
        logger.warning(
            "Chat answer failed: %s; code %s, retryable %s, %d deltas; %s; %s; %s.",
            request,
            event.code,
            event.retryable,
            deltas,
            tokens,
            times,
            event.chat_version,
        )
