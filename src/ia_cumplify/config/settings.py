from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ia_cumplify.domain.action_plan import DEFAULT_MAX_FINDINGS


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

    # POST /api/v1/evidence-reviews. Empty EVIDENCE_REVIEW_MODEL and EVIDENCE_REVIEW_REASONING_EFFORT
    # use OPENAI_MODEL and OPENAI_REASONING_EFFORT. The timeout is its own: one structured call over
    # several files, under the shared 180 s used by classification.
    evidence_review_model: str = ""
    evidence_review_reasoning_effort: str = ""
    evidence_review_timeout_seconds: float = 80
    # 0: one attempt. The SDK also retries a timed-out call, so each retry adds up to another
    # 80 s, past the 90 s the backend plans to wait (AI_EVIDENCE_REVIEW_TIMEOUT_SECONDS).
    evidence_review_max_retries: int = 0

    # POST /api/v1/action-plans. Empty ACTION_PLAN_MODEL and ACTION_PLAN_REASONING_EFFORT use OPENAI_MODEL and
    # OPENAI_REASONING_EFFORT. One structured call, under the 90 s the backend waits (AI_ACTION_PLAN_TIMEOUT_SECONDS).
    action_plan_model: str = ""
    action_plan_reasoning_effort: str = ""
    action_plan_timeout_seconds: float = 80
    # 0: one attempt. The SDK also retries a timed-out call, which would pass the backend's 90 s.
    action_plan_max_retries: int = 0
    # Findings per request; one more gets 422. Not agreed with the backend yet, which sends every open finding of
    # the review with up to 5 fragments of 4000 characters each (docs/ActionPlan/api.md).
    action_plan_max_findings: int = Field(default=DEFAULT_MAX_FINDINGS, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
