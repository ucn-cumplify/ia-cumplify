import json

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from ia_cumplify.adapters.outbound.openai.llm_profile_schema import LlmCompanyProfileClassification
from ia_cumplify.adapters.outbound.openai.profile_prompts import (
    PROFILE_PROMPT_VERSION,
    PROFILE_SYSTEM_PROMPT,
    render_profile_candidate_labels,
)
from ia_cumplify.adapters.outbound.openai.prompt_cache import PROMPT_CACHE_OPTIONS, cached_prefix
from ia_cumplify.adapters.outbound.openai.prompt_text import block_text
from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.classification import ArticleClassification, CandidateLabels
from ia_cumplify.domain.company_profile import ProfileClassifierOutput
from ia_cumplify.domain.exceptions import ClassificationError

_INVALID_OUTPUT = "Model did not return a valid company profile classification."


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
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=_messages(text, candidates),  # type: ignore[arg-type]
                response_format=LlmCompanyProfileClassification,
                reasoning_effort=self._reasoning_effort,
                # No copy for OpenAI's distillation and evals.
                store=False,
                prompt_cache_options=PROMPT_CACHE_OPTIONS,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise ClassificationError(
                f"OpenAI error while classifying a company profile: {type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc
        except (ValidationError, json.JSONDecodeError):
            # parse() reads the body as JSON (JSONDecodeError) and validates the answer against the schema
            # (ValidationError). Both are ValueError, but they are failures of the model or the provider,
            # worth retrying (502), not of the request. Their message and the chained error quote the
            # model output, which can repeat the description: neither goes on.
            raise ClassificationError(_INVALID_OUTPUT) from None

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise ClassificationError(f"{_INVALID_OUTPUT}{refused}")

        return ProfileClassifierOutput(
            classification=_to_domain(message.parsed),
            usage=call_usage(getattr(completion, "usage", None)),
        )


def _messages(text: str, candidates: CandidateLabels | None) -> list[dict[str, object]]:
    """The labels go first and the description last. The system prompt plus the labels are the same for
    every company at a given time, so the cacheable prefix ends after the labels or, without them, after
    the system prompt (prompt_cache.py): the description is never cached."""
    description = _render_description(text)
    if candidates is None or candidates.is_empty():
        return [
            {"role": "system", "content": [cached_prefix(PROFILE_SYSTEM_PROMPT)]},
            {"role": "user", "content": description},
        ]
    # The two parts keep the text of a single string: the separator goes at the end of the labels.
    return [
        {"role": "system", "content": PROFILE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                cached_prefix(render_profile_candidate_labels(candidates) + "\n\n"),
                {"type": "text", "text": description},
            ],
        },
    ]


def _render_description(text: str) -> str:
    # A line of the description that imitates a delimiter gets "> " (step 2.5 of the sprint 4 plan). Only the data
    # changes, so profile-v1 stays.
    return (
        "--- COMPANY DESCRIPTION ---\n"
        f"{block_text(text)}\n"
        "--- END COMPANY DESCRIPTION ---"
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
