from typing import Protocol

from ia_cumplify.domain.evidence_review import EvidenceReviewSubject, ReviewJudgment


class EvidenceReviewerPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model, as ``"<prompt>@<model>"``."""
        ...

    def review(self, subject: EvidenceReviewSubject) -> ReviewJudgment:
        """Judge the subject. Raises EvidenceReviewError when the provider fails."""
        ...
