from collections.abc import Sequence
from typing import Protocol

from ia_cumplify.domain.embedding import TextEmbeddings


class TextEmbedderPort(Protocol):
    def embed(self, texts: Sequence[str], model: str, dimensions: int) -> TextEmbeddings: ...
