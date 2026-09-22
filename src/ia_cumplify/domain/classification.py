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
class ClassifiedArticle:
    article_id: str
    number: str
    classification: ArticleClassification


@dataclass(frozen=True, slots=True)
class ClassifiedLegalBody:
    legal_body: LegalBody
    articles: tuple[ClassifiedArticle, ...]
