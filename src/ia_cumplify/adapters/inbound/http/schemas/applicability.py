from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload  # DEV-ONLY
from ia_cumplify.adapters.inbound.http.schemas.classification import CandidateLabel, TokenUsageResponse
from ia_cumplify.domain.applicability import (
    MAX_ACTIONS,
    MAX_ARTICLES,
    MAX_MATCHED_VALUES,
    MAX_OBLIGATION_EVIDENCE_CHARS,
    MAX_OBLIGATION_SUMMARY_CHARS,
    MAX_OBLIGATION_TITLE_CHARS,
    MAX_OBLIGATIONS,
    MAX_PROCEDURE_FOR_CHARS,
    MAX_REASON_CHARS,
    MAX_REFERRED_LEGAL_BODY_CHARS,
    ApplicabilityArticleInput,
    ApplicabilityNature,
    ApplicabilityReasons,
    ArticleAnalysis,
)


def _unique(labels: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(labels))


class ApplicabilityReasonArticleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    article_id: UUID
    # At least one value when the article gets a reason (see the validator). An analysis-only request may
    # send none: 80 of the 111 suggestions of the AI alerts in the local database kept no matched values.
    matched_values: list[CandidateLabel] = Field(default_factory=list, max_length=MAX_MATCHED_VALUES)
    actions: list[CandidateLabel] = Field(default_factory=list, max_length=MAX_ACTIONS)
    write_reason: bool = Field(
        default=True,
        description=(
            "False when the caller keeps its own text for this article and only wants the analysis: "
            "the article gets no entry in reasons, and matched_values may be empty."
        ),
    )

    @model_validator(mode="after")
    def a_reason_needs_matched_values(self) -> "ApplicabilityReasonArticleRequest":
        if self.write_reason and not self.matched_values:
            raise ValueError("matched_values needs at least one value when write_reason is true.")
        return self

    def to_domain(self) -> ApplicabilityArticleInput:
        return ApplicabilityArticleInput(
            article_id=str(self.article_id),
            matched_values=_unique(self.matched_values),
            actions=_unique(self.actions),
            write_reason=self.write_reason,
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


class ArticleObligationResponse(BaseModel):
    title: str = Field(..., max_length=MAX_OBLIGATION_TITLE_CHARS)
    summary: str = Field(..., max_length=MAX_OBLIGATION_SUMMARY_CHARS)
    evidence: str = Field(
        ..., max_length=MAX_OBLIGATION_EVIDENCE_CHARS, description="The respaldo that accredits the obligation"
    )


class ArticleAnalysisResponse(BaseModel):
    article_id: str
    nature: ApplicabilityNature = Field(..., description="explicit_obligations, informative, referral or procedural")
    obligations: list[ArticleObligationResponse] = Field(
        ...,
        max_length=MAX_OBLIGATIONS,
        description="At least one for explicit_obligations, each with its respaldo; empty for the other natures",
    )
    referred_legal_body: str | None = Field(
        ...,
        max_length=MAX_REFERRED_LEGAL_BODY_CHARS,
        description="For a referral, the law it amends, as the article names it; otherwise null",
    )
    procedure_for: str | None = Field(
        ...,
        max_length=MAX_PROCEDURE_FOR_CHARS,
        description=(
            "For a procedural article, the authorization it serves, as the article or the legal body names it; "
            "otherwise null"
        ),
    )

    @classmethod
    def from_domain(cls, analysis: ArticleAnalysis) -> "ArticleAnalysisResponse":
        return cls(
            article_id=analysis.article_id,
            nature=analysis.nature,
            obligations=[
                ArticleObligationResponse(title=item.title, summary=item.summary, evidence=item.evidence)
                for item in analysis.obligations
            ],
            referred_legal_body=analysis.referred_legal_body,
            procedure_for=analysis.procedure_for,
        )


class ExplainApplicabilityResponse(BaseModel):
    reasons: list[ArticleApplicabilityReasonResponse]
    analyses: list[ArticleAnalysisResponse] = Field(
        ...,
        description="What each requested article asks of the company. An article missing here was not evaluated",
    )
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
            analyses=[ArticleAnalysisResponse.from_domain(item) for item in result.analyses],
            reason_version=result.reason_version,
            usage=TokenUsageResponse.from_domain(result.usage),
        )
