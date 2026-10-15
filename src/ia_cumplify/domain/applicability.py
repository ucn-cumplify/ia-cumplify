from dataclasses import dataclass
from typing import Literal, get_args

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
# The analysis of applicability-v2. Past these caps an obligation, or the name of the law a referral
# points to, is dropped instead of reaching the alert.
MAX_OBLIGATIONS = 10
MAX_OBLIGATION_TITLE_CHARS = 200
MAX_OBLIGATION_SUMMARY_CHARS = 1000
MAX_REFERRED_LEGAL_BODY_CHARS = 300

# What an article asks of the company (C9, decision 7 of the sprint 3 plan): obligaciones explícitas,
# informativo, remisión. The evidence review declares its own ArticleNature: the two may differ until both
# follow the same rules.
ApplicabilityNature = Literal["explicit_obligations", "informative", "referral"]
APPLICABILITY_NATURES: frozenset[str] = frozenset(get_args(ApplicabilityNature))


@dataclass(frozen=True, slots=True)
class ApplicabilityArticleInput:
    """One article the backend sends: labels and actions, never profile text.

    ``write_reason`` is false when the backend keeps its own text for the article, such as a
    fuzzy pair of level 2 or an alert that already has its reason, and only wants the analysis.
    """

    article_id: str
    matched_values: tuple[str, ...]
    actions: tuple[str, ...]
    write_reason: bool = True


@dataclass(frozen=True, slots=True)
class ApplicabilityArticleContext:
    """An input whose article was found on the legal body, ready to send to the model."""

    article: Article
    matched_values: tuple[str, ...]
    actions: tuple[str, ...]
    write_reason: bool = True


@dataclass(frozen=True, slots=True)
class ArticleApplicabilityReason:
    article_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ArticleObligation:
    """An obligation the article itself imposes on the company. Never taken from activity_action."""

    title: str
    summary: str


@dataclass(frozen=True, slots=True)
class ArticleAnalysis:
    """What one article asks of the company (C9).

    ``explicit_obligations`` carries at least one obligation; ``informative`` and ``referral``
    carry none. ``referred_legal_body`` is the law a referral amends or points to, as the
    article names it, or None.
    """

    article_id: str
    nature: ApplicabilityNature
    obligations: tuple[ArticleObligation, ...]
    referred_legal_body: str | None


@dataclass(frozen=True, slots=True)
class ApplicabilityReasonerOutput:
    reasons: tuple[ArticleApplicabilityReason, ...]
    usage: TokenUsage
    analyses: tuple[ArticleAnalysis, ...] = ()


@dataclass(frozen=True, slots=True)
class ApplicabilityReasons:
    legal_body: LegalBody
    reasons: tuple[ArticleApplicabilityReason, ...]
    # "<prompt version>@<model>", e.g. "applicability-v2@gpt-5.6-luna".
    reason_version: str
    usage: TokenUsage
    analyses: tuple[ArticleAnalysis, ...] = ()


def close_analysis(analysis: ArticleAnalysis) -> ArticleAnalysis | None:
    """Applies in code the part of C9 the model could get wrong.

    An informative or referral article has no obligations. Only a referral keeps the law it
    names. An article with explicit obligations and no usable one is dropped, so the backend
    shows it as not evaluated instead of an empty list. Texts come back on one line.
    """
    if analysis.nature not in APPLICABILITY_NATURES:
        return None
    if analysis.nature == "explicit_obligations":
        obligations = _usable_obligations(analysis.obligations)
        if not obligations:
            return None
        return ArticleAnalysis(analysis.article_id, "explicit_obligations", obligations, None)
    referred = None
    if analysis.nature == "referral":
        referred = _one_line(analysis.referred_legal_body or "") or None
        if referred is not None and len(referred) > MAX_REFERRED_LEGAL_BODY_CHARS:
            referred = None
    return ArticleAnalysis(analysis.article_id, analysis.nature, (), referred)


def _usable_obligations(obligations: tuple[ArticleObligation, ...]) -> tuple[ArticleObligation, ...]:
    kept: list[ArticleObligation] = []
    seen: set[tuple[str, str]] = set()
    for obligation in obligations:
        title = _one_line(obligation.title)
        summary = _one_line(obligation.summary)
        if not title or not summary:
            continue
        if len(title) > MAX_OBLIGATION_TITLE_CHARS or len(summary) > MAX_OBLIGATION_SUMMARY_CHARS:
            continue
        key = (title.casefold(), summary.casefold())
        if key in seen:
            continue
        seen.add(key)
        kept.append(ArticleObligation(title=title, summary=summary))
        if len(kept) == MAX_OBLIGATIONS:
            break
    return tuple(kept)


def _one_line(text: str) -> str:
    return " ".join(text.split())
