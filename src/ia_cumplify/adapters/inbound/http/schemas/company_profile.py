from pydantic import BaseModel, Field

from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload  # DEV-ONLY

from ia_cumplify.adapters.inbound.http.schemas.classification import (
    ArticleClassificationParameters,
    CandidateValues,
    TokenUsageResponse,
)
from ia_cumplify.domain.company_profile import ClassifiedCompanyProfile


class ClassifyCompanyProfileRequest(BaseModel):
    text: str = Field(
        ...,
        description=(
            "What the company writes about itself. Trimmed, it must have between 1 and "
            "PROFILE_TEXT_MAX_CHARS characters."
        ),
    )
    candidate_values: CandidateValues | None = Field(
        default=None,
        description=(
            "Labels already used for the articles of legal bodies, chosen by the backend. The model "
            "reuses them when they fit, so the profile speaks the same vocabulary as the articles."
        ),
    )


class ClassifyCompanyProfileResponse(BaseModel):
    classification: ArticleClassificationParameters = Field(
        ..., description="The six dimensions of the company, the same ones an article gets"
    )
    classifier_version: str = Field(..., description='Prompt version and model, as "<prompt>@<model>"')
    usage: TokenUsageResponse = Field(..., description="Tokens of the model call, sent even without dev metrics")
    dev_metrics: DevMetricsPayload | None = None  # DEV-ONLY

    @classmethod
    def from_domain(cls, result: ClassifiedCompanyProfile) -> "ClassifyCompanyProfileResponse":
        return cls(
            classification=ArticleClassificationParameters.from_domain(result.classification),
            classifier_version=result.classifier_version,
            usage=TokenUsageResponse.from_domain(result.usage),
        )
