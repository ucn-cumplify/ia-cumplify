"""The real evidence reviewer over a fake OpenAI transport, and the evaluation set it will be scored with.

The set itself is not sent to OpenAI here. scripts/evidence_review_eval.py does that when the user runs it.
"""

import json

import httpx2
from chat_http import HEADERS, configure
from fastapi.testclient import TestClient
from openai import OpenAI

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import reset_wiring_cache
from ia_cumplify.adapters.outbound.openai import evidence_reviewer as evidence_reviewer_module
from scripts.evidence_review_eval import checks, load_set, main

REVIEW = "/api/v1/evidence-reviews"


def test_the_real_adapter_sends_a_strict_schema_and_maps_the_fake_review(monkeypatch):
    configure(monkeypatch, openai_api_key="sk-test-fake", openai_model="fake-model")
    reset_wiring_cache()
    requests: list[dict] = []
    review = {
        "nature": "explicit_obligations",
        "procedure_for": "",
        "requirements": [
            {
                "text": "Constituir el comité.",
                "evidence": "Acta de constitución",
                "status": "accredited",
                "citations": [{"file_id": "acta", "locator": "body", "reference": "", "quote": "Ana;1-9"}],
                "missing": "",
            }
        ],
        "verdict": "accredited",
        "summary": "El registro acredita la constitución.",
    }
    usage = {
        "prompt_tokens": 40,
        "completion_tokens": 12,
        "total_tokens": 52,
        "prompt_tokens_details": {"cached_tokens": 8},
    }

    def answer(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        message = {"role": "assistant", "content": json.dumps(review), "refusal": None}
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-fake",
                "object": "chat.completion",
                "created": 1,
                "model": "fake-model",
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}],
                "usage": usage,
            },
        )

    def build(**kwargs: object) -> OpenAI:
        return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(answer)))  # type: ignore[arg-type]

    monkeypatch.setattr(evidence_reviewer_module, "OpenAI", build)
    payload = {
        "reference_date": "2026-10-14",
        "article": {"number": "1", "text": "La empresa debe constituir un Comité Paritario."},
        "legal_body": {"title": "Reglamento", "type": "Reglamento"},
        "files": [{"id": "acta", "filename": "registro.csv"}],
    }
    with TestClient(create_app()) as client:
        response = client.post(
            REVIEW,
            headers=HEADERS,
            data={"review": json.dumps(payload)},
            files=[("files", ("registro.csv", b"Nombre;RUT\nAna;1-9\n", "text/csv"))],
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["nature"] == "explicit_obligations"
    assert body["verdict"] == "accredited"
    assert body["requirements"][0]["evidence"] == "Acta de constitución"
    assert body["requirements"][0]["citations"][0]["quote"] == "Ana;1-9"
    assert body["procedure_for"] is None
    assert body["review_version"] == "evidence-review-v3@fake-model"
    assert body["usage"]["cached_tokens"] == 8
    assert body["usage"]["llm_calls"] == 1
    [sent] = requests
    assert sent["reasoning_effort"] == "low"
    assert "Nombre;RUT" in sent["messages"][1]["content"]
    assert "data:image" not in sent["messages"][1]["content"]
    schema = sent["response_format"]["json_schema"]
    assert schema["strict"] is True
    objects: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            assert "default" not in node, node
            if node.get("type") == "object":
                objects.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema["schema"])
    assert objects
    for node in objects:
        assert sorted(node["required"]) == sorted(node["properties"]), node
        assert node["additionalProperties"] is False


def test_the_evaluation_set_builds_its_cases_and_scores_a_response():
    assert main(["--check-set"]) == 0
    cases = load_set(__import__("pathlib").Path("scripts/evidence_review_eval_set.json"))
    assert [case["id"] for case in cases] == [f"EVR-{number:03d}" for number in range(12, 32)] + [
        "EVR-034",
        "EVR-035",
    ]
    acta = cases[0]
    passed = {
        "nature": "explicit_obligations",
        "verdict": "accredited",
        "review_version": "evidence-review-v3@gpt-5.6-luna",
        "requirements": [{"text": "Constituir.", "status": "accredited", "citations": [], "missing": ""}],
        "files": [{"filename": "acta.pdf", "status": "read", "truncated": False}],
    }
    assert checks(acta, passed) == []
    passed["nature"] = "informative"
    assert checks(acta, passed)


def test_a_periodic_case_needs_each_missing_month_and_not_the_current_one():
    cases = {case["id"]: case for case in load_set(__import__("pathlib").Path("scripts/evidence_review_eval_set.json"))}
    response = {
        "nature": "explicit_obligations",
        "verdict": "partial",
        "review_version": "evidence-review-v3@gpt-5.6-luna",
        "requirements": [{"text": "Sesionar cada mes.", "status": "partial", "citations": [], "missing": ""}],
        "files": [{"filename": "acta-sesion-marzo-2026.docx", "status": "read", "truncated": False}],
    }

    def with_missing(case_id, text):
        response["requirements"][0]["missing"] = text
        response["files"] = [
            {"filename": item["filename"], "status": "read", "truncated": False} for item in cases[case_id]["files"]
        ]
        return checks(cases[case_id], response)

    # What the three evidence-review-v2 reviews of that row said in the second end-to-end run: marzo, marzo and
    # abril, marzo again.
    assert with_missing(
        "EVR-034",
        "No aparecen actas de las sesiones correspondientes a enero, febrero, abril, mayo, junio, julio, agosto, "
        "septiembre y octubre de 2026. Por ello, no aparecen las actas de todos los meses transcurridos del año en "
        "curso.",
    ) == ["missing lists octubre"]
    assert with_missing(
        "EVR-035",
        "No aparecen actas de enero, febrero, mayo, junio, julio, agosto ni septiembre de 2026. Octubre aún no es un "
        "mes transcurrido a la fecha de referencia del 20 de octubre de 2026.",
    ) == []
    assert with_missing(
        "EVR-034",
        "No aparecen actas de los demás meses transcurridos de 2026. Solo consta el acta correspondiente a marzo de 2026.",
    ) == ["missing does not name enero", "missing does not name septiembre"]
    # What evidence-review-v2 wrote in the baseline of the evaluation set: «ni» lists the month too.
    assert with_missing(
        "EVR-034",
        "No aparecen actas de enero, febrero, abril, mayo, junio, julio, agosto, septiembre ni octubre de 2026.",
    ) == ["missing lists octubre"]
    # A range that ends in the current month leaves out the last month that ended.
    assert with_missing("EVR-034", "No aparecen actas de enero, febrero y de abril a octubre de 2026.") == [
        "missing does not name septiembre",
        "missing lists octubre",
    ]


def test_compare_reports_two_runs(tmp_path):
    from scripts.evidence_review_eval import compare

    low = tmp_path / "low.jsonl"
    medium = tmp_path / "medium.jsonl"
    low.write_text(
        json.dumps(
            {"id": "EVR-012", "passed": True, "response": {"verdict": "accredited"}, "total_ms": 10, "cost_usd": 0.001}
        )
        + "\n",
        encoding="utf-8",
    )
    medium.write_text(
        json.dumps(
            {"id": "EVR-012", "passed": True, "response": {"verdict": "partial"}, "total_ms": 20, "cost_usd": 0.002}
        )
        + "\n",
        encoding="utf-8",
    )
    assert compare(low, medium) == 1
