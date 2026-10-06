from collections.abc import AsyncIterator

from ia_cumplify.domain.chat import (
    ChatPrompt,
    ChatUsage,
    ProviderEvent,
    ProviderFinishEvent,
    ProviderTextEvent,
    ProviderUsageEvent,
)

# Lets the backend tell these answers from real ones, for example in the 4.8 log.
FAKE_CHAT_VERSION = "fake-v1@fake"

# The key is split between fragments on purpose: the answer goes through the same citation filter.
_FRAGMENTS = (
    "Respuesta de prueba: el respondedor falso no llamó al modelo. ",
    "Se apoya en el primer pasaje recibido [P",
    "1].",
)


class FakeChatResponder:
    """CHT-014: answers with a fixed text that cites the first passage, without calling OpenAI.

    It lets the backend integrate the chat without spending tokens (CHAT_FAKE_RESPONDER) and gives the
    tests the stream contract. Its usage is zero, with no model call.
    """

    @property
    def version(self) -> str:
        return FAKE_CHAT_VERSION

    async def open(self, prompt: ChatPrompt) -> "FakeChatStream":
        return FakeChatStream()


class FakeChatStream:
    def __init__(self) -> None:
        self.closed = False

    def __aiter__(self) -> AsyncIterator[ProviderEvent]:
        return self._events()

    async def aclose(self) -> None:
        self.closed = True

    async def _events(self) -> AsyncIterator[ProviderEvent]:
        for fragment in _FRAGMENTS:
            if self.closed:
                return
            yield ProviderTextEvent(fragment)
        yield ProviderFinishEvent("stop")
        yield ProviderUsageEvent(ChatUsage())
