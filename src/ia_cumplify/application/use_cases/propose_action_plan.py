"""Proposes tasks for the findings of one request.

A task that cites a fragment is kept only when that fragment belongs to the finding and the quote is in it.
A task with an empty citation is kept too: the knowledge base did not cover the finding, and the task
summarizes it. The model call goes through ActionPlannerPort. This use case does not read the database.
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


class ProposeActionPlanUseCase:
    """Asks the planner once for every finding, and keeps the tasks it can verify or that summarize the finding."""

    def __init__(self, planner: ActionPlannerPort) -> None:
        self._planner = planner

    def execute(self, request: ActionPlanRequest) -> ActionPlanProposal:
        # Every finding is sent, also one with no fragments: the model still summarizes it into tasks.
        draft = self._planner.propose(request.findings)
        draft_tasks: dict[str, tuple[ProposedTask, ...]] = {}
        for item in draft.findings:
            # The first entry of a finding wins, as in ActionPlanResponseReader.
            draft_tasks.setdefault(item.id, item.tasks)
        return ActionPlanProposal(
            findings=tuple(
                FindingTasks(id=finding.id, tasks=verified_tasks(finding, draft_tasks.get(finding.id, ())))
                for finding in request.findings
            ),
            usage=draft.usage,
            model=self._planner.model,
            proposal_version=self._planner.version,
        )


def verified_tasks(finding: ActionPlanFinding, tasks: tuple[ProposedTask, ...]) -> tuple[ProposedTask, ...]:
    """Cited tasks whose quote is in a fragment of this finding, and tasks that summarize the finding.

    An empty citation is a summary of the finding: the base did not cover it, and the backend stores it
    without a document. A citation that names the wrong fragment, or that the fragment does not contain, is
    dropped. It is not turned into a summary. A name past the cap drops the task. A description past the cap,
    or a priority outside the catalog, is cleared instead. These are the rules ActionPlanResponseReader applies.
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
        description = (task.description or "").strip()
        priority = (task.priority or "").strip().lower()
        fitted = ProposedTask(
            name=name,
            description=(
                description if description and utf16_length(description) <= TASK_DESCRIPTION_MAX_CHARS else None
            ),
            priority=priority if priority in TASK_PRIORITIES else None,  # type: ignore[arg-type]
            citation=None,
        )
        citation = task.citation
        if citation is None or _is_summary(citation):
            kept.append(fitted)
            continue
        document_id = citation.document_id.strip().lower()
        fragment = fragments.get((document_id, citation.chunk_index))
        quote = citation.quote.strip()
        if fragment is None or not quote_in_fragment(quote, fragment.text):
            continue
        kept.append(
            ProposedTask(
                name=fitted.name,
                description=fitted.description,
                priority=fitted.priority,
                citation=TaskCitation(document_id=document_id, chunk_index=citation.chunk_index, quote=quote),
            )
        )
    return tuple(kept)


def _is_summary(citation: TaskCitation) -> bool:
    """An empty document and an empty quote mark a task the model summarized from the finding."""
    return not citation.document_id.strip() and not citation.quote.strip()


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
