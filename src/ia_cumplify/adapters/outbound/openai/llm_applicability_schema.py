from pydantic import BaseModel, Field

from ia_cumplify.domain.applicability import ApplicabilityNature

# Every field is required and has no default, like the schema of applicability-v1 that already ran against
# OpenAI. Lengths are checked in code after parsing: a constraint here would fail the whole response, and
# with it the analysis of every article, for one long text.


class LlmObligation(BaseModel):
    title: str = Field(..., description="Short Spanish title of one obligation the article imposes on the company.")
    summary: str = Field(
        ...,
        description=(
            "One or two Spanish sentences: who must do what, and when if the article says it. "
            "Only from the article text."
        ),
    )


class LlmArticleReason(BaseModel):
    """One listed article: what it asks of the company, then the reason for the match."""

    article_id: str = Field(..., description="The exact article_id from ARTICLES TO EXPLAIN.")
    # Nature and obligations come before the reason, so the model reads the article before it writes
    # about the match.
    nature: ApplicabilityNature = Field(
        ..., description="explicit_obligations, informative or referral, decided only from the article text."
    )
    referred_legal_body: str = Field(
        ...,
        description=(
            "For a referral, the law or regulation it amends or points to, as the article names it. "
            "Empty when the article names none or is not a referral."
        ),
    )
    obligations: list[LlmObligation] = Field(
        ...,
        description=(
            "For explicit_obligations, each obligation the article imposes on the company. "
            "Empty for informative and referral."
        ),
    )
    reason: str = Field(
        ...,
        description=(
            "Spanish paragraph of two or three sentences explaining the match. Name the matched "
            "values in «guillemets». Do not decide applicability. Do not quote a company description. "
            "Empty when the article says Write a reason: no."
        ),
    )


class LlmApplicabilityReasons(BaseModel):
    reasons: list[LlmArticleReason]
