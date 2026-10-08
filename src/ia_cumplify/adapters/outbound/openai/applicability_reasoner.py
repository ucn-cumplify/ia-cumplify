from collections.abc import Sequence

from openai import OpenAI, OpenAIError

from ia_cumplify.adapters.outbound.openai.applicability_prompts import (
    APPLICABILITY_PROMPT_VERSION,
    APPLICABILITY_SYSTEM_PROMPT,
    render_applicability_user_content,
)
from ia_cumplify.adapters.outbound.openai.llm_applicability_schema import LlmApplicabilityReasons
from ia_cumplify.domain.applicability import (
    ApplicabilityArticleContext,
    ApplicabilityReasonerOutput,
    ArticleApplicabilityReason,
    MAX_REASON_CHARS,
)
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import ApplicabilityError
from ia_cumplify.domain.legal_body import LegalBody


class OpenAIApplicabilityReasonerAdapter:
    """One model call per request: one reason per requested article that was found on the legal body."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float = 25,
        max_retries: int = 0,
        max_reason_chars: int = MAX_REASON_CHARS,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._max_reason_chars = max_reason_chars
        self._client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def version(self) -> str:
        return f"{APPLICABILITY_PROMPT_VERSION}@{self._model}"

    def explain(
        self,
        legal_body: LegalBody,
        articles: Sequence[ApplicabilityArticleContext],
    ) -> ApplicabilityReasonerOutput:
        if not articles:
            return ApplicabilityReasonerOutput(reasons=(), usage=TokenUsage())

        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": APPLICABILITY_SYSTEM_PROMPT},
                    {"role": "user", "content": render_applicability_user_content(legal_body, articles)},
                ],
                response_format=LlmApplicabilityReasons,
                reasoning_effort=self._reasoning_effort,
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise ApplicabilityError(
                f"OpenAI error while writing applicability reasons for legal body {legal_body.id}: "
                f"{type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise ApplicabilityError(
                f"Model did not return valid applicability reasons for legal body {legal_body.id}.{refused}"
            )

        allowed = {item.article.id for item in articles}
        return ApplicabilityReasonerOutput(
            reasons=map_parsed_reasons(message.parsed, allowed, self._max_reason_chars),
            usage=_to_usage(getattr(completion, "usage", None)),
        )


def map_parsed_reasons(
    parsed: LlmApplicabilityReasons,
    allowed: set[str],
    max_reason_chars: int,
) -> tuple[ArticleApplicabilityReason, ...]:
    """Keeps the first usable reason per allowed article_id. Extra or empty ids are dropped."""
    seen: set[str] = set()
    reasons: list[ArticleApplicabilityReason] = []
    for item in parsed.reasons:
        article_id = item.article_id.strip()
        reason = item.reason.strip()
        if article_id not in allowed or article_id in seen:
            continue
        if not reason or len(reason) > max_reason_chars:
            continue
        seen.add(article_id)
        reasons.append(ArticleApplicabilityReason(article_id=article_id, reason=reason))
    return tuple(reasons)


def _to_usage(usage: object | None) -> TokenUsage:
    if usage is None:
        return TokenUsage(llm_calls=1)
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        llm_calls=1,
    )
