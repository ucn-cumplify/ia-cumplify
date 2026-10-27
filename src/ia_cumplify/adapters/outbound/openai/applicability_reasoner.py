import json
from collections.abc import Sequence

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from ia_cumplify.adapters.outbound.openai.applicability_prompts import (
    APPLICABILITY_PROMPT_VERSION,
    APPLICABILITY_SYSTEM_PROMPT,
    render_applicability_user_content,
)
from ia_cumplify.adapters.outbound.openai.llm_applicability_schema import LlmApplicabilityReasons
from ia_cumplify.adapters.outbound.openai.prompt_cache import PROMPT_CACHE_OPTIONS, cached_prefix
from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.applicability import (
    ApplicabilityArticleContext,
    ApplicabilityReasonerOutput,
    ArticleAnalysis,
    ArticleApplicabilityReason,
    ArticleObligation,
    MAX_REASON_CHARS,
    close_analysis,
    has_foreign_letters,
)
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.exceptions import ApplicabilityError
from ia_cumplify.domain.legal_body import LegalBody


class OpenAIApplicabilityReasonerAdapter:
    """One model call per request. For each requested article found on the legal body: what it asks of
    the company and, unless the caller keeps its own text, one reason for the match."""

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
                # Only the instructions can be cached (prompt_cache.py): the legal body and the articles, with
                # their matched values and actions, come after the breakpoint.
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": [cached_prefix(APPLICABILITY_SYSTEM_PROMPT)]},
                    {"role": "user", "content": render_applicability_user_content(legal_body, articles)},
                ],
                response_format=LlmApplicabilityReasons,
                reasoning_effort=self._reasoning_effort,
                # No copy for OpenAI's distillation and evals.
                store=False,
                prompt_cache_options=PROMPT_CACHE_OPTIONS,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise ApplicabilityError(
                f"OpenAI error while writing applicability reasons for legal body {legal_body.id}: "
                f"{type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc
        except (ValidationError, json.JSONDecodeError):
            # parse() reads the body as JSON (JSONDecodeError) and validates the answer against the schema
            # (ValidationError). Their message and the chained error quote the model output, which can repeat
            # the article or the profile values: neither goes on.
            raise ApplicabilityError(
                f"Model did not return valid applicability reasons for legal body {legal_body.id}."
            ) from None

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise ApplicabilityError(
                f"Model did not return valid applicability reasons for legal body {legal_body.id}.{refused}"
            )

        found = {item.article.id for item in articles}
        with_reason = {item.article.id for item in articles if item.write_reason}
        return ApplicabilityReasonerOutput(
            reasons=map_parsed_reasons(message.parsed, with_reason, self._max_reason_chars),
            usage=call_usage(getattr(completion, "usage", None)),
            analyses=map_parsed_analyses(message.parsed, found),
        )


def map_parsed_reasons(
    parsed: LlmApplicabilityReasons,
    allowed: set[str],
    max_reason_chars: int,
) -> tuple[ArticleApplicabilityReason, ...]:
    """Keeps the first usable reason per allowed article_id. Extra or empty ids are dropped, and so is a
    reason with letters of a foreign script."""
    seen: set[str] = set()
    reasons: list[ArticleApplicabilityReason] = []
    for item in parsed.reasons:
        article_id = item.article_id.strip()
        reason = item.reason.strip()
        if article_id not in allowed or article_id in seen:
            continue
        if not reason or len(reason) > max_reason_chars or has_foreign_letters(reason):
            continue
        seen.add(article_id)
        reasons.append(ArticleApplicabilityReason(article_id=article_id, reason=reason))
    return tuple(reasons)


def map_parsed_analyses(parsed: LlmApplicabilityReasons, allowed: set[str]) -> tuple[ArticleAnalysis, ...]:
    """Keeps the first usable analysis per allowed article_id, closed with the C9 rules.

    An analysis survives when the reason of the same item is dropped: the two are independent.
    """
    seen: set[str] = set()
    analyses: list[ArticleAnalysis] = []
    for item in parsed.reasons:
        article_id = item.article_id.strip()
        if article_id not in allowed or article_id in seen:
            continue
        analysis = close_analysis(
            ArticleAnalysis(
                article_id=article_id,
                nature=item.nature,
                obligations=tuple(
                    ArticleObligation(title=obligation.title, summary=obligation.summary, evidence=obligation.evidence)
                    for obligation in item.obligations
                ),
                referred_legal_body=item.referred_legal_body,
                procedure_for=item.procedure_for,
            )
        )
        if analysis is None:
            continue
        seen.add(article_id)
        analyses.append(analysis)
    return tuple(analyses)
