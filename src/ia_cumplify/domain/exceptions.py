class DomainError(Exception):
    """Base error for domain and application rules."""


class ClassificationError(DomainError):
    """Classification could not be produced for an article."""


class LegalBodyNotFoundError(DomainError):
    """No legal body exists for the given identifier."""


class EmbeddingError(DomainError):
    """The provider could not produce the embeddings (worth retrying)."""


class EmbeddingInputError(DomainError):
    """The provider rejected the input, e.g. a text over the model's token limit (retrying won't help)."""
