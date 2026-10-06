"""Test doubles and helpers for the chat core: a responder that plays a script of provider events."""

import asyncio
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass, field

from ia_cumplify.adapters.inbound.http.routers.chat import chat_limits
from ia_cumplify.application.ports.chat_responder import ProviderStreamSlot
from ia_cumplify.application.use_cases.answer_chat import AnswerChatUseCase, ChatLimits
from ia_cumplify.config.settings import Settings
from ia_cumplify.domain.chat import (
    ChatDeltaEvent,
    ChatDoneEvent,
    ChatErrorEvent,
    ChatEvent,
    ChatMessage,
    ChatPassage,
    ChatPrompt,
    ChatRequest,
    ChatUsage,
    ProviderEvent,
    ProviderFinishEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
)

TEST_VERSION = "test-v1@test-model"
USAGE = ChatUsage(
    prompt_tokens=1200, completion_tokens=300, total_tokens=1500, cached_tokens=640, reasoning_tokens=96, llm_calls=1
)


class ScriptedStream:
    """Plays the script; an exception in it is raised at that point, as a failing provider would."""

    def __init__(self, script: Sequence[ProviderEvent | BaseException], *, close_error: Exception | None = None):
        self._script = tuple(script)
        self._close_error = close_error
        self.closed = False
        self.close_calls = 0
        self.delivered = 0

    def __aiter__(self) -> AsyncIterator[ProviderEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[ProviderEvent]:
        for item in self._script:
            if isinstance(item, BaseException):
                raise item
            self.delivered += 1
            yield item

    async def aclose(self) -> None:
        self.closed = True
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


class ScriptedResponder:
    def __init__(
        self,
        script: Sequence[ProviderEvent | BaseException] = (),
        *,
        open_error: BaseException | None = None,
        close_error: Exception | None = None,
        version: str = TEST_VERSION,
    ) -> None:
        self._script = script
        self._open_error = open_error
        self._close_error = close_error
        self._version = version
        self.prompts: list[ChatPrompt] = []
        self.streams: list[ScriptedStream] = []

    @property
    def version(self) -> str:
        return self._version

    async def open(self, prompt: ChatPrompt) -> ScriptedStream:
        self.prompts.append(prompt)
        if self._open_error is not None:
            raise self._open_error
        stream = ScriptedStream(self._script, close_error=self._close_error)
        self.streams.append(stream)
        return stream


def make_passages(count: int) -> tuple[ChatPassage, ...]:
    return tuple(
        ChatPassage(id=f"id-{i}", kind="article", reference=f"Ley 16.744, art. {i}", text=f"Texto del pasaje {i}.")
        for i in range(1, count + 1)
    )


def completed(*texts: str, finish: str = "stop", usage: ChatUsage | None = USAGE) -> list[ProviderEvent]:
    """A provider answer that ends normally: its texts, finish_reason and, unless None, the usage chunk."""
    events: list[ProviderEvent] = [ProviderTextEvent(text) for text in texts]
    events.append(ProviderFinishEvent(finish))
    if usage is not None:
        events.append(ProviderUsageEvent(usage))
    return events


def chunked(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


@dataclass
class ChatRun:
    events: list[ChatEvent]
    responder: ScriptedResponder
    slot: ProviderStreamSlot
    deltas: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.deltas = [event.text for event in self.events if isinstance(event, ChatDeltaEvent)]

    @property
    def text(self) -> str:
        return "".join(self.deltas)

    @property
    def final(self) -> ChatEvent:
        return self.events[-1]

    @property
    def done(self) -> ChatDoneEvent:
        assert isinstance(self.final, ChatDoneEvent), self.final
        return self.final

    @property
    def error(self) -> ChatErrorEvent:
        assert isinstance(self.final, ChatErrorEvent), self.final
        return self.final


# The default CHAT_* limits, from their only source. model_construct reads neither .env nor the shell,
# and this runs at import time, before the autouse fixture clears the environment.
LIMITS = chat_limits(Settings.model_construct())


def run_chat(
    script: Sequence[ProviderEvent | BaseException] = (),
    *,
    passages: Iterable[ChatPassage] | None = None,
    question: str = "¿Qué exige la norma?",
    history: Iterable[ChatMessage] = (),
    app_name: str | None = None,
    responder: ScriptedResponder | None = None,
    limits: ChatLimits = LIMITS,
) -> ChatRun:
    responder = responder if responder is not None else ScriptedResponder(script)
    use_case = AnswerChatUseCase(responder, limits)
    request = ChatRequest(
        question=question,
        history=tuple(history),
        passages=tuple(passages) if passages is not None else make_passages(3),
        app_name=app_name,
    )
    prepared = use_case.prepare(request)
    slot = ProviderStreamSlot()
    events = asyncio.run(collect(use_case.stream(prepared, slot)))
    run = ChatRun(events=events, responder=responder, slot=slot)
    assert_stream_contract(run)
    return run


async def collect(events: AsyncIterator[ChatEvent]) -> list[ChatEvent]:
    return [event async for event in events]


_MARKER = re.compile(r"\[(\d+)\]")
# What the filter treats as a bracketed number.
_ANY_NUMBER = re.compile(r"\[\s*\d+\s*\]")


def assert_stream_contract(run: ChatRun) -> None:
    """The rules every answer keeps: deltas, then exactly one final event, and coherent citations."""
    *body, final = run.events
    assert all(isinstance(event, ChatDeltaEvent) and event.text for event in body), run.events
    assert isinstance(final, ChatDoneEvent | ChatErrorEvent), final
    # A bracketed number never comes split between two deltas, and every one is a marker written "[n]".
    assert sum(len(_ANY_NUMBER.findall(delta)) for delta in run.deltas) == len(_ANY_NUMBER.findall(run.text))
    assert all(re.fullmatch(r"\[[0-9]+\]", number) for number in _ANY_NUMBER.findall(run.text)), run.text
    # No lone surrogate reaches the client.
    assert not re.search("[\ud800-\udfff]", run.text)
    if isinstance(final, ChatDoneEvent):
        assert body, "done always comes after at least one delta"
        numbers = [citation.n for citation in final.citations]
        assert numbers == list(range(1, len(numbers) + 1))
        ids = [citation.id for citation in final.citations]
        assert len(ids) == len(set(ids))
        assert {int(n) for n in _MARKER.findall(run.text)} == set(numbers)
        # Numbered by first appearance: 1, 2, 3...
        assert list(dict.fromkeys(int(n) for n in _MARKER.findall(run.text))) == numbers
