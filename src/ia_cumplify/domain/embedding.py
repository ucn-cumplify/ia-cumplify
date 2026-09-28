from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextEmbeddings:
    """Vectors for a batch of texts, in the same order as the input texts."""

    model: str
    dimensions: int
    vectors: tuple[tuple[float, ...], ...]
    total_tokens: int
