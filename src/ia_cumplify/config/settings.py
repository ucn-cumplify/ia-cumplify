from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ia-cumplify"
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: str = "low"
    database_url: str = ""
    # DEV-ONLY: include elapsed time and token usage on classify responses
    include_dev_metrics: bool = True
    # Defaults for POST /api/v1/embeddings when the request does not set them. The backend stores
    # vectors in a vector(1024) column, so 1024 dimensions is the agreed contract.
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 1024
    embeddings_max_texts: int = 256


@lru_cache
def get_settings() -> Settings:
    return Settings()
