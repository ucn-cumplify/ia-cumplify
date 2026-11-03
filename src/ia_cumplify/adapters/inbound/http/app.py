import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import fields

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.inbound.http.dependencies import require_api_key
from ia_cumplify.adapters.inbound.http.routers import (
    action_plans,
    applicability,
    articles,
    chat,
    company_profiles,
    documents,
    embeddings,
    evidence_reviews,
)
from ia_cumplify.application.use_cases.answer_chat import ChatLimits
from ia_cumplify.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# What a 422 keeps of each validation error. FastAPI also sends input (the request itself: the whole
# body when a top-level field is missing) and ctx; neither may leave the service.
_VALIDATION_ERROR_KEYS = ("type", "loc", "msg")


def chat_limits_below_default(settings: Settings) -> list[tuple[str, int, int]]:
    """The CHAT_* request limits (the fields of ChatLimits) set below their default, as (variable, value,
    default). The backend mirrors each default as a constant (sprint 4, step 1.5): below it, a request the
    backend builds within its own limit gets 422 here. A value at or above the default is compatible."""
    below = []
    for limit in fields(ChatLimits):
        name = f"chat_{limit.name}"
        default = Settings.model_fields[name].default
        value = getattr(settings, name)
        if value < default:
            below.append((name.upper(), value, default))
    return below


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if settings.chat_fake_responder:
        logger.warning(
            "CHAT_FAKE_RESPONDER is on: POST /api/v1/chat answers a fixed text without calling OpenAI "
            "(chat_version fake-v1@fake)."
        )
    # Only the variable and two integers: no other setting, so no key, reaches the log.
    for variable, value, default in chat_limits_below_default(settings):
        logger.warning(
            "%s=%d is below its default %d, which the backend mirrors as a constant: a chat request the "
            "backend builds within its own limit can get 422 here.",
            variable,
            value,
            default,
        )
    pool: ConnectionPool | None = None
    if settings.database_url:
        pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True},
            open=True,
        )
        app.state.db_pool = pool
    else:
        app.state.db_pool = None
    try:
        yield
    finally:
        if pool is not None:
            pool.close()


async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 without the request in it, for every endpoint (CHT-012, PRF-006).

    msg stays: Pydantic's messages give types, limits or patterns, and the app's own validators use
    fixed texts. exc, str(exc), exc.errors() and exc.body carry the request, so only type and loc are
    logged.
    """
    errors = [
        {key: error[key] for key in _VALIDATION_ERROR_KEYS if key in error} for error in exc.errors()
    ]
    logger.info(
        "Request validation failed on %s %s: %s",
        request.method,
        request.url.path,
        [(error.get("type"), error.get("loc")) for error in errors],
    )
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    # APIRouter takes no exception handlers: this one is the app's, so it covers every endpoint.
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)  # type: ignore[arg-type]
    # /health stays outside: container probes do not carry the key.
    protected = [Depends(require_api_key)]
    app.include_router(articles.router, prefix="/api/v1", dependencies=protected)
    app.include_router(applicability.router, prefix="/api/v1", dependencies=protected)
    app.include_router(chat.router, prefix="/api/v1", dependencies=protected)
    app.include_router(company_profiles.router, prefix="/api/v1", dependencies=protected)
    app.include_router(embeddings.router, prefix="/api/v1", dependencies=protected)
    app.include_router(evidence_reviews.router, prefix="/api/v1", dependencies=protected)
    app.include_router(action_plans.router, prefix="/api/v1", dependencies=protected)
    app.include_router(documents.router, prefix="/api/v1", dependencies=protected)
    return app


app = create_app()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
