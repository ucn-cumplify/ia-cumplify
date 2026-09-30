from dataclasses import dataclass

from ia_cumplify.domain.legal_body import LegalBody


@dataclass(frozen=True, slots=True)
class ArticleClassification:
    scope: tuple[str, ...]
    productive_sector: tuple[str, ...]
    territorial_coverage: tuple[str, ...]
    activity_action: tuple[str, ...]
    facility_installation_equipment: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateLabels:
    """Labels already used for other legal bodies, for the three low-cardinality dimensions.

    The model reuses one when it fits, so the same concept gets the same label across legal
    bodies. The backend chooses them from its taxonomy; empty means classify without them.
    """

    scope: tuple[str, ...] = ()
    productive_sector: tuple[str, ...] = ()
    territorial_coverage: tuple[str, ...] = ()

    def by_dimension(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        return (
            ("scope", self.scope),
            ("productive_sector", self.productive_sector),
            ("territorial_coverage", self.territorial_coverage),
        )

    def is_empty(self) -> bool:
        return not (self.scope or self.productive_sector or self.territorial_coverage)


@dataclass(frozen=True, slots=True)
class ClassifiedArticle:
    article_id: str
    number: str
    classification: ArticleClassification


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Tokens billed by the model, summed over every call made for one legal body."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            llm_calls=self.llm_calls + other.llm_calls,
        )


@dataclass(frozen=True, slots=True)
class ClassifierOutput:
    articles: tuple[ClassifiedArticle, ...]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ClassifiedLegalBody:
    legal_body: LegalBody
    articles: tuple[ClassifiedArticle, ...]
    # "<prompt version>@<model>". The backend stores it with each response to find legal bodies
    # classified with an older prompt or model.
    classifier_version: str
    usage: TokenUsage
