from dataclasses import dataclass
import logging

from fastapi import APIRouter, Depends, HTTPException
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.inbound.http.dependencies import (
    get_classifier,
    get_db_pool,
)
from ia_cumplify.adapters.inbound.http.dev_metrics import (  
    DevTokenMeter,
    MeteredArticleClassifier,
)
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


@dataclass
class _ClassifyWiring:
    use_case: ClassifyLegalBodyUseCase
    meter: DevTokenMeter | None  


def get_classify_wiring(
    pool: ConnectionPool = Depends(get_db_pool),
    classifier: OpenAIArticleClassifierAdapter = Depends(get_classifier),
) -> _ClassifyWiring:
    meter: DevTokenMeter | None = None
    if get_settings().include_dev_metrics:  
        meter = DevTokenMeter()
        classifier = MeteredArticleClassifier(classifier, meter)  
    return _ClassifyWiring(
        use_case=ClassifyLegalBodyUseCase(
            repository=PostgresLegalBodyRepository(pool),
            classifier=classifier,
        ),
        meter=meter,
    )


@router.post("/classify", response_model=ClassifyLegalBodyResponse)
def classify_legal_body(
    body: ClassifyLegalBodyRequest,
    wiring: _ClassifyWiring = Depends(get_classify_wiring),
) -> ClassifyLegalBodyResponse:
    try:
        result = wiring.use_case.execute(str(body.legal_body_id))
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
    if wiring.meter is not None:  
        response.dev_metrics = wiring.meter.payload()
    return response
