from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import get_db_pool
from ia_cumplify.adapters.inbound.http.routers.applicability import get_explain_applicability_use_case
from ia_cumplify.domain.applicability import ApplicabilityReasons, ArticleApplicabilityReason
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import ApplicabilityError, LegalBodyNotFoundError
from ia_cumplify.domain.legal_body import LegalBody

PATH = "/api/v1/applicability-reasons"
APP_ID = "7c8e9f01-2345-6789-abcd-ef0123456789"
NORM_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
ARTICLE_ID = "8b2c1a40-1d3e-4f5a-9c6b-7d8e9f0a1b2c"
OTHER_ID = "9c3d2b51-2e4f-5a6b-0d7c-8e9f0a1b2c3d"


def payload(**changes: object) -> dict:
    body = {
        "legal_requirement_id": APP_ID,
        "legal_body_id": NORM_ID,
        "articles": [
            {
                "article_id": ARTICLE_ID,
                "matched_values": ["Minería"],
                "actions": ["Monitoreo de relaves"],
            }
        ],
    }
    body.update(changes)
    return body


class ScriptedUseCase:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._result = result
        self._error = error

    def execute(self, legal_body_id: str, articles) -> ApplicabilityReasons:
        self.calls.append((legal_body_id, tuple(articles)))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


def post(*, use_case=None, headers=HEADERS, **kwargs):
    app = create_app()
    if use_case is not None:
        app.dependency_overrides[get_explain_applicability_use_case] = lambda: use_case
    with TestClient(app) as client:
        return client.post(PATH, headers=headers, **kwargs)


def success_result() -> ApplicabilityReasons:
    return ApplicabilityReasons(
        legal_body=LegalBody(id=NORM_ID, title="Ley 16744", summary="", type="Ley"),
        reasons=(ArticleApplicabilityReason(ARTICLE_ID, "Motivo de prueba."),),
        reason_version="applicability-v1@test",
        usage=TokenUsage(prompt_tokens=9, completion_tokens=3, total_tokens=12, llm_calls=1),
    )


def test_missing_or_wrong_api_key_is_401(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    use_case = ScriptedUseCase(success_result())
    for headers in ({}, {"X-API-Key": "otra-clave"}):
        response = post(json=payload(), headers=headers, use_case=use_case)
        assert response.status_code == 401
        assert response.json() == {"detail": "Missing or invalid X-API-Key."}
    assert use_case.calls == []


def test_missing_service_api_key_is_503() -> None:
    response = post(json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "SERVICE_API_KEY is not configured."}


def test_missing_database_url_is_503(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    response = post(json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "DATABASE_URL is not configured."}


def test_missing_openai_key_is_503(monkeypatch) -> None:
    configure(monkeypatch)
    app = create_app()
    app.dependency_overrides[get_db_pool] = lambda: object()
    with TestClient(app) as client:
        response = client.post(PATH, headers=HEADERS, json=payload())
    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY is not configured."}


def test_200_returns_reasons_and_does_not_send_the_app_id_to_the_use_case(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    use_case = ScriptedUseCase(success_result())
    response = post(json=payload(), use_case=use_case)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reasons"] == [{"article_id": ARTICLE_ID, "reason": "Motivo de prueba."}]
    assert body["reason_version"] == "applicability-v1@test"
    assert body["usage"]["llm_calls"] == 1
    assert "dev_metrics" in body
    assert use_case.calls[0][0] == NORM_ID
    assert use_case.calls[0][1][0].article_id == ARTICLE_ID
    assert use_case.calls[0][1][0].matched_values == ("Minería",)


def test_404_when_the_legal_body_is_missing(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    response = post(json=payload(), use_case=ScriptedUseCase(error=LegalBodyNotFoundError(NORM_ID)))
    assert response.status_code == 404
    assert NORM_ID in response.json()["detail"]


def test_502_when_the_provider_fails(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    response = post(
        json=payload(),
        use_case=ScriptedUseCase(error=ApplicabilityError("OpenAI error while writing applicability reasons")),
    )
    assert response.status_code == 502
    assert "OpenAI" in response.json()["detail"]
    assert SECRET not in response.text


def test_extra_profile_text_field_is_422(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    use_case = ScriptedUseCase(success_result())
    body = payload()
    body["text"] = SECRET
    response = post(json=body, use_case=use_case)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert use_case.calls == []


def test_empty_articles_duplicate_id_and_empty_values_are_422(monkeypatch) -> None:
    configure(monkeypatch, openai_api_key="sk-test-fake")
    use_case = ScriptedUseCase(success_result())
    empty = post(json=payload(articles=[]), use_case=use_case)
    assert empty.status_code == 422
    duplicate = post(
        json=payload(
            articles=[
                {"article_id": ARTICLE_ID, "matched_values": ["Minería"], "actions": []},
                {"article_id": ARTICLE_ID, "matched_values": ["Energía"], "actions": []},
            ]
        ),
        use_case=use_case,
    )
    assert duplicate.status_code == 422
    no_values = post(
        json=payload(articles=[{"article_id": ARTICLE_ID, "matched_values": [], "actions": []}]),
        use_case=use_case,
    )
    assert no_values.status_code == 422
    assert use_case.calls == []
    assert OTHER_ID not in empty.text
