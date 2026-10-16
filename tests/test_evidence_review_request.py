"""POST /api/v1/evidence-reviews accepts the article, its regulation, the row obligation
and the files, and returns the review.
"""

import asyncio
import json

import pytest
from chat_http import HEADERS, SECRET, configure
from fastapi.testclient import TestClient

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.dependencies import get_db_pool, get_evidence_reviewer
from ia_cumplify.adapters.inbound.http.routers import evidence_reviews
from ia_cumplify.adapters.inbound.http.schemas.evidence_review import EvidenceReviewResponse, parse_evidence_review
from ia_cumplify.adapters.outbound.documents.text_extractor import extract_text
from ia_cumplify.application.use_cases.review_evidence import assemble_evidence_review
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.evidence_review import ReviewCitation, ReviewJudgment, ReviewRequirement
from ia_cumplify.domain.evidence_text import ReadLimits
from ia_cumplify.domain.exceptions import EvidenceReviewError

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


def test_the_response_reports_each_file_and_the_judgment(monkeypatch):
    configure(monkeypatch)
    reviewer = ScriptedReviewer(_plain_judgment())
    response = _post(
        _review(
            text=SECRET,
            files=[
                {"id": "file-1", "filename": "nomina.csv"},
                {"id": "file-2", "filename": "foto.png"},
                {"id": "file-3", "filename": "notas.txt"},
                {"id": "file-4", "filename": "roto.pdf"},
            ],
            obligation={"name": "Capacitación", "description": SECRET},
        ),
        [
            ("nomina.csv", b"Nombre;RUT\nAna;1-9\n"),
            ("foto.png", b"\x89PNG\r\n\x1a\nnot-parsed"),
            ("notas.txt", b"texto que no se lee"),
            ("roto.pdf", b"this is not a pdf"),
        ],
        reviewer=reviewer,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["files"] == [
        {"id": "file-1", "filename": "nomina.csv", "status": "read", "truncated": False, "detail": None},
        {"id": "file-2", "filename": "foto.png", "status": "unreadable", "truncated": False, "detail": None},
        {"id": "file-3", "filename": "notas.txt", "status": "unsupported", "truncated": False, "detail": None},
        {
            "id": "file-4",
            "filename": "roto.pdf",
            "status": "error",
            "truncated": False,
            "detail": body["files"][3]["detail"],
        },
    ]
    assert body["files"][3]["detail"]
    assert body["nature"] == "informative"
    assert body["requirements"] == []
    assert body["verdict"] == "not_evaluable"
    assert body["summary"] == ""
    assert body["usage"] == _zero_usage()
    assert body["model"] == "gpt-5.6-luna"
    assert body["review_version"] == "evidence-review-v2@gpt-5.6-luna"
    assert reviewer.subjects[0].files[0].fragments[0].text.startswith("Nombre")
    assert SECRET not in response.text
    assert "Nombre;RUT" not in response.text


def test_a_judgment_is_returned_with_its_citations(monkeypatch):
    configure(monkeypatch)
    response = _post(
        _review(text=SECRET, files=[{"id": "file-1", "filename": "nomina.csv"}]),
        [("nomina.csv", "Nombre;RUT\nAna;1-9\nEn la faena Calama, el 15 de marzo de 2026\n".encode())],
        reviewer=ScriptedReviewer(_judgment()),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["files"][0]["status"] == "read"
    assert body["nature"] == "explicit_obligations"
    assert body["verdict"] == "partial"
    assert body["summary"] == "El acta acredita la constitución y falta la nómina completa."
    assert body["model"] == "gpt-5.6-luna"
    assert body["review_version"] == "evidence-review-v2@gpt-5.6-luna"
    assert body["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28,
        "cached_tokens": 0,
        "llm_calls": 1,
    }
    assert body["requirements"] == [
        {
            "text": "El acta indica la fecha, la faena y a quién representa cada integrante.",
            "evidence": "Acta de constitución",
            "status": "accredited",
            "missing": "",
            "citations": [
                {
                    "file_id": "file-1",
                    "locator": "page",
                    "reference": "1",
                    "quote": "En la faena Calama, el 15 de marzo de 2026",
                }
            ],
        },
        {
            "text": "Cada integrante figura con su nombre, su RUT y la fecha de su última capacitación.",
            "evidence": "Registro de capacitaciones",
            "status": "partial",
            "missing": "Falta la fecha de la última capacitación.",
            "citations": [
                {"file_id": "file-1", "locator": "sheet", "reference": "Nomina", "quote": "Ana;1-9"},
                {"file_id": "file-1", "locator": "body", "reference": "", "quote": "Nombre;RUT"},
            ],
        },
    ]
    assert SECRET not in response.text
    assert "file-2" not in response.text


def test_a_file_past_the_character_cap_is_marked_in_the_response():
    request = parse_evidence_review(
        json.dumps(_review(files=[{"id": "file-1", "filename": "lista.csv"}])),
        [("lista.csv", b"abcdefghij\n")],
    )
    extracted = (extract_text(".csv", request.files[0].content, ReadLimits(max_chars=4)),)
    result = assemble_evidence_review(request, extracted, None)
    body = EvidenceReviewResponse.from_domain(result).model_dump()

    assert body["files"] == [
        {"id": "file-1", "filename": "lista.csv", "status": "read", "truncated": True, "detail": None}
    ]
    assert "abcdefghij" not in EvidenceReviewResponse.from_domain(result).model_dump_json()


def test_a_file_that_passes_the_size_cap_is_not_kept(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(evidence_reviews, "MAX_TOTAL_BYTES", 4)
    response = _post(_review(), [("acta.pdf", b"12345")])

    assert response.status_code == 422
    assert response.json() == {"detail": "The files are over the limit of 4 bytes."}
    assert SECRET not in response.text


def test_the_review_works_without_a_database(monkeypatch):
    configure(monkeypatch, database_url="")
    app = _open_app()
    with TestClient(app) as client:
        assert app.state.db_pool is None
        response = client.post(REVIEW, headers=HEADERS, data={"review": json.dumps(_review(text=SECRET))})
    assert response.status_code == 200
    assert response.json()["review_version"] == "evidence-review-v2@gpt-5.6-luna"
    assert response.json()["files"] == []
    assert SECRET not in response.text
    included, route = _evidence_route(app)
    assert get_db_pool not in _dependency_calls(route.dependant)
    assert get_evidence_reviewer in _dependency_calls(route.dependant)
    assert get_db_pool not in [item.dependency for item in included.include_context.dependencies]


def test_a_configured_database_is_not_opened_for_the_review(monkeypatch):
    configure(monkeypatch, database_url="postgresql://db.example/cumplify")
    monkeypatch.setattr("ia_cumplify.adapters.inbound.http.app.ConnectionPool", _IdlePool)
    app = _open_app()
    with TestClient(app) as client:
        pool = app.state.db_pool
        assert isinstance(pool, _IdlePool)
        before = list(pool.operations)
        response = client.post(REVIEW, headers=HEADERS, data={"review": json.dumps(_review())})
        assert response.status_code == 200
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


def test_a_missing_openai_key_is_503_before_the_body(monkeypatch):
    configure(monkeypatch)
    response = _post("{", override=False)
    assert response.status_code == 503
    assert response.json() == {"detail": "OPENAI_API_KEY is not configured."}
    assert SECRET not in response.text


def test_a_provider_failure_is_502_and_keeps_the_request_out_of_the_error(monkeypatch):
    configure(monkeypatch)
    response = _post(_review(text=SECRET), reviewer=_BrokenReviewer())
    assert response.status_code == 502
    assert response.json() == {"detail": "OpenAI error while reviewing evidence: APIError (HTTP 500)"}
    assert SECRET not in response.text


def test_the_reviewer_runs_off_the_event_loop(monkeypatch):
    # A blocking model call on the loop would hold every other request of the process, /health included.
    configure(monkeypatch)
    reviewer = LoopProbeReviewer(_plain_judgment())
    review = _review(files=[{"id": "file-1", "filename": "acta.csv"}])
    response = _post(review, [("acta.csv", b"a;b\n1;2\n")], reviewer=reviewer)
    assert response.status_code == 200
    assert reviewer.on_event_loop is False


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


def _zero_usage() -> dict:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "llm_calls": 0,
    }


def _judgment() -> ReviewJudgment:
    return ReviewJudgment(
        nature="explicit_obligations",
        requirements=(
            ReviewRequirement(
                text="El acta indica la fecha, la faena y a quién representa cada integrante.",
                evidence="Acta de constitución",
                status="accredited",
                citations=(
                    ReviewCitation(
                        file_id="file-1",
                        locator="page",
                        reference="1",
                        quote="En la faena Calama, el 15 de marzo de 2026",
                    ),
                    ReviewCitation(file_id="file-1", locator="page", reference="2", quote="  "),
                ),
                missing="",
            ),
            ReviewRequirement(
                text="Cada integrante figura con su nombre, su RUT y la fecha de su última capacitación.",
                evidence="Registro de capacitaciones",
                status="partial",
                citations=(
                    ReviewCitation(file_id="file-1", locator="sheet", reference="Nomina", quote="Ana;1-9"),
                    ReviewCitation(file_id="file-2", locator="slide", reference="2", quote="Capacitación 2024"),
                    ReviewCitation(file_id="file-1", locator="body", reference="", quote="Nombre;RUT"),
                ),
                missing="Falta la fecha de la última capacitación.",
            ),
        ),
        verdict="partial",
        summary="El acta acredita la constitución y falta la nómina completa.",
        usage=TokenUsage(prompt_tokens=20, completion_tokens=8, total_tokens=28, llm_calls=1),
        model="gpt-5.6-luna",
        review_version="evidence-review-v2@gpt-5.6-luna",
    )


class ScriptedReviewer:
    def __init__(self, judgment: ReviewJudgment) -> None:
        self.judgment = judgment
        self.subjects = []

    @property
    def version(self) -> str:
        return self.judgment.review_version

    def review(self, subject):
        self.subjects.append(subject)
        return self.judgment


class LoopProbeReviewer(ScriptedReviewer):
    """Records whether review() ran on the thread of a running event loop."""

    on_event_loop: bool | None = None

    def review(self, subject):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.on_event_loop = False
        else:
            self.on_event_loop = True
        return super().review(subject)


class _BrokenReviewer:
    version = "evidence-review-v2@gpt-5.6-luna"

    def review(self, subject) -> ReviewJudgment:
        raise EvidenceReviewError("OpenAI error while reviewing evidence: APIError (HTTP 500)")


def _plain_judgment() -> ReviewJudgment:
    return ReviewJudgment(
        nature="informative",
        requirements=(),
        verdict="not_evaluable",
        summary="",
        usage=TokenUsage(),
        model="gpt-5.6-luna",
        review_version="evidence-review-v2@gpt-5.6-luna",
    )


def _open_app(reviewer=None):
    app = create_app()
    app.dependency_overrides[get_evidence_reviewer] = lambda: reviewer or ScriptedReviewer(_plain_judgment())
    return app


def _post(review, uploads: list[tuple[str, bytes]] | None = None, *, headers=HEADERS, reviewer=None, override=True):
    app = create_app()
    if override:
        app.dependency_overrides[get_evidence_reviewer] = lambda: reviewer or ScriptedReviewer(_plain_judgment())
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
