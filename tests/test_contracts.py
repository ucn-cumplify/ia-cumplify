"""Shared contract examples between this service and backend-cumplify (sprint 4 plan, step 1.4).

Each tests/contracts/<name>.json holds one request and one response of an endpoint, as they travel. The
backend keeps a copy in Cumplify.Tests/AI/Contracts/ and runs it through the readers of IaCumplifyClient,
so a field renamed, dropped or retyped here has to change the example, and with it the backend copy.

A model_validate that does not raise is not enough: a response model ignores unknown fields, so an example
that kept a field this service dropped would still validate. Each example must also come back unchanged
from model_dump. exclude_unset leaves out the optional fields the example does not send, so their defaults
do not count as a difference.
"""

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

from ia_cumplify.adapters.inbound.http.app import create_app
from ia_cumplify.adapters.inbound.http.schemas.action_plan import (
    ActionPlanBody,
    ActionPlanResponse,
    FindingTasksResponse,
    ProposedTaskResponse,
    TaskCitationResponse,
)
from ia_cumplify.adapters.inbound.http.schemas.applicability import (
    ArticleAnalysisResponse,
    ArticleObligationResponse,
    ExplainApplicabilityRequest,
    ExplainApplicabilityResponse,
)
from ia_cumplify.adapters.inbound.http.routers.chat import _done_data, _error_data
from ia_cumplify.adapters.inbound.http.schemas.chat import ChatRequestBody
from ia_cumplify.adapters.inbound.http.schemas.classification import (
    ArticleClassificationParameters,
    CandidateValues,
    ClassifyLegalBodyRequest,
    ClassifyLegalBodyResponse,
    TokenUsageResponse,
)
from ia_cumplify.adapters.inbound.http.schemas.company_profile import (
    ClassifyCompanyProfileRequest,
    ClassifyCompanyProfileResponse,
)
from ia_cumplify.adapters.inbound.http.schemas.embeddings import EmbedTextsRequest, EmbedTextsResponse
from ia_cumplify.adapters.inbound.http.schemas.evidence_review import (
    EvidenceReviewBody,
    EvidenceReviewResponse,
    ReviewCitationResponse,
    ReviewedFileResponse,
    ReviewRequirementResponse,
    parse_evidence_review,
)
from ia_cumplify.application.use_cases.propose_action_plan import verified_tasks
from ia_cumplify.application.use_cases.review_evidence import ReviewEvidenceUseCase
from ia_cumplify.config.settings import Settings
from ia_cumplify.domain.action_plan import (
    ACTION_PLAN_PROMPT_VERSION,
    TASK_PRIORITIES,
    ActionPlanProposal,
    FindingTasks,
    ProposedTask,
    TaskCitation,
)
from ia_cumplify.domain.applicability import (
    APPLICABILITY_NATURES,
    ApplicabilityReasons,
    ArticleAnalysis,
    ArticleApplicabilityReason,
    ArticleObligation,
    close_analysis,
)
from ia_cumplify.domain.chat import (
    APP_NAME_MAX_CHARS,
    CHAT_ROLES,
    FINISH_REASONS,
    PASSAGE_ID_MAX_CHARS,
    PASSAGE_KINDS,
    PASSAGE_REFERENCE_MAX_CHARS,
    RETRYABLE_ERROR_CODES,
    ChatCitation,
    ChatDeltaEvent,
    ChatDoneEvent,
    ChatErrorCode,
    ChatErrorEvent,
    ChatUsage,
    Coverage,
)
from ia_cumplify.domain.classification import (
    ArticleClassification,
    ClassifiedArticle,
    ClassifiedLegalBody,
    TokenUsage,
)
from ia_cumplify.domain.company_profile import ClassifiedCompanyProfile
from ia_cumplify.domain.embedding import TextEmbeddings
from ia_cumplify.domain.evidence_review import (
    EvidenceReviewSubject,
    ReviewCitation,
    ReviewJudgment,
    ReviewRequirement,
)
from ia_cumplify.domain.evidence_text import FileReadStatus
from ia_cumplify.domain.legal_body import LegalBody

CONTRACTS = Path(__file__).parent / "contracts"

# The backend rejects any other size (IaCumplifyClient.EmbedAsync, AI_EMBEDDING_DIMENSIONS).
EMBEDDING_DIMENSIONS = 1024


def load_contract(name: str) -> dict[str, Any]:
    with (CONTRACTS / f"{name}.json").open(encoding="utf-8") as file:
        contract = json.load(file)
    # A stream ends in done or in error, never both, so only the chat adds a second response.
    expected = ["endpoint", "request", "response", *(["error_response"] if name == "chat" else [])]
    assert list(contract) == expected
    return contract


def round_trip(model: type[BaseModel], example: dict[str, Any]) -> BaseModel:
    """Validate the example and require it back unchanged, with the field names that travel."""
    parsed = model.model_validate(example)
    assert parsed.model_dump(mode="json", by_alias=True, exclude_unset=True) == example
    return parsed


def operation_of(endpoint: str) -> dict[str, Any]:
    """The OpenAPI operation of "<METHOD> <path>", as the app mounts it. The app includes its routers
    lazily, so app.routes does not list them; its OpenAPI document does."""
    method, path = endpoint.split(" ", 1)
    paths = create_app().openapi()["paths"]
    assert path in paths, f"No route for {endpoint}"
    assert method.lower() in paths[path], f"No route for {endpoint}"
    return paths[path][method.lower()]


def schema_name(content: dict[str, Any]) -> str:
    return content["application/json"]["schema"]["$ref"].removeprefix("#/components/schemas/")


def assert_served_with(endpoint: str, request: type[BaseModel], response: type[BaseModel]) -> None:
    """The route takes a JSON body of the request model and answers 200 with the response model."""
    operation = operation_of(endpoint)
    assert schema_name(operation["requestBody"]["content"]) == request.__name__
    assert schema_name(operation["responses"]["200"]["content"]) == response.__name__


# --- POST /api/v1/embeddings ------------------------------------------------------------------------------


def synthetic_component(text: int, position: int) -> float:
    """The vectors of the example: deterministic, in [-1, 1), and not from any model."""
    return ((31 * text + 17 * position + 7) % 2001 - 1000) / 1000


@pytest.fixture
def embeddings() -> dict[str, Any]:
    return load_contract("embeddings")


def test_embeddings_is_served_with_the_models_of_the_example(embeddings) -> None:
    assert_served_with(embeddings["endpoint"], EmbedTextsRequest, EmbedTextsResponse)


def test_embeddings_request_is_what_the_backend_sends(embeddings) -> None:
    request = round_trip(EmbedTextsRequest, embeddings["request"])

    # The backend always sends the three fields (IaCumplifyClient.EmbedAsync).
    assert list(embeddings["request"]) == ["texts", "model", "dimensions"]
    assert len(request.texts) == 2
    assert all(text.strip() for text in request.texts)
    assert request.model == "text-embedding-3-large"
    assert request.dimensions == EMBEDDING_DIMENSIONS


def test_embeddings_response_matches_its_request(embeddings) -> None:
    request = EmbedTextsRequest.model_validate(embeddings["request"])
    response = round_trip(EmbedTextsResponse, embeddings["response"])

    assert list(embeddings["response"]) == ["model", "dimensions", "vectors", "usage"]
    assert response.model == request.model
    assert response.dimensions == request.dimensions
    assert len(response.vectors) == len(request.texts)
    assert all(len(vector) == response.dimensions for vector in response.vectors)
    assert embeddings["response"]["usage"] == {"total_tokens": response.usage.total_tokens}
    assert response.usage.total_tokens > 0


def test_embeddings_vectors_are_synthetic(embeddings) -> None:
    vectors = embeddings["response"]["vectors"]

    assert vectors == [
        [synthetic_component(text, position) for position in range(EMBEDDING_DIMENSIONS)]
        for text in range(len(vectors))
    ]


def test_embeddings_response_is_what_the_endpoint_builds(embeddings) -> None:
    example = embeddings["response"]
    result = TextEmbeddings(
        model=example["model"],
        dimensions=example["dimensions"],
        vectors=tuple(tuple(vector) for vector in example["vectors"]),
        total_tokens=example["usage"]["total_tokens"],
    )

    assert EmbedTextsResponse.from_domain(result).model_dump(mode="json") == example


# --- POST /api/v1/legal-bodies/classify -------------------------------------------------------------------

# The six dimensions an article gets, in the order the backend reads them (ClassifyResponse.cs).
DIMENSIONS = [
    "scope",
    "productive_sector",
    "territorial_coverage",
    "activity_action",
    "facility_installation_equipment",
    "others",
]


# Shared by the two classifications: the legal body's articles and the company profile.


def assert_candidate_values(request: dict[str, Any]) -> None:
    """With candidates, the backend sends the three lists, each with labels (IaCumplifyClient)."""
    assert list(request["candidate_values"]) == list(CandidateValues.model_fields)
    assert all(labels for labels in request["candidate_values"].values())


def assert_six_dimensions(classification: dict[str, Any]) -> None:
    assert list(ArticleClassificationParameters.model_fields) == DIMENSIONS
    assert list(classification) == DIMENSIONS


def assert_usage_and_version(response: dict[str, Any], version: str = "classifier_version") -> None:
    assert list(response["usage"]) == list(TokenUsageResponse.model_fields)
    # "<prompt>@<model>": the backend stores it to find what an older prompt or model produced.
    prompt, model = response[version].split("@")
    assert prompt and model


def classification_of(example: dict[str, Any]) -> ArticleClassification:
    return ArticleClassification(**{dimension: tuple(example[dimension]) for dimension in DIMENSIONS})


@pytest.fixture
def classify() -> dict[str, Any]:
    return load_contract("legal-bodies-classify")


def test_classify_is_served_with_the_models_of_the_example(classify) -> None:
    assert_served_with(classify["endpoint"], ClassifyLegalBodyRequest, ClassifyLegalBodyResponse)


def test_classify_request_is_what_the_backend_sends(classify) -> None:
    request = round_trip(ClassifyLegalBodyRequest, classify["request"])

    # With candidates, the backend sends the legal body and the three lists (IaCumplifyClient.ClassifyAsync).
    assert list(classify["request"]) == ["legal_body_id", "candidate_values"]
    assert_candidate_values(classify["request"])
    assert str(request.legal_body_id) == classify["request"]["legal_body_id"]


def test_classify_request_without_candidates_is_valid(classify) -> None:
    # Without candidates the backend leaves candidate_values out (IaCumplifyClient.ClassifyAsync).
    request = {key: value for key, value in classify["request"].items() if key != "candidate_values"}

    assert round_trip(ClassifyLegalBodyRequest, request).candidate_values is None


def test_classify_response_matches_its_request(classify) -> None:
    response = round_trip(ClassifyLegalBodyResponse, classify["response"])
    example = classify["response"]

    assert list(example) == [
        "legal_body_id",
        "title",
        "results",
        "classifier_version",
        "usage",
        "failed_article_ids",
    ]
    assert response.legal_body_id == classify["request"]["legal_body_id"]
    classified = [result.article_id for result in response.results]
    assert len(classified) == len(set(classified))
    # An article is either classified or failed, never both.
    assert response.failed_article_ids
    assert not set(classified) & set(response.failed_article_ids)


def test_classify_results_carry_the_six_dimensions(classify) -> None:
    results = classify["response"]["results"]

    assert results
    for result in results:
        assert list(result) == ["article_id", "number", "classification"]
        assert_six_dimensions(result["classification"])


def test_classify_reports_its_usage_and_version(classify) -> None:
    assert_usage_and_version(classify["response"])


def test_classify_response_is_what_the_endpoint_builds(classify) -> None:
    example = classify["response"]
    result = ClassifiedLegalBody(
        # summary and type feed the prompt only; the response does not carry them.
        legal_body=LegalBody(id=example["legal_body_id"], title=example["title"], summary="", type=""),
        articles=tuple(
            ClassifiedArticle(
                article_id=item["article_id"],
                number=item["number"],
                classification=classification_of(item["classification"]),
            )
            for item in example["results"]
        ),
        classifier_version=example["classifier_version"],
        usage=TokenUsage(**example["usage"]),
        failed_article_ids=tuple(example["failed_article_ids"]),
    )

    # dev_metrics is DEV-ONLY: the endpoint adds it after from_domain, and the example leaves it out.
    assert ClassifyLegalBodyResponse.from_domain(result).model_dump(mode="json", exclude_unset=True) == example


# --- POST /api/v1/company-profiles/classify ---------------------------------------------------------------


@pytest.fixture
def profile() -> dict[str, Any]:
    return load_contract("company-profiles-classify")


def test_profile_is_served_with_the_models_of_the_example(profile) -> None:
    assert_served_with(profile["endpoint"], ClassifyCompanyProfileRequest, ClassifyCompanyProfileResponse)


def test_profile_request_is_what_the_backend_sends(profile) -> None:
    request = round_trip(ClassifyCompanyProfileRequest, profile["request"])

    # With candidates, the backend sends the text and the three lists (IaCumplifyClient.ClassifyProfileAsync).
    assert list(profile["request"]) == ["text", "candidate_values"]
    assert_candidate_values(profile["request"])
    # The use case takes a trimmed text of 1 to PROFILE_TEXT_MAX_CHARS characters (422 otherwise).
    assert 1 <= len(request.text.strip()) <= Settings().profile_text_max_chars


def test_profile_request_without_candidates_is_valid(profile) -> None:
    # Without candidates the backend leaves candidate_values out (IaCumplifyClient.ClassifyProfileAsync).
    request = {key: value for key, value in profile["request"].items() if key != "candidate_values"}

    assert round_trip(ClassifyCompanyProfileRequest, request).candidate_values is None


def test_profile_response_carries_what_the_backend_reads(profile) -> None:
    round_trip(ClassifyCompanyProfileResponse, profile["response"])
    example = profile["response"]

    # The backend needs the six dimensions and classifier_version, and reads the usage counts.
    assert list(example) == ["classification", "classifier_version", "usage"]
    assert_six_dimensions(example["classification"])
    assert_usage_and_version(example)


def test_profile_response_is_what_the_endpoint_builds(profile) -> None:
    example = profile["response"]
    result = ClassifiedCompanyProfile(
        classification=classification_of(example["classification"]),
        classifier_version=example["classifier_version"],
        usage=TokenUsage(**example["usage"]),
    )

    # dev_metrics is DEV-ONLY: the endpoint adds it after from_domain, and the example leaves it out.
    assert ClassifyCompanyProfileResponse.from_domain(result).model_dump(mode="json", exclude_unset=True) == example


# --- POST /api/v1/applicability-reasons -------------------------------------------------------------------


@pytest.fixture
def applicability() -> dict[str, Any]:
    return load_contract("applicability-reasons")


def analysis_of(example: dict[str, Any]) -> ArticleAnalysis:
    return ArticleAnalysis(
        article_id=example["article_id"],
        nature=example["nature"],
        obligations=tuple(ArticleObligation(**item) for item in example["obligations"]),
        referred_legal_body=example["referred_legal_body"],
        procedure_for=example["procedure_for"],
    )


def test_applicability_is_served_with_the_models_of_the_example(applicability) -> None:
    assert_served_with(applicability["endpoint"], ExplainApplicabilityRequest, ExplainApplicabilityResponse)


def test_applicability_request_is_what_the_backend_sends(applicability) -> None:
    request = round_trip(ExplainApplicabilityRequest, applicability["request"])
    articles = applicability["request"]["articles"]

    assert list(applicability["request"]) == ["legal_requirement_id", "legal_body_id", "articles"]
    ids = [str(article.article_id) for article in request.articles]
    assert ids == [article["article_id"] for article in articles]
    assert len(ids) == len(set(ids))
    # The backend always sends both lists, and write_reason only when it is false: true is the default
    # (IaCumplifyClient.ApplicabilityReasonsAsync).
    for article in articles:
        assert list(article)[:3] == ["article_id", "matched_values", "actions"]
        assert article.get("write_reason", False) is False
    # Both paths travel: an article that gets a reason and needs matched values, and an analysis-only one.
    writing = [article for article in request.articles if article.write_reason]
    assert writing and all(article.matched_values for article in writing)
    assert any(not article.write_reason for article in request.articles)


def test_applicability_request_rules_hold(applicability) -> None:
    request = applicability["request"]
    writing = next(article for article in request["articles"] if "write_reason" not in article)
    repeated = {**request, "articles": [*request["articles"], request["articles"][0]]}

    with pytest.raises(ValueError, match="matched_values"):
        ExplainApplicabilityRequest.model_validate(
            {**request, "articles": [{**writing, "matched_values": []}]}
        )
    with pytest.raises(ValueError, match="article_id"):
        ExplainApplicabilityRequest.model_validate(repeated)


def test_applicability_response_matches_its_request(applicability) -> None:
    response = round_trip(ExplainApplicabilityResponse, applicability["response"])
    example = applicability["response"]
    request = ExplainApplicabilityRequest.model_validate(applicability["request"])
    requested = {str(article.article_id) for article in request.articles}
    writing = {str(article.article_id) for article in request.articles if article.write_reason}

    assert list(example) == ["reasons", "analyses", "reason_version", "usage"]
    reasons = [reason.article_id for reason in response.reasons]
    analyses = [analysis.article_id for analysis in response.analyses]
    assert len(reasons) == len(set(reasons))
    assert len(analyses) == len(set(analyses))
    # Only an article that asked for a reason gets one; any requested article may get an analysis.
    assert set(reasons) <= writing
    assert set(analyses) <= requested
    assert all(reason.reason.strip() for reason in response.reasons)
    assert_usage_and_version(example, "reason_version")


def test_applicability_analyses_follow_the_domain(applicability) -> None:
    analyses = applicability["response"]["analyses"]

    for analysis in analyses:
        assert list(analysis) == list(ArticleAnalysisResponse.model_fields)
        for obligation in analysis["obligations"]:
            assert list(obligation) == list(ArticleObligationResponse.model_fields)
        # The example is already what the service closes: obligations only for explicit_obligations, the
        # law only for a referral, the authorization only for a procedural article.
        assert close_analysis(analysis_of(analysis)) == analysis_of(analysis)
    assert {analysis["nature"] for analysis in analyses} <= set(APPLICABILITY_NATURES)


def test_applicability_response_is_what_the_endpoint_builds(applicability) -> None:
    example = applicability["response"]
    result = ApplicabilityReasons(
        # Only the id matters here: the response does not carry the legal body.
        legal_body=LegalBody(id=applicability["request"]["legal_body_id"], title="", summary="", type=""),
        reasons=tuple(ArticleApplicabilityReason(**reason) for reason in example["reasons"]),
        reason_version=example["reason_version"],
        usage=TokenUsage(**example["usage"]),
        analyses=tuple(analysis_of(analysis) for analysis in example["analyses"]),
    )

    # dev_metrics is DEV-ONLY: the endpoint adds it after from_domain, and the example leaves it out.
    assert ExplainApplicabilityResponse.from_domain(result).model_dump(mode="json", exclude_unset=True) == example


# --- POST /api/v1/action-plans ----------------------------------------------------------------------------


@pytest.fixture
def action_plan() -> dict[str, Any]:
    return load_contract("action-plans")


def tasks_of(example: dict[str, Any]) -> tuple[ProposedTask, ...]:
    return tuple(
        ProposedTask(
            name=task["name"],
            description=task["description"],
            priority=task["priority"],
            citation=None if task["citation"] is None else TaskCitation(**task["citation"]),
        )
        for task in example["tasks"]
    )


def test_action_plan_is_served_with_the_models_of_the_example(action_plan) -> None:
    assert_served_with(action_plan["endpoint"], ActionPlanBody, ActionPlanResponse)


def test_action_plan_request_is_what_the_backend_sends(action_plan) -> None:
    body = round_trip(ActionPlanBody, action_plan["request"])
    findings = action_plan["request"]["findings"]

    assert list(action_plan["request"]) == ["prompt_version", "findings"]
    assert body.prompt_version == ACTION_PLAN_PROMPT_VERSION
    # The backend sends every field, null included, in this order (tests/action_plan_backend.py).
    for finding in findings:
        assert list(finding) == ["id", "description", "requirement", "missing", "fragments"]
        for fragment in finding["fragments"]:
            assert list(fragment) == ["document_id", "chunk_index", "locator", "text"]
    # The cap on findings is a setting, checked after the schema: the example stays under the default.
    assert len(body.to_request(Settings().action_plan_max_findings).findings) == len(findings)


def test_action_plan_request_rules_hold(action_plan) -> None:
    request = action_plan["request"]
    first = request["findings"][0]
    fragment = first["fragments"][0]

    def with_first(**changes: object) -> dict[str, Any]:
        return {**request, "findings": [{**first, **changes}]}

    invalid = [
        {**request, "prompt_version": "action-plan-v0"},
        {**request, "findings": []},
        {**request, "findings": [first, first]},
        with_first(id="00000000-0000-0000-0000-000000000000"),
        with_first(description="   "),
        # chunk_index is strict: System.Text.Json never sends a string or a boolean for it.
        with_first(fragments=[{**fragment, "chunk_index": "0"}]),
        with_first(fragments=[{**fragment, "chunk_index": True}]),
        with_first(fragments=[{**fragment, "chunk_index": -1}]),
        with_first(fragments=[{**fragment, "document_id": "00000000-0000-0000-0000-000000000000"}]),
        with_first(fragments=[{**fragment, "page": 1}]),
    ]
    for body in invalid:
        with pytest.raises(ValueError):
            ActionPlanBody.model_validate(body)


def test_action_plan_response_answers_every_finding_in_order(action_plan) -> None:
    response = round_trip(ActionPlanResponse, action_plan["response"])
    example = action_plan["response"]

    assert list(example) == ["proposal_version", "model", "usage", "findings"]
    # Every finding of the request comes back, in its order (ActionPlanResponse).
    assert [finding.id for finding in response.findings] == [
        finding["id"] for finding in action_plan["request"]["findings"]
    ]
    for finding in example["findings"]:
        assert list(finding) == list(FindingTasksResponse.model_fields)
        for task in finding["tasks"]:
            assert list(task) == list(ProposedTaskResponse.model_fields)
            if task["citation"] is not None:
                assert list(task["citation"]) == list(TaskCitationResponse.model_fields)
            assert task["priority"] in (*TASK_PRIORITIES, None)
    assert_usage_and_version(example, "proposal_version")
    # Coherence of the example: the planner reports "<prompt>@<model>" and the same model apart.
    assert example["proposal_version"].split("@")[1] == example["model"]


def test_action_plan_tasks_cite_their_fragments(action_plan) -> None:
    request = ActionPlanBody.model_validate(action_plan["request"]).to_request(Settings().action_plan_max_findings)
    returned = {finding["id"]: finding for finding in action_plan["response"]["findings"]}

    # The service keeps a cited task only if its citation names a fragment of that finding and quotes it, the
    # same rule as ActionPlanResponseReader, and it keeps a task without a citation as a summary of the finding:
    # every task of the example survives it unchanged.
    for finding in request.findings:
        tasks = tasks_of(returned[finding.id])
        assert verified_tasks(finding, tasks) == tasks
    every_task = [task for finding in request.findings for task in tasks_of(returned[finding.id])]
    assert any(task.citation is not None for task in every_task)
    assert any(task.citation is None for task in every_task)


def test_action_plan_response_is_what_the_endpoint_builds(action_plan) -> None:
    example = action_plan["response"]
    result = ActionPlanProposal(
        findings=tuple(FindingTasks(id=finding["id"], tasks=tasks_of(finding)) for finding in example["findings"]),
        usage=TokenUsage(**example["usage"]),
        model=example["model"],
        proposal_version=example["proposal_version"],
    )

    assert ActionPlanResponse.from_domain(result).model_dump(mode="json", exclude_unset=True) == example


# --- POST /api/v1/evidence-reviews (multipart) ------------------------------------------------------------

# The request is multipart/form-data, not JSON: a "review" field with the JSON of EvidenceReviewBody, and one
# part named "files" per file, whose filename is one files[].filename of that JSON. The example keeps the
# review as JSON and, for each part, its field, filename, content type and synthetic text, never real files.


@pytest.fixture
def evidence_review() -> dict[str, Any]:
    return load_contract("evidence-reviews")


def uploads_of(contract: dict[str, Any]) -> list[tuple[str | None, bytes]]:
    """The parts as the endpoint reads them: (filename, content), in the order they travel."""
    return [(part["filename"], part["text"].encode("utf-8")) for part in contract["request"]["files"]]


class ExampleReviewer:
    """The judgment of the example, in place of the model: the use case still reads the files, checks every
    quote against them and sets the verdict, as it does with the real reviewer."""

    def __init__(self, example: dict[str, Any]) -> None:
        self._example = example

    @property
    def version(self) -> str:
        return self._example["review_version"]

    def review(self, subject: EvidenceReviewSubject) -> ReviewJudgment:
        example = self._example
        return ReviewJudgment(
            nature=example["nature"],
            requirements=tuple(
                ReviewRequirement(
                    text=item["text"],
                    status=item["status"],
                    citations=tuple(ReviewCitation(**citation) for citation in item["citations"]),
                    missing=item["missing"],
                    evidence=item["evidence"],
                )
                for item in example["requirements"]
            ),
            verdict=example["verdict"],
            summary=example["summary"],
            usage=TokenUsage(**example["usage"]),
            model=example["model"],
            review_version=example["review_version"],
            procedure_for=example["procedure_for"],
        )


def test_evidence_review_is_served_as_multipart(evidence_review) -> None:
    operation = operation_of(evidence_review["endpoint"])
    schema = operation["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
    form = create_app().openapi()["components"]["schemas"][schema.removeprefix("#/components/schemas/")]

    assert form["required"] == ["review"]
    assert form["properties"]["review"]["type"] == "string"
    assert {"type": "array", "items": {"type": "string", "contentMediaType": "application/octet-stream"}} in (
        form["properties"]["files"]["anyOf"]
    )
    assert schema_name(operation["responses"]["200"]["content"]) == EvidenceReviewResponse.__name__


def test_evidence_review_request_is_what_the_backend_sends(evidence_review) -> None:
    review = evidence_review["request"]["review"]
    body = round_trip(EvidenceReviewBody, review)
    parts = evidence_review["request"]["files"]

    assert list(evidence_review["request"]) == ["review", "files"]
    # The backend sends every field, null included, in this order (IaCumplifyClient.ReviewEvidenceAsync).
    assert list(review) == ["reference_date", "article", "legal_body", "obligation", "files"]
    assert list(review["article"]) == ["number", "text", "title", "section"]
    assert list(review["legal_body"]) == ["title", "type", "number"]
    assert list(review["obligation"]) == ["name", "description"]
    assert [list(item) for item in review["files"]] == [["id", "filename"]] * len(review["files"])
    # Each declared file travels as one part named "files", with that filename, as application/octet-stream.
    assert [part["filename"] for part in parts] == [item.filename for item in body.files]
    for part in parts:
        assert list(part) == ["part", "filename", "content_type", "text"]
        assert part["part"] == "files"
        assert part["content_type"] == "application/octet-stream"


def test_evidence_review_parts_pair_with_the_review(evidence_review) -> None:
    review = evidence_review["request"]["review"]
    request = parse_evidence_review(json.dumps(review), uploads_of(evidence_review))

    assert request.reference_date.isoformat() == review["reference_date"]
    assert [(item.id, item.filename) for item in request.files] == [
        (item["id"], item["filename"]) for item in review["files"]
    ]
    assert [item.content for item in request.files] == [content for _, content in uploads_of(evidence_review)]
    # A path in the part's filename is cut to its last segment, as some clients send one, and the part still
    # pairs with the file declared under that name.
    (filename, content), *rest = uploads_of(evidence_review)
    for path in (f"carpeta/{filename}", f"C:\\carpeta\\{filename}"):
        paired = parse_evidence_review(json.dumps(review), [(path, content), *rest]).files[0]
        assert (paired.id, paired.filename, paired.content) == (review["files"][0]["id"], filename, content)


def test_evidence_review_request_rules_hold(evidence_review) -> None:
    review = evidence_review["request"]["review"]
    uploads = uploads_of(evidence_review)
    first = review["files"][0]

    def with_review(**changes: object) -> str:
        return json.dumps({**review, **changes})

    # A review JSON the schema rejects is a 422 for the review field.
    schema_errors = [
        "{",
        with_review(reference_date="15-10-2026"),
        with_review(files=[first, first]),
        with_review(files=[first, {**first, "id": "otro"}]),
        with_review(files=[{**first, "filename": "carpeta/registro-revisiones.csv"}]),
        with_review(article={**review["article"], "text": "   "}),
        with_review(page=1),
    ]
    for body in schema_errors:
        with pytest.raises(RequestValidationError):
            parse_evidence_review(body, uploads)
    # Parts that do not pair with the declared files are a 422 too, raised as ValueError by the parser.
    pairing_errors = [
        uploads[1:],
        [*uploads, ("otro.csv", b"x")],
        [*uploads, uploads[0]],
        [(None, b"x"), *uploads],
    ]
    for parts in pairing_errors:
        with pytest.raises(ValueError):
            parse_evidence_review(json.dumps(review), parts)


def test_evidence_review_response_matches_its_request(evidence_review) -> None:
    response = round_trip(EvidenceReviewResponse, evidence_review["response"])
    example = evidence_review["response"]
    declared = evidence_review["request"]["review"]["files"]

    assert list(example) == list(EvidenceReviewResponse.model_fields)
    # Every declared file comes back, in its order, with its read status.
    assert [(item.id, item.filename) for item in response.files] == [
        (item["id"], item["filename"]) for item in declared
    ]
    for item in example["files"]:
        assert list(item) == list(ReviewedFileResponse.model_fields)
        assert item["status"] in get_args(FileReadStatus)
    read = {item["id"] for item in example["files"] if item["status"] == "read"}
    for requirement in example["requirements"]:
        assert list(requirement) == list(ReviewRequirementResponse.model_fields)
        for citation in requirement["citations"]:
            assert list(citation) == list(ReviewCitationResponse.model_fields)
            # Coherence of the example: a quote names a file of the request that was read.
            assert citation["file_id"] in read
    assert_usage_and_version(example, "review_version")
    assert example["review_version"].split("@")[1] == example["model"]


def test_evidence_review_response_is_what_the_endpoint_builds(evidence_review) -> None:
    example = evidence_review["response"]
    request = parse_evidence_review(json.dumps(evidence_review["request"]["review"]), uploads_of(evidence_review))

    # The real use case reads the synthetic parts, keeps only the quotes found in them and sets the verdict.
    result = ReviewEvidenceUseCase(ExampleReviewer(example)).execute(request)

    assert EvidenceReviewResponse.from_domain(result).model_dump(mode="json", exclude_unset=True) == example


# --- POST /api/v1/chat (server-sent events) ---------------------------------------------------------------

# The answer is a stream, not one JSON object: delta events, then exactly one done or error. Each response of
# the example is that stream, as {"events": [{"event": ..., "data": ...}]}. The events have no Pydantic model:
# routers/chat.py builds their data with _done_data and _error_data, so the example is checked against those.


@pytest.fixture
def chat() -> dict[str, Any]:
    return load_contract("chat")


def done_event_of(data: dict[str, Any]) -> ChatDoneEvent:
    return ChatDoneEvent(
        citations=tuple(ChatCitation(**citation) for citation in data["citations"]),
        coverage=data["coverage"],
        citations_dropped=data["citations_dropped"],
        finish_reason=data["finish_reason"],
        usage=None if data["usage"] is None else ChatUsage(**data["usage"]),
        chat_version=data["chat_version"],
    )


def error_event_of(data: dict[str, Any]) -> ChatErrorEvent:
    return ChatErrorEvent(
        code=data["code"],
        retryable=data["retryable"],
        usage=None if data["usage"] is None else ChatUsage(**data["usage"]),
        chat_version=data["chat_version"],
    )


def assert_stream_ends_in(stream: dict[str, Any], final: str) -> list[dict[str, Any]]:
    """Deltas, then exactly one final event of the given kind. Returns the deltas."""
    assert list(stream) == ["events"]
    *deltas, last = stream["events"]
    assert last["event"] == final
    for event in stream["events"]:
        assert list(event) == ["event", "data"]
    for event in deltas:
        assert event["event"] == "delta"
        # A delta is the router's {"text": event.text} and nothing else.
        assert list(event["data"]) == [field.name for field in fields(ChatDeltaEvent)]
        assert isinstance(event["data"]["text"], str)
    return deltas


def test_chat_is_served_as_a_stream(chat) -> None:
    operation = operation_of(chat["endpoint"])

    assert schema_name(operation["requestBody"]["content"]) == ChatRequestBody.__name__
    # The 200 is text/event-stream, with no response model to point to.
    assert "text/event-stream" in operation["responses"]["200"]["content"]


def test_chat_request_is_what_the_backend_sends(chat) -> None:
    body = round_trip(ChatRequestBody, chat["request"])
    request = chat["request"]

    # The backend always sends history, as a list, and context only with an app name
    # (IaCumplifyChatAnswerSource.RequestBody).
    assert list(request) == ["question", "history", "passages", "context"]
    assert [list(message) for message in request["history"]] == [["role", "content"]] * len(request["history"])
    assert [list(passage) for passage in request["passages"]] == [["id", "kind", "reference", "text"]] * len(
        request["passages"]
    )
    assert list(request["context"]) == ["app_name"]
    assert {message.role for message in body.history or []} <= set(CHAT_ROLES)
    assert {passage.kind for passage in body.passages} <= set(PASSAGE_KINDS)
    ids = [passage.id for passage in body.passages]
    assert len(ids) == len(set(ids))


def test_chat_request_without_context_is_valid(chat) -> None:
    # Without an app name the backend leaves context out (IaCumplifyChatAnswerSource.RequestBody).
    request = {key: value for key, value in chat["request"].items() if key != "context"}

    assert round_trip(ChatRequestBody, request).context is None


def test_chat_request_fits_the_default_limits(chat) -> None:
    request = chat["request"]
    settings = Settings()

    # The CHAT_* limits are settings the use case checks after the schema (422 above them).
    assert len(request["question"].strip()) <= settings.chat_question_max_chars
    assert len(request["history"]) <= settings.chat_history_max_messages
    assert sum(len(message["content"]) for message in request["history"]) <= settings.chat_history_max_chars
    assert len(request["passages"]) <= settings.chat_max_passages
    assert all(len(passage["text"]) <= settings.chat_passage_max_chars for passage in request["passages"])
    assert sum(len(passage["text"]) for passage in request["passages"]) <= settings.chat_passages_max_total_chars


def test_chat_request_rules_hold(chat) -> None:
    request = chat["request"]
    passage = request["passages"][0]
    message = request["history"][0]

    invalid = [
        {key: value for key, value in request.items() if key != "question"},
        {**request, "history": [{**message, "role": "system"}]},
        {**request, "passages": [{**passage, "kind": "law"}]},
        {**request, "passages": [passage, passage]},
        {**request, "passages": [{**passage, "id": "x" * (PASSAGE_ID_MAX_CHARS + 1)}]},
        {**request, "passages": [{**passage, "reference": "Ley\nart. 1"}]},
        {**request, "context": {"app_name": "x" * (APP_NAME_MAX_CHARS + 1)}},
        {**request, "stream": True},
    ]
    for body in invalid:
        with pytest.raises(ValueError):
            ChatRequestBody.model_validate(body)


def test_chat_answer_ends_in_one_done(chat) -> None:
    deltas = assert_stream_ends_in(chat["response"], "done")
    done = chat["response"]["events"][-1]["data"]

    assert deltas
    # The data of done is what the router builds from the event, with no dev metrics outside development.
    assert done == _done_data(done_event_of(done), None)
    assert done["coverage"] in get_args(Coverage)
    assert done["finish_reason"] in FINISH_REASONS
    # The chat usage adds reasoning_tokens to the counts of the other endpoints.
    assert list(done["usage"]) == [field.name for field in fields(ChatUsage)]
    prompt, model = done["chat_version"].split("@")
    assert prompt and model


def test_chat_citations_name_the_passages(chat) -> None:
    done = chat["response"]["events"][-1]["data"]
    text = "".join(event["data"]["text"] for event in chat["response"]["events"][:-1])
    passages = {passage["id"] for passage in chat["request"]["passages"]}
    citations = done["citations"]

    # The protocol: each citation is the [n] marker it numbers and the id of a passage of the request.
    assert [list(citation) for citation in citations] == [["n", "id"]] * len(citations)
    assert {citation["id"] for citation in citations} <= passages
    # Coherence of the example: the markers are numbered from 1 in the order they appear in the text, each
    # once, and the answer cites a passage, so its coverage is answered.
    assert [citation["n"] for citation in citations] == list(range(1, len(citations) + 1))
    assert all(f"[{citation['n']}]" in text for citation in citations)
    assert len({citation["id"] for citation in citations}) == len(citations)
    assert done["coverage"] == "answered"
    assert done["citations_dropped"] == 0


def test_chat_failure_ends_in_one_error(chat) -> None:
    assert_stream_ends_in(chat["error_response"], "error")
    error = chat["error_response"]["events"][-1]["data"]

    assert error == _error_data(error_event_of(error))
    assert error["code"] in get_args(ChatErrorCode)
    # Whether the same request may work later, as the use case sets it.
    assert error["retryable"] is (error["code"] in RETRYABLE_ERROR_CODES)
    # usage is null when the provider did not report it; the backend then estimates the cost.
    assert error["usage"] is None
    assert error["chat_version"] == chat["response"]["events"][-1]["data"]["chat_version"]


def test_chat_final_events_carry_the_router_fields(chat) -> None:
    done = chat["response"]["events"][-1]["data"]
    error = chat["error_response"]["events"][-1]["data"]

    assert list(done) == list(_done_data(done_event_of(done), None))
    assert list(error) == list(_error_data(error_event_of(error)))
    # A final event never carries the fields of the other one.
    assert not {"code", "retryable"} & set(done)
    assert not {"citations", "coverage", "finish_reason"} & set(error)


# --- Chat limits shared with the backend (prepares step 1.5) ----------------------------------------------

# Not an HTTP contract: the limits the backend mirrors in ChatTurnRules, ChatHistory and ChatPassageLimits.
# "settings" are the defaults of the CHAT_* variables of this service, named as the Settings fields (the
# variable is the name in upper case). "fixed" are the constants of domain/chat.py, which no variable changes.

CHAT_LIMIT_SETTINGS = [
    "chat_question_max_chars",
    "chat_history_max_messages",
    "chat_history_max_chars",
    "chat_max_passages",
    "chat_passage_max_chars",
    "chat_passages_max_total_chars",
]
CHAT_LIMIT_CONSTANTS = {
    "passage_id_max_chars": PASSAGE_ID_MAX_CHARS,
    "passage_reference_max_chars": PASSAGE_REFERENCE_MAX_CHARS,
    "app_name_max_chars": APP_NAME_MAX_CHARS,
}


@pytest.fixture
def chat_limits() -> dict[str, Any]:
    with (CONTRACTS / "chat-limits.json").open(encoding="utf-8") as file:
        return json.load(file)


def test_chat_limits_has_the_nine_limits(chat_limits) -> None:
    # Two flat objects of snake_case names and integers: System.Text.Json reads them as dictionaries.
    assert list(chat_limits) == ["settings", "fixed"]
    assert list(chat_limits["settings"]) == CHAT_LIMIT_SETTINGS
    assert list(chat_limits["fixed"]) == list(CHAT_LIMIT_CONSTANTS)
    for value in [*chat_limits["settings"].values(), *chat_limits["fixed"].values()]:
        assert type(value) is int and value > 0


def test_chat_limits_settings_are_the_code_defaults(chat_limits) -> None:
    # The defaults written in Settings, not what this machine configures: a field default reads no .env
    # and no environment variable.
    defaults = {name: Settings.model_fields[name].default for name in CHAT_LIMIT_SETTINGS}

    assert chat_limits["settings"] == defaults


def test_chat_limits_settings_are_what_the_service_starts_with(chat_limits) -> None:
    # conftest.py clears every Settings variable and .env for each test, so these are the values the service
    # would start with when nothing configures them.
    settings = Settings()

    assert chat_limits["settings"] == {name: getattr(settings, name) for name in CHAT_LIMIT_SETTINGS}


def test_chat_limits_fixed_are_the_domain_constants(chat_limits) -> None:
    assert chat_limits["fixed"] == CHAT_LIMIT_CONSTANTS
