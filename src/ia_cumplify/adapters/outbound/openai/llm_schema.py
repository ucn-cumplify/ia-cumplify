from pydantic import BaseModel, Field


class LlmArticleClassification(BaseModel):
    """Six regulatory dimensions for one article."""

    # The descriptions travel in the JSON schema, next to each field the model fills.
    scope: list[str] = Field(
        ...,
        min_length=1,
        description="Regulatory field, at most 4 words per item (e.g. Medio Ambiente, Laboral).",
    )
    productive_sector: list[str] = Field(
        ...,
        min_length=1,
        description="Industry or line of business, at most 3 words per item (e.g. Minería, Energía).",
    )
    territorial_coverage: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Places only: Nacional, or the official name with its level "
            "(Región de ..., Provincia de ..., Comuna de ...)."
        ),
    )
    activity_action: list[str] = Field(
        ...,
        min_length=1,
        description="One short phrase per activity, at most 12 words, starting with a noun.",
    )
    facility_installation_equipment: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Physical facilities or equipment, at most 4 words per item. Infer typical installations "
            "implied by the activity and legal body even if they are not named in the article text."
        ),
    )
    others: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Metrics, thresholds, sizes, building types, and other applicability conditions stated "
            "in the article that do not belong in the other dimensions. Do not infer. "
            "Use No especificado if there is nothing extra."
        ),
    )


class LlmClassifiedArticle(BaseModel):
    article_id: str
    classification: LlmArticleClassification


class LlmLegalBodyClassification(BaseModel):
    results: list[LlmClassifiedArticle]
