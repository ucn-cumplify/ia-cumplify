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


def context(article_id: str, number: str, text: str, *, write_reason: bool = True) -> ApplicabilityArticleContext:
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
        write_reason=write_reason,
    )


def test_prompt_version() -> None:
    assert APPLICABILITY_PROMPT_VERSION == "applicability-v3"


def test_system_prompt_forbids_quoting_the_profile_and_dictating() -> None:
    text = APPLICABILITY_SYSTEM_PROMPT.casefold()
    assert "company description" in text
    assert "do not dictate" in text
    assert "do not mention articles that are not in" in text.casefold() or "not in articles to explain" in text


def test_system_prompt_decides_nature_with_the_c9_rules() -> None:
    # Decisions 7 and 21 of the sprint 3 plan. The evidence review still has only the first three values.
    text = APPLICABILITY_SYSTEM_PROMPT
    for nature in ("explicit_obligations", "informative", "referral", "procedural"):
        assert nature in text
    assert "Check the natures in this order and keep the first that fits" in text
    assert "only obliges or empowers the State" in text
    assert "It is a referral even when the added text imposes an obligation" in text
    assert "referred_legal_body" in text
    assert "Decide it only from the article text, never from the matched values or the actions" in text


def test_obligations_come_only_from_the_article_text() -> None:
    text = APPLICABILITY_SYSTEM_PROMPT
    assert "Take them only from the article text" in text
    assert "never turn an action into an obligation" in text
    assert "An obligation that falls only on the State is not an obligation of the company" in text
    assert "For informative, referral and procedural articles, obligations is empty" in text


def test_an_obligation_needs_the_respaldo_that_accredits_it() -> None:
    # Decision 21, the client's criterion: an obligation corresponds to a respaldo at all times.
    text = APPLICABILITY_SYSTEM_PROMPT
    assert "Before you list an obligation, name its respaldo" in text
    assert "If you cannot name one, it is not an obligation" in text
    assert "imposes the obligation to hold it" in text
    assert "A prohibition counts only when a respaldo accredits compliance with it" in text
    assert "evidence: the respaldo that accredits it" in text


def test_the_antecedents_for_an_application_are_procedural() -> None:
    text = APPLICABILITY_SYSTEM_PROMPT
    assert "The antecedents for an application are not obligations" in text
    assert "what the authority does with the application" in text
    assert "Put the authorization it serves in procedure_for" in text
    assert "unless it regulates an application, which is procedural" in text


def test_naming_another_law_is_not_a_referral() -> None:
    # Ley 21.800, art. 4° (2026-10-15): it requires an authorization and says that another decree governs the
    # navigable channels. The model took it for a referral while the field said "amends or points to".
    text = APPLICABILITY_SYSTEM_PROMPT
    assert "does not amend it and is not a referral" in text
    assert "decide its nature from what the article itself requires" in text
    assert "points to" not in text


def test_the_rule_against_dictating_is_limited_to_the_reason() -> None:
    text = APPLICABILITY_SYSTEM_PROMPT
    assert "Do not dictate in the reason" in text
    assert "This rule is only for the reason" in text
    assert 'Write a reason only for the articles marked "Write a reason: yes"' in text


def test_user_content_says_which_articles_get_a_reason() -> None:
    body = LegalBody(id=BODY_ID, title="Ley 16744", summary="Resumen.", type="Ley")
    content = render_applicability_user_content(
        body,
        [
            context(A1, "Artículo 66", "Texto del artículo 66."),
            context(A2, "Artículo 67", "Texto del artículo 67.", write_reason=False),
        ],
    )
    first, second = content.split(f"Article ID: {A2}")
    assert "Write a reason: yes" in first and "Write a reason: no" not in first
    assert "Write a reason: no" in second
    assert f"Return exactly these 2 article_id values: {A1}, {A2}" in content


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
