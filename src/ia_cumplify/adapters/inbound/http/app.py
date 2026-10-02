from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from psycopg_pool import ConnectionPool

from ia_cumplify.adapters.inbound.http.dependencies import require_api_key
from ia_cumplify.adapters.inbound.http.routers import articles, company_profiles, embeddings
from ia_cumplify.config.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
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


def create_app() -> FastAPI:
    app = FastAPI(title=get_settings().app_name, lifespan=lifespan)
    # /health stays outside: container probes do not carry the key.
    protected = [Depends(require_api_key)]
    app.include_router(articles.router, prefix="/api/v1", dependencies=protected)
    app.include_router(company_profiles.router, prefix="/api/v1", dependencies=protected)
    app.include_router(embeddings.router, prefix="/api/v1", dependencies=protected)
    return app


app = create_app()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
