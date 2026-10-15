from dataclasses import dataclass, field

import pytest

from ia_cumplify.adapters.outbound.openai.applicability_reasoner import map_parsed_analyses, map_parsed_reasons
from ia_cumplify.adapters.outbound.openai.llm_applicability_schema import (
    LlmApplicabilityReasons,
    LlmArticleReason,
    LlmObligation,
)
from ia_cumplify.application.use_cases.explain_applicability import ExplainApplicabilityUseCase
from ia_cumplify.domain.applicability import (
    MAX_OBLIGATION_EVIDENCE_CHARS,
    MAX_OBLIGATION_SUMMARY_CHARS,
    MAX_OBLIGATION_TITLE_CHARS,
    MAX_OBLIGATIONS,
    MAX_PROCEDURE_FOR_CHARS,
    MAX_REASON_CHARS,
    MAX_REFERRED_LEGAL_BODY_CHARS,
    ApplicabilityArticleContext,
    ApplicabilityArticleInput,
    ApplicabilityReasonerOutput,
    ArticleAnalysis,
    ArticleApplicabilityReason,
    ArticleObligation,
    close_analysis,
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


def item(
    article_id: str,
    values: tuple[str, ...] = ("Minería",),
    actions: tuple[str, ...] = (),
    *,
    write_reason: bool = True,
) -> ApplicabilityArticleInput:
    return ApplicabilityArticleInput(
        article_id=article_id, matched_values=values, actions=actions, write_reason=write_reason
    )


OBLIGATION = ArticleObligation(
    "Constituir el comité",
    "El empleador constituye un Comité Paritario en la faena.",
    "Acta de constitución del Comité Paritario",
)


def explicit(article_id: str, *obligations: ArticleObligation) -> ArticleAnalysis:
    return ArticleAnalysis(article_id, "explicit_obligations", obligations or (OBLIGATION,), None)


def llm_item(
    article_id: str,
    reason: str = "",
    *,
    nature: str = "informative",
    obligations: tuple[tuple[str, str, str], ...] = (),
    referred: str = "",
    procedure: str = "",
) -> LlmArticleReason:
    return LlmArticleReason(
        article_id=article_id,
        nature=nature,
        referred_legal_body=referred,
        procedure_for=procedure,
        obligations=[
            LlmObligation(title=title, summary=summary, evidence=evidence) for title, summary, evidence in obligations
        ],
        reason=reason,
    )


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
            llm_item(A1, "  Uno.  "),
            llm_item(A1, "Dos."),
            llm_item(A3, "Ajeno."),
            llm_item(A2, "   "),
        ]
    )
    mapped = map_parsed_reasons(parsed, {A1, A2}, MAX_REASON_CHARS)
    assert mapped == (ArticleApplicabilityReason(A1, "Uno."),)


def test_an_analysis_survives_a_dropped_reason() -> None:
    # The two lists are independent: a reason past the cap does not take the article's analysis with it.
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(ArticleApplicabilityReason(A1, "x" * (MAX_REASON_CHARS + 1)),),
            usage=TokenUsage(llm_calls=1),
            analyses=(explicit(A1),),
        )
    )
    result, _ = run([item(A1)], reasoner=reasoner)
    assert result.reasons == ()
    assert result.analyses == (explicit(A1),)


def test_an_article_without_write_reason_gets_only_its_analysis() -> None:
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(
                ArticleApplicabilityReason(A1, "Motivo pedido."),
                ArticleApplicabilityReason(A2, "Motivo que nadie pidió."),
            ),
            usage=TokenUsage(llm_calls=1),
            analyses=(explicit(A1), ArticleAnalysis(A2, "informative", (), None)),
        )
    )
    result, adapter = run([item(A1), item(A2, write_reason=False)], reasoner=reasoner)
    assert adapter.calls == [(BODY_ID, [A1, A2])]
    assert [entry.article_id for entry in result.reasons] == [A1]
    assert [entry.article_id for entry in result.analyses] == [A1, A2]


def test_write_reason_reaches_the_reasoner() -> None:
    seen: list[bool] = []

    class RecordingReasoner(FakeReasoner):
        def explain(self, body, articles):
            seen.extend(entry.write_reason for entry in articles)
            return super().explain(body, articles)

    run([item(A1), item(A2, write_reason=False)], reasoner=RecordingReasoner())
    assert seen == [True, False]


def test_foreign_and_duplicate_analyses_keep_the_first_usable() -> None:
    second = ArticleObligation(
        "Sesionar cada mes", "El comité sesiona al menos una vez al mes.", "Actas de las sesiones"
    )
    reasoner = FakeReasoner(
        output=ApplicabilityReasonerOutput(
            reasons=(),
            usage=TokenUsage(llm_calls=1),
            analyses=(
                ArticleAnalysis(A1, "explicit_obligations", (), None),
                explicit(A1, second),
                explicit(A1),
                explicit(A3),
            ),
        )
    )
    result, _ = run([item(A1), item(A2)], reasoner=reasoner)
    assert result.analyses == (explicit(A1, second),)


def test_informative_and_referral_carry_no_obligations() -> None:
    informative = close_analysis(ArticleAnalysis(A1, "informative", (OBLIGATION,), "Ley N° 20.780"))
    referral = close_analysis(ArticleAnalysis(A1, "referral", (OBLIGATION,), "  Ley N°\n20.780 "))
    assert informative == ArticleAnalysis(A1, "informative", (), None)
    assert referral == ArticleAnalysis(A1, "referral", (), "Ley N° 20.780")


def test_a_referral_without_a_usable_law_keeps_its_nature() -> None:
    assert close_analysis(ArticleAnalysis(A1, "referral", (), "   ")) == ArticleAnalysis(A1, "referral", (), None)
    too_long = "L" * (MAX_REFERRED_LEGAL_BODY_CHARS + 1)
    assert close_analysis(ArticleAnalysis(A1, "referral", (), too_long)) == ArticleAnalysis(A1, "referral", (), None)


def test_explicit_obligations_without_a_usable_obligation_is_not_evaluated() -> None:
    unusable = (
        ArticleObligation("   ", "Resumen.", "Respaldo."),
        ArticleObligation("Título", " ", "Respaldo."),
        ArticleObligation("T" * (MAX_OBLIGATION_TITLE_CHARS + 1), "Resumen.", "Respaldo."),
        ArticleObligation("Título", "S" * (MAX_OBLIGATION_SUMMARY_CHARS + 1), "Respaldo."),
    )
    assert close_analysis(ArticleAnalysis(A1, "explicit_obligations", unusable, None)) is None
    assert close_analysis(ArticleAnalysis(A1, "explicit_obligations", (), None)) is None


def test_obligations_are_trimmed_deduplicated_and_capped() -> None:
    noisy = ArticleObligation(
        "  Constituir\nel comité ",
        " El empleador constituye  un Comité Paritario en la faena. ",
        " Acta de constitución del\nComité Paritario ",
    )
    many = tuple(
        ArticleObligation(f"Obligación {n}", f"Resumen {n}.", f"Respaldo {n}.") for n in range(MAX_OBLIGATIONS + 3)
    )
    closed = close_analysis(ArticleAnalysis(A1, "explicit_obligations", (noisy, OBLIGATION, *many), "Ley 1"))
    assert closed is not None
    assert closed.referred_legal_body is None
    assert closed.obligations[0] == OBLIGATION
    assert len(closed.obligations) == MAX_OBLIGATIONS
    assert closed.obligations[1] == many[0]


def test_an_unknown_nature_is_dropped() -> None:
    assert close_analysis(ArticleAnalysis(A1, "obligatorio", (OBLIGATION,), None)) is None  # type: ignore[arg-type]


def test_map_parsed_analyses_closes_each_article_with_the_c9_rules() -> None:
    parsed = LlmApplicabilityReasons(
        reasons=[
            llm_item(
                A1,
                "Motivo.",
                nature="explicit_obligations",
                obligations=(
                    (
                        "Constituir el comité",
                        "El empleador constituye un Comité Paritario en la faena.",
                        "Acta de constitución del Comité Paritario",
                    ),
                ),
                referred="Ley N° 16.744",
                procedure="Permiso de edificación",
            ),
            llm_item(
                A2,
                nature="referral",
                obligations=(("Pagar", "Paga el impuesto.", "Comprobante de pago"),),
                referred="Ley N° 20.780",
            ),
            llm_item(A3, nature="informative"),
        ]
    )
    assert map_parsed_analyses(parsed, {A1, A2}) == (
        explicit(A1),
        ArticleAnalysis(A2, "referral", (), "Ley N° 20.780"),
    )


def test_an_obligation_without_its_respaldo_is_not_an_obligation() -> None:
    # Decision 21, the client's criterion: an obligation corresponds to a respaldo at all times.
    without = ArticleObligation("No retener el bono", "Las instituciones no retienen el bono.", "  ")
    too_long = ArticleObligation(
        "Informar", "El titular informa a la autoridad.", "R" * (MAX_OBLIGATION_EVIDENCE_CHARS + 1)
    )
    assert close_analysis(ArticleAnalysis(A1, "explicit_obligations", (without, too_long), None)) is None
    closed = close_analysis(ArticleAnalysis(A1, "explicit_obligations", (without, OBLIGATION), None))
    assert closed == explicit(A1)


def test_a_procedural_article_keeps_only_the_authorization_it_serves() -> None:
    closed = close_analysis(
        ArticleAnalysis(A1, "procedural", (OBLIGATION,), "Ley N° 20.780", "  Permiso de\nedificación ")
    )
    assert closed == ArticleAnalysis(A1, "procedural", (), None, "Permiso de edificación")
    too_long = "P" * (MAX_PROCEDURE_FOR_CHARS + 1)
    assert close_analysis(ArticleAnalysis(A1, "procedural", (), None, too_long)) == ArticleAnalysis(
        A1, "procedural", (), None, None
    )
    assert close_analysis(ArticleAnalysis(A1, "procedural", (), None, "   ")) == ArticleAnalysis(
        A1, "procedural", (), None, None
    )


def test_only_a_procedural_article_keeps_procedure_for() -> None:
    for nature in ("informative", "referral"):
        analysis = ArticleAnalysis(A1, nature, (), None, "Permiso de edificación")  # type: ignore[arg-type]
        closed = close_analysis(analysis)
        assert closed == ArticleAnalysis(A1, nature, (), None, None)  # type: ignore[arg-type]
    closed = close_analysis(ArticleAnalysis(A1, "explicit_obligations", (OBLIGATION,), None, "Permiso de edificación"))
    assert closed == explicit(A1)


def test_map_parsed_analyses_reads_the_respaldo_and_the_procedure() -> None:
    parsed = LlmApplicabilityReasons(
        reasons=[
            llm_item(
                A1,
                nature="explicit_obligations",
                obligations=(
                    ("Sin respaldo", "El titular cumple algo que ningún documento acredita.", ""),
                    (
                        "Constituir el comité",
                        "El empleador constituye un Comité Paritario en la faena.",
                        "Acta de constitución del Comité Paritario",
                    ),
                ),
            ),
            llm_item(A2, nature="procedural", procedure="Permiso de edificación"),
        ]
    )
    assert map_parsed_analyses(parsed, {A1, A2}) == (
        explicit(A1),
        ArticleAnalysis(A2, "procedural", (), None, "Permiso de edificación"),
    )
