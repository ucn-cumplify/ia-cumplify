from collections.abc import Sequence

from ia_cumplify.application.ports.text_embedder import TextEmbedderPort
from ia_cumplify.domain.embedding import TextEmbeddings
from ia_cumplify.domain.exceptions import EmbeddingError


class EmbedTextsUseCase:
    """Embeds a batch of texts. The caller builds the texts (for taxonomy values, the backend
    prefixes the dimension label), so this service stays generic: taxonomy values, profile values
    and, later, article chunks all go through the same endpoint."""

    def __init__(
        self,
        embedder: TextEmbedderPort,
        *,
        default_model: str,
        default_dimensions: int,
        max_texts: int,
    ) -> None:
        self._embedder = embedder
        self._default_model = default_model
        self._default_dimensions = default_dimensions
        self._max_texts = max_texts

    def execute(
        self,
        texts: Sequence[str],
        model: str | None = None,
        dimensions: int | None = None,
    ) -> TextEmbeddings:
        if not texts:
            raise ValueError("texts must contain at least one text.")
        if len(texts) > self._max_texts:
            raise ValueError(f"texts accepts at most {self._max_texts} texts per request; got {len(texts)}.")

        blank = [index for index, text in enumerate(texts) if not text.strip()]
        if blank:
            raise ValueError(f"texts must not be blank (indexes {blank}).")

        result = self._embedder.embed(
            texts,
            model or self._default_model,
            dimensions or self._default_dimensions,
        )

        # The contract promises one vector per text, in order: a mismatch would silently attach
        # vectors to the wrong values on the backend.
        if len(result.vectors) != len(texts):
            raise EmbeddingError(
                f"The provider returned {len(result.vectors)} vectors for {len(texts)} texts."
            )

        return result
