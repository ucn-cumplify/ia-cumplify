import secrets
from functools import lru_cache

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.outbound.fake.chat_responder import FakeChatResponder
from ia_cumplify.adapters.outbound.openai.chat_responder import OpenAIChatResponderAdapter
from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.adapters.outbound.openai.embedder import OpenAITextEmbedderAdapter
from ia_cumplify.adapters.outbound.openai.profile_classifier import OpenAICompanyProfileClassifierAdapter
from ia_cumplify.application.ports.chat_responder import ChatResponderPort
from ia_cumplify.config.settings import get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    expected = get_settings().service_api_key
    if not expected:
        raise HTTPException(status_code=503, detail="SERVICE_API_KEY is not configured.")
    if not api_key or not secrets.compare_digest(api_key.encode(), expected.encode()):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


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
def get_profile_classifier() -> OpenAICompanyProfileClassifierAdapter:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured.",
        )
    return OpenAICompanyProfileClassifierAdapter(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        reasoning_effort=settings.openai_reasoning_effort,
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


@lru_cache
def get_chat_responder() -> ChatResponderPort:
    settings = get_settings()
    if settings.chat_fake_responder:
        # It never calls OpenAI, so it needs no key. The app warns at startup that it is on.
        return FakeChatResponder()
    if not settings.openai_api_key:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not configured.",
        )
    return OpenAIChatResponderAdapter(
        api_key=settings.openai_api_key,
        model=settings.chat_model or settings.openai_model,
        reasoning_effort=settings.chat_reasoning_effort or settings.openai_reasoning_effort,
        max_completion_tokens=settings.chat_max_completion_tokens,
        timeout_seconds=settings.chat_timeout_seconds,
        max_retries=settings.chat_max_retries,
    )


def reset_wiring_cache() -> None:
    get_classifier.cache_clear()
    get_profile_classifier.cache_clear()
    get_text_embedder.cache_clear()
    get_chat_responder.cache_clear()
    get_settings.cache_clear()
