import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.inbound.http.dependencies import get_applicability_reasoner, get_db_pool
from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload
from ia_cumplify.adapters.inbound.http.schemas.applicability import (
    ExplainApplicabilityRequest,
    ExplainApplicabilityResponse,
)
from ia_cumplify.adapters.outbound.openai.applicability_reasoner import OpenAIApplicabilityReasonerAdapter
from ia_cumplify.adapters.outbound.postgres.repository import PostgresLegalBodyRepository
from ia_cumplify.application.use_cases.explain_applicability import ExplainApplicabilityUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.exceptions import ApplicabilityError, LegalBodyNotFoundError

router = APIRouter(prefix="/applicability-reasons", tags=["applicability-reasons"])
logger = logging.getLogger(__name__)


def get_explain_applicability_use_case(
    pool: ConnectionPool = Depends(get_db_pool),
    reasoner: OpenAIApplicabilityReasonerAdapter = Depends(get_applicability_reasoner),
) -> ExplainApplicabilityUseCase:
    return ExplainApplicabilityUseCase(
        repository=PostgresLegalBodyRepository(pool),
        reasoner=reasoner,
    )


@router.post("", response_model=ExplainApplicabilityResponse)
def explain_applicability(
    body: ExplainApplicabilityRequest,
    use_case: ExplainApplicabilityUseCase = Depends(get_explain_applicability_use_case),
) -> ExplainApplicabilityResponse:
    started_at = perf_counter()
    articles = [article.to_domain() for article in body.articles]
    try:
        result = use_case.execute(str(body.legal_body_id), articles)
    except LegalBodyNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"legal_bodies row not found: {exc}",
        ) from exc
    except ApplicabilityError as exc:
        logger.exception(
            "Applicability reasons failed for legal body %s (%d articles)",
            body.legal_body_id,
            len(articles),
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(
            "Unexpected applicability-reasons failure for legal body %s (%d articles): %s",
            body.legal_body_id,
            len(articles),
            type(exc).__name__,
        )
        raise HTTPException(status_code=502, detail=type(exc).__name__) from exc

    logger.info(
        "Applicability reasons for legal body %s: %d of %d articles",
        body.legal_body_id,
        len(result.reasons),
        len(articles),
    )
    response = ExplainApplicabilityResponse.from_domain(result)
    if get_settings().include_dev_metrics:  # DEV-ONLY
        response.dev_metrics = DevMetricsPayload.from_usage(
            result.usage, (perf_counter() - started_at) * 1000
        )
    return response
