from functools import lru_cache

from fastapi import HTTPException, Request
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.openai.embedder import OpenAITextEmbedderAdapter
from ia_cumplify.config.settings import get_settings


def get_db_pool(request: Request) -> ConnectionPool:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured.")
    return pool


@lru_cache
def get_classifier() -> OpenAIArticleClassifierAdapter:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured.",
        )
    return OpenAIArticleClassifierAdapter(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort,
        batch_size=settings.classify_batch_size,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


@lru_cache
def get_text_embedder() -> OpenAITextEmbedderAdapter:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured.",
        )
    return OpenAITextEmbedderAdapter(
        api_key=settings.openai_api_key,
        timeout_seconds=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )


def reset_wiring_cache() -> None:
    get_classifier.cache_clear()
    get_text_embedder.cache_clear()
    get_settings.cache_clear()
