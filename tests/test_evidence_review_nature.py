"""C9, decision 7: nature comes from the article, and an informative or referral article is not evaluable."""

from datetime import date

import pytest

from ia_cumplify.adapters.outbound.openai.evidence_review_prompts import EVIDENCE_REVIEW_SYSTEM_PROMPT
from ia_cumplify.application.use_cases.review_evidence import ReviewEvidenceUseCase
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import (
    EvidenceReviewRequest,
    ReviewArticle,
    ReviewCitation,
    ReviewJudgment,
    ReviewLegalBody,
    ReviewRequirement,
)


def test_the_prompt_decides_nature_with_the_c9_rules():
    text = EVIDENCE_REVIEW_SYSTEM_PROMPT.casefold()
    assert "only from the article text" in text
    assert "only obliges the state is informative" in text
    assert "adds an obligation inside another law" in text
    assert "even when the added text itself imposes an obligation" in text
    assert "has nothing to verify" in text
    assert "not_evaluable" in text


def test_a_file_that_proves_a_breach_does_not_accredit_the_requirement():
    text = EVIDENCE_REVIEW_SYSTEM_PROMPT.casefold()
    assert "accredited: the files show that the company meets it." in text
    assert "partial: the files show that the company meets only part of it." in text
    assert "not_accredited: the files do not show that the company meets it." in text
    assert "not_evaluable: the files that were read have no text from which to judge it." in text
    assert "proves the company fails the exigencia does not accredit it" in text
    assert "even when it talks about the same subject" in text
    assert "talking about the subject is not enough" in text


@pytest.mark.parametrize("nature", ["informative", "referral"])
def test_an_article_with_nothing_to_verify_drops_requirements_and_is_not_evaluable(nature):
    judgment = ReviewJudgment(
        nature=nature,
        requirements=(
            ReviewRequirement(
                text="Constituir el comité.",
                status="accredited",
                citations=(
                    ReviewCitation(file_id="file-1", locator="page", reference="1", quote="Comité constituido"),
                ),
                missing="",
            ),
        ),
        verdict="accredited",
        summary="El artículo no exige nada a la empresa.",
        usage=TokenUsage(prompt_tokens=4, completion_tokens=2, total_tokens=6, llm_calls=1),
        model="gpt-5.6-luna",
        review_version="evidence-review-v1@gpt-5.6-luna",
    )

    result = ReviewEvidenceUseCase(_Reviewer(judgment)).execute(_request())

    assert result.nature == nature
    assert result.requirements == ()
    assert result.verdict == "not_evaluable"
    assert result.summary == "El artículo no exige nada a la empresa."
    assert result.usage.prompt_tokens == 4


def test_an_article_with_explicit_obligations_keeps_what_the_model_returned():
    judgment = ReviewJudgment(
        nature="explicit_obligations",
        requirements=(
            ReviewRequirement(text="Constituir el comité.", status="partial", citations=(), missing="Falta el acta."),
        ),
        verdict="partial",
        summary="Falta el acta.",
        usage=TokenUsage(),
        model="gpt-5.6-luna",
        review_version="evidence-review-v1@gpt-5.6-luna",
    )

    result = ReviewEvidenceUseCase(_Reviewer(judgment)).execute(_request())

    assert result.nature == "explicit_obligations"
    assert result.verdict == "partial"
    assert result.requirements[0].text == "Constituir el comité."


def _request() -> EvidenceReviewRequest:
    return EvidenceReviewRequest(
        reference_date=date(2026, 10, 14),
        article=ReviewArticle(number="74", text="Agrégase el siguiente inciso a la ley N° 20.780."),
        legal_body=ReviewLegalBody(title="Ley 21.806", type="Ley", number="21806"),
        obligation=None,
        files=(),
    )


class _Reviewer:
    def __init__(self, judgment: ReviewJudgment) -> None:
        self.judgment = judgment

    @property
    def version(self) -> str:
        return self.judgment.review_version

    def review(self, subject) -> ReviewJudgment:
        return self.judgment
