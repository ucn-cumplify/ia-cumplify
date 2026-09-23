from ia_cumplify.application.ports.article_classifier import ArticleClassifierPort
from ia_cumplify.application.ports.legal_body_repository import LegalBodyRepositoryPort
from ia_cumplify.domain.classification import ClassifiedArticle, ClassifiedLegalBody
from ia_cumplify.domain.exceptions import LegalBodyNotFoundError


class ClassifyLegalBodyUseCase:
    def __init__(
        self,
        repository: LegalBodyRepositoryPort,
        classifier: ArticleClassifierPort,
    ) -> None:
        self._repository = repository
        self._classifier = classifier

    def execute(self, legal_body_id: str) -> ClassifiedLegalBody:
        legal_body = self._repository.get_by_id(legal_body_id)
        if legal_body is None:
            raise LegalBodyNotFoundError(legal_body_id)

        articles = self._repository.list_articles(legal_body.id)
        classified = tuple(
            ClassifiedArticle(
                article_id=article.id,
                number=article.number,
                classification=self._classifier.classify(legal_body, article, articles),
            )
            for article in articles
            if article.should_classify()
        )
        return ClassifiedLegalBody(legal_body=legal_body, articles=classified)
