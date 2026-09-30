import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.inbound.http.dependencies import (
    get_classifier,
    get_db_pool,
)
from ia_cumplify.adapters.inbound.http.dev_metrics import DevMetricsPayload
from ia_cumplify.adapters.inbound.http.schemas.classification import (
    ClassifyLegalBodyRequest,
    ClassifyLegalBodyResponse,
)
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.postgres.repository import PostgresLegalBodyRepository
from ia_cumplify.application.use_cases.classify_articles import ClassifyLegalBodyUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.exceptions import ClassificationError, LegalBodyNotFoundError

router = APIRouter(prefix="/legal-bodies", tags=["legal-bodies"])
logger = logging.getLogger(__name__)


def get_classify_use_case(
    pool: ConnectionPool = Depends(get_db_pool),
    classifier: OpenAIArticleClassifierAdapter = Depends(get_classifier),
) -> ClassifyLegalBodyUseCase:
    return ClassifyLegalBodyUseCase(
        repository=PostgresLegalBodyRepository(pool),
        classifier=classifier,
    )


@router.post("/classify", response_model=ClassifyLegalBodyResponse)
def classify_legal_body(
    body: ClassifyLegalBodyRequest,
    use_case: ClassifyLegalBodyUseCase = Depends(get_classify_use_case),
) -> ClassifyLegalBodyResponse:
    candidates = body.candidate_values.to_domain() if body.candidate_values else None
    started_at = perf_counter()
    try:
        result = use_case.execute(str(body.legal_body_id), candidates)
    except LegalBodyNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"legal_bodies row not found: {exc}",
        ) from exc
    except ClassificationError as exc:
        logger.exception("Classification failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected classify failure")
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    response = ClassifyLegalBodyResponse.from_domain(result)
    if get_settings().include_dev_metrics:  # DEV-ONLY
        response.dev_metrics = DevMetricsPayload.from_usage(
            result.usage, (perf_counter() - started_at) * 1000
        )
    return response
