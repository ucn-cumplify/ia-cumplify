from collections.abc import Sequence

from openai import (
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    UnprocessableEntityError,
)

from ia_cumplify.domain.embedding import TextEmbeddings
from ia_cumplify.domain.exceptions import EmbeddingError, EmbeddingInputError


class OpenAITextEmbedderAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 180,
        max_retries: int = 2,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    def embed(self, texts: Sequence[str], model: str, dimensions: int) -> TextEmbeddings:
        try:
            response = self._client.embeddings.create(
                model=model,
                input=list(texts),
                dimensions=dimensions,
            )
        except (BadRequestError, NotFoundError, UnprocessableEntityError) as exc:
            # Unknown model (404), unsupported dimensions or a text over the token limit (400):
            # the same request will fail again, so it is reported as invalid input, not retried.
            raise EmbeddingInputError(f"OpenAI rejected the embedding request: {exc}") from exc
        except OpenAIError as exc:
            raise EmbeddingError(f"OpenAI error while creating embeddings: {exc}") from exc

        # Order by index instead of trusting the response order: vectors must line up with texts.
        items = sorted(response.data, key=lambda item: item.index)
        vectors = tuple(tuple(item.embedding) for item in items)

        return TextEmbeddings(
            model=response.model,
            dimensions=len(vectors[0]) if vectors else dimensions,
            vectors=vectors,
            total_tokens=response.usage.total_tokens,
        )
