"""Proposes tasks for the findings of one request from the knowledge-base fragments sent with them.

The model call goes through ActionPlannerPort. This use case does not read the database: the backend finds
the fragments and sends them.
"""

from ia_cumplify.application.ports.action_planner import ActionPlannerPort
from ia_cumplify.domain.action_plan import (
    MAX_TASKS_PER_FINDING,
    TASK_DESCRIPTION_MAX_CHARS,
    TASK_NAME_MAX_CHARS,
    TASK_PRIORITIES,
    ActionPlanFinding,
    ActionPlanProposal,
    ActionPlanRequest,
    FindingTasks,
    KnowledgeFragment,
    ProposedTask,
    TaskCitation,
)
from ia_cumplify.domain.classification import TokenUsage


class ProposeActionPlanUseCase:
    """Asks the planner once, for the findings that have fragments, and keeps only the tasks it can verify."""

    def __init__(self, planner: ActionPlannerPort) -> None:
        self._planner = planner

    def execute(self, request: ActionPlanRequest) -> ActionPlanProposal:
        supported = tuple(finding for finding in request.findings if finding.fragments)
        # A finding without fragments has nothing in the base to cite. Without any, the model is not called.
        draft_tasks: dict[str, tuple[ProposedTask, ...]] = {}
        usage = TokenUsage()
        if supported:
            draft = self._planner.propose(supported)
            usage = draft.usage
            for item in draft.findings:
                # The first entry of a finding wins, as in ActionPlanResponseReader.
                draft_tasks.setdefault(item.id, item.tasks)
        return ActionPlanProposal(
            findings=tuple(
                FindingTasks(id=finding.id, tasks=verified_tasks(finding, draft_tasks.get(finding.id, ())))
                for finding in request.findings
            ),
            usage=usage,
            model=self._planner.model,
            proposal_version=self._planner.version,
        )


def verified_tasks(finding: ActionPlanFinding, tasks: tuple[ProposedTask, ...]) -> tuple[ProposedTask, ...]:
    """The tasks whose citation names a fragment of this finding and quotes it, with their fields fitted.

    The rules are the ones ActionPlanResponseReader applies in the backend, so no task leaves this service that
    the backend would drop. A name past the cap drops the task. A description past the cap, or a priority
    outside the catalog, is cleared instead.
    """
    fragments: dict[tuple[str, int], KnowledgeFragment] = {}
    for fragment in finding.fragments:
        fragments.setdefault((fragment.document_id, fragment.chunk_index), fragment)
    kept: list[ProposedTask] = []
    for task in tasks:
        if len(kept) == MAX_TASKS_PER_FINDING:
            break
        name = " ".join(task.name.split())
        if not name or utf16_length(name) > TASK_NAME_MAX_CHARS:
            continue
        citation = task.citation
        document_id = citation.document_id.strip().lower()
        fragment = fragments.get((document_id, citation.chunk_index))
        quote = citation.quote.strip()
        if fragment is None or not quote_in_fragment(quote, fragment.text):
            continue
        description = (task.description or "").strip()
        priority = (task.priority or "").strip().lower()
        kept.append(
            ProposedTask(
                name=name,
                description=(
                    description if description and utf16_length(description) <= TASK_DESCRIPTION_MAX_CHARS else None
                ),
                priority=priority if priority in TASK_PRIORITIES else None,  # type: ignore[arg-type]
                citation=TaskCitation(document_id=document_id, chunk_index=citation.chunk_index, quote=quote),
            )
        )
    return tuple(kept)


def quote_in_fragment(quote: str, fragment_text: str) -> bool:
    """The quote, without case or whitespace, is inside the fragment. A quote that is all whitespace is not."""
    compact_quote = _compact(quote)
    return bool(compact_quote) and compact_quote in _compact(fragment_text)


def _compact(text: str) -> str:
    # lower(), not casefold(): ActionPlanResponseReader uses ToLowerInvariant, and casefold() would match quotes
    # (ß and ss, for one) that the backend then drops.
    return "".join(character.lower() for character in text if not character.isspace())


def utf16_length(text: str) -> int:
    """The length .NET gives the string. The backend caps names and descriptions by it, and a character outside
    the BMP, such as an emoji, counts twice there."""
    return len(text.encode("utf-16-le")) // 2
