from ia_cumplify.adapters.outbound.openai.applicability_prompts import (
    APPLICABILITY_PROMPT_VERSION,
    APPLICABILITY_SYSTEM_PROMPT,
    render_applicability_user_content,
)
from ia_cumplify.domain.applicability import ApplicabilityArticleContext
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.legal_body import LegalBody

BODY_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
A1 = "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c"
A2 = "9c3d2b51-2e4f-5a6b-0d7c-8e9f0a1b2c3d"
A3 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def context(article_id: str, number: str, text: str) -> ApplicabilityArticleContext:
    return ApplicabilityArticleContext(
        article=Article(
            id=article_id,
            legal_body_id=BODY_ID,
            number=number,
            section="Capítulo I",
            text=text,
            order=1,
        ),
        matched_values=("Minería", "Gestión de relaves"),
        actions=("Monitoreo de la estabilidad de los depósitos de relaves",),
    )


def test_prompt_version() -> None:
    assert APPLICABILITY_PROMPT_VERSION == "applicability-v1"


def test_system_prompt_forbids_quoting_the_profile_and_dictating() -> None:
    text = APPLICABILITY_SYSTEM_PROMPT.casefold()
    assert "company description" in text
    assert "do not dictate" in text
    assert "do not mention articles that are not in" in text.casefold() or "not in articles to explain" in text


def test_user_content_has_only_requested_articles_and_no_profile_block() -> None:
    body = LegalBody(id=BODY_ID, title="Ley 16744", summary="Resumen.", type="Ley")
    content = render_applicability_user_content(
        body,
        [
            context(A1, "Artículo 66", "Texto del artículo 66."),
            context(A2, "Artículo 67", "Texto del artículo 67."),
        ],
    )
    assert A1 in content and A2 in content
    assert A3 not in content
    assert "Texto del artículo 66." in content
    assert "«" not in content
    assert "Minería" in content
    assert "Monitoreo de la estabilidad de los depósitos de relaves" in content
    assert "COMPANY DESCRIPTION" not in content
    assert "company profile" not in content.casefold()
    assert "--- ARTICLES TO EXPLAIN ---" in content
    assert "--- LEGAL BODY (context) ---" in content
    assert "Ley 16744" in content
