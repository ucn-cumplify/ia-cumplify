from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload  # DEV-ONLY
from ia_cumplify.adapters.inbound.http.schemas.classification import CandidateLabel, TokenUsageResponse
from ia_cumplify.domain.applicability import (
    MAX_ACTIONS,
    MAX_ARTICLES,
    MAX_MATCHED_VALUES,
    MAX_REASON_CHARS,
    ApplicabilityArticleInput,
    ApplicabilityReasons,
)


def _unique(labels: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(labels))


class ApplicabilityReasonArticleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: UUID
    matched_values: list[CandidateLabel] = Field(..., min_length=1, max_length=MAX_MATCHED_VALUES)
    actions: list[CandidateLabel] = Field(default_factory=list, max_length=MAX_ACTIONS)

    def to_domain(self) -> ApplicabilityArticleInput:
        return ApplicabilityArticleInput(
            article_id=str(self.article_id),
            matched_values=_unique(self.matched_values),
            actions=_unique(self.actions),
        )


class ExplainApplicabilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_requirement_id: UUID = Field(
        ...,
        description=(
            "UUID of the Legal Requirements app the alert belongs to. Accepted so the backend body "
            "validates; this service does not read that table and does not send the id to the model."
        ),
    )
    legal_body_id: UUID = Field(..., description="UUID of the legal_bodies row")
    articles: list[ApplicabilityReasonArticleRequest] = Field(..., min_length=1, max_length=MAX_ARTICLES)

    @model_validator(mode="after")
    def article_ids_are_unique(self) -> "ExplainApplicabilityRequest":
        ids = [article.article_id for article in self.articles]
        if len(ids) != len(set(ids)):
            raise ValueError("articles must not repeat article_id.")
        return self


class ArticleApplicabilityReasonResponse(BaseModel):
    article_id: str
    reason: str = Field(..., max_length=MAX_REASON_CHARS)


class ExplainApplicabilityResponse(BaseModel):
    reasons: list[ArticleApplicabilityReasonResponse]
    reason_version: str = Field(..., description='Prompt version and model, as "<prompt>@<model>"')
    usage: TokenUsageResponse = Field(
        ..., description="Tokens of the model call, sent even without dev metrics"
    )
    dev_metrics: DevMetricsPayload | None = None  # DEV-ONLY

    @classmethod
    def from_domain(cls, result: ApplicabilityReasons) -> "ExplainApplicabilityResponse":
        return cls(
            reasons=[
                ArticleApplicabilityReasonResponse(article_id=item.article_id, reason=item.reason)
                for item in result.reasons
            ],
            reason_version=result.reason_version,
            usage=TokenUsageResponse.from_domain(result.usage),
        )
