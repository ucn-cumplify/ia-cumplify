from openai import OpenAI, OpenAIError

from ia_cumplify.adapters.outbound.openai.llm_profile_schema import LlmCompanyProfileClassification
from ia_cumplify.adapters.outbound.openai.profile_prompts import (
    PROFILE_PROMPT_VERSION,
    PROFILE_SYSTEM_PROMPT,
    render_profile_candidate_labels,
)
from ia_cumplify.adapters.outbound.openai.strip_images import strip_base64_images
from ia_cumplify.domain.classification import ArticleClassification, CandidateLabels, TokenUsage
from ia_cumplify.domain.company_profile import ProfileClassifierOutput
from ia_cumplify.domain.exceptions import ClassificationError


class OpenAICompanyProfileClassifierAdapter:
    """One model call per company description.

    The description is the company's own text: no error message carries it, nor the model's refusal,
    which could quote it. Errors report only the kind of failure.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        timeout_seconds: float = 180,
        max_retries: int = 2,
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
        return f"{PROFILE_PROMPT_VERSION}@{self._model}"

    def classify(self, text: str, candidates: CandidateLabels | None = None) -> ProfileClassifierOutput:
        # The labels go first and the description last: the system prompt plus the labels are the
        # same for every company at a given time, so that shared prefix stays cacheable.
        blocks = []
        if candidates is not None and not candidates.is_empty():
            blocks.append(render_profile_candidate_labels(candidates))
        blocks.append(_render_description(text))

        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": PROFILE_SYSTEM_PROMPT},
                    {"role": "user", "content": "\n\n".join(blocks)},
                ],
                response_format=LlmCompanyProfileClassification,
                reasoning_effort=self._reasoning_effort,
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise ClassificationError(
                f"OpenAI error while classifying a company profile: {type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise ClassificationError(f"Model did not return a valid company profile classification.{refused}")

        return ProfileClassifierOutput(
            classification=_to_domain(message.parsed),
            usage=_to_usage(getattr(completion, "usage", None)),
        )


def _render_description(text: str) -> str:
    return (
        "--- COMPANY DESCRIPTION ---\n"
        f"{strip_base64_images(text)}\n"
        "--- END COMPANY DESCRIPTION ---"
    )


def _to_usage(usage: object | None) -> TokenUsage:
    # Same rule as the article classifier: the call counts even when the provider omits usage.
    if usage is None:
        return TokenUsage(llm_calls=1)
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        llm_calls=1,
    )


def _to_domain(parsed: LlmCompanyProfileClassification) -> ArticleClassification:
    return ArticleClassification(
        scope=tuple(parsed.scope),
        productive_sector=tuple(parsed.productive_sector),
        territorial_coverage=tuple(parsed.territorial_coverage),
        activity_action=tuple(parsed.activity_action),
        facility_installation_equipment=tuple(parsed.facility_installation_equipment),
        others=tuple(parsed.others),
    )
