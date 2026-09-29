import logging

from fastapi import APIRouter, Depends, HTTPException

from ia_cumplify.adapters.inbound.http.dependencies import get_text_embedder
from ia_cumplify.adapters.inbound.http.schemas.embeddings import (
    EmbedTextsRequest,
    EmbedTextsResponse,
)
from ia_cumplify.adapters.outbound.openai.embedder import OpenAITextEmbedderAdapter
from ia_cumplify.application.use_cases.embed_texts import EmbedTextsUseCase
from ia_cumplify.config.settings import get_settings
from ia_cumplify.domain.exceptions import EmbeddingError, EmbeddingInputError

router = APIRouter(prefix="/embeddings", tags=["embeddings"])
logger = logging.getLogger(__name__)


def get_embed_texts_use_case(
    embedder: OpenAITextEmbedderAdapter = Depends(get_text_embedder),
) -> EmbedTextsUseCase:
    settings = get_settings()
    return EmbedTextsUseCase(
        embedder,
        default_model=settings.openai_embedding_model,
        default_dimensions=settings.openai_embedding_dimensions,
        max_texts=settings.embeddings_max_texts,
    )


@router.post("", response_model=EmbedTextsResponse)
def create_embeddings(
    body: EmbedTextsRequest,
    use_case: EmbedTextsUseCase = Depends(get_embed_texts_use_case),
) -> EmbedTextsResponse:
    try:
        result = use_case.execute(body.texts, model=body.model, dimensions=body.dimensions)
    except (ValueError, EmbeddingInputError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except EmbeddingError as exc:
        logger.exception("Embedding failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected embedding failure")
        raise HTTPException(
            status_code=502,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    return EmbedTextsResponse.from_domain(result)
