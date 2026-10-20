"""The OpenAI evidence reviewer: one structured call, mapped onto the review judgment."""

from datetime import date
from types import SimpleNamespace

import pytest
from chat_http import SECRET, configure
from openai import OpenAIError

from ia_cumplify.adapters.inbound.http.dependencies import get_evidence_reviewer, reset_wiring_cache
from ia_cumplify.adapters.outbound.openai.evidence_review_prompts import EVIDENCE_REVIEW_SYSTEM_PROMPT
from ia_cumplify.adapters.outbound.openai.evidence_reviewer import OpenAIEvidenceReviewerAdapter
from ia_cumplify.adapters.outbound.openai.llm_evidence_review_schema import LlmEvidenceReview, LlmReviewRequirement
from ia_cumplify.domain.evidence_review import EvidenceReviewSubject, ReviewArticle, ReviewLegalBody
from ia_cumplify.domain.exceptions import EvidenceReviewError


def test_the_adapter_sends_the_subject_and_maps_the_review():
    adapter = OpenAIEvidenceReviewerAdapter(
        api_key="sk-test",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=80,
        max_retries=0,
    )
    completions = _Completions(
        LlmEvidenceReview(
            nature="explicit_obligations",
            procedure_for="",
            requirements=[
                LlmReviewRequirement(
                    text=" Constituir el comité. ",
                    evidence=" Acta de constitución ",
                    status="accredited",
                    citations=[],
                    missing="",
                )
            ],
            verdict="accredited",
            summary=" El acta acredita la constitución. ",
        ),
        usage=SimpleNamespace(prompt_tokens=20, completion_tokens=8, total_tokens=28, prompt_tokens_details=None),
    )
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    subject = _subject()

    judgment = adapter.review(subject)

    assert completions.kwargs["model"] == "gpt-5.6-luna"
    assert completions.kwargs["reasoning_effort"] == "low"
    assert completions.kwargs["response_format"] is LlmEvidenceReview
    assert completions.kwargs["messages"][0]["role"] == "system"
    # Option (b) of "Caché de prompts": the explicit mode, with the only breakpoint at the end of the
    # instructions, so the article and the files never enter the cache.
    assert completions.kwargs["prompt_cache_options"] == {"mode": "explicit"}
    assert completions.kwargs["messages"][0]["content"] == [
        {"type": "text", "text": EVIDENCE_REVIEW_SYSTEM_PROMPT, "prompt_cache_breakpoint": {"mode": "explicit"}}
    ]
    assert isinstance(completions.kwargs["messages"][1]["content"], str)
    assert subject.article.text in completions.kwargs["messages"][1]["content"]
    assert "data:image" not in completions.kwargs["messages"][1]["content"]
    assert judgment.nature == "explicit_obligations"
    assert judgment.requirements[0].text == "Constituir el comité."
    assert judgment.requirements[0].evidence == "Acta de constitución"
    assert judgment.procedure_for is None
    assert judgment.summary == "El acta acredita la constitución."
    assert judgment.model == "gpt-5.6-luna"
    assert judgment.review_version == "evidence-review-v2@gpt-5.6-luna"
    assert judgment.usage.prompt_tokens == 20
    assert judgment.usage.llm_calls == 1


def test_a_provider_error_does_not_repeat_the_request():
    adapter = OpenAIEvidenceReviewerAdapter(
        api_key="sk-test",
        model="gpt-5.6-luna",
        reasoning_effort="low",
        timeout_seconds=80,
        max_retries=0,
    )
    adapter._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions(None, error=OpenAIError(SECRET)))
    )

    with pytest.raises(EvidenceReviewError) as raised:
        adapter.review(_subject())

    assert str(raised.value) == "OpenAI error while reviewing evidence: OpenAIError"
    assert SECRET not in str(raised.value)


def test_empty_review_model_and_effort_use_the_shared_ones(monkeypatch):
    configure(
        monkeypatch,
        openai_api_key="sk-test",
        openai_model="gpt-test",
        openai_reasoning_effort="high",
        evidence_review_model="",
        evidence_review_reasoning_effort="",
        openai_timeout_seconds="42",
        # conftest sets 0; the shared value must not reach the review.
        openai_max_retries="2",
    )
    created: dict = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)

    monkeypatch.setattr("ia_cumplify.adapters.outbound.openai.evidence_reviewer.OpenAI", _Client)
    reset_wiring_cache()

    reviewer = get_evidence_reviewer()

    assert reviewer._model == "gpt-test"
    assert reviewer._reasoning_effort == "high"
    assert created["api_key"] == "sk-test"
    assert created["timeout"] == 80
    assert created["max_retries"] == 0


def test_the_review_model_effort_timeout_and_retries_are_its_own(monkeypatch):
    configure(
        monkeypatch,
        openai_api_key="sk-test",
        openai_model="modelo-general",
        openai_reasoning_effort="high",
        evidence_review_model="modelo-revision",
        evidence_review_reasoning_effort="medium",
        evidence_review_timeout_seconds="15",
        evidence_review_max_retries="1",
    )
    created: dict = {}

    class _Client:
        def __init__(self, **kwargs) -> None:
            created.update(kwargs)

    monkeypatch.setattr("ia_cumplify.adapters.outbound.openai.evidence_reviewer.OpenAI", _Client)
    reset_wiring_cache()

    reviewer = get_evidence_reviewer()

    assert reviewer._model == "modelo-revision"
    assert reviewer._reasoning_effort == "medium"
    assert created["timeout"] == 15
    assert created["max_retries"] == 1


class _Completions:
    def __init__(self, parsed, usage=None, error: Exception | None = None) -> None:
        self.parsed = parsed
        self.usage = usage
        self.error = error
        self.kwargs = None

    def parse(self, **kwargs):
        self.kwargs = kwargs
        if self.error is not None:
            raise self.error
        message = SimpleNamespace(parsed=self.parsed, refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=self.usage)


def _subject() -> EvidenceReviewSubject:
    return EvidenceReviewSubject(
        reference_date=date(2026, 10, 14),
        article=ReviewArticle(
            number="1",
            text=f"Acta {SECRET}",
            title="Portada ![foto](data:image/png;base64,AAAA)",
            section=None,
        ),
        legal_body=ReviewLegalBody(title="Reglamento", type="Reglamento", number=None),
        obligation=None,
        files=(),
    )
