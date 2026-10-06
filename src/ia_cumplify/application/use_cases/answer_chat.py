import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from ia_cumplify.application.ports.chat_responder import ChatResponderPort, ProviderStreamSlot
from ia_cumplify.application.use_cases.chat_citations import CitationFilter, neutralize_markers
from ia_cumplify.domain.chat import (
    APP_NAME_MAX_CHARS,
    CHAT_ROLES,
    FINISH_REASONS,
    PASSAGE_ID_MAX_CHARS,
    PASSAGE_KINDS,
    PASSAGE_REFERENCE_MAX_CHARS,
    ChatCitation,
    ChatDeltaEvent,
    ChatDoneEvent,
    ChatErrorCode,
    ChatErrorEvent,
    ChatEvent,
    ChatMessage,
    ChatPrompt,
    ChatRequest,
    ChatUsage,
    Coverage,
    FinishReason,
    PromptPassage,
    ProviderEvent,
    ProviderFinishEvent,
    ProviderRefusalEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
    is_retryable,
    normalize_finish_reason,
    replace_lone_surrogates,
)
from ia_cumplify.domain.exceptions import ChatProviderError

logger = logging.getLogger(__name__)

# Fixed texts of the contract. Changing one bumps CHAT_PROMPT_VERSION, and the backend mirrors the
# no_passages one when it answers without calling this service.
NO_PASSAGES_TEXT = (
    "No encontré información para responder esta pregunta en la normativa ni en los datos de la empresa."
)
REFUSED_TEXT = "No puedo responder esta pregunta."
NOT_COVERED_TEXT = "Las fuentes disponibles no alcanzan para responder esta pregunta."

# Unicode White_Space, what .NET string.Trim() and Pydantic's strip_whitespace remove. str.strip()
# also removes U+001C to U+001F, and the backend could not mirror the lengths.
_WHITE_SPACE = (
    "\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ChatLimits:
    """The configurable CHAT_* limits. The backend mirrors them: a request over one gets 422. Their
    defaults live only in Settings."""

    question_max_chars: int
    history_max_messages: int
    history_max_chars: int
    max_passages: int
    passage_max_chars: int
    passages_max_total_chars: int


@dataclass(frozen=True, slots=True)
class PreparedChat:
    """A validated request: the prompt for the model and the passage ids its citations point to."""

    prompt: ChatPrompt
    # Index i holds the id of key P(i+1).
    passage_ids: tuple[str, ...]


class AnswerChatUseCase:
    """Answers a chat question using only the passages of the request. It reads no database and keeps
    no state between requests.

    The conversation is company data: errors and logs carry lengths, counts and positions, never its
    content, the provider's messages or the model's refusal.
    """

    def __init__(self, responder: ChatResponderPort, limits: ChatLimits) -> None:
        self._responder = responder
        self._limits = limits

    @property
    def version(self) -> str:
        return self._responder.version

    def prepare(self, request: ChatRequest) -> PreparedChat:
        """Validates the whole request before the stream opens. Raises ValueError (a 422).

        Lengths are measured after trimming and before removing images, as the backend measures them.
        """
        limits = self._limits
        question = _trim(request.question)
        if not question:
            raise ValueError("The question is empty.")
        if len(question) > limits.question_max_chars:
            raise ValueError(
                f"The question has {len(question)} characters; the limit is {limits.question_max_chars}."
            )

        history: list[ChatMessage] = []
        for index, message in enumerate(request.history):
            if message.role not in CHAT_ROLES:
                raise ValueError(f"history[{index}].role must be user or assistant.")
            content = _trim(message.content)
            if content:  # an empty message is dropped before counting
                history.append(ChatMessage(role=message.role, content=content))
        if len(history) > limits.history_max_messages:
            raise ValueError(
                f"The history has {len(history)} messages; the limit is {limits.history_max_messages}."
            )
        history_chars = sum(len(message.content) for message in history)
        if history_chars > limits.history_max_chars:
            raise ValueError(
                f"The history has {history_chars} characters; the limit is {limits.history_max_chars}."
            )

        if len(request.passages) > limits.max_passages:
            raise ValueError(
                f"The request has {len(request.passages)} passages; the limit is {limits.max_passages}."
            )
        seen_ids: set[str] = set()
        passages: list[PromptPassage] = []
        total_chars = 0
        for index, passage in enumerate(request.passages, start=1):
            field = f"passages[{index - 1}]"
            # The id is not trimmed: it goes back unchanged in the citations.
            if not 1 <= len(passage.id) <= PASSAGE_ID_MAX_CHARS:
                raise ValueError(
                    f"{field}.id has {len(passage.id)} characters; "
                    f"it must have between 1 and {PASSAGE_ID_MAX_CHARS}."
                )
            if not _is_single_line(passage.id):
                raise ValueError(f"{field}.id must be a single line.")
            if passage.id in seen_ids:
                raise ValueError("Passage ids must be unique.")
            seen_ids.add(passage.id)
            if passage.kind not in PASSAGE_KINDS:
                raise ValueError(f"{field}.kind is not one of the accepted kinds.")
            reference = _trim(passage.reference)
            if not 1 <= len(reference) <= PASSAGE_REFERENCE_MAX_CHARS:
                raise ValueError(
                    f"{field}.reference has {len(reference)} characters; "
                    f"it must have between 1 and {PASSAGE_REFERENCE_MAX_CHARS}."
                )
            if not _is_single_line(reference):
                raise ValueError(f"{field}.reference must be a single line.")
            text = _trim(passage.text)
            if not text:
                raise ValueError(f"{field}.text is empty.")
            if len(text) > limits.passage_max_chars:
                raise ValueError(
                    f"{field}.text has {len(text)} characters; the limit is {limits.passage_max_chars}."
                )
            total_chars += len(text)
            passages.append(
                PromptPassage(
                    key=f"P{index}",
                    kind=passage.kind,
                    reference=_for_model(reference),
                    text=_for_model(text),
                )
            )
        if total_chars > limits.passages_max_total_chars:
            raise ValueError(
                f"The passages have {total_chars} characters in total; "
                f"the limit is {limits.passages_max_total_chars}."
            )

        app_name = None
        if request.app_name is not None:
            app_name = _trim(request.app_name)
            if not 1 <= len(app_name) <= APP_NAME_MAX_CHARS:
                raise ValueError(
                    f"context.app_name has {len(app_name)} characters; "
                    f"it must have between 1 and {APP_NAME_MAX_CHARS}."
                )
            if not _is_single_line(app_name):
                raise ValueError("context.app_name must be a single line.")

        prompt_history = []
        for message in history:
            content = _for_model(message.content, assistant=message.role == "assistant")
            # A message the neutralization leaves empty (an earlier answer made only of markers, or a
            # message made only of the not_covered mark) is not sent.
            if content:
                prompt_history.append(ChatMessage(role=message.role, content=content))
        prompt = ChatPrompt(
            question=_for_model(question),
            history=tuple(prompt_history),
            passages=tuple(passages),
            # A name made only of the not_covered mark is left out, not sent as an empty "App:" line.
            app_name=(_for_model(app_name) or None) if app_name is not None else None,
        )
        return PreparedChat(prompt=prompt, passage_ids=tuple(passage.id for passage in request.passages))

    async def stream(self, prepared: PreparedChat, slot: ProviderStreamSlot) -> AsyncIterator[ChatEvent]:
        """Deltas, then exactly one done or error event.

        The provider stream goes into slot as soon as it opens, so the request scope can close it if the
        client disconnects. Any failure becomes the error event; cancellation propagates untouched.
        """
        version = self._responder.version
        if not prepared.passage_ids:
            # Nothing to answer from: a fixed refusal, without spending tokens.
            yield ChatDeltaEvent(NO_PASSAGES_TEXT)
            yield ChatDoneEvent(
                citations=(),
                coverage="no_passages",
                citations_dropped=0,
                finish_reason="stop",
                usage=ChatUsage(),
                chat_version=version,
            )
            return

        answer = _Answer(prepared.passage_ids, version)
        closing: list[ChatEvent]
        try:
            try:
                try:
                    provider_stream = await self._responder.open(prepared.prompt)
                    slot.hold(provider_stream)
                    async for event in provider_stream:
                        text = answer.on_event(event)
                        if text:
                            yield ChatDeltaEvent(text)
                except ChatProviderError as exc:
                    closing = answer.on_failure(exc)
                else:
                    closing = answer.on_end()
            except Exception as exc:
                # The message of an unexpected exception could carry content: only its type is logged.
                logger.error("Chat answer failed unexpectedly: %s", type(exc).__name__)
                closing = [answer.error("internal")]
            for event in closing:
                yield event
        finally:
            try:
                await slot.aclose()
            except Exception as exc:
                logger.warning("Closing the chat provider stream failed: %s", type(exc).__name__)


class _Answer:
    """One answer in progress: its citation filter, the refusal and what the provider reported."""

    def __init__(self, passage_ids: tuple[str, ...], version: str) -> None:
        self._ids = passage_ids
        self._version = version
        self._filter = CitationFilter(len(passage_ids))
        self._refused = False
        self._finish: FinishReason | None = None
        self._usage: ChatUsage | None = None

    def on_event(self, event: ProviderEvent) -> str:
        """The text to emit now, often empty."""
        if isinstance(event, ProviderTextEvent):
            if self._refused:
                return ""
            return self._filter.feed(replace_lone_surrogates(event.text))
        if isinstance(event, ProviderRefusalEvent):
            if self._refused:
                return ""
            # From here on nothing the model writes is emitted, but the stream is read to the end
            # to report finish_reason and usage.
            self._refused = True
            self._filter.discard()
            return f"\n\n{REFUSED_TEXT}" if self._filter.visible else REFUSED_TEXT
        if isinstance(event, ProviderFinishEvent):
            if self._finish is None:
                if event.reason not in FINISH_REASONS:
                    logger.warning("Unexpected finish_reason from the chat provider, reported as stop.")
                self._finish = normalize_finish_reason(event.reason)
            return ""
        if isinstance(event, ProviderUsageEvent):
            self._usage = event.usage
            return ""
        raise TypeError(f"Unknown provider event: {type(event).__name__}")

    def on_end(self) -> list[ChatEvent]:
        if self._finish is None:
            # The provider closed the stream without finish_reason: the answer was cut halfway.
            return [self.error("provider_error")]
        return self._close()

    def on_failure(self, exc: ChatProviderError) -> list[ChatEvent]:
        if self._finish is None:
            return [self.error(exc.code)]
        # The model had already finished; the failure only lost the usage chunk.
        return self._close()

    def error(self, code: ChatErrorCode) -> ChatErrorEvent:
        return ChatErrorEvent(
            code=code, retryable=is_retryable(code), usage=self._usage, chat_version=self._version
        )

    def _close(self) -> list[ChatEvent]:
        events: list[ChatEvent] = []
        coverage: Coverage
        if self._refused:
            coverage = "refused"
        else:
            tail = self._filter.finish()
            if tail:
                events.append(ChatDeltaEvent(tail))
            if self._filter.not_covered:
                coverage = "not_covered"
                if not self._filter.visible:
                    events.append(ChatDeltaEvent(NOT_COVERED_TEXT))
            elif not self._filter.visible:
                # No text, only removed markers, or a stream cut inside the not_covered mark.
                return [self.error("empty_output")]
            else:
                coverage = "answered" if self._filter.cited_keys else "uncited"
        citations = tuple(
            ChatCitation(n=n, id=self._ids[key - 1])
            for n, key in enumerate(self._filter.cited_keys, start=1)
        )
        events.append(
            ChatDoneEvent(
                citations=citations,
                coverage=coverage,
                citations_dropped=self._filter.dropped,
                finish_reason=self._finish or "stop",
                usage=self._usage,
                chat_version=self._version,
            )
        )
        return events


def _trim(text: str) -> str:
    return text.strip(_WHITE_SPACE)


def _is_single_line(text: str) -> bool:
    # No line break of str.splitlines anywhere, the last character included.
    return text.splitlines() == [text]


def _for_model(text: str, *, assistant: bool = False) -> str:
    """Input text as the model gets it. Lone surrogates become U+FFFD, which keeps every length."""
    return _trim(neutralize_markers(replace_lone_surrogates(text), assistant=assistant))
