from typing import Protocol

from ia_cumplify.domain.classification import CandidateLabels
from ia_cumplify.domain.company_profile import ProfileClassifierOutput


class CompanyProfileClassifierPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model that produce the labels, as "<prompt>@<model>"."""
        ...

    def classify(self, text: str, candidates: CandidateLabels | None = None) -> ProfileClassifierOutput: ...
