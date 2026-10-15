from collections.abc import Sequence

from ia_cumplify.application.ports.applicability_reasoner import ApplicabilityReasonerPort
from ia_cumplify.application.ports.legal_body_repository import LegalBodyRepositoryPort
from ia_cumplify.domain.applicability import (
    ApplicabilityArticleContext,
    ApplicabilityArticleInput,
    ApplicabilityReasons,
    ArticleAnalysis,
    ArticleApplicabilityReason,
    MAX_REASON_CHARS,
    close_analysis,
)
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import LegalBodyNotFoundError


class ExplainApplicabilityUseCase:
    """Loads the legal body and only the requested articles, then asks what each one asks of the
    company and, unless the caller keeps its own text, one reason for the match.

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
                write_reason=item.write_reason,
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
        with_reason = {item.article.id for item in found if item.write_reason}
        seen: set[str] = set()
        reasons: list[ArticleApplicabilityReason] = []
        for item in output.reasons:
            reason = _usable(item, with_reason, self._max_reason_chars)
            if reason is None or reason.article_id in seen:
                continue
            seen.add(reason.article_id)
            reasons.append(reason)
        return ApplicabilityReasons(
            legal_body=legal_body,
            reasons=tuple(reasons),
            reason_version=self._reasoner.version,
            usage=output.usage,
            analyses=_usable_analyses(output.analyses, {item.article.id for item in found}),
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


def _usable_analyses(analyses: Sequence[ArticleAnalysis], allowed: set[str]) -> tuple[ArticleAnalysis, ...]:
    """The first usable analysis per requested article, closed again with the C9 rules whatever the port."""
    seen: set[str] = set()
    kept: list[ArticleAnalysis] = []
    for item in analyses:
        if item.article_id not in allowed or item.article_id in seen:
            continue
        analysis = close_analysis(item)
        if analysis is None:
            continue
        seen.add(analysis.article_id)
        kept.append(analysis)
    return tuple(kept)
