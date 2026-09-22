from fastapi import APIRouter, Depends, HTTPException

from ia_cumplify.adapters.inbound.http.dependencies import (
    get_classifier,
    get_db_pool,
)
from ia_cumplify.adapters.inbound.http.schemas.classification import (
    ClassifyLegalBodyRequest,
    ClassifyLegalBodyResponse,
)
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.postgres.repository import PostgresLegalBodyRepository
from ia_cumplify.application.use_cases.classify_articles import ClassifyLegalBodyUseCase
from ia_cumplify.domain.exceptions import ClassificationError, LegalBodyNotFoundError
from psycopg_pool import ConnectionPool

router = APIRouter(prefix="/legal-bodies", tags=["legal-bodies"])


def get_classify_legal_body_use_case(
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
    use_case: ClassifyLegalBodyUseCase = Depends(get_classify_legal_body_use_case),
) -> ClassifyLegalBodyResponse:
    try:
        result = use_case.execute(str(body.legal_body_id))
    except LegalBodyNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"legal_bodies row not found: {exc}",
        ) from exc
    except ClassificationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Classification failed; check logs, database, and OpenAI configuration.",
        ) from exc

    return ClassifyLegalBodyResponse.from_domain(result)
