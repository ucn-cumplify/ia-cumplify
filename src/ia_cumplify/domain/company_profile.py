from dataclasses import dataclass

from ia_cumplify.domain.classification import ArticleClassification, TokenUsage


@dataclass(frozen=True, slots=True)
class ProfileClassifierOutput:
    """Six dimensions the model returned for a company description, and what the call cost."""

    classification: ArticleClassification
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class ClassifiedCompanyProfile:
    """The declared profile of a company, in the same six dimensions as an article.

    The backend compares these labels with the labels of legal articles, so both sides use the same
    dimensions and, through the candidate labels, the same vocabulary.
    """

    classification: ArticleClassification
    # "<prompt version>@<model>", e.g. "profile-v1@gpt-5.6-luna". The backend stores it with the text.
    classifier_version: str
    usage: TokenUsage
