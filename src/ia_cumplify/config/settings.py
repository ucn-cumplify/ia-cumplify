from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "ia-cumplify"
    # Shared secret the backend sends in X-API-Key. Empty rejects every /api/v1 request with 503.
    service_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-5.6-luna"
    openai_reasoning_effort: str = "low"
    database_url: str = ""
    # DEV-ONLY: elapsed time and token usage on classify responses, and elapsed_ms and first_delta_ms
    # in the done event of the chat
    include_dev_metrics: bool = True
    classify_batch_size: int = 25
    # Defaults for POST /api/v1/embeddings when the request does not set them. The backend stores
    # vectors in a vector(1024) column, so 1024 dimensions is the agreed contract.
    openai_embedding_model: str = "text-embedding-3-large"
    openai_embedding_dimensions: int = 1024
    embeddings_max_texts: int = 256
    # POST /api/v1/company-profiles/classify: longest description accepted, after trimming. Mirrors the
    # backend's AI_PROFILE_TEXT_MAX_CHARS; a longer text gets 422.
    profile_text_max_chars: int = 4000
    openai_timeout_seconds: float = 180
    openai_max_retries: int = 2
    # POST /api/v1/applicability-reasons. The backend waits AI_APPLICABILITY_REASONS_TIMEOUT_SECONDS
    # (30). This client stays under that so a slow model call fails here instead of being cancelled.
    applicability_reasons_timeout_seconds: float = 25
    # 0: one attempt. A retry would not fit in the backend's 30 s budget.
    applicability_reasons_max_retries: int = 0

    # POST /api/v1/chat. It is interactive, so it does not share the timeout and retries above.
    # Empty CHAT_MODEL and CHAT_REASONING_EFFORT use OPENAI_MODEL and OPENAI_REASONING_EFFORT.
    chat_model: str = ""
    chat_reasoning_effort: str = ""
    # Output cap per answer, reasoning included.
    chat_max_completion_tokens: int = 4000
    # Per attempt: the longest wait for the provider's headers or for its next chunk. Connecting has 5 s.
    chat_timeout_seconds: float = 60
    # SDK retries, only before the provider answers 2xx. 0 leaves the retry to the backend.
    chat_max_retries: int = 0
    # Request limits: a request over one gets 422. The backend should mirror them (proposed names:
    # AI_CHAT_*; the backend does not define them yet).
    chat_question_max_chars: int = 2000
    chat_history_max_messages: int = 6
    chat_history_max_chars: int = 8000
    chat_max_passages: int = 12
    chat_passage_max_chars: int = 6000
    chat_passages_max_total_chars: int = 48000
    # Fixed answers without OpenAI (chat_version fake-v1@fake), to integrate the backend without tokens.
    chat_fake_responder: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
