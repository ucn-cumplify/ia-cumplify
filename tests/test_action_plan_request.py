"""POST /api/v1/action-plans: the request backend-cumplify sends, the limits, the errors and the 200 it reads.

The planner is replaced by a scripted one, so nothing calls OpenAI.
"""

import json
import logging

import pytest
from action_plan_backend import backend_body, finding, fragment, read_like_backend
from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import get_action_planner, get_db_pool, reset_wiring_cache
from ia_cumplify.adapters.inbound.http.routers import action_plans
from ia_cumplify.domain.action_plan import ActionPlanDraft, FindingTasks, ProposedTask, TaskCitation
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import ActionPlanError

ACTION_PLANS = "/api/v1/action-plans"
FINDING_A = "3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f01"
FINDING_B = "3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f02"
DOCUMENT = "9b2d7c4a-1e3f-4a5b-8c6d-7e8f9a0b1c01"
TEXT = "El acta se archiva en la carpeta de prevención y se renueva cada dos años."


class ScriptedPlanner:
    def __init__(self, *findings: FindingTasks, error: Exception | None = None) -> None:
        self.findings = findings
        self.error = error
        self.calls: list = []

    @property
    def model(self) -> str:
        return "gpt-5.6-luna"

    @property
    def version(self) -> str:
        return "action-plan-v1@gpt-5.6-luna"

    def propose(self, findings):
        self.calls.append(findings)
        if self.error is not None:
            raise self.error
        return ActionPlanDraft(
            findings=self.findings,
            usage=TokenUsage(prompt_tokens=20, completion_tokens=10, total_tokens=30, llm_calls=1, cached_tokens=0),
        )


def archive_task(finding_id: str = FINDING_A, quote: str = "El acta se archiva") -> FindingTasks:
    return FindingTasks(
        finding_id,
        (
            ProposedTask(
                name="Archivar el acta",
                description="Guardar el acta en la carpeta de prevención.",
                priority="alta",
                citation=TaskCitation(document_id=DOCUMENT, chunk_index=0, quote=quote),
            ),
        ),
    )


def body(**changes) -> dict:
    """The example of «Proponer el plan de acción» in docs/AI/api.md of backend-cumplify, with real ids."""
    return {
        **backend_body(
            [
                finding(
                    FINDING_A,
                    "Falta el acta.",
                    [fragment(DOCUMENT, 0, TEXT, "Acta (parte 1 de 3)")],
                    requirement="Tener el acta.",
                    missing="Falta la firma.",
                )
            ]
        ),
        **changes,
    }


def post(payload, *, planner=None, headers=HEADERS, override=True):
    # The settings are cached: a test that changes a variable between two requests needs them read again.
    reset_wiring_cache()
    app = create_app()
    if override:
        app.dependency_overrides[get_action_planner] = lambda: planner or ScriptedPlanner()
    with TestClient(app) as client:
        if isinstance(payload, (bytes, str)):
            return client.post(ACTION_PLANS, headers={**headers, "Content-Type": "application/json"}, content=payload)
        return client.post(ACTION_PLANS, headers=headers, json=payload)


@pytest.fixture(autouse=True)
def _service_key(monkeypatch):
    configure(monkeypatch, openai_api_key="sk-test-fake")


def test_the_backend_request_gets_the_contract_the_backend_reads():
    planner = ScriptedPlanner(archive_task())
    response = post(body(), planner=planner)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert list(payload) == ["proposal_version", "model", "usage", "findings"]
    assert payload["proposal_version"] == "action-plan-v1@gpt-5.6-luna"
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
        "cached_tokens": 0,
        "llm_calls": 1,
    }
    assert payload["findings"] == [
        {
            "id": FINDING_A,
            "tasks": [
                {
                    "name": "Archivar el acta",
                    "description": "Guardar el acta en la carpeta de prevención.",
                    "priority": "alta",
                    "citation": {"document_id": DOCUMENT, "chunk_index": 0, "quote": "El acta se archiva"},
                }
            ],
        }
    ]
    reading = read_like_backend(response.text, body()["findings"])
    assert reading.can_store
    [stored] = reading.findings
    assert not stored.unsupported
    assert [(t.name, t.priority, t.locator, t.citation) for t in stored.tasks] == [
        ("Archivar el acta", "alta", "Acta (parte 1 de 3)", "El acta se archiva")
    ]
    [sent] = planner.calls
    assert [(item.id, item.description, item.requirement, item.missing) for item in sent] == [
        (FINDING_A, "Falta el acta.", "Tener el acta.", "Falta la firma.")
    ]
    assert [(f.document_id, f.chunk_index, f.locator, f.text) for f in sent[0].fragments] == [
        (DOCUMENT, 0, "Acta (parte 1 de 3)", TEXT)
    ]


def test_uppercase_ids_from_the_request_come_back_as_the_backend_parses_them():
    planner = ScriptedPlanner(archive_task())
    upper = backend_body(
        [finding(FINDING_A.upper(), "Falta el acta.", [fragment(DOCUMENT.upper(), 0, TEXT)])]
    )

    response = post(upper, planner=planner)

    assert response.status_code == 200
    assert response.json()["findings"][0]["id"] == FINDING_A
    assert read_like_backend(response.text, upper["findings"]).findings[0].tasks


def test_a_citation_outside_the_fragment_leaves_the_finding_without_support():
    response = post(body(), planner=ScriptedPlanner(archive_task(quote="El acta se destruye")))

    assert response.status_code == 200
    assert response.json()["findings"] == [{"id": FINDING_A, "tasks": []}]
    assert read_like_backend(response.text, body()["findings"]).findings[0].unsupported


def test_findings_without_fragments_answer_without_calling_the_model():
    planner = ScriptedPlanner()
    request = backend_body(
        [finding(FINDING_A, "Falta el acta.", []), finding(FINDING_B, "Falta el registro.", None)]  # type: ignore[arg-type]
    )

    response = post(request, planner=planner)

    assert response.status_code == 200
    assert planner.calls == []
    payload = response.json()
    assert payload["findings"] == [{"id": FINDING_A, "tasks": []}, {"id": FINDING_B, "tasks": []}]
    assert payload["usage"]["llm_calls"] == 0
    reading = read_like_backend(response.text, request["findings"])
    # The backend can still store a proposal with both findings marked without support.
    assert reading.can_store
    assert [item.unsupported for item in reading.findings] == [True, True]


def test_a_repeated_fragment_is_sent_once():
    planner = ScriptedPlanner(archive_task())
    request = backend_body([finding(FINDING_A, "Falta el acta.", [fragment(DOCUMENT, 0, TEXT), fragment(DOCUMENT, 0, "otro")])])

    response = post(request, planner=planner)

    assert response.status_code == 200
    assert [f.text for f in planner.calls[0][0].fragments] == [TEXT]


def test_blank_optional_fields_are_null_and_lone_surrogates_are_replaced():
    planner = ScriptedPlanner()
    request = backend_body(
        [finding(FINDING_A, "Falta \ud800 el acta.", [fragment(DOCUMENT, 0, TEXT, "  ")], requirement=" ", missing="")]
    )

    response = post(json.dumps(request), planner=planner)

    assert response.status_code == 200
    [sent] = planner.calls[0]
    assert sent.description == "Falta � el acta."
    assert (sent.requirement, sent.missing, sent.fragments[0].locator) == (None, None, None)


def _with_finding(**changes) -> dict:
    item = finding(FINDING_A, "Falta el acta.", [fragment(DOCUMENT, 0, TEXT)])
    item.update(changes)
    return backend_body([item])


def _with_fragment(**changes) -> dict:
    item = fragment(DOCUMENT, 0, TEXT)
    item.update(changes)
    return backend_body([finding(FINDING_A, "Falta el acta.", [item])])


@pytest.mark.parametrize(
    ("payload", "loc"),
    [
        (body(prompt_version="action-plan-v2"), ["body", "prompt_version"]),
        ({"findings": body()["findings"]}, ["body", "prompt_version"]),
        (body(findings=[]), ["body", "findings"]),
        (body(findings=None), ["body", "findings"]),
        (body(extra=SECRET), ["body", "extra"]),
        (_with_finding(description="   "), ["body", "findings", 0, "description"]),
        (_with_finding(description=None), ["body", "findings", 0, "description"]),
        (_with_finding(description="d" * 2001), ["body", "findings", 0, "description"]),
        (_with_finding(id="no-es-un-guid"), ["body", "findings", 0, "id"]),
        (_with_finding(id="00000000-0000-0000-0000-000000000000"), ["body", "findings", 0, "id"]),
        (_with_finding(requirement="r" * 20_001), ["body", "findings", 0, "requirement"]),
        (_with_finding(missing="m" * 20_001), ["body", "findings", 0, "missing"]),
        (_with_finding(note=SECRET), ["body", "findings", 0, "note"]),
        (
            _with_finding(fragments=[fragment(DOCUMENT, index, TEXT) for index in range(6)]),
            ["body", "findings", 0, "fragments"],
        ),
        (_with_fragment(text="t" * 4001), ["body", "findings", 0, "fragments", 0, "text"]),
        (_with_fragment(text=" "), ["body", "findings", 0, "fragments", 0, "text"]),
        (_with_fragment(chunk_index=-1), ["body", "findings", 0, "fragments", 0, "chunk_index"]),
        (_with_fragment(chunk_index="0"), ["body", "findings", 0, "fragments", 0, "chunk_index"]),
        (_with_fragment(chunk_index=True), ["body", "findings", 0, "fragments", 0, "chunk_index"]),
        (_with_fragment(document_id=None), ["body", "findings", 0, "fragments", 0, "document_id"]),
        (_with_fragment(locator="l" * 1001), ["body", "findings", 0, "fragments", 0, "locator"]),
    ],
    ids=[
        "other-prompt",
        "no-prompt",
        "no-findings",
        "null-findings",
        "extra-field",
        "blank-description",
        "null-description",
        "long-description",
        "bad-id",
        "nil-id",
        "long-requirement",
        "long-missing",
        "extra-finding-field",
        "six-fragments",
        "long-fragment",
        "blank-fragment",
        "negative-chunk",
        "string-chunk",
        "bool-chunk",
        "no-document",
        "long-locator",
    ],
)
def test_an_invalid_request_is_422_without_its_content(caplog, payload, loc):
    caplog.set_level(logging.DEBUG)
    planner = ScriptedPlanner()

    response = post(payload, planner=planner)

    assert response.status_code == 422, response.text
    assert [error["loc"] for error in response.json()["detail"]] == [loc]
    assert SECRET not in response.text
    assert TEXT not in response.text
    assert planner.calls == []


def test_repeated_finding_ids_are_rejected():
    request = backend_body([finding(FINDING_A, "Uno.", []), finding(FINDING_A, "Dos.", [])])

    response = post(request)

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "Value error, Finding ids must be unique."


def test_the_finding_cap_is_a_setting(monkeypatch):
    request = backend_body([finding(f"3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a2f{n:02d}", "Falta.", []) for n in range(1, 4)])

    configure(monkeypatch, action_plan_max_findings="2")
    rejected = post(request)
    configure(monkeypatch, action_plan_max_findings="3")
    accepted = post(request)

    assert rejected.status_code == 422
    assert rejected.json()["detail"] == [
        {"type": "too_long", "loc": ["body", "findings"], "msg": "The request has 3 findings; the limit is 2."}
    ]
    assert accepted.status_code == 200


def test_the_default_cap_is_thirty_findings():
    request = backend_body([finding(f"3f1c6b0e-8a6d-4b8e-9d2a-0c7e5b1a{n:04d}", "Falta.", []) for n in range(31)])

    response = post(request)

    assert response.status_code == 422
    assert response.json()["detail"][0]["msg"] == "The request has 31 findings; the limit is 30."


def test_a_body_that_is_not_json_is_422():
    response = post(b"{" + SECRET.encode())

    assert response.status_code == 422
    assert SECRET not in response.text


def test_a_provider_failure_is_502_without_the_request(caplog):
    caplog.set_level(logging.DEBUG)
    planner = ScriptedPlanner(error=ActionPlanError("OpenAI error while proposing the action plan: APITimeoutError"))

    response = post(body(findings=[finding(FINDING_A, SECRET, [fragment(DOCUMENT, 0, TEXT)])]), planner=planner)

    assert response.status_code == 502
    assert response.json() == {"detail": "OpenAI error while proposing the action plan: APITimeoutError"}
    assert SECRET not in caplog.text
    assert TEXT not in caplog.text
    assert "Action plan failed for 1 findings: ActionPlanError" in caplog.text


def test_the_log_counts_findings_and_tasks_without_their_text(caplog):
    caplog.set_level(logging.INFO)

    response = post(
        body(findings=[finding(FINDING_A, SECRET, [fragment(DOCUMENT, 0, TEXT)])]),
        planner=ScriptedPlanner(archive_task()),
    )

    assert response.status_code == 200
    assert (
        "Action plan response: 1 findings, 1 fragments, 1 with tasks, 1 tasks, "
        "proposal_version action-plan-v1@gpt-5.6-luna." in caplog.text
    )
    assert SECRET not in caplog.text
    assert TEXT not in caplog.text


def test_the_key_is_checked_before_the_findings(monkeypatch):
    planner = ScriptedPlanner(archive_task())
    # A body that would be 422: the key decides first. A body that is not JSON at all is a 422 before any
    # dependency runs, in every JSON endpoint of FastAPI.
    invalid = _with_finding(description=SECRET * 1000)

    missing = post(invalid, headers={}, planner=planner)
    wrong = post(body(), headers={"X-API-Key": "otra"}, planner=planner)
    monkeypatch.delenv("SERVICE_API_KEY")
    unconfigured = post(body(), planner=planner)

    assert (missing.status_code, wrong.status_code, unconfigured.status_code) == (401, 401, 503)
    assert SECRET not in missing.text
    assert planner.calls == []


def test_without_an_openai_key_the_endpoint_is_503(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")

    response = post(body(), override=False)
    # The key is required before the findings are validated.
    invalid = post(_with_finding(description="   "), override=False)

    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY is not configured."}
    assert invalid.status_code == 503


def test_the_proposal_reads_no_database():
    app = create_app()
    app.dependency_overrides[get_action_planner] = lambda: ScriptedPlanner(archive_task())

    def no_database():
        raise AssertionError("POST /api/v1/action-plans must not open the database.")

    app.dependency_overrides[get_db_pool] = no_database
    with TestClient(app) as client:
        response = client.post(ACTION_PLANS, headers=HEADERS, json=body())

    assert response.status_code == 200
    assert app.state.db_pool is None
    route = next(route for route in action_plans.router.routes if route.path == "/action-plans")
    assert get_db_pool not in [dependency.call for dependency in route.dependant.dependencies]
