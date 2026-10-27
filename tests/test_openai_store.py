"""Every call to chat completions asks OpenAI to keep no copy: store=False (sprint 4, step 2.3).

With store=True, OpenAI keeps the request and the answer for its distillation and evals products. The
default is false today; the field goes explicit so that a change of default does not decide for us. Each
adapter runs the real SDK over a fake transport (httpx2.MockTransport) that records the body it would send
and answers 400, so nothing leaves the machine and no test depends on the schema of an answer. The chat
checks the same field in test_openai_chat_responder.py.
"""

import ast
import json
from datetime import date
from pathlib import Path

import httpx2
import pytest
from openai import OpenAI

from ia_cumplify.adapters.outbound.openai import classifier as classifier_module
from ia_cumplify.adapters.outbound.openai.action_planner import OpenAIActionPlannerAdapter
from ia_cumplify.adapters.outbound.openai.applicability_reasoner import OpenAIApplicabilityReasonerAdapter
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.openai.evidence_reviewer import OpenAIEvidenceReviewerAdapter
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter
from ia_cumplify.domain.action_plan import ActionPlanFinding, KnowledgeFragment
from ia_cumplify.domain.applicability import ApplicabilityArticleContext
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.evidence_review import EvidenceReviewSubject, ReviewArticle, ReviewLegalBody
from ia_cumplify.domain.exceptions import (
    ActionPlanError,
    ApplicabilityError,
    ClassificationError,
    EvidenceReviewError,
)
from ia_cumplify.domain.legal_body import LegalBody

ADAPTERS = Path(classifier_module.__file__).parent
OPTIONS = {
    "api_key": "sk-test",
    "model": "gpt-5.6-luna",
    "reasoning_effort": "low",
    "timeout_seconds": 5,
    "max_retries": 0,
}
LEGAL_BODY = LegalBody(id="ley-1", title="Ley de prueba", summary="", type="Ley")
ARTICLE = Article(
    id="art-1",
    legal_body_id="ley-1",
    number="1",
    section="",
    text="El empleador debe constituir un comité paritario.",
    order=1,
)
SUBJECT = EvidenceReviewSubject(
    reference_date=date(2026, 10, 26),
    article=ReviewArticle(number="1", text=ARTICLE.text),
    legal_body=ReviewLegalBody(title="Ley de prueba"),
    obligation=None,
    files=(),
)
FINDING = ActionPlanFinding(
    id="hallazgo-1",
    description="No hay acta de constitución del comité.",
    requirement=None,
    missing=None,
    fragments=(KnowledgeFragment(document_id="doc-1", chunk_index=0, text="El comité se constituye por acta."),),
)

CALLS = [
    pytest.param(
        OpenAIArticleClassifierAdapter,
        lambda adapter: adapter.classify_many(LEGAL_BODY, [ARTICLE], [ARTICLE]),
        ClassificationError,
        id="classification",
    ),
    pytest.param(
        OpenAICompanyProfileClassifierAdapter,
        lambda adapter: adapter.classify("Faena minera en Antofagasta."),
        ClassificationError,
        id="profile",
    ),
    pytest.param(
        OpenAIApplicabilityReasonerAdapter,
        lambda adapter: adapter.explain(
            LEGAL_BODY, [ApplicabilityArticleContext(article=ARTICLE, matched_values=("Minería",), actions=())]
        ),
        ApplicabilityError,
        id="applicability-reasons",
    ),
    pytest.param(
        OpenAIEvidenceReviewerAdapter,
        lambda adapter: adapter.review(SUBJECT),
        EvidenceReviewError,
        id="evidence-review",
    ),
    pytest.param(
        OpenAIActionPlannerAdapter,
        lambda adapter: adapter.propose((FINDING,)),
        ActionPlanError,
        id="action-plan",
    ),
]


@pytest.mark.parametrize(("adapter_class", "call", "error"), CALLS)
def test_the_request_asks_openai_to_keep_no_copy(adapter_class, call, error) -> None:
    sent: list[dict] = []

    def reject(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(400, json={"error": {"message": "rechazado", "type": "invalid_request_error"}})

    adapter = adapter_class(**OPTIONS)
    adapter._client = OpenAI(
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(reject)),  # type: ignore[arg-type]
    )

    with pytest.raises(error):
        call(adapter)

    [body] = sent
    assert body.get("store") is False, sorted(body)


def test_every_call_to_chat_completions_in_the_adapters_sets_store_false() -> None:
    """A new call has to set it too: this reads every module of the OpenAI adapters."""
    explicit: dict[str, bool] = {}
    for path in sorted(ADAPTERS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("create", "parse")
                and "completions" in ast.unparse(node.func.value)
            ):
                store = next((keyword.value for keyword in node.keywords if keyword.arg == "store"), None)
                explicit[f"{path.name}:{node.lineno}"] = isinstance(store, ast.Constant) and store.value is False
    # The six calls of today, so that a search that finds nothing cannot pass.
    assert {site.split(":")[0] for site in explicit} >= {
        "action_planner.py",
        "applicability_reasoner.py",
        "chat_responder.py",
        "classifier.py",
        "evidence_reviewer.py",
        "profile_classifier.py",
    }
    assert [site for site, is_false in explicit.items() if not is_false] == []
