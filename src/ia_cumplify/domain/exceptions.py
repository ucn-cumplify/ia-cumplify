from ia_cumplify.domain.chat import PROVIDER_ERROR_CODES, ProviderErrorCode


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


class ChatProviderError(DomainError):
    """The chat provider failed. It carries only the kind of failure, never the provider's message,
    which could repeat the conversation."""

    def __init__(self, code: ProviderErrorCode) -> None:
        if code not in PROVIDER_ERROR_CODES:
            raise ValueError(f"Unknown chat provider error code: {code}.")
        super().__init__(f"Chat provider failure: {code}.")
        self.code: ProviderErrorCode = code
