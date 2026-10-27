"""What every call to chat completions lets OpenAI keep (sprint 4 plan, steps 2.3 and 2.4).

- No copy: store=False (step 2.3). With store=True, OpenAI keeps the request and the answer for its
  distillation and evals products. The default is false today; the field goes explicit so that a change of
  default does not decide for us.
- Only the fixed part of the prompt in the prompt cache (step 2.4): the explicit mode with a single breakpoint
  at its end (prompt_cache.py). In the implicit mode, the default, OpenAI picks the prefix it caches, and that
  prefix can hold the law, the labels or the company data of the request.

Each adapter runs the real SDK over a fake transport (httpx2.MockTransport) that records the body it would send
and answers 400, so nothing leaves the machine and no test depends on the schema of an answer. The chat checks
the same fields in test_openai_chat_responder.py.
"""

import ast
import json
from datetime import date
from pathlib import Path

import httpx2
import pytest
from openai import OpenAI

from ia_cumplify.adapters.outbound.openai import classifier as classifier_module
from ia_cumplify.adapters.outbound.openai.action_plan_prompts import ACTION_PLAN_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.action_planner import OpenAIActionPlannerAdapter
from ia_cumplify.adapters.outbound.openai.applicability_prompts import APPLICABILITY_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.applicability_reasoner import OpenAIApplicabilityReasonerAdapter
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.openai.evidence_review_prompts import EVIDENCE_REVIEW_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.evidence_reviewer import OpenAIEvidenceReviewerAdapter
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter
from ia_cumplify.adapters.outbound.openai.profile_prompts import PROFILE_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.prompts import SYSTEM_PROMPT
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


# The fixed instructions of each call: all its cacheable prefix, with the company profile called without
# labels. The chat checks its own in test_openai_chat_responder.py.
INSTRUCTIONS = {
    OpenAIArticleClassifierAdapter: SYSTEM_PROMPT,
    OpenAICompanyProfileClassifierAdapter: PROFILE_SYSTEM_PROMPT,
    OpenAIApplicabilityReasonerAdapter: APPLICABILITY_SYSTEM_PROMPT,
    OpenAIEvidenceReviewerAdapter: EVIDENCE_REVIEW_SYSTEM_PROMPT,
    OpenAIActionPlannerAdapter: ACTION_PLAN_SYSTEM_PROMPT,
}


def sent_body(adapter_class, call, error) -> dict:
    """The body the adapter sends to OpenAI. The fake transport records it and answers 400."""
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
    return body


@pytest.mark.parametrize(("adapter_class", "call", "error"), CALLS)
def test_the_request_asks_openai_to_keep_no_copy(adapter_class, call, error) -> None:
    body = sent_body(adapter_class, call, error)
    assert body.get("store") is False, sorted(body)


@pytest.mark.parametrize(("adapter_class", "call", "error"), CALLS)
def test_only_the_instructions_can_enter_the_prompt_cache(adapter_class, call, error) -> None:
    body = sent_body(adapter_class, call, error)
    assert body.get("prompt_cache_options") == {"mode": "explicit"}, sorted(body)
    # A single breakpoint, at the end of the system message, which holds only the fixed text of the module: no
    # law, label, profile value or company text comes before it.
    assert json.dumps(body["messages"]).count("prompt_cache_breakpoint") == 1
    system, *after = body["messages"]
    assert system == {
        "role": "system",
        "content": [
            {"type": "text", "text": INSTRUCTIONS[adapter_class], "prompt_cache_breakpoint": {"mode": "explicit"}}
        ],
    }
    # The data of the request goes after the breakpoint, as plain text.
    assert after and all(isinstance(message["content"], str) for message in after)


def argument_at_every_call(name: str) -> dict[str, ast.expr | None]:
    """One argument of every call to chat completions in the modules of the OpenAI adapters, by file and line:
    its value in the source, or None where the call leaves it out.

    What the search sees: a create, parse or stream call on an expression whose source contains "completions",
    which takes in the sync and the async stream() of the SDK. It does not see a call on an alias without
    "completions" in its name, nor a call outside this folder."""
    values: dict[str, ast.expr | None] = {}
    for path in sorted(ADAPTERS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("create", "parse", "stream")
                and "completions" in ast.unparse(node.func.value)
            ):
                values[f"{path.name}:{node.lineno}"] = next(
                    (keyword.value for keyword in node.keywords if keyword.arg == name), None
                )
    # The six calls of today, so that a search that finds nothing cannot pass.
    assert {site.split(":")[0] for site in values} >= {
        "action_planner.py",
        "applicability_reasoner.py",
        "chat_responder.py",
        "classifier.py",
        "evidence_reviewer.py",
        "profile_classifier.py",
    }
    return values


def test_every_call_to_chat_completions_in_the_adapters_sets_store_false() -> None:
    """A new create, parse or stream call on an expression that contains "completions" has to set it too: this
    reads every module of the OpenAI adapters."""
    store = argument_at_every_call("store")
    explicit = {site: isinstance(value, ast.Constant) and value.value is False for site, value in store.items()}
    assert [site for site, is_false in explicit.items() if not is_false] == []


def test_every_call_to_chat_completions_in_the_adapters_uses_the_explicit_prompt_cache() -> None:
    """A new create, parse or stream call on an expression that contains "completions" has to use it too: without
    it, the implicit mode lets OpenAI cache the data of the request."""
    options = argument_at_every_call("prompt_cache_options")
    explicit = {
        site: isinstance(value, ast.Name) and value.id == "PROMPT_CACHE_OPTIONS" for site, value in options.items()
    }
    assert [site for site, is_explicit in explicit.items() if not is_explicit] == []
