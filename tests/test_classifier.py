"""The article classifier and POST /api/v1/legal-bodies/classify, without OpenAI or PostgreSQL.

The adapter's OpenAI client gets an httpx2.MockTransport: every model call is answered by the next step
of a script, and the test reads the prompt each call received. The repository reads a fake pool with the
rows the test writes. Nothing leaves the process and no token is spent. OUTPUT_MARK marks the model
output: when a batch fails, it appears neither in the response nor in the log.
"""

import json
import logging
from contextlib import contextmanager

import httpx2
import pytest
from chat_http import HEADERS, configure
from fastapi.testclient import TestClient
from openai import OpenAI

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import (
    get_classifier,
    get_db_pool,
    reset_wiring_cache,
)
from ia_cumplify.adapters.inbound.http.routers.articles import get_classify_use_case
from ia_cumplify.adapters.outbound.openai import classifier as classifier_module
from ia_cumplify.adapters.outbound.openai.classifier import (
    OpenAIArticleClassifierAdapter,
)

PATH = "/api/v1/legal-bodies/classify"
BODY_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
TARGETS_MARKER = "--- ARTICLES TO CLASSIFY ---"
OUTPUT_MARK = "MARCA-de-la-salida-del-modelo"
# What every scripted completion reports, as OpenAI does in completion.usage.
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
# completion() leaves the usage key out.
NO_USAGE = object()


def usage_of(calls: int) -> dict:
    """The usage of the response when `calls` model calls reported CALL_USAGE."""
    return {
        "prompt_tokens": 1000 * calls,
        "completion_tokens": 100 * calls,
        "total_tokens": 1100 * calls,
        "cached_tokens": 640 * calls,
    }


class FakePool:
    """What PostgresLegalBodyRepository uses of a psycopg pool: the connection, the cursor and the rows."""

    def __init__(
        self, articles: list[dict], summary: str | None = "Resumen de la ley."
    ) -> None:
        self.legal_body = {
            "id": BODY_ID,
            "title": "Ley de prueba",
            "summary": summary,
            "type": "Ley",
        }
        self.articles = articles
        self.statements: list[str] = []

    @contextmanager
    def connection(self):
        yield self

    @contextmanager
    def cursor(self, row_factory=None):
        yield self

    def execute(self, query: str, params: tuple) -> None:
        self.statements.append(" ".join(query.split()))

    def fetchone(self) -> dict:
        return self.legal_body

    def fetchall(self) -> list[dict]:
        return self.articles


def article(index: int, **changes: object) -> dict:
    row = {
        "id": f"a{index:03d}",
        "legal_body_id": BODY_ID,
        "number": f"Artículo {index}",
        "section": "Párrafo 1°",
        "text": f"Texto del artículo {index}.",
        "order": index,
    }
    row.update(changes)
    return row


def legal_body(targets: int) -> list[dict]:
    """A heading, which is context but not a target, and `targets` classifiable articles."""
    return [article(0, number="Encabezado")] + [
        article(index) for index in range(1, targets + 1)
    ]


def classification() -> dict:
    return {
        "scope": ["Laboral"],
        "productive_sector": ["No especificado"],
        "territorial_coverage": ["Nacional"],
        "activity_action": ["Registro de trabajadores"],
        "facility_installation_equipment": ["No especificado"],
        "others": ["No especificado"],
    }


def completion(
    content: str | None,
    *,
    finish: str = "stop",
    refusal: str | None = None,
    choices: list | None = None,
    usage: object = CALL_USAGE,
) -> httpx2.Response:
    body = {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": 1,
        "model": "fake-model",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "refusal": refusal,
                },
            }
        ]
        if choices is None
        else choices,
    }
    if usage is not NO_USAGE:
        body["usage"] = usage
    return httpx2.Response(200, json=body)


def target_ids(prompt: str) -> list[str]:
    targets = prompt.split(TARGETS_MARKER, 1)[1]
    return [
        line.split(": ", 1)[1]
        for line in targets.splitlines()
        if line.startswith("Article ID: ")
    ]


class ScriptedOpenAI:
    """Answers each request to chat/completions with the next step of the script.

    ok: every requested article. omit: all but the first. refusal: a refusal. length and content_filter: an
    answer cut for that reason. invalid: JSON that does not fit the schema. not_json: content that is not
    JSON. bad_body: an HTTP body that is not JSON. 500: a provider error. timeout: no answer in time.
    Malformed 200s: html (a body that is not JSON, as a proxy would send), empty_choices, null_message,
    bad_utf8 (a body that is not UTF-8), list_body (a JSON list) and nested_body (JSON nested deeper than
    the decoder allows). Usage blocks: malformed_usage, refusal_malformed_usage and invalid_malformed_usage
    answer like ok, refusal and invalid with MALFORMED_USAGE; no_usage and null_usage answer like invalid,
    without the usage key or with a null usage. The refusals, the invalid outputs and not_json carry
    OUTPUT_MARK.
    """

    def __init__(self, *steps: str) -> None:
        self.steps = list(steps)
        self.bodies: list[dict] = []
        self.timeouts: list[dict] = []

    @property
    def prompts(self) -> list[str]:
        return [body["messages"][1]["content"] for body in self.bodies]

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.timeouts.append(request.extensions["timeout"])
        step = self.steps.pop(0)
        ids = target_ids(body["messages"][1]["content"])
        usage = {
            "malformed_usage": MALFORMED_USAGE,
            "refusal_malformed_usage": MALFORMED_USAGE,
            "invalid_malformed_usage": MALFORMED_USAGE,
            "no_usage": NO_USAGE,
            "null_usage": None,
        }.get(step, CALL_USAGE)
        if step in ("ok", "omit", "malformed_usage"):
            kept = ids[1:] if step == "omit" else ids
            results = [
                {"article_id": item, "classification": classification()}
                for item in kept
            ]
            return completion(json.dumps({"results": results}), usage=usage)
        if step in ("refusal", "refusal_malformed_usage"):
            return completion(
                None, refusal=f"No puedo clasificar «{OUTPUT_MARK}».", usage=usage
            )
        if step == "length":
            return completion('{"results": [', finish="length")
        if step == "content_filter":
            return completion(None, finish="content_filter")
        if step in ("invalid", "invalid_malformed_usage", "no_usage", "null_usage"):
            results = [
                {
                    "article_id": ids[0],
                    "classification": {**classification(), "scope": OUTPUT_MARK},
                }
            ]
            return completion(json.dumps({"results": results}), usage=usage)
        if step == "not_json":
            return completion(f"Estas son las etiquetas de {OUTPUT_MARK}.")
        if step == "bad_body":
            return httpx2.Response(
                200,
                content=b'{"id": "chatcmpl-fake", "choices": [',
                headers={"content-type": "application/json"},
            )
        if step == "html":
            return httpx2.Response(
                200,
                content=b"<html>Proxy error</html>",
                headers={"content-type": "text/html"},
            )
        if step == "empty_choices":
            return completion(None, choices=[])
        if step == "null_message":
            return completion(
                None, choices=[{"index": 0, "finish_reason": "stop", "message": None}]
            )
        if step == "bad_utf8":
            return httpx2.Response(
                200,
                content=b'{"id": "chatcmpl-\xff\xfe"}',
                headers={"content-type": "application/json"},
            )
        if step == "list_body":
            # A usage block inside a list is not the usage of a completion.
            return httpx2.Response(200, json=[{"usage": CALL_USAGE}])
        if step == "nested_body":
            return httpx2.Response(
                200,
                content=b"[" * 200_000,
                headers={"content-type": "application/json"},
            )
        if step == "500":
            # retry-after-ms keeps the SDK's retries fast.
            return httpx2.Response(
                500,
                json={"error": {"message": "fake", "type": "server_error"}},
                headers={"retry-after-ms": "1"},
            )
        if step == "timeout":
            raise httpx2.ReadTimeout("fake timeout", request=request)
        raise AssertionError(f"Unknown step {step}")


@pytest.fixture
def openai_script(monkeypatch):
    """Sends every OpenAI client the classifier builds to the given script. Returns the clients built."""

    def install(script: ScriptedOpenAI) -> list[OpenAI]:
        clients: list[OpenAI] = []

        def build(**kwargs: object) -> OpenAI:
            transport = httpx2.MockTransport(script.handler)
            client = OpenAI(**kwargs, http_client=httpx2.Client(transport=transport))  # type: ignore[arg-type]
            clients.append(client)
            return client

        monkeypatch.setattr(classifier_module, "OpenAI", build)
        return clients

    return install


@pytest.fixture
def adapter(monkeypatch, openai_script):
    """An adapter with batches of 2 and no SDK retries, answered by the script."""

    def build(*steps: str) -> tuple[OpenAIArticleClassifierAdapter, ScriptedOpenAI]:
        configure(monkeypatch)
        script = ScriptedOpenAI(*steps)
        openai_script(script)
        classifier = OpenAIArticleClassifierAdapter(
            api_key="sk-test-fake",
            model="fake-model",
            reasoning_effort="low",
            batch_size=2,
            max_retries=0,
        )
        return classifier, script

    return build


def post(pool: FakePool, classifier=None, *, headers: dict = HEADERS):
    app = create_app()
    app.dependency_overrides[get_db_pool] = lambda: pool
    if classifier is not None:
        app.dependency_overrides[get_classifier] = lambda: classifier
    with TestClient(app) as client:
        return client.post(PATH, headers=headers, json={"legal_body_id": BODY_ID})


def classified_ids(response) -> list[str]:
    return [item["article_id"] for item in response.json()["results"]]


def full_body(prompt: str) -> str:
    return prompt.split(TARGETS_MARKER, 1)[0]


def only_reads(pool: FakePool) -> bool:
    """The service reads the legal body and its articles, and writes nothing."""
    return [statement.split()[0] for statement in pool.statements] == [
        "SELECT",
        "SELECT",
    ]


def test_a_failed_batch_in_the_middle_keeps_the_others(adapter) -> None:
    classifier, script = adapter("ok", "500", "ok")
    pool = FakePool(legal_body(5))
    response = post(pool, classifier)

    assert response.status_code == 200, response.text
    body = response.json()
    assert classified_ids(response) == ["a001", "a002", "a005"]
    assert body["failed_article_ids"] == ["a003", "a004"]
    # The failed batch counts its call; the provider reported no tokens for it.
    assert body["usage"] == {**usage_of(2), "llm_calls": 3}
    assert [target_ids(prompt) for prompt in script.prompts] == [
        ["a001", "a002"],
        ["a003", "a004"],
        ["a005"],
    ]
    # Every batch gets the whole legal body as context, the heading included.
    for prompt in script.prompts:
        assert all(
            f"Article ID: a{index:03d}" in full_body(prompt) for index in range(6)
        )
    assert only_reads(pool)


def test_an_omitted_article_goes_to_failed_article_ids(adapter) -> None:
    classifier, _ = adapter("omit", "ok", "ok")
    response = post(FakePool(legal_body(5)), classifier)

    assert response.status_code == 200, response.text
    assert classified_ids(response) == ["a002", "a003", "a004", "a005"]
    assert response.json()["failed_article_ids"] == ["a001"]
    assert response.json()["usage"] == {**usage_of(3), "llm_calls": 3}


@pytest.mark.parametrize(
    ("step", "billed"),
    [
        ("invalid", True),
        ("not_json", True),
        ("bad_body", False),
        ("html", False),
        ("empty_choices", True),
        ("null_message", True),
        ("bad_utf8", False),
        ("no_usage", False),
        ("null_usage", False),
        ("list_body", False),
        ("nested_body", False),
    ],
)
def test_an_answer_that_is_not_a_classification_fails_only_its_batch(
    adapter, step: str, billed: bool
) -> None:
    # parse() raises a ValidationError (invalid, not_json) or a JSONDecodeError (bad_body): before, they
    # left classify_many, lost the batches already paid for and ended in a permanent 422. The malformed
    # 200s escaped too (an AttributeError, an IndexError, a UnicodeDecodeError or a RecursionError) and
    # failed the whole legal body.
    classifier, _ = adapter("ok", step, "ok")
    response = post(FakePool(legal_body(5)), classifier)

    assert response.status_code == 200, response.text
    assert classified_ids(response) == ["a001", "a002", "a005"]
    assert response.json()["failed_article_ids"] == ["a003", "a004"]
    # A body with a usage block counts its tokens: the provider billed the call. Without one, only the
    # call counts.
    assert response.json()["usage"] == {**usage_of(3 if billed else 2), "llm_calls": 3}


# parse() dumps the completion it built, and pydantic warns that the malformed counts are not int.
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
@pytest.mark.parametrize(
    ("step", "failed"),
    [
        ("malformed_usage", []),
        ("refusal_malformed_usage", ["a003", "a004"]),
        ("invalid_malformed_usage", ["a003", "a004"]),
    ],
)
def test_an_unreadable_usage_count_is_zero(
    adapter, step: str, failed: list[str]
) -> None:
    # The SDK builds the usage block without validating it. Each count is read on its own, as in the chat:
    # one that is not a positive integer counts 0, and it fails neither its batch nor the legal body.
    classifier, _ = adapter("ok", step, "ok")
    response = post(FakePool(legal_body(5)), classifier)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["failed_article_ids"] == failed
    assert len(body["results"]) == 5 - len(failed)
    # The middle batch adds only completion_tokens, the one count of MALFORMED_USAGE that can be read.
    assert body["usage"] == {**usage_of(2), "completion_tokens": 300, "llm_calls": 3}


@pytest.mark.parametrize("step", ["refusal", "length", "content_filter"])
def test_a_billed_failed_batch_counts_its_tokens(adapter, step: str) -> None:
    classifier, _ = adapter("ok", step, "ok")
    response = post(FakePool(legal_body(5)), classifier)

    assert response.status_code == 200, response.text
    assert response.json()["failed_article_ids"] == ["a003", "a004"]
    # The provider answered and billed the failed batch: its tokens count with the other two.
    assert response.json()["usage"] == {**usage_of(3), "llm_calls": 3}


@pytest.mark.parametrize(
    ("step", "tokens"),
    [("500", 0), ("refusal", 3300), ("invalid", 3300), ("bad_body", 0)],
)
def test_every_batch_failing_is_502(adapter, step: str, tokens: int) -> None:
    classifier, _ = adapter(step, step, step)
    pool = FakePool(legal_body(5))
    response = post(pool, classifier)

    assert response.status_code == 502
    assert response.json()["detail"].startswith(
        f"No article could be classified for legal body {BODY_ID} (3 model calls, {tokens} tokens counted): "
    )
    assert only_reads(pool)


@pytest.mark.parametrize(
    ("step", "reason"),
    [
        ("invalid", ": ValidationError."),
        ("not_json", ": ValidationError."),
        ("refusal", ". The model refused."),
    ],
)
def test_the_model_output_never_reaches_the_response_or_the_log(
    adapter, caplog, step: str, reason: str
) -> None:
    # The 502 repeats the error of the last batch, and every failed batch is logged with its traceback:
    # neither carries the output, nor the error that quotes it.
    caplog.set_level(logging.DEBUG)
    classifier, _ = adapter(step, step, step)
    response = post(FakePool(legal_body(5)), classifier)

    assert response.status_code == 502
    assert response.json()["detail"].endswith(
        f"Model did not return a valid classification for legal body {BODY_ID}{reason}"
    )
    assert OUTPUT_MARK not in response.text
    assert OUTPUT_MARK not in caplog.text
    if step == "invalid":
        # The log keeps where the output broke the schema, without the value.
        assert "list_type at results.0.classification.scope" in caplog.text


def test_more_articles_than_the_batch_size(monkeypatch, openai_script) -> None:
    # CLS-009: CLASSIFY_BATCH_SIZE in its default of 25, through the real wiring of get_classifier.
    configure(monkeypatch, openai_api_key="sk-test-fake")
    script = ScriptedOpenAI("ok", "500", "ok")
    openai_script(script)
    response = post(FakePool(legal_body(51)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert [len(target_ids(prompt)) for prompt in script.prompts] == [25, 25, 1]
    assert classified_ids(response) == [
        f"a{index:03d}" for index in [*range(1, 26), 51]
    ]
    assert body["failed_article_ids"] == [f"a{index:03d}" for index in range(26, 51)]
    assert body["usage"]["llm_calls"] == 3
    for prompt in script.prompts:
        assert all(
            f"Article ID: a{index:03d}" in full_body(prompt) for index in range(52)
        )


def test_usage_adds_up_every_batch_and_dev_metrics_repeats_it(
    monkeypatch, openai_script
) -> None:
    # CLS-012: INCLUDE_DEV_METRICS on (its default) and more than 25 classifiable articles.
    configure(monkeypatch, openai_api_key="sk-test-fake")
    openai_script(ScriptedOpenAI("ok", "ok", "ok"))
    response = post(FakePool(legal_body(51)))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usage"] == {**usage_of(3), "llm_calls": 3}
    assert body["classifier_version"] == "classify-v2@gpt-5.6-luna"
    metrics = body["dev_metrics"]
    assert metrics["elapsed_ms"] >= 0
    assert {
        key: metrics[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_calls")
    } == {
        key: body["usage"][key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "llm_calls")
    }

    monkeypatch.setenv("INCLUDE_DEV_METRICS", "false")
    openai_script(ScriptedOpenAI("ok", "ok", "ok"))
    reset_wiring_cache()
    without_metrics = post(FakePool(legal_body(51)))
    assert without_metrics.json()["dev_metrics"] is None
    assert without_metrics.json()["usage"] == body["usage"]


def test_no_classifiable_article_does_not_call_the_model(adapter) -> None:
    # CLS-013: only structural pieces.
    classifier, script = adapter()
    rows = [
        article(1, number="Encabezado"),
        article(2, number="Promulgación"),
        article(3, number="Título I"),
    ]
    response = post(FakePool(rows), classifier)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == []
    assert body["failed_article_ids"] == []
    assert body["usage"] == {**usage_of(0), "llm_calls": 0}
    assert body["classifier_version"] == "classify-v2@fake-model"
    assert script.bodies == []


def test_images_and_attachments_never_reach_the_model(adapter) -> None:
    # CLS-014, and data URIs of other types, such as a PDF if the BCN attaches one to an article.
    image = "data:image/png;base64," + "A" * 298 + "=="
    # Wrapped in lines of 76 characters, as MIME does: the last one is short and ends with the padding.
    wrapped = "\n".join(image[start : start + 76] for start in range(0, len(image), 76))
    pdf = (
        "data:application/pdf;base64,"
        + "JVBERi0xLjQKJcfsj6IKNSAwIG9iago8PC9MZW5ndGggNiAwIFIvRmlsdGVyIC9GbGF0ZURl" * 4
    )
    text = (
        f'Plano: ![plano]({image}) Figura: <img src="{image}"> Suelta: {wrapped} El empleador debe informar'
        f" al trabajador. Anexo: ![anexo [1].pdf]({pdf}) Copia: {pdf} 12 de marzo."
    )
    classifier, script = adapter("ok")
    response = post(
        FakePool(
            [article(1, text=text)], summary=f"Resumen con ![logo]({image}) al final."
        ),
        classifier,
    )

    assert response.status_code == 200, response.text
    [prompt] = script.prompts
    assert "base64" not in prompt
    # The article travels twice: in the legal body and among the targets.
    assert prompt.count("[imagen omitida]") == 1 + 2 * 3
    assert prompt.count("[archivo omitido]") == 2 * 2
    assert prompt.count("El empleador debe informar al trabajador.") == 2
    assert prompt.count("Copia: [archivo omitido] 12 de marzo.") == 2
    assert "Resumen con [imagen omitida] al final." in prompt


def test_a_null_number_or_section_is_empty_text(adapter) -> None:
    # Both columns are nullable in the backend. Before, a null number failed the whole legal body with 502.
    classifier, script = adapter("ok")
    rows = [article(1, number=None), article(2, section=None)]
    response = post(FakePool(rows), classifier)

    assert response.status_code == 200, response.text
    assert [
        (item["article_id"], item["number"]) for item in response.json()["results"]
    ] == [
        ("a001", ""),
        ("a002", "Artículo 2"),
    ]
    [prompt] = script.prompts
    assert "None" not in prompt
    assert "Article ID: a001\nNumber: \nSection: Párrafo 1°" in prompt
    assert "Number: Artículo 2\nSection: \nOrder: 2" in prompt


def test_the_client_uses_the_configured_timeout_and_retries(
    monkeypatch, openai_script
) -> None:
    # Without OPENAI_TIMEOUT_SECONDS or OPENAI_MAX_RETRIES: 180 s per call and 2 SDK retries.
    configure(monkeypatch, openai_api_key="sk-test-fake")
    monkeypatch.delenv("OPENAI_MAX_RETRIES")
    script = ScriptedOpenAI("500", "500", "ok")
    clients = openai_script(script)
    response = post(FakePool(legal_body(3)))

    assert response.status_code == 200, response.text
    [client] = clients
    assert (client.timeout, client.max_retries) == (180, 2)
    # The SDK retried the batch twice and the third attempt answered.
    assert len(script.bodies) == 3
    assert classified_ids(response) == ["a001", "a002", "a003"]
    assert all(timeout["read"] == 180 for timeout in script.timeouts)


def test_a_timeout_fails_only_its_batch(monkeypatch, openai_script) -> None:
    configure(
        monkeypatch,
        openai_api_key="sk-test-fake",
        openai_timeout_seconds="42",
        classify_batch_size="2",
    )
    script = ScriptedOpenAI("timeout", "ok", "ok")
    openai_script(script)
    response = post(FakePool(legal_body(5)))

    assert response.status_code == 200, response.text
    assert response.json()["failed_article_ids"] == ["a001", "a002"]
    assert classified_ids(response) == ["a003", "a004", "a005"]
    assert [timeout["read"] for timeout in script.timeouts] == [42, 42, 42]


def test_an_unexpected_value_error_is_502(monkeypatch) -> None:
    # The request is validated before the use case, so a ValueError from it is never a 422.
    class FailingUseCase:
        def execute(self, legal_body_id: str, candidates=None):
            raise ValueError("unexpected")

    configure(monkeypatch)
    app = create_app()
    app.dependency_overrides[get_classify_use_case] = lambda: FailingUseCase()
    with TestClient(app) as client:
        response = client.post(PATH, headers=HEADERS, json={"legal_body_id": BODY_ID})

    assert response.status_code == 502
    assert response.json() == {"detail": "ValueError: unexpected"}


def test_missing_or_wrong_api_key_is_401(adapter) -> None:
    # CLS-015.
    classifier, script = adapter("ok")
    pool = FakePool(legal_body(1))
    for headers in ({}, {"X-API-Key": "otra-clave"}):
        response = post(pool, classifier, headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "Missing or invalid X-API-Key."}
    assert pool.statements == []
    assert script.bodies == []


def test_missing_service_api_key_is_503() -> None:
    # CLS-015: without SERVICE_API_KEY no request passes, not even one with a key.
    response = post(FakePool(legal_body(1)))
    assert response.status_code == 503
    assert response.json() == {"detail": "SERVICE_API_KEY is not configured."}


def test_missing_database_url_or_openai_key_is_503(monkeypatch) -> None:
    # CLS-007.
    configure(monkeypatch, openai_api_key="sk-test-fake")
    with TestClient(create_app()) as client:
        response = client.post(PATH, headers=HEADERS, json={"legal_body_id": BODY_ID})
    assert response.status_code == 503
    assert response.json() == {"detail": "DATABASE_URL is not configured."}

    monkeypatch.delenv("OPENAI_API_KEY")
    reset_wiring_cache()
    response = post(FakePool(legal_body(1)))
    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY is not configured."}
