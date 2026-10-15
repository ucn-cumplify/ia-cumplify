from pydantic import BaseModel, Field

from ia_cumplify.domain.evidence_review import ArticleNature, CitationLocator, RequirementStatus

# Every field is required and has no default. Structured outputs reject a schema that leaves a
# property out of "required", and a default is what leaves it out.


class LlmReviewCitation(BaseModel):
    file_id: str = Field(..., description="The file_id of one FILE block")
    locator: CitationLocator
    reference: str = Field(..., description="1-based page or slide, sheet name, or empty for body")
    quote: str = Field(..., description="Literal excerpt from that file")


class LlmReviewRequirement(BaseModel):
    text: str
    status: RequirementStatus
    citations: list[LlmReviewCitation]
    missing: str


class LlmEvidenceReview(BaseModel):
    nature: ArticleNature
    requirements: list[LlmReviewRequirement]
    verdict: RequirementStatus
    summary: str
