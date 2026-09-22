from uuid import UUID

from pydantic import BaseModel, Field

from ia_cumplify.domain.classification import (
    ArticleClassification,
    ClassifiedArticle,
    ClassifiedLegalBody,
)


class ClassifyLegalBodyRequest(BaseModel):
    legal_body_id: UUID = Field(..., description="UUID of the legal_bodies row")


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


class ClassifyLegalBodyResponse(BaseModel):
    legal_body_id: str
    title: str
    results: list[ClassifyArticleResponse]

    @classmethod
    def from_domain(cls, result: ClassifiedLegalBody) -> "ClassifyLegalBodyResponse":
        return cls(
            legal_body_id=result.legal_body.id,
            title=result.legal_body.title,
            results=[ClassifyArticleResponse.from_domain(item) for item in result.articles],
        )
