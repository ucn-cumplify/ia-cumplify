from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ia_cumplify.domain.classification_catalogs import (
    UNSPECIFIED,
    ProductiveSector,
    Scope,
    TerritorialCoverage,
)


class LlmArticleClassification(BaseModel):
    """Six regulatory dimensions for one article."""

    # The descriptions travel in the JSON schema, next to each field the model fills.
    scope: list[Scope | Literal["No especificado"]] = Field(
        ...,
        min_length=1,
        description="Exact official regulatory-field label, or No especificado alone.",
    )
    productive_sector: list[ProductiveSector | Literal["No especificado"]] = Field(
        ...,
        min_length=1,
        description="Exact official productive-sector label, or No especificado alone.",
    )
    territorial_coverage: list[TerritorialCoverage | Literal["No especificado"]] = Field(
        ...,
        min_length=1,
        description=(
            "Exact official territorial-coverage label, or No especificado alone. "
            "Do not add territories outside the catalog."
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

    @field_validator(
        "scope", "productive_sector", "territorial_coverage",
        "activity_action", "facility_installation_equipment", "others",
    )
    @classmethod
    def sentinel_is_exclusive(cls, values: list[str]) -> list[str]:
        if UNSPECIFIED in values and len(values) != 1:
            raise ValueError("No especificado must be the only item in its dimension")
        return values


class LlmClassifiedArticle(BaseModel):
    article_id: str
    classification: LlmArticleClassification


class LlmLegalBodyClassification(BaseModel):
    results: list[LlmClassifiedArticle]
