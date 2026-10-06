import logging
from collections.abc import AsyncIterator

from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AsyncStream,
    Timeout,
)
from openai._httpx2 import request_exceptions, timeout_exceptions
from openai.types.chat import ChatCompletionChunk

from ia_cumplify.adapters.outbound.openai.chat_prompts import CHAT_PROMPT_VERSION, build_chat_messages
from ia_cumplify.domain.chat import (
    ChatPrompt,
    ChatUsage,
    ProviderErrorCode,
    ProviderEvent,
    ProviderFinishEvent,
    ProviderRefusalEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
)
from ia_cumplify.domain.exceptions import ChatProviderError

logger = logging.getLogger(__name__)

# Reads (the headers and each chunk) wait CHAT_TIMEOUT_SECONDS; connecting gets this shorter limit.
_CONNECT_TIMEOUT_SECONDS = 5.0


class OpenAIChatResponderAdapter:
    """Streams the answer of one chat request from OpenAI, with a single model call.

    The conversation is company data: a failure carries only its code, never the provider's message
    or body, which could quote the request. Neither does the log.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        max_completion_tokens: int,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_completion_tokens = max_completion_tokens
        client = AsyncOpenAI(
            api_key=api_key,
            timeout=Timeout(timeout_seconds, connect=_CONNECT_TIMEOUT_SECONDS),
            max_retries=max_retries,
        )
        # The first access imports the chat resources (about half a second). The dependency that
        # builds this adapter runs in the threadpool; inside the stream it would block the event loop.
        self._completions = client.chat.completions

    @property
    def version(self) -> str:
        return f"{CHAT_PROMPT_VERSION}@{self._model}"

    async def open(self, prompt: ChatPrompt) -> "OpenAIChatStream":
        try:
            stream = await self._completions.create(
                model=self._model,
                messages=build_chat_messages(prompt),  # type: ignore[arg-type]
                stream=True,
                stream_options={"include_usage": True},
                # No copy for OpenAI's distillation and evals. The prompt cache mode is still to be
                # agreed (api.md), so prompt_cache_options is not sent: the implicit mode rules.
                store=False,
                reasoning_effort=self._reasoning_effort,  # type: ignore[arg-type]
                max_completion_tokens=self._max_completion_tokens,
            )
        except APIError as exc:
            raise ChatProviderError(_opening_error_code(exc)) from None
        # The SDK reads the body of an error status outside the block that wraps transport failures, so
        # a body that stalls or is cut escapes as a raw httpx2 error. Same mapping as its own stream.
        except timeout_exceptions() as exc:
            logger.warning("Reading the chat provider's error answer failed: %s.", type(exc).__name__)
            raise ChatProviderError("timeout") from None
        except request_exceptions() as exc:
            logger.warning("Reading the chat provider's error answer failed: %s.", type(exc).__name__)
            raise ChatProviderError("provider_error") from None
        return OpenAIChatStream(stream)


class OpenAIChatStream:
    """The chunks of an answer OpenAI is streaming, read as provider events.

    It ends without error when the provider closes the stream, with or without finish_reason: the
    use case tells a finished answer from a cut one. Closing it closes the HTTP response.
    """

    def __init__(self, stream: AsyncStream[ChatCompletionChunk]) -> None:
        self._stream = stream

    def __aiter__(self) -> AsyncIterator[ProviderEvent]:
        return self._events()

    async def aclose(self) -> None:
        await self._stream.close()

    async def _events(self) -> AsyncIterator[ProviderEvent]:
        while True:
            try:
                chunk = await anext(self._stream)
            except StopAsyncIteration:
                return
            except APIError as exc:
                raise ChatProviderError(_streaming_error_code(exc)) from None
            for event in _chunk_events(chunk):
                yield event


def _opening_error_code(exc: APIError) -> ProviderErrorCode:
    """CHT-010, from the most specific exception to the most general one."""
    if isinstance(exc, APITimeoutError):  # a subclass of APIConnectionError
        return "timeout"
    if isinstance(exc, APIConnectionError):
        return "provider_error"
    if isinstance(exc, APIStatusError):
        # The status and the exception type only: the message and the body can quote the request.
        logger.warning("The chat provider answered HTTP %d (%s).", exc.status_code, type(exc).__name__)
        return _status_error_code(exc)
    return "provider_error"


def _status_error_code(exc: APIStatusError) -> ProviderErrorCode:
    status = exc.status_code
    if status == 429:
        # Retrying does not help an exhausted quota until someone fixes it.
        return "provider_rejected" if exc.code == "insufficient_quota" else "rate_limited"
    if status in (408, 409) or status >= 500:
        return "provider_error"
    if 400 <= status < 500:
        return "provider_rejected"
    # A status outside 4xx and 5xx has no row in the contract: an unexpected answer of the provider.
    return "provider_error"


def _streaming_error_code(exc: APIError) -> ProviderErrorCode:
    # Once the provider accepted the call, a failure is never rate_limited nor provider_rejected:
    # a cut connection, an error inside the stream or a status error are all provider_error.
    return "timeout" if isinstance(exc, APITimeoutError) else "provider_error"


def _chunk_events(chunk: ChatCompletionChunk) -> list[ProviderEvent]:
    """The SDK builds chunks without validating them, so every field is read defensively."""
    events: list[ProviderEvent] = []
    for choice in getattr(chunk, "choices", None) or ():
        delta = getattr(choice, "delta", None)
        # The refusal goes before the text of the same chunk, so that text is never emitted. An empty
        # refusal field is not a refusal.
        if getattr(delta, "refusal", None):
            events.append(ProviderRefusalEvent())
        content = getattr(delta, "content", None)
        if isinstance(content, str) and content:
            events.append(ProviderTextEvent(content))
        finish_reason = getattr(choice, "finish_reason", None)
        if isinstance(finish_reason, str) and finish_reason:
            events.append(ProviderFinishEvent(finish_reason))
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        events.append(ProviderUsageEvent(_usage(usage)))
    return events


def _usage(usage: object) -> ChatUsage:
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    return ChatUsage(
        prompt_tokens=_count(getattr(usage, "prompt_tokens", None)),
        completion_tokens=_count(getattr(usage, "completion_tokens", None)),
        total_tokens=_count(getattr(usage, "total_tokens", None)),
        cached_tokens=_count(getattr(prompt_details, "cached_tokens", None)),
        reasoning_tokens=_count(getattr(completion_details, "reasoning_tokens", None)),
        llm_calls=1,
    )


def _count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0
