from collections.abc import Sequence

from ia_cumplify.application.ports.applicability_reasoner import ApplicabilityReasonerPort
from ia_cumplify.application.ports.legal_body_repository import LegalBodyRepositoryPort
from ia_cumplify.domain.applicability import (
    ApplicabilityArticleContext,
    ApplicabilityArticleInput,
    ApplicabilityReasons,
    ArticleApplicabilityReason,
    MAX_REASON_CHARS,
)
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import LegalBodyNotFoundError


class ExplainApplicabilityUseCase:
    """Loads the legal body and only the requested articles, then asks for one reason each.

    Articles that are not on the legal body are omitted: the backend keeps the template for those.
    The company profile text is not an input and is never sent to the model.
    """

    def __init__(
        self,
        repository: LegalBodyRepositoryPort,
        reasoner: ApplicabilityReasonerPort,
        max_reason_chars: int = MAX_REASON_CHARS,
    ) -> None:
        self._repository = repository
        self._reasoner = reasoner
        self._max_reason_chars = max_reason_chars

    def execute(
        self,
        legal_body_id: str,
        articles: Sequence[ApplicabilityArticleInput],
    ) -> ApplicabilityReasons:
        legal_body = self._repository.get_by_id(legal_body_id)
        if legal_body is None:
            raise LegalBodyNotFoundError(legal_body_id)

        by_id = {article.id: article for article in self._repository.list_articles(legal_body.id)}
        found = [
            ApplicabilityArticleContext(
                article=by_id[item.article_id],
                matched_values=item.matched_values,
                actions=item.actions,
            )
            for item in articles
            if item.article_id in by_id
        ]
        if not found:
            return ApplicabilityReasons(
                legal_body=legal_body,
                reasons=(),
                reason_version=self._reasoner.version,
                usage=TokenUsage(),
            )

        output = self._reasoner.explain(legal_body, found)
        allowed = {item.article.id for item in found}
        seen: set[str] = set()
        reasons: list[ArticleApplicabilityReason] = []
        for item in output.reasons:
            reason = _usable(item, allowed, self._max_reason_chars)
            if reason is None or reason.article_id in seen:
                continue
            seen.add(reason.article_id)
            reasons.append(reason)
        return ApplicabilityReasons(
            legal_body=legal_body,
            reasons=tuple(reasons),
            reason_version=self._reasoner.version,
            usage=output.usage,
        )


def _usable(
    item: ArticleApplicabilityReason,
    allowed: set[str],
    max_chars: int,
) -> ArticleApplicabilityReason | None:
    if item.article_id not in allowed:
        return None
    reason = item.reason.strip()
    if not reason or len(reason) > max_chars:
        return None
    return ArticleApplicabilityReason(article_id=item.article_id, reason=reason)
