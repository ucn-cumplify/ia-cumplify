from openai import OpenAI, OpenAIError

from ia_cumplify.adapters.outbound.openai.action_plan_prompts import (
    ACTION_PLAN_SYSTEM_PROMPT,
    render_action_plan_user_content,
)
from ia_cumplify.adapters.outbound.openai.llm_action_plan_schema import LlmActionPlan
from ia_cumplify.adapters.outbound.openai.prompt_cache import PROMPT_CACHE_OPTIONS, cached_prefix
from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.action_plan import (
    ACTION_PLAN_PROMPT_VERSION,
    ActionPlanDraft,
    ActionPlanFinding,
    FindingTasks,
    ProposedTask,
    TaskCitation,
)
from ia_cumplify.domain.exceptions import ActionPlanError


class OpenAIActionPlannerAdapter:
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
    def model(self) -> str:
        return self._model

    @property
    def version(self) -> str:
        return f"{ACTION_PLAN_PROMPT_VERSION}@{self._model}"

    def propose(self, findings: tuple[ActionPlanFinding, ...]) -> ActionPlanDraft:
        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                # Only the instructions can be cached (prompt_cache.py): the findings and the fragments come
                # after the breakpoint.
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": [cached_prefix(ACTION_PLAN_SYSTEM_PROMPT)]},
                    {"role": "user", "content": render_action_plan_user_content(findings)},
                ],
                response_format=LlmActionPlan,
                reasoning_effort=self._reasoning_effort,
                # No copy for OpenAI's distillation and evals.
                store=False,
                prompt_cache_options=PROMPT_CACHE_OPTIONS,  # type: ignore[arg-type]
            )
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            raise ActionPlanError(
                "OpenAI error while proposing the action plan: "
                f"{type(exc).__name__}"
                + (f" (HTTP {status})" if status else "")
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refused = " The model refused." if getattr(message, "refusal", None) else ""
            raise ActionPlanError(f"The action plan model did not return a valid proposal.{refused}")
        return _draft(message.parsed, getattr(completion, "usage", None))


def _draft(parsed: LlmActionPlan, usage: object | None) -> ActionPlanDraft:
    return ActionPlanDraft(
        findings=tuple(
            FindingTasks(
                id=item.finding_id.strip().lower(),
                tasks=tuple(
                    ProposedTask(
                        name=task.name,
                        description=task.description,
                        priority=task.priority,
                        citation=TaskCitation(
                            document_id=task.citation.document_id,
                            chunk_index=task.citation.chunk_index,
                            quote=task.citation.quote,
                        ),
                    )
                    for task in item.tasks
                ),
            )
            for item in parsed.findings
        ),
        usage=call_usage(usage),
    )
