from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload  # DEV-ONLY

from ia_cumplify.domain.classification import (
    ArticleClassification,
    CandidateLabels,
    ClassifiedArticle,
    ClassifiedLegalBody,
    TokenUsage,
)

# Keeps the EXISTING LABELS block of the prompt short: one line per dimension.
MAX_CANDIDATES_PER_DIMENSION = 60

# A line break or "|" would break the one-line-per-dimension layout of the prompt block.
CandidateLabel = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100, pattern=r"^[^|\r\n]+$"),
]


class CandidateValues(BaseModel):
    """Existing labels per dimension, chosen by the backend from its taxonomy."""

    model_config = ConfigDict(extra="forbid")

    scope: list[CandidateLabel] = Field(default_factory=list, max_length=MAX_CANDIDATES_PER_DIMENSION)
    productive_sector: list[CandidateLabel] = Field(
        default_factory=list, max_length=MAX_CANDIDATES_PER_DIMENSION
    )
    territorial_coverage: list[CandidateLabel] = Field(
        default_factory=list, max_length=MAX_CANDIDATES_PER_DIMENSION
    )

    def to_domain(self) -> CandidateLabels:
        return CandidateLabels(
            scope=_unique(self.scope),
            productive_sector=_unique(self.productive_sector),
            territorial_coverage=_unique(self.territorial_coverage),
        )


def _unique(labels: list[str]) -> tuple[str, ...]:
    # Keeps the order the backend chose (most used first).
    return tuple(dict.fromkeys(labels))


class ClassifyLegalBodyRequest(BaseModel):
    legal_body_id: UUID = Field(..., description="UUID of the legal_bodies row")
    candidate_values: CandidateValues | None = Field(
        default=None,
        description=(
            "Optional labels already used for other legal bodies. The model reuses them when "
            "they fit, so the same concept gets the same label."
        ),
    )


class ArticleClassificationParameters(BaseModel):
    scope: list[str]
    productive_sector: list[str]
    territorial_coverage: list[str]
    activity_action: list[str]
    facility_installation_equipment: list[str]

    @classmethod
    def from_domain(cls, classification: ArticleClassification) -> "ArticleClassificationParameters":
        return cls(
            scope=list(classification.scope),
            productive_sector=list(classification.productive_sector),
            territorial_coverage=list(classification.territorial_coverage),
            activity_action=list(classification.activity_action),
            facility_installation_equipment=list(classification.facility_installation_equipment),
        )


class ClassifyArticleResponse(BaseModel):
    article_id: str
    number: str
    classification: ArticleClassificationParameters

    @classmethod
    def from_domain(cls, result: ClassifiedArticle) -> "ClassifyArticleResponse":
        return cls(
            article_id=result.article_id,
            number=result.number,
            classification=ArticleClassificationParameters.from_domain(result.classification),
        )


class TokenUsageResponse(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int

    @classmethod
    def from_domain(cls, usage: TokenUsage) -> "TokenUsageResponse":
        return cls(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            llm_calls=usage.llm_calls,
        )


class ClassifyLegalBodyResponse(BaseModel):
    legal_body_id: str
    title: str
    results: list[ClassifyArticleResponse]
    classifier_version: str = Field(
        ..., description='Prompt version and model, as "<prompt>@<model>"'
    )
    usage: TokenUsageResponse = Field(
        ..., description="Tokens of every model call, sent even without dev metrics"
    )
    dev_metrics: DevMetricsPayload | None = None  # DEV-ONLY

    @classmethod
    def from_domain(cls, result: ClassifiedLegalBody) -> "ClassifyLegalBodyResponse":
        return cls(
            legal_body_id=result.legal_body.id,
            title=result.legal_body.title,
            results=[ClassifyArticleResponse.from_domain(item) for item in result.articles],
            classifier_version=result.classifier_version,
            usage=TokenUsageResponse.from_domain(result.usage),
        )
