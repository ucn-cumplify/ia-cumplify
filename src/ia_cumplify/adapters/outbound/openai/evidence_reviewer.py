from openai import OpenAI, OpenAIError

from ia_cumplify.adapters.outbound.openai.evidence_review_prompts import (
    EVIDENCE_REVIEW_PROMPT_VERSION,
    EVIDENCE_REVIEW_SYSTEM_PROMPT,
    render_evidence_review_user_content,
)
from ia_cumplify.adapters.outbound.openai.llm_evidence_review_schema import LlmEvidenceReview
from ia_cumplify.adapters.outbound.openai.prompt_cache import PROMPT_CACHE_OPTIONS, cached_prefix
from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.evidence_review import (
    EvidenceReviewSubject,
    ReviewCitation,
    ReviewJudgment,
    ReviewRequirement,
)
from ia_cumplify.domain.exceptions import EvidenceReviewError


class OpenAIEvidenceReviewerAdapter:
    """One structured model call. It does not read the database."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float,
        max_retries: int,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = OpenAI(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    @property
    def version(self) -> str:
        return f"{EVIDENCE_REVIEW_PROMPT_VERSION}@{self._model}"

    def review(self, subject: EvidenceReviewSubject) -> ReviewJudgment:
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                # Only the instructions can be cached (prompt_cache.py): the article and the text of the
                # evidence files come after the breakpoint.
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": [cached_prefix(EVIDENCE_REVIEW_SYSTEM_PROMPT)]},
                    {"role": "user", "content": render_evidence_review_user_content(subject)},
                ],
                response_format=LlmEvidenceReview,
                reasoning_effort=self._reasoning_effort,
                # No copy for OpenAI's distillation and evals.
                store=False,
                prompt_cache_options=PROMPT_CACHE_OPTIONS,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise EvidenceReviewError(
                "OpenAI error while reviewing evidence: "
                f"{type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise EvidenceReviewError(f"The evidence review model did not return a valid review.{refused}")
        return _judgment(message.parsed, self._model, self.version, getattr(completion, "usage", None))


def _judgment(parsed: LlmEvidenceReview, model: str, version: str, usage: object | None) -> ReviewJudgment:
    return ReviewJudgment(
        nature=parsed.nature,
        requirements=tuple(
            ReviewRequirement(
                text=item.text.strip(),
                evidence=item.evidence.strip(),
                status=item.status,
                citations=tuple(
                    ReviewCitation(
                        file_id=citation.file_id.strip(),
                        locator=citation.locator,
                        reference=citation.reference.strip(),
                        quote=citation.quote.strip(),
                    )
                    for citation in item.citations
                ),
                missing=item.missing.strip(),
            )
            for item in parsed.requirements
        ),
        verdict=parsed.verdict,
        summary=parsed.summary.strip(),
        usage=call_usage(usage),
        model=model,
        review_version=version,
        procedure_for=parsed.procedure_for.strip() or None,
    )
