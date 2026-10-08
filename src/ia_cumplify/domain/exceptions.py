from ia_cumplify.domain.chat import PROVIDER_ERROR_CODES, ProviderErrorCode
from ia_cumplify.domain.classification import TokenUsage


class DomainError(Exception):
    """Base error for domain and application rules."""


class ClassificationError(DomainError):
    """Classification could not be produced for an article.

    usage carries what the provider reported for the calls that failed, when it did: a refusal or an
    answer cut by length or by the content filter is billed all the same.
    """

    def __init__(self, message: str, *, usage: TokenUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


class LegalBodyNotFoundError(DomainError):
    """No legal body exists for the given identifier."""


class ApplicabilityError(DomainError):
    """Applicability reasons could not be produced."""


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
