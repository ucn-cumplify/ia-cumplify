from dataclasses import dataclass

from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import TokenUsage
from ia_cumplify.domain.legal_body import LegalBody

# Hard caps of the HTTP contract. The backend sends at most 3 articles and 3 matched values
# (AI_SCORE_TOP_ARTICLES and AlertDraftBuilder.MaxReasonValues); these leave room without
# letting a request dump a whole legal body into one OpenAI call.
MAX_ARTICLES = 10
MAX_MATCHED_VALUES = 10
MAX_ACTIONS = 20
# A reason longer than this is dropped; the backend keeps the template (MaxApplicabilityReasonLength).
MAX_REASON_CHARS = 2000


@dataclass(frozen=True, slots=True)
class ApplicabilityArticleInput:
    """One article the backend wants a reason for: labels and actions, never profile text."""

    article_id: str
    matched_values: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplicabilityArticleContext:
    """An input whose article was found on the legal body, ready to send to the model."""

    article: Article
    matched_values: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ArticleApplicabilityReason:
    article_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ApplicabilityReasonerOutput:
    reasons: tuple[ArticleApplicabilityReason, ...]
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ApplicabilityReasons:
    legal_body: LegalBody
    reasons: tuple[ArticleApplicabilityReason, ...]
    # "<prompt version>@<model>", e.g. "applicability-v1@gpt-5.6-luna".
    reason_version: str
    usage: TokenUsage
