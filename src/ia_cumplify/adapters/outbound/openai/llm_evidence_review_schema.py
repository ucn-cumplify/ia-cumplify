from pydantic import BaseModel, Field

from ia_cumplify.domain.evidence_review import ArticleNature, CitationLocator, RequirementStatus


class LlmReviewCitation(BaseModel):
    file_id: str = Field(..., description="The file_id of one FILE block")
    locator: CitationLocator
    reference: str = Field(default="", description="1-based page or slide, sheet name, or empty for body")
    quote: str = Field(..., description="Literal excerpt from that file")


class LlmReviewRequirement(BaseModel):
    text: str
    status: RequirementStatus
    citations: list[LlmReviewCitation] = Field(default_factory=list)
    missing: str = ""


class LlmEvidenceReview(BaseModel):
    nature: ArticleNature
    requirements: list[LlmReviewRequirement]
    verdict: RequirementStatus
    summary: str
