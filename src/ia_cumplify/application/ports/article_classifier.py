from collections.abc import Sequence
from typing import Protocol

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import ClassifiedArticle
from ia_cumplify.domain.legal_body import LegalBody


class ArticleClassifierPort(Protocol):
    def classify_many(
        self,
        legal_body: LegalBody,
        all_articles: Sequence[Article],
        targets: Sequence[Article],
    ) -> list[ClassifiedArticle]: ...
