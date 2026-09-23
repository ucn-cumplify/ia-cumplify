class DomainError(Exception):
    """Base error for domain and application rules."""


class ClassificationError(DomainError):
    """Classification could not be produced for an article."""


class LegalBodyNotFoundError(DomainError):
    """No legal body exists for the given identifier."""
