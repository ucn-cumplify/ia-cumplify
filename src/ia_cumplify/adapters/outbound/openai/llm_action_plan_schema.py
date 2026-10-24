from pydantic import BaseModel, Field

from ia_cumplify.domain.action_plan import TaskPriority

# Every field is required and has no default. Structured outputs reject a schema that leaves a
# property out of "required", and a default is what leaves it out.


class LlmTaskCitation(BaseModel):
    document_id: str = Field(..., description="The document_id of one FRAGMENT of this finding")
    chunk_index: int = Field(..., description="The chunk_index of that FRAGMENT")
    quote: str = Field(..., description="Literal excerpt from that fragment's text")


class LlmProposedTask(BaseModel):
    name: str = Field(..., description="A short imperative action, in Spanish, on one line")
    description: str = Field(..., description="In Spanish, taken from the fragment. Empty when the name is enough")
    priority: TaskPriority
    citation: LlmTaskCitation


class LlmFindingTasks(BaseModel):
    finding_id: str = Field(..., description="The id of one FINDING block")
    tasks: list[LlmProposedTask] = Field(
        ..., description="Empty when no fragment of this finding supports an action that closes it"
    )


class LlmActionPlan(BaseModel):
    findings: list[LlmFindingTasks]
