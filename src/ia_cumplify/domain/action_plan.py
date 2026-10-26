from dataclasses import dataclass
from typing import Literal

from ia_cumplify.domain.classification import TokenUsage

# Prompt id. The model name is appended once a proposal is built: "action-plan-v1@<model>". The backend sends
# this same id in prompt_version (IaCumplifyClient.ActionPlanPromptVersion) and stores the full one.
ACTION_PLAN_PROMPT_VERSION = "action-plan-v1"

# Request caps, from what backend-cumplify sends (step 2B.10 of its sprint 3 plan). Lengths and positions may
# appear in a 422; the text itself must not.
# A finding's description is capped at 2000 in legal_requirement_findings.
FINDING_DESCRIPTION_MAX_CHARS = 2_000
# The exigencia and what is missing come from the stored evidence review, which has no cap of its own.
FINDING_REQUIREMENT_MAX_CHARS = 20_000
FINDING_MISSING_MAX_CHARS = 20_000
# FragmentsPerFinding of ProposeActionPlanUseCase.
MAX_FRAGMENTS_PER_FINDING = 5
# kb-v1 cuts with ArticleChunker.MaxChunkLength.
FRAGMENT_TEXT_MAX_CHARS = 4_000
# The document title plus «(parte i de n)». The base has no table yet, so the title has no cap there.
FRAGMENT_LOCATOR_MAX_CHARS = 1_000
# Not agreed with the backend, which sends every open finding of the review: ACTION_PLAN_MAX_FINDINGS sets it.
DEFAULT_MAX_FINDINGS = 30

# What ActionPlanResponseReader keeps (LegalRequirementActionPlanProposal and its task). A task past one of them
# is dropped here, so the backend never receives one it would discard.
PROPOSAL_VERSION_MAX_CHARS = 200
MODEL_MAX_CHARS = 100
TASK_NAME_MAX_CHARS = 255
TASK_DESCRIPTION_MAX_CHARS = 2_000
# The tasks kept per finding, in the model's order. The backend has no cap; a plan is reviewed task by task.
MAX_TASKS_PER_FINDING = 5

# alta, media, baja: ActionTaskCatalogs.Priorities of the backend.
TaskPriority = Literal["alta", "media", "baja"]
TASK_PRIORITIES: tuple[TaskPriority, ...] = ("alta", "media", "baja")


@dataclass(frozen=True, slots=True)
class KnowledgeFragment:
    """One fragment of the knowledge base, as the backend found it for a finding.

    ``document_id`` and ``chunk_index`` name it in ai_embeddings. ``locator`` is the document title and the
    part, and may be absent.
    """

    document_id: str
    chunk_index: int
    text: str
    locator: str | None = None


@dataclass(frozen=True, slots=True)
class ActionPlanFinding:
    """An open finding of an evidence review, and the fragments of the base closest to it."""

    id: str
    description: str
    requirement: str | None
    missing: str | None
    fragments: tuple[KnowledgeFragment, ...]


@dataclass(frozen=True, slots=True)
class ActionPlanRequest:
    """The body of POST /api/v1/action-plans, already validated. The service reads no database."""

    findings: tuple[ActionPlanFinding, ...]


@dataclass(frozen=True, slots=True)
class TaskCitation:
    """The fragment a task comes from, and a literal excerpt of it."""

    document_id: str
    chunk_index: int
    quote: str


@dataclass(frozen=True, slots=True)
class ProposedTask:
    name: str
    description: str | None
    priority: TaskPriority | None
    citation: TaskCitation


@dataclass(frozen=True, slots=True)
class FindingTasks:
    """The tasks for one finding. No task means the base has nothing that supports one."""

    id: str
    tasks: tuple[ProposedTask, ...]


@dataclass(frozen=True, slots=True)
class ActionPlanDraft:
    """What the model returns, before any citation is checked."""

    findings: tuple[FindingTasks, ...]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ActionPlanProposal:
    """The body of a 200: one entry per finding of the request, in its order."""

    findings: tuple[FindingTasks, ...]
    usage: TokenUsage
    model: str
    proposal_version: str
