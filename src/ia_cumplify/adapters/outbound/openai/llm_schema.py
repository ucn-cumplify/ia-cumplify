from pydantic import BaseModel, Field


class LlmArticleClassification(BaseModel):
    """Five regulatory dimensions for one article."""

    scope: list[str] = Field(..., min_length=1)
    productive_sector: list[str] = Field(..., min_length=1)
    territorial_coverage: list[str] = Field(..., min_length=1)
    activity_action: list[str] = Field(..., min_length=1)
    facility_installation_equipment: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Physical facilities or equipment. Infer typical installations implied by "
            "the activity and legal body even if they are not named in the article text."
        ),
    )


class LlmClassifiedArticle(BaseModel):
    article_id: str
    classification: LlmArticleClassification


class LlmLegalBodyClassification(BaseModel):
    results: list[LlmClassifiedArticle]
