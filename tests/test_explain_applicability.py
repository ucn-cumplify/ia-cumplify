from dataclasses import dataclass, field

import pytest

from ia_cumplify.adapters.outbound.openai.applicability_reasoner import map_parsed_reasons
from ia_cumplify.adapters.outbound.openai.llm_applicability_schema import (
    LlmApplicabilityReasons,
    LlmArticleReason,
)
from ia_cumplify.application.use_cases.explain_applicability import ExplainApplicabilityUseCase
from ia_cumplify.domain.applicability import (
    MAX_REASON_CHARS,
    ApplicabilityArticleContext,
    ApplicabilityArticleInput,
    ApplicabilityReasonerOutput,
    ArticleApplicabilityReason,
)
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import LegalBodyNotFoundError
from ia_cumplify.domain.legal_body import LegalBody

BODY_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
A1 = "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c"
A2 = "9c3d2b51-2e4f-5a6b-0d7c-8e9f0a1b2c3d"
A3 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def legal_body() -> LegalBody:
    return LegalBody(id=BODY_ID, title="Ley 16744", summary="Resumen de la norma.", type="Ley")


def article(article_id: str, *, number: str = "Artículo 1", text: str = "Texto del artículo.", order: int = 1) -> Article:
    return Article(
        id=article_id,
        legal_body_id=BODY_ID,
        number=number,
        section="",
        text=text,
        order=order,
    )


def item(article_id: str, values: tuple[str, ...] = ("Minería",), actions: tuple[str, ...] = ()) -> ApplicabilityArticleInput:
    return ApplicabilityArticleInput(article_id=article_id, matched_values=values, actions=actions)


@dataclass
class FakeRepository:
    body: LegalBody | None
    articles: list[Article]

    def get_by_id(self, legal_body_id: str) -> LegalBody | None:
        if self.body is None or self.body.id != legal_body_id:
            return None
        return self.body

    def list_articles(self, legal_body_id: str) -> list[Article]:
        return [row for row in self.articles if row.legal_body_id == legal_body_id]


@dataclass
class FakeReasoner:
    output: ApplicabilityReasonerOutput = field(
        default_factory=lambda: ApplicabilityReasonerOutput(reasons=(), usage=TokenUsage(llm_calls=1))
    )
    version: str = "applicability-v1@test"
    calls: list[tuple[str, list[str]]] = field(default_factory=list)

    def explain(self, body: LegalBody, articles: list[ApplicabilityArticleContext]) -> ApplicabilityReasonerOutput:
        self.calls.append((body.id, [entry.article.id for entry in articles]))
        return self.output


def run(
    requested: list[ApplicabilityArticleInput],
    *,
    repo: FakeRepository | None = None,
    reasoner: FakeReasoner | None = None,
):
    repository = repo or FakeRepository(legal_body(), [article(A1), article(A2, number="Artículo 2", order=2)])
    adapter = reasoner or FakeReasoner()
    return ExplainApplicabilityUseCase(repository, adapter).execute(BODY_ID, requested), adapter


def test_missing_legal_body_is_not_found() -> None:
    use_case = ExplainApplicabilityUseCase(FakeRepository(None, []), FakeReasoner())
    with pytest.raises(LegalBodyNotFoundError):
        use_case.execute(BODY_ID, [item(A1)])


def test_only_requested_articles_reach_the_reasoner() -> None:
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(ArticleApplicabilityReason(A1, "Motivo del artículo 1."),),
            usage=TokenUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14, llm_calls=1),
        )
    )
    result, adapter = run(
        [item(A1)],
        repo=FakeRepository(legal_body(), [article(A1), article(A2, order=2), article(A3, order=3)]),
        reasoner=reasoner,
    )
    assert adapter.calls == [(BODY_ID, [A1])]
    assert [entry.article_id for entry in result.reasons] == [A1]
    assert result.reason_version == "applicability-v1@test"
    assert result.usage.llm_calls == 1


def test_unknown_article_is_omitted_and_does_not_abort() -> None:
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(ArticleApplicabilityReason(A1, "Motivo."),),
            usage=TokenUsage(llm_calls=1),
        )
    )
    result, adapter = run([item(A1), item(A3)], reasoner=reasoner)
    assert adapter.calls == [(BODY_ID, [A1])]
    assert [entry.article_id for entry in result.reasons] == [A1]


def test_no_found_article_skips_the_model() -> None:
    result, adapter = run([item(A3)])
    assert adapter.calls == []
    assert result.reasons == ()
    assert result.usage == TokenUsage()
    assert result.reason_version == "applicability-v1@test"


def test_empty_too_long_and_foreign_reasons_are_dropped() -> None:
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(
                ArticleApplicabilityReason(A1, "   "),
                ArticleApplicabilityReason(A2, "x" * (MAX_REASON_CHARS + 1)),
                ArticleApplicabilityReason(A3, "De un artículo que no se pidió."),
                ArticleApplicabilityReason(A1, "Motivo usable."),
            ),
            usage=TokenUsage(llm_calls=1),
        )
    )
    result, _ = run([item(A1), item(A2)], reasoner=reasoner)
    assert [entry.reason for entry in result.reasons] == ["Motivo usable."]


def test_duplicate_reasons_keep_the_first() -> None:
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(
                ArticleApplicabilityReason(A1, "Primero."),
                ArticleApplicabilityReason(A1, "Segundo."),
            ),
            usage=TokenUsage(llm_calls=1),
        )
    )
    result, _ = run([item(A1)], reasoner=reasoner)
    assert [entry.reason for entry in result.reasons] == ["Primero."]


def test_map_parsed_reasons_drops_extra_empty_and_duplicates() -> None:
    parsed = LlmApplicabilityReasons(
        reasons=[
            LlmArticleReason(article_id=A1, reason="  Uno.  "),
            LlmArticleReason(article_id=A1, reason="Dos."),
            LlmArticleReason(article_id=A3, reason="Ajeno."),
            LlmArticleReason(article_id=A2, reason="   "),
        ]
    )
    mapped = map_parsed_reasons(parsed, {A1, A2}, MAX_REASON_CHARS)
    assert mapped == (ArticleApplicabilityReason(A1, "Uno."),)
