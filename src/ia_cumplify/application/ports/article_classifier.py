from collections.abc import Sequence
from typing import Protocol

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import CandidateLabels, ClassifierOutput
from ia_cumplify.domain.legal_body import LegalBody


class ArticleClassifierPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model that produce the labels, as "<prompt>@<model>"."""
        ...

    def classify_many(
        self,
        legal_body: LegalBody,
        all_articles: Sequence[Article],
        targets: Sequence[Article],
        candidates: CandidateLabels | None = None,
    ) -> ClassifierOutput: ...
