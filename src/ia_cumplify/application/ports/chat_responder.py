from collections.abc import AsyncIterator
from typing import Protocol

from ia_cumplify.domain.chat import ChatPrompt, ProviderEvent


class ChatProviderStream(Protocol):
    """An answer the provider is producing. Iterating it yields its events; closing it ends the call.

    Iteration raises ChatProviderError when the connection fails, times out or the provider sends an
    error inside the stream. A stream that ends without a finish event was cut halfway.
    """

    def __aiter__(self) -> AsyncIterator[ProviderEvent]: ...

    async def aclose(self) -> None:
        """Closes the provider call. Safe to call more than once."""
        ...


class ChatResponderPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model that produce the answer, as "<prompt>@<model>"."""
        ...

    async def open(self, prompt: ChatPrompt) -> ChatProviderStream:
        """Starts the model call. Raises ChatProviderError when the provider rejects or cannot take it."""
        ...


class ProviderStreamSlot:
    """The open provider stream: the use case holds it as soon as it opens, and whoever owns the request
    closes it, also when the answer is cancelled (CHT-011). aclose is idempotent."""

    __slots__ = ("_stream",)

    def __init__(self) -> None:
        self._stream: ChatProviderStream | None = None

    def hold(self, stream: ChatProviderStream) -> None:
        self._stream = stream

    async def aclose(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            await stream.aclose()
