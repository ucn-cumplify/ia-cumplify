"""POST /api/v1/evidence-reviews accepts the article, its regulation, the row obligation
and the files. It does not review them yet.
"""

import json

import pytest
from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import get_db_pool
from ia_cumplify.adapters.inbound.http.routers import evidence_reviews
from ia_cumplify.adapters.inbound.http.schemas.evidence_review import parse_evidence_review

REVIEW = "/api/v1/evidence-reviews"


def test_a_request_carries_the_article_its_regulation_the_date_and_the_files():
    article = "La empresa debe constituir un Comité Paritario.\nLa constitución queda acreditada con un acta."
    parsed = parse_evidence_review(
        json.dumps(
            _review(
                text=article,
                title="  Constitución del Comité Paritario  ",
                section="",
            obligation={
                "name": " Incluir nombre, RUT y fecha ",
                "description": "Cada integrante figura con su nombre.\nLa fecha no puede tener más de dos años.",
            },
            files=[
                {"id": "file-1", "filename": "acta.pdf"},
                {"id": "file-2", "filename": "nomina.csv"},
            ],
            )
        ),
        [
            (r"C:\fakepath\acta.pdf", b"%PDF-acta"),
            ("nomina.csv", "Ana;1-9".encode()),
        ],
    )

    assert parsed.reference_date.isoformat() == "2026-10-14"
    assert parsed.article.number == "6"
    assert parsed.article.title == "Constitución del Comité Paritario"
    assert parsed.article.section is None
    assert parsed.article.text == article
    assert parsed.legal_body.title == "Reglamento de demostración del Comité Paritario"
    assert parsed.legal_body.type == "Reglamento"
    assert parsed.legal_body.number is None
    assert parsed.obligation is not None
    assert parsed.obligation.name == "Incluir nombre, RUT y fecha"
    assert parsed.obligation.description == (
        "Cada integrante figura con su nombre.\nLa fecha no puede tener más de dos años."
    )
    assert [(item.id, item.filename, item.content) for item in parsed.files] == [
        ("file-1", "acta.pdf", b"%PDF-acta"),
        ("file-2", "nomina.csv", b"Ana;1-9"),
    ]


def test_a_row_without_an_obligation_is_accepted():
    for obligation in (None,):
        parsed = parse_evidence_review(json.dumps(_review(obligation=obligation)), [])
        assert parsed.obligation is None
        assert parsed.files == ()


def test_omitting_the_obligation_is_the_same_as_null():
    body = _review()
    del body["obligation"]
    parsed = parse_evidence_review(json.dumps(body), [])
    assert parsed.obligation is None


def test_a_lone_surrogate_in_the_article_is_replaced():
    parsed = parse_evidence_review(json.dumps(_review(text="Acta \ud800 firmada")), [])
    assert parsed.article.text == "Acta \ufffd firmada"


@pytest.mark.parametrize(
    ("uploads", "message"),
    [
        ([("otro.pdf", b"x")], "An uploaded file is not listed in the review."),
        ([("acta.pdf", b"uno"), ("acta.pdf", b"dos")], "The same filename was uploaded more than once."),
        ([(None, b"x")], "Each uploaded file needs a filename."),
        ([], "A file listed in the review was not uploaded."),
    ],
)
def test_files_must_match_the_review_one_to_one(uploads, message):
    review = json.dumps(_review(files=[{"id": "file-1", "filename": "acta.pdf"}]))
    with pytest.raises(ValueError, match=message):
        parse_evidence_review(review, uploads)


def test_eleven_files_and_a_payload_over_25_mb_are_rejected():
    uploads = [(f"{number}.txt", b"x") for number in range(11)]
    with pytest.raises(ValueError, match="The review has 11 files; the limit is 10."):
        parse_evidence_review(json.dumps(_review()), uploads)
    with pytest.raises(ValueError, match="The files are over the limit of 26214400 bytes."):
        parse_evidence_review(json.dumps(_review()), [("grande.bin", b"x" * (25 * 1024 * 1024 + 1))])


def test_the_endpoint_accepts_multipart_and_returns_no_review_yet(monkeypatch):
    configure(monkeypatch)
    response = _post(
        _review(
            text=SECRET,
            files=[{"id": "file-1", "filename": "acta.pdf"}],
            obligation={"name": "Capacitación", "description": SECRET},
        ),
        [("acta.pdf", b"%PDF")],
    )

    assert response.status_code == 204
    assert response.content == b""
    assert SECRET not in response.text


def test_a_file_that_passes_the_size_cap_is_not_kept(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(evidence_reviews, "MAX_TOTAL_BYTES", 4)
    response = _post(_review(), [("acta.pdf", b"12345")])

    assert response.status_code == 422
    assert response.json() == {"detail": "The files are over the limit of 4 bytes."}
    assert SECRET not in response.text


def test_the_review_works_without_a_database(monkeypatch):
    configure(monkeypatch, database_url="")
    app = create_app()
    with TestClient(app) as client:
        assert app.state.db_pool is None
        response = client.post(REVIEW, headers=HEADERS, data={"review": json.dumps(_review(text=SECRET))})
    assert response.status_code == 204
    assert SECRET not in response.text
    included, route = _evidence_route(app)
    assert get_db_pool not in _dependency_calls(route.dependant)
    assert get_db_pool not in [item.dependency for item in included.include_context.dependencies]


def test_a_configured_database_is_not_opened_for_the_review(monkeypatch):
    configure(monkeypatch, database_url="postgresql://db.example/cumplify")
    monkeypatch.setattr("ia_cumplify.adapters.inbound.http.app.ConnectionPool", _IdlePool)
    app = create_app()
    with TestClient(app) as client:
        pool = app.state.db_pool
        assert isinstance(pool, _IdlePool)
        before = list(pool.operations)
        response = client.post(REVIEW, headers=HEADERS, data={"review": json.dumps(_review())})
        assert response.status_code == 204
        assert pool.operations == before
    assert pool.operations == ["close"]


def test_an_id_is_not_accepted_in_place_of_the_article_or_the_regulation(monkeypatch):
    configure(monkeypatch)
    for changes in (
        {"legal_body_id": "e7d00000-0000-4000-8000-000000000001"},
        {"article_id": "e7d00000-0000-4000-8000-000000000011"},
    ):
        response = _post(_review(**changes))
        assert response.status_code == 422
        assert "e7d00000" not in response.text


def test_missing_or_wrong_api_key_is_rejected_before_the_body(monkeypatch):
    configure(monkeypatch)
    body = {"reference_date": "2026-10-14", "article": {"text": SECRET}}
    for headers in ({}, {"X-API-Key": "otra-clave"}):
        response = _post(body, headers=headers)
        assert response.status_code == 401
        assert SECRET not in response.text


def test_a_missing_service_api_key_is_503_before_the_body():
    response = _post({"reference_date": "not-a-date", "article": {"text": SECRET}})
    assert response.status_code == 503
    assert response.json() == {"detail": "SERVICE_API_KEY is not configured."}
    assert SECRET not in response.text


def test_what_is_rejected_does_not_come_back_in_the_error(monkeypatch):
    configure(monkeypatch)
    cases = [
        _post("{", []),
        _post(_review(reference_date="2026-10-14T00:00:00"), []),
        _post({"reference_date": "2026-10-14", "legal_body": {"title": "Norma"}}, []),
        _post(_review(text=SECRET, files=[{"id": "file-1", "filename": "acta.pdf"}]), []),
        _post(_review(extra=SECRET), []),
    ]

    for response in cases:
        assert response.status_code == 422
        assert SECRET not in response.text


def _evidence_route(app):
    for included in app.routes:
        original = getattr(included, "original_router", None)
        if original is not None and original.prefix == "/evidence-reviews":
            return included, original.routes[0]
    raise AssertionError("POST /api/v1/evidence-reviews is not registered")


def _dependency_calls(dependant):
    found = []
    pending = [dependant]
    while pending:
        current = pending.pop()
        found.append(current.call)
        pending.extend(current.dependencies)
    return found


class _IdlePool:
    """Stands in for the process pool. A review must not call it."""

    def __init__(self, *args, **kwargs) -> None:
        self.operations: list[str] = []

    def close(self) -> None:
        self.operations.append("close")


def _post(review, uploads: list[tuple[str, bytes]] | None = None, *, headers=HEADERS):
    app = create_app()
    data = {"review": review if isinstance(review, str) else json.dumps(review)}
    files = [("files", (filename, content, "application/octet-stream")) for filename, content in uploads or []]
    with TestClient(app) as client:
        if files:
            return client.post(REVIEW, headers=headers, data=data, files=files)
        return client.post(REVIEW, headers=headers, data=data)


def _review(**changes) -> dict:
    body = {
        "reference_date": "2026-10-14",
        "article": {
            "number": "6",
            "title": "Constitución del Comité Paritario",
            "text": "La empresa debe constituir un Comité Paritario.",
        },
        "legal_body": {
            "title": "Reglamento de demostración del Comité Paritario",
            "type": "Reglamento",
            "number": None,
        },
        "obligation": None,
        "files": [],
    }
    article = {key: changes.pop(key) for key in ("text", "title", "section") if key in changes}
    if article:
        body["article"].update(article)
    if "obligation" in changes:
        body["obligation"] = changes.pop("obligation")
    body.update(changes)
    return body
