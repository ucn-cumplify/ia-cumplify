import json

from openai import OpenAI, OpenAIError
from pydantic import ValidationError

from ia_cumplify.adapters.outbound.openai.documentary_situation_prompts import (
    DOCUMENTARY_SITUATION_PROMPT_VERSION_NAME,
    DOCUMENTARY_SITUATION_SYSTEM_PROMPT,
    render_documentary_situation_user_content,
)
from ia_cumplify.adapters.outbound.openai.llm_documentary_situation_schema import LlmDocumentarySituation
from ia_cumplify.adapters.outbound.openai.prompt_cache import PROMPT_CACHE_OPTIONS, cached_prefix
from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.documentary_situation import DocumentarySituationJudgment, DocumentarySituationSubject
from ia_cumplify.domain.exceptions import DocumentarySituationError


class OpenAIDocumentarySituationExtractorAdapter:
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
        return f"{DOCUMENTARY_SITUATION_PROMPT_VERSION_NAME}@{self._model}"

    def extract(self, subject: DocumentarySituationSubject) -> DocumentarySituationJudgment:
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": [cached_prefix(DOCUMENTARY_SITUATION_SYSTEM_PROMPT)]},
                    {"role": "user", "content": render_documentary_situation_user_content(subject)},
                ],
                response_format=LlmDocumentarySituation,
                reasoning_effort=self._reasoning_effort,
                store=False,
                prompt_cache_options=PROMPT_CACHE_OPTIONS,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise DocumentarySituationError(
                "OpenAI error while extracting the documentary situation: "
                f"{type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc
        except (ValidationError, json.JSONDecodeError):
            raise DocumentarySituationError(
                "The documentary situation model did not return a valid situation."
            ) from None

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise DocumentarySituationError(
                f"The documentary situation model did not return a valid situation.{refused}"
            )
        return DocumentarySituationJudgment(
            content=message.parsed.content.strip(),
            usage=call_usage(getattr(completion, "usage", None)),
            model=self._model,
            prompt_version=self.version,
        )
