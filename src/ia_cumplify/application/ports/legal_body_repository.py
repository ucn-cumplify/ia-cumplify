from typing import Protocol

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.legal_body import LegalBody


class LegalBodyRepositoryPort(Protocol):
    def get_by_id(self, legal_body_id: str) -> LegalBody | None: ...

    def list_articles(self, legal_body_id: str) -> list[Article]: ...
