"""Body and response of POST /api/v1/action-plans.

The contract is the one backend-cumplify sends and reads (IaCumplifyClient.ProposeActionPlanAsync and
ActionPlanResponseReader, step 2B.10 of its sprint 3 plan). The backend finds the fragments of the knowledge
base; this service reads no database.
"""

from typing import Annotated, Literal
from uuid import UUID

from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ia_cumplify.adapters.inbound.http.schemas.classification import TokenUsageResponse
from ia_cumplify.domain.action_plan import (
    ACTION_PLAN_PROMPT_VERSION,
    FINDING_DESCRIPTION_MAX_CHARS,
    FINDING_MISSING_MAX_CHARS,
    FINDING_REQUIREMENT_MAX_CHARS,
    FRAGMENT_LOCATOR_MAX_CHARS,
    FRAGMENT_TEXT_MAX_CHARS,
    MAX_FRAGMENTS_PER_FINDING,
    ActionPlanFinding,
    ActionPlanProposal,
    ActionPlanRequest,
    KnowledgeFragment,
    TaskPriority,
)
from ia_cumplify.domain.chat import replace_lone_surrogates

# Text keeps its line breaks. A pattern or a cap keeps the value out of the 422.
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _without_lone_surrogates(value: object) -> object:
    return replace_lone_surrogates(value) if isinstance(value, str) else value


def _blank_as_missing(value: object) -> object:
    """Null, or a string that is empty after trimming, means the optional field was not sent."""
    value = _without_lone_surrogates(value)
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _not_nil(value: UUID) -> UUID:
    if value.int == 0:
        raise ValueError("The id cannot be the nil UUID.")
    return value


class KnowledgeFragmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID = Field(..., description="Id of the knowledge-base document, as in ai_embeddings.entity_id")
    # strict: a "1" or a true is not a chunk_index, as System.Text.Json never sends one.
    chunk_index: int = Field(..., ge=0, strict=True)
    locator: Text | None = Field(default=None, max_length=FRAGMENT_LOCATOR_MAX_CHARS)
    text: Text = Field(..., max_length=FRAGMENT_TEXT_MAX_CHARS)

    @field_validator("document_id")
    @classmethod
    def _document_id(cls, value: UUID) -> UUID:
        return _not_nil(value)

    @field_validator("text", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("locator", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> object:
        return _blank_as_missing(value)


class ActionPlanFindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(..., description="Id of the finding in legal_requirement_findings")
    description: Text = Field(..., max_length=FINDING_DESCRIPTION_MAX_CHARS)
    requirement: Text | None = Field(default=None, max_length=FINDING_REQUIREMENT_MAX_CHARS)
    missing: Text | None = Field(default=None, max_length=FINDING_MISSING_MAX_CHARS)
    fragments: list[KnowledgeFragmentBody] = Field(default_factory=list, max_length=MAX_FRAGMENTS_PER_FINDING)

    @field_validator("id")
    @classmethod
    def _id(cls, value: UUID) -> UUID:
        return _not_nil(value)

    @field_validator("description", mode="before")
    @classmethod
    def _required_text(cls, value: object) -> object:
        return _without_lone_surrogates(value)

    @field_validator("requirement", "missing", mode="before")
    @classmethod
    def _optional_text(cls, value: object) -> object:
        return _blank_as_missing(value)

    @field_validator("fragments", mode="before")
    @classmethod
    def _null_fragments(cls, value: object) -> object:
        return [] if value is None else value


class ActionPlanBody(BaseModel):
    """What the backend sends. Optional fields accept null, as System.Text.Json sends them."""

    model_config = ConfigDict(extra="forbid")

    prompt_version: Literal["action-plan-v1"] = Field(
        ..., description=f"The prompt the backend expects: {ACTION_PLAN_PROMPT_VERSION}"
    )
    findings: list[ActionPlanFindingBody] = Field(..., min_length=1)

    @field_validator("findings")
    @classmethod
    def _unique_findings(cls, findings: list[ActionPlanFindingBody]) -> list[ActionPlanFindingBody]:
        ids = [item.id for item in findings]
        if len(ids) != len(set(ids)):
            raise ValueError("Finding ids must be unique.")
        return findings

    def to_request(self, max_findings: int) -> ActionPlanRequest:
        """The validated request. The cap on findings is a setting, so it is checked here, not in the schema."""
        if len(self.findings) > max_findings:
            raise RequestValidationError(
                [
                    {
                        "type": "too_long",
                        "loc": ("body", "findings"),
                        "msg": f"The request has {len(self.findings)} findings; the limit is {max_findings}.",
                    }
                ]
            )
        return ActionPlanRequest(
            findings=tuple(
                ActionPlanFinding(
                    id=str(item.id),
                    description=item.description,
                    requirement=item.requirement,
                    missing=item.missing,
                    fragments=_unique_fragments(item.fragments),
                )
                for item in self.findings
            )
        )


def _unique_fragments(fragments: list[KnowledgeFragmentBody]) -> tuple[KnowledgeFragment, ...]:
    """The first fragment of each (document_id, chunk_index) stays, as in ActionPlanResponseReader."""
    seen: set[tuple[str, int]] = set()
    kept: list[KnowledgeFragment] = []
    for item in fragments:
        key = (str(item.document_id), item.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        kept.append(
            KnowledgeFragment(document_id=key[0], chunk_index=item.chunk_index, text=item.text, locator=item.locator)
        )
    return tuple(kept)


class TaskCitationResponse(BaseModel):
    document_id: str
    chunk_index: int
    quote: str = Field(..., description="Literal excerpt of that fragment; found in it without case or whitespace")


class ProposedTaskResponse(BaseModel):
    name: str
    description: str | None = None
    priority: TaskPriority | None = None
    citation: TaskCitationResponse


class FindingTasksResponse(BaseModel):
    id: str
    tasks: list[ProposedTaskResponse] = Field(
        ..., description="Empty when the fragments of the knowledge base do not support a task for the finding"
    )


class ActionPlanResponse(BaseModel):
    """What POST /api/v1/action-plans returns: every finding of the request, in its order."""

    proposal_version: str = Field(..., description='Prompt version and model, as "<prompt>@<model>"')
    model: str
    usage: TokenUsageResponse
    findings: list[FindingTasksResponse]

    @classmethod
    def from_domain(cls, result: ActionPlanProposal) -> "ActionPlanResponse":
        return cls(
            proposal_version=result.proposal_version,
            model=result.model,
            usage=TokenUsageResponse.from_domain(result.usage),
            findings=[
                FindingTasksResponse(
                    id=item.id,
                    tasks=[
                        ProposedTaskResponse(
                            name=task.name,
                            description=task.description,
                            priority=task.priority,
                            citation=TaskCitationResponse(
                                document_id=task.citation.document_id,
                                chunk_index=task.citation.chunk_index,
                                quote=task.citation.quote,
                            ),
                        )
                        for task in item.tasks
                    ],
                )
                for item in result.findings
            ],
        )
