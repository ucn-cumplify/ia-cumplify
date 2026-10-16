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
    text: str = Field(..., description="The exigencia, in Spanish, taken only from the article text.")
    evidence: str = Field(
        ...,
        description=(
            "The respaldo that accredits the exigencia: a short Spanish name of the document or record. "
            "Without one, it is not a requirement."
        ),
    )
    status: RequirementStatus
    citations: list[LlmReviewCitation]
    missing: str


class LlmEvidenceReview(BaseModel):
    nature: ArticleNature
    procedure_for: str = Field(
        ...,
        description=(
            "For a procedural article, the authorization, permit, license or registration it serves, as the "
            "article or the legal body names it. Empty when the article is not procedural."
        ),
    )
    requirements: list[LlmReviewRequirement]
    verdict: RequirementStatus
    summary: str
