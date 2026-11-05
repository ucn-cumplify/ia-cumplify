from typing import Protocol

from ia_cumplify.domain.documentary_situation import DocumentarySituationJudgment, DocumentarySituationSubject


class DocumentarySituationExtractorPort(Protocol):
    @property
    def version(self) -> str:
        """Prompt version and model, as ``"<prompt>@<model>"``."""
        ...

    def extract(self, subject: DocumentarySituationSubject) -> DocumentarySituationJudgment:
        """Write one company documentary situation. Raises DocumentarySituationError when the provider fails."""
        ...
