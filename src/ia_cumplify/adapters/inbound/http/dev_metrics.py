from pydantic import BaseModel

from ia_cumplify.domain.classification import TokenUsage


class DevMetricsPayload(BaseModel):
    """DEV-ONLY: elapsed time plus the token counts of the usage the response always reports, without
    cached_tokens."""

    elapsed_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int

    @classmethod
    def from_usage(cls, usage: TokenUsage, elapsed_ms: float) -> "DevMetricsPayload":
        return cls(
            elapsed_ms=round(elapsed_ms, 2),
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            llm_calls=usage.llm_calls,
        )
