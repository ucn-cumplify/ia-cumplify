from ia_cumplify.application.ports.company_profile_classifier import CompanyProfileClassifierPort
from ia_cumplify.domain.classification import CandidateLabels
from ia_cumplify.domain.company_profile import ClassifiedCompanyProfile


class ClassifyCompanyProfileUseCase:
    """Classifies the text a company writes about itself. It does not read the database.

    The text is the company's own description: errors report its length, never its content.
    """

    def __init__(self, classifier: CompanyProfileClassifierPort, max_chars: int) -> None:
        self._classifier = classifier
        self._max_chars = max_chars

    def execute(self, text: str, candidates: CandidateLabels | None = None) -> ClassifiedCompanyProfile:
        content = text.strip()
        if not content:
            raise ValueError("The company profile text is empty.")
        if len(content) > self._max_chars:
            raise ValueError(
                f"The company profile text has {len(content)} characters; the limit is {self._max_chars}."
            )

        output = self._classifier.classify(content, candidates)
        return ClassifiedCompanyProfile(
            classification=output.classification,
            classifier_version=self._classifier.version,
            usage=output.usage,
        )
