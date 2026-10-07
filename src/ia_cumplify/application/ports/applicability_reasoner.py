from collections.abc import Sequence
from typing import Protocol

from ia_cumplify.domain.applicability import ApplicabilityArticleContext, ApplicabilityReasonerOutput
from ia_cumplify.domain.legal_body import LegalBody


class ApplicabilityReasonerPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model that produce the reasons, as "<prompt>@<model>"."""
        ...

    def explain(
        self,
        legal_body: LegalBody,
        articles: Sequence[ApplicabilityArticleContext],
    ) -> ApplicabilityReasonerOutput: ...
