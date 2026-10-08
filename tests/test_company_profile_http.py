"""POST /api/v1/company-profiles/classify with the real adapter, without OpenAI (PRF-008 to PRF-012, PRF-014, PRF-015).

The adapter's OpenAI client gets an httpx2.MockTransport that answers each model call with a scripted
completion and keeps the request, so the tests read the prompt the model would get. Nothing leaves the
process. SECRET marks the company text and OUTPUT_MARK the model output: when a call fails, neither
appears in the response or in the log.
"""

import json
import logging
import traceback

import httpx2
import pytest
from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient
from openai import OpenAI

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import (
    get_profile_classifier,
    reset_wiring_cache,
)
from ia_cumplify.adapters.outbound.openai import (
    profile_classifier as profile_classifier_module,
)
from ia_cumplify.adapters.outbound.openai.profile_classifier import (
    OpenAICompanyProfileClassifierAdapter,
)
from ia_cumplify.domain.exceptions import ClassificationError

PATH = "/api/v1/company-profiles/classify"
OUTPUT_MARK = "MARCA-de-la-salida-del-modelo"
INVALID_OUTPUT = "Model did not return a valid company profile classification."
USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "llm_calls")
PROFILE = {
    "scope": ["Transporte y tránsito", "Laboral"],
    "productive_sector": ["Transporte"],
    "territorial_coverage": ["Región de Valparaíso"],
    "activity_action": ["Transporte de carga por carretera"],
    "facility_installation_equipment": ["Camión"],
    "others": ["85 trabajadores"],
}
IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
PDF = "data:application/pdf;base64,JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFIvRmlsdGVyIC9GbGF0ZURlY29kZT4+"


CALL_USAGE = {
    "prompt_tokens": 1000,
    "completion_tokens": 100,
    "total_tokens": 1100,
    "prompt_tokens_details": {"cached_tokens": 640},
}
# A usage block a proxy or a compatible provider could send: only completion_tokens can be read.
MALFORMED_USAGE = {
    "prompt_tokens": "mil",
    "completion_tokens": 100,
    "total_tokens": [1100],
    "prompt_tokens_details": {"cached_tokens": "x"},
}


def completion(
    content: str | None, *, refusal: str | None = None, usage: dict = CALL_USAGE
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": 1,
            "model": "fake-model",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": content,
                        "refusal": refusal,
                    },
                }
            ],
            "usage": usage,
        },
    )


class ProfileScript:
    """Answers each model call with the next step: ok, malformed_usage (ok with MALFORMED_USAGE), refusal,
    invalid (JSON that does not fit the schema), not_json (content that is not JSON), bad_body (an HTTP
    body that is not JSON) or 500. Every failure carries OUTPUT_MARK, as a model or a provider repeating
    what it got would."""

    def __init__(self, *steps: str) -> None:
        self.steps = list(steps)
        self.bodies: list[dict] = []

    @property
    def prompts(self) -> list[str]:
        return [body["messages"][1]["content"] for body in self.bodies]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.bodies.append(json.loads(request.content))
        step = self.steps.pop(0)
        if step == "ok":
            return completion(json.dumps(PROFILE))
        if step == "malformed_usage":
            return completion(json.dumps(PROFILE), usage=MALFORMED_USAGE)
        if step == "refusal":
            return completion(None, refusal=f"No clasifico «{OUTPUT_MARK}».")
        if step == "invalid":
            return completion(json.dumps({**PROFILE, "scope": OUTPUT_MARK}))
        if step == "not_json":
            return completion(f"La empresa {OUTPUT_MARK} se dedica al transporte.")
        if step == "bad_body":
            body = f'{{"id": "chatcmpl-fake", "note": "{OUTPUT_MARK}", "choices": ['
            return httpx2.Response(
                200, content=body.encode(), headers={"content-type": "application/json"}
            )
        if step == "500":
            error = {
                "error": {"message": f"Rejected: {OUTPUT_MARK}", "type": "server_error"}
            }
            return httpx2.Response(500, json=error)
        raise AssertionError(f"Unknown step {step}")


@pytest.fixture
def profile_api(monkeypatch):
    """The service with SERVICE_API_KEY and OPENAI_API_KEY, whose profile adapter calls the script."""

    def start(*steps: str):
        configure(monkeypatch, openai_api_key="sk-test-fake", openai_model="fake-model")
        script = ProfileScript(*steps)

        def build(**kwargs: object) -> OpenAI:
            transport = httpx2.MockTransport(script.handler)
            return OpenAI(**kwargs, http_client=httpx2.Client(transport=transport))  # type: ignore[arg-type]

        monkeypatch.setattr(profile_classifier_module, "OpenAI", build)

        def post(*, headers: dict = HEADERS, **kwargs: object):
            with TestClient(create_app()) as client:
                return client.post(PATH, headers=headers, **kwargs)

        return script, post

    return start


def description(prompt: str) -> str:
    return prompt.split("--- COMPANY DESCRIPTION ---\n", 1)[1].rsplit(
        "\n--- END COMPANY DESCRIPTION ---", 1
    )[0]


def label_lines(prompt: str) -> list[str]:
    block = prompt.split("--- EXISTING LABELS (reuse when they fit) ---\n", 1)[1].split(
        "\n--- END EXISTING LABELS ---", 1
    )[0]
    # The first line explains the block; the rest are one line per dimension.
    return block.splitlines()[1:]


def test_200_returns_the_six_dimensions_the_version_and_the_usage(profile_api) -> None:
    script, post = profile_api("ok")
    response = post(
        json={"text": "Transportamos carga por carretera en la Región de Valparaíso."}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"] == PROFILE
    assert body["classifier_version"] == "profile-v1@fake-model"
    assert body["usage"] == {
        "prompt_tokens": 1000,
        "completion_tokens": 100,
        "total_tokens": 1100,
        "cached_tokens": 640,
        "llm_calls": 1,
    }
    [request] = script.bodies
    assert request["model"] == "fake-model"
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert {
        name: field["minItems"]
        for name, field in schema["schema"]["properties"].items()
    } == dict.fromkeys(PROFILE, 1)


# parse() dumps the completion it built, and pydantic warns that the malformed counts are not int.
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
def test_an_unreadable_usage_count_is_zero(profile_api) -> None:
    # PRF-015. The SDK builds the usage block without validating it: a count that is not a positive integer
    # counts 0 instead of failing a valid answer with 502, which the backend would retry and pay again.
    script, post = profile_api("malformed_usage")
    response = post(json={"text": "Empresa de transporte de carga."})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["classification"] == PROFILE
    assert body["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 100,
        "total_tokens": 0,
        "cached_tokens": 0,
        "llm_calls": 1,
    }
    assert len(script.bodies) == 1


def test_text_limits(profile_api) -> None:
    # PRF-008, with PROFILE_TEXT_MAX_CHARS in its default of 4000.
    script, post = profile_api("ok", "ok")
    exact = "a" * 4000

    assert post(json={"text": exact}).status_code == 200
    assert post(json={"text": f"  {exact}\n "}).status_code == 200
    # The spaces around the text do not count, and the model gets it trimmed.
    assert [description(prompt) for prompt in script.prompts] == [exact, exact]

    too_long = post(json={"text": "a" * 4001})
    assert (too_long.status_code, too_long.json()) == (
        422,
        {"detail": "The company profile text has 4001 characters; the limit is 4000."},
    )
    blank = post(json={"text": " \n\t "})
    assert (blank.status_code, blank.json()) == (
        422,
        {"detail": "The company profile text is empty."},
    )
    assert post(json={}).status_code == 422
    assert post(json={"text": 42}).status_code == 422
    assert len(script.bodies) == 2


def test_a_lone_surrogate_is_422_without_calling_the_model(profile_api, caplog) -> None:
    # PRF-008. JSON allows the escape \ud800, which no request to the model can carry. Before, it failed
    # while sending the request to OpenAI; now the use case rejects it, so it stays a 422 and the backend
    # does not retry it.
    caplog.set_level(logging.DEBUG)
    script, post = profile_api()
    raw = json.dumps({"text": f"{SECRET} \ud800 fin"})
    response = post(
        content=raw.encode(), headers={**HEADERS, "Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json() == {
        "detail": "The company profile text has a lone surrogate at position 22; it is not valid Unicode."
    }
    assert SECRET not in response.text
    assert SECRET not in caplog.text
    assert script.bodies == []


def test_candidate_values_rules(profile_api) -> None:
    # PRF-009.
    script, post = profile_api("ok")
    text = {"text": "Empresa de transporte de carga."}
    for candidates in (
        {"scope": ["Laboral | Minería"]},
        {"scope": [f"Etiqueta {index}" for index in range(61)]},
        {"activity_action": ["Transporte de carga"]},
    ):
        assert post(json={**text, "candidate_values": candidates}).status_code == 422
    assert script.bodies == []

    repeated = {
        "scope": ["Laboral", "Laboral", "Medio Ambiente"],
        "territorial_coverage": ["Nacional"],
    }
    assert post(json={**text, "candidate_values": repeated}).status_code == 200
    assert label_lines(script.prompts[0]) == [
        "scope: Laboral | Medio Ambiente",
        "territorial_coverage: Nacional",
    ]


def test_the_existing_labels_go_before_the_description_and_only_with_candidates(
    profile_api,
) -> None:
    script, post = profile_api("ok", "ok", "ok", "ok")
    text = "Empresa de transporte de carga."
    post(json={"text": text, "candidate_values": {"scope": ["Laboral"]}})
    post(json={"text": text})
    post(json={"text": text, "candidate_values": None})
    post(
        json={
            "text": text,
            "candidate_values": {
                "scope": [],
                "productive_sector": [],
                "territorial_coverage": [],
            },
        }
    )

    with_labels, *without_labels = script.prompts
    assert with_labels.index("--- EXISTING LABELS") < with_labels.index(
        "--- COMPANY DESCRIPTION ---"
    )
    assert len(without_labels) == 3
    assert all("EXISTING LABELS" not in prompt for prompt in without_labels)
    assert all(
        prompt.startswith("--- COMPANY DESCRIPTION ---\n") for prompt in without_labels
    )


def test_images_and_attachments_are_removed_from_the_description(profile_api) -> None:
    script, post = profile_api("ok")
    text = (
        f"Planta en Pudahuel. ![plano]({IMAGE}) Anexo: {PDF} Tenemos 120 trabajadores."
    )

    assert post(json={"text": text}).status_code == 200
    assert description(script.prompts[0]) == (
        "Planta en Pudahuel. [imagen omitida] Anexo: [archivo omitido] Tenemos 120 trabajadores."
    )


def test_api_key_and_configuration(monkeypatch, profile_api) -> None:
    # PRF-010.
    script, post = profile_api("ok")
    for headers in ({}, {"X-API-Key": "otra-clave"}):
        response = post(json={"text": "Empresa."}, headers=headers)
        assert (response.status_code, response.json()) == (
            401,
            {"detail": "Missing or invalid X-API-Key."},
        )

    monkeypatch.delenv("OPENAI_API_KEY")
    reset_wiring_cache()
    response = post(json={"text": "Empresa."})
    assert (response.status_code, response.json()) == (
        503,
        {"detail": "OPENAI_API_KEY is not configured."},
    )

    monkeypatch.delenv("SERVICE_API_KEY")
    reset_wiring_cache()
    response = post(json={"text": "Empresa."})
    assert (response.status_code, response.json()) == (
        503,
        {"detail": "SERVICE_API_KEY is not configured."},
    )
    assert script.bodies == []


@pytest.mark.parametrize(
    ("step", "detail"),
    [
        (
            "500",
            "OpenAI error while classifying a company profile: InternalServerError (HTTP 500)",
        ),
        ("refusal", f"{INVALID_OUTPUT} The model refused."),
    ],
)
def test_a_provider_failure_never_repeats_the_text(
    profile_api, caplog, step: str, detail: str
) -> None:
    # PRF-011.
    caplog.set_level(logging.DEBUG)
    _, post = profile_api(step)
    text = f"Somos {SECRET}."
    response = post(json={"text": text})

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    for mark in (SECRET, OUTPUT_MARK):
        assert mark not in response.text
        assert mark not in caplog.text
    assert (
        f"Company profile classification failed ({len(text)} characters)" in caplog.text
    )


def test_an_unexpected_failure_reports_only_its_type(monkeypatch, caplog) -> None:
    # PRF-011. Any ValueError other than a rejected text is a 502 with the type alone: before, it was a 422
    # with the error message, which could carry the text.
    class FailingClassifier:
        version = "profile-v1@fake-model"

        def classify(self, text: str, candidates=None):
            raise ValueError(f"Could not classify {text}")

    caplog.set_level(logging.DEBUG)
    configure(monkeypatch)
    app = create_app()
    app.dependency_overrides[get_profile_classifier] = lambda: FailingClassifier()
    with TestClient(app) as client:
        response = client.post(PATH, headers=HEADERS, json={"text": f"Somos {SECRET}."})

    assert response.status_code == 502
    assert response.json() == {"detail": "ValueError"}
    assert SECRET not in response.text
    assert SECRET not in caplog.text


@pytest.mark.parametrize("step", ["invalid", "not_json", "bad_body"])
def test_an_invalid_model_output_is_502_without_the_text(
    profile_api, caplog, step: str
) -> None:
    # PRF-014. parse() raises a ValidationError (invalid, not_json) or a JSONDecodeError (bad_body). Both
    # are ValueError: before, they were a permanent 422 that could repeat the output in detail.
    caplog.set_level(logging.DEBUG)
    script, post = profile_api(step)
    response = post(json={"text": f"Somos {SECRET}."})

    assert response.status_code == 502
    assert response.json() == {"detail": INVALID_OUTPUT}
    for mark in (SECRET, OUTPUT_MARK):
        assert mark not in response.text
        assert mark not in caplog.text
    assert len(script.bodies) == 1


@pytest.mark.parametrize("step", ["invalid", "not_json", "bad_body"])
def test_the_invalid_output_error_carries_no_cause(profile_api, step: str) -> None:
    # PRF-014. The router logs this failure without its traceback; the error does not chain the original
    # one either, so no traceback of it can print the output.
    profile_api(step)
    classifier = OpenAICompanyProfileClassifierAdapter(
        api_key="sk-test-fake",
        model="fake-model",
        reasoning_effort="low",
        max_retries=0,
    )
    with pytest.raises(ClassificationError) as raised:
        classifier.classify(f"Somos {SECRET}.")

    assert str(raised.value) == INVALID_OUTPUT
    assert raised.value.__cause__ is None
    printed = "".join(traceback.format_exception(raised.value))
    for mark in (SECRET, OUTPUT_MARK):
        assert mark not in printed


def test_dev_metrics_repeat_the_usage(monkeypatch, profile_api) -> None:
    # PRF-012.
    _, post = profile_api("ok", "ok")
    body = post(json={"text": "Empresa de transporte de carga."}).json()

    metrics = body["dev_metrics"]
    assert metrics["elapsed_ms"] >= 0
    assert {key: metrics[key] for key in USAGE_KEYS} == {
        key: body["usage"][key] for key in USAGE_KEYS
    }

    monkeypatch.setenv("INCLUDE_DEV_METRICS", "false")
    reset_wiring_cache()
    without_metrics = post(json={"text": "Empresa de transporte de carga."}).json()
    assert without_metrics["dev_metrics"] is None
    assert without_metrics["usage"] == body["usage"]
