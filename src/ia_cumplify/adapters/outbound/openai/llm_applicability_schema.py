from pydantic import BaseModel, Field

from ia_cumplify.domain.applicability import MAX_REASON_CHARS


class LlmArticleReason(BaseModel):
    article_id: str = Field(..., description="The exact article_id from ARTICLES TO EXPLAIN.")
    reason: str = Field(
        ...,
        min_length=1,
        max_length=MAX_REASON_CHARS,
        description=(
            "Spanish paragraph of two or three sentences explaining the match. Name the matched "
            "values in «guillemets». Do not decide applicability. Do not quote a company description."
        ),
    )


class LlmApplicabilityReasons(BaseModel):
    reasons: list[LlmArticleReason]
