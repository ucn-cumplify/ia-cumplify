"""Sprint 4 plan, step 2.12: no text of a company, a law or a file reaches the log or an error.

Every endpoint that calls OpenAI with a structured answer runs here with its real adapter and the real SDK,
over a fake transport (httpx2.MockTransport): nothing leaves the machine. CONTENT goes in every text the
request carries or the database returns, and the fake provider repeats it in what it answers, next to the
end of an API key as OpenAI quotes it in a 401 (KEY_TAIL). Whatever fails, neither reaches the log, at any
level, nor the response:

- a request the schema rejects (422);
- an error status from the provider: 400, 401 and 500;
- a 200 whose output does not fit the schema and repeats CONTENT;
- an unexpected error inside the adapter, with CONTENT in its message.

The chat and the document extraction check the same in their own modules: test_chat_http.py for the 422 and
the log of each answer, TestProviderFailures in test_openai_chat_responder.py for the provider, and
test_document_extraction_http.py for a parser message that quotes the file.
"""

import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass

import httpx2
import pytest
from action_plan_backend import backend_body, finding, fragment
from chat_http import HEADERS, configure
from fastapi.testclient import TestClient
from openai import OpenAI

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import (
    get_action_planner,
    get_applicability_reasoner,
    get_classifier,
    get_db_pool,
    get_evidence_reviewer,
    get_profile_classifier,
    get_text_embedder,
)
from ia_cumplify.adapters.outbound.openai.action_planner import OpenAIActionPlannerAdapter
from ia_cumplify.adapters.outbound.openai.applicability_reasoner import OpenAIApplicabilityReasonerAdapter
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.openai.embedder import OpenAITextEmbedderAdapter
from ia_cumplify.adapters.outbound.openai.evidence_reviewer import OpenAIEvidenceReviewerAdapter
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter

CONTENT = "CONTENIDO-de-la-empresa"
KEY_TAIL = "Kx9Q"
LAW_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
ARTICLE_ID = "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c"
APP_ID = "7c8e9f01-2345-6789-abcd-ef0123456789"
FINDING_ID = "3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f01"
DOCUMENT_ID = "9b2d7c4a-1e3f-4a5b-8c6d-7e8f9a0b1c01"
OPTIONS = {"api_key": "sk-test", "timeout_seconds": 5, "max_retries": 0}
MODEL = {"model": "fake-model", "reasoning_effort": "low"}
PROVIDER_ERROR = {
    "error": {
        "message": f"Rejected: {CONTENT}. Incorrect API key provided: sk-proj-****{KEY_TAIL}.",
        "type": "invalid_request_error",
        "code": "invalid_api_key",
    }
}


def provider(mode: str) -> Callable[[httpx2.Request], httpx2.Response]:
    """The fake OpenAI: an error status that repeats CONTENT and KEY_TAIL, or a 200 whose output does."""

    def answer(request: httpx2.Request) -> httpx2.Response:
        if mode != "invalid_output":
            return httpx2.Response(int(mode), json=PROVIDER_ERROR)
        message = {"role": "assistant", "content": json.dumps({"unexpected": CONTENT}), "refusal": None}
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 1,
                "model": "fake-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    return answer


class Unexpected:
    """An adapter that fails in a way nobody planned for, with CONTENT in the message."""

    version = "fake-v1@fake-model"
    model = "fake-model"

    def __getattr__(self, name: str) -> Callable[..., object]:
        def fail(*args: object, **kwargs: object) -> object:
            raise RuntimeError(f"{name} failed on {CONTENT}")

        return fail


class LawPool:
    """What the repository reads of a psycopg pool: a law whose summary and only article carry CONTENT."""

    @contextmanager
    def connection(self):
        yield self

    @contextmanager
    def cursor(self, row_factory=None):
        yield self

    def execute(self, query: str, params: tuple) -> None:
        pass

    def fetchone(self) -> dict:
        return {"id": LAW_ID, "title": "Ley de prueba", "summary": f"Resumen: {CONTENT}.", "type": "Ley"}

    def fetchall(self) -> list[dict]:
        return [
            {
                "id": ARTICLE_ID,
                "legal_body_id": LAW_ID,
                "number": "Artículo 1",
                "section": "",
                "text": f"El empleador debe {CONTENT}.",
                "order": 1,
            }
        ]


@dataclass(frozen=True)
class Endpoint:
    path: str
    dependency: Callable[..., object]
    adapter: Callable[[], object]
    # The request, as TestClient.post keyword arguments; the 422 one has CONTENT where the schema rejects it.
    request: dict
    rejected: dict
    reads_the_law: bool = False
    # A provider 400 that the endpoint reports as a rejected input instead of a failure.
    status_of_400: int = 502


def _review_form(**changes: object) -> dict:
    review = {
        "reference_date": "2026-10-26",
        "article": {"number": "6", "title": "Comité Paritario", "text": f"La empresa debe {CONTENT}."},
        "legal_body": {"title": "Reglamento de prueba", "type": "Reglamento", "number": None},
        "obligation": {"name": "Acta", "description": f"El acta {CONTENT}."},
        "files": [{"id": "file-1", "filename": "nomina.csv"}],
        **changes,
    }
    return {
        "data": {"review": json.dumps(review)},
        "files": [("files", ("nomina.csv", f"Ana;{CONTENT}".encode(), "text/csv"))],
    }


ENDPOINTS = {
    "classification": Endpoint(
        path="/api/v1/legal-bodies/classify",
        dependency=get_classifier,
        adapter=lambda: OpenAIArticleClassifierAdapter(**OPTIONS, **MODEL),
        request={"json": {"legal_body_id": LAW_ID}},
        rejected={"json": {"legal_body_id": CONTENT}},
        reads_the_law=True,
    ),
    "applicability-reasons": Endpoint(
        path="/api/v1/applicability-reasons",
        dependency=get_applicability_reasoner,
        adapter=lambda: OpenAIApplicabilityReasonerAdapter(**OPTIONS, **MODEL),
        request={
            "json": {
                "legal_requirement_id": APP_ID,
                "legal_body_id": LAW_ID,
                "articles": [
                    {"article_id": ARTICLE_ID, "matched_values": [CONTENT], "actions": [f"Monitoreo {CONTENT}"]}
                ],
            }
        },
        rejected={"json": {"legal_requirement_id": APP_ID, "legal_body_id": LAW_ID, "articles": CONTENT}},
        reads_the_law=True,
    ),
    "profile": Endpoint(
        path="/api/v1/company-profiles/classify",
        dependency=get_profile_classifier,
        adapter=lambda: OpenAICompanyProfileClassifierAdapter(**OPTIONS, **MODEL),
        request={"json": {"text": f"Somos {CONTENT}."}},
        rejected={"json": {"text": [CONTENT]}},
    ),
    "embeddings": Endpoint(
        path="/api/v1/embeddings",
        dependency=get_text_embedder,
        adapter=lambda: OpenAITextEmbedderAdapter(**OPTIONS),
        request={"json": {"texts": [f"Obligación: {CONTENT}."]}},
        rejected={"json": {"texts": CONTENT}},
        status_of_400=422,
    ),
    "evidence-review": Endpoint(
        path="/api/v1/evidence-reviews",
        dependency=get_evidence_reviewer,
        adapter=lambda: OpenAIEvidenceReviewerAdapter(**OPTIONS, **MODEL),
        request=_review_form(),
        rejected=_review_form(reference_date=CONTENT),
    ),
    "action-plan": Endpoint(
        path="/api/v1/action-plans",
        dependency=get_action_planner,
        adapter=lambda: OpenAIActionPlannerAdapter(**OPTIONS, **MODEL),
        request={
            "json": backend_body(
                [
                    finding(
                        FINDING_ID,
                        f"Falta el acta: {CONTENT}.",
                        [fragment(DOCUMENT_ID, 0, f"El acta se archiva {CONTENT}.", "Manual")],
                        requirement=f"Constituir {CONTENT}.",
                        missing=f"El acta de {CONTENT}.",
                    )
                ]
            )
        },
        rejected={"json": {"prompt_version": "action-plan-v1", "findings": CONTENT}},
    ),
}
# Embeddings come back as numbers, without a schema to break: that endpoint has no invalid output.
CASES = [
    pytest.param(name, mode, id=f"{name}-{mode}")
    for name in ENDPOINTS
    for mode in ("422", "400", "401", "500", "invalid_output", "unexpected")
    if not (name == "embeddings" and mode == "invalid_output")
]


def _adapter(endpoint: Endpoint, mode: str) -> object:
    if mode == "unexpected":
        return Unexpected()
    adapter = endpoint.adapter()
    adapter._client = OpenAI(  # type: ignore[attr-defined]
        api_key="sk-test",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(provider(mode))),  # type: ignore[arg-type]
    )
    return adapter


def _expected_status(endpoint: Endpoint, mode: str) -> int:
    if mode == "422":
        return 422
    return endpoint.status_of_400 if mode == "400" else 502


@pytest.mark.parametrize(("name", "mode"), CASES)
def test_no_content_reaches_the_log_or_the_response(monkeypatch, caplog, name: str, mode: str) -> None:
    caplog.set_level(logging.DEBUG)
    configure(monkeypatch, openai_api_key="sk-test")
    endpoint = ENDPOINTS[name]
    adapter = _adapter(endpoint, mode)
    app = create_app()
    app.dependency_overrides[endpoint.dependency] = lambda: adapter
    if endpoint.reads_the_law:
        app.dependency_overrides[get_db_pool] = lambda: LawPool()
    request = endpoint.rejected if mode == "422" else endpoint.request

    # An error the app does not handle becomes a 500, as uvicorn would answer it, instead of failing the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(endpoint.path, headers=HEADERS, **request)

    assert response.status_code == _expected_status(endpoint, mode), response.text
    for mark in (CONTENT, KEY_TAIL):
        assert mark not in response.text
        assert mark not in caplog.text
    if response.status_code == 502:
        # Quiet, not silent: the failure is still logged, by its type.
        assert any(
            record.levelno == logging.ERROR and record.name.startswith("ia_cumplify") for record in caplog.records
        )
