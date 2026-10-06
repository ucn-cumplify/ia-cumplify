"""The app-wide 422 handler: no endpoint repeats the request in a validation error (CHT-012, PRF-006).

FastAPI puts the request in input: the whole body when a top-level field is missing or the body is not
a JSON object. The handler keeps type, loc and msg only, and logs only type and loc.
"""

import json
import logging

import pytest
from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app

CLASSIFY_PROFILE = "/api/v1/company-profiles/classify"
EMBEDDINGS = "/api/v1/embeddings"


@pytest.fixture(autouse=True)
def _openai_key(monkeypatch) -> None:
    # Without it these endpoints answer 503 before validating the body. Nothing calls OpenAI: the
    # request never gets past the schema, and OPENAI_BASE_URL points to a closed port of 127.0.0.1.
    configure(monkeypatch, openai_api_key="sk-test-fake")


def post(path: str, *, headers: dict | None = None, **kwargs):
    with TestClient(create_app()) as client:
        return client.post(path, headers={**HEADERS, **(headers or {})}, **kwargs)


@pytest.mark.parametrize(
    ("kwargs", "error_type", "loc"),
    [
        ({"json": {"text": [SECRET]}}, "string_type", ["body", "text"]),
        ({"json": {"text": {"descripcion": SECRET}}}, "string_type", ["body", "text"]),
        # The text under another name: input would be the whole body.
        ({"json": {"txt": SECRET}}, "missing", ["body", "text"]),
        ({"json": {"Text": SECRET}}, "missing", ["body", "text"]),
        # A body that is not a JSON object.
        ({"json": SECRET}, "model_attributes_type", ["body"]),
        ({"json": [SECRET]}, "model_attributes_type", ["body"]),
        ({"content": json.dumps({"text": SECRET}).encode()}, "model_attributes_type", ["body"]),
        (
            {"content": json.dumps({"text": SECRET}).encode(), "headers": {"Content-Type": "text/plain"}},
            "model_attributes_type",
            ["body"],
        ),
    ],
    ids=["text-list", "text-object", "txt", "Text", "json-string", "json-list", "no-content-type", "text-plain"],
)
def test_company_profile_422_never_repeats_the_text(caplog, kwargs: dict, error_type: str, loc: list) -> None:
    caplog.set_level(logging.DEBUG)
    response = post(CLASSIFY_PROFILE, **kwargs)
    assert response.status_code == 422
    assert response.json() == {"detail": [{"type": error_type, "loc": loc, "msg": response.json()["detail"][0]["msg"]}]}
    assert SECRET not in response.text
    assert SECRET not in caplog.text
    assert f"('{error_type}', ('body'" in caplog.text


def test_embeddings_422_never_repeats_the_texts(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    response = post(EMBEDDINGS, json={"texts": SECRET, "dimensions": 0})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert {error["type"] for error in detail} == {"list_type", "greater_than_equal"}
    assert all(list(error) == ["type", "loc", "msg"] for error in detail)
    assert SECRET not in response.text and SECRET not in caplog.text


def test_malformed_json_keeps_only_type_loc_and_msg() -> None:
    response = post(CLASSIFY_PROFILE, content=f'{{"text": "{SECRET}"'.encode(), headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (list(error), error["type"], error["msg"]) == (["type", "loc", "msg"], "json_invalid", "JSON decode error")
    assert SECRET not in response.text
