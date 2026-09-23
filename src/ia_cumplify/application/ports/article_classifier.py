from collections.abc import Sequence
from typing import Protocol

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import ArticleClassification
from ia_cumplify.domain.legal_body import LegalBody


class ArticleClassifierPort(Protocol):
    def classify(
        self,
        legal_body: LegalBody,
        article: Article,
        all_articles: Sequence[Article],
    ) -> ArticleClassification: ...
