from ia_cumplify.application.ports.company_profile_classifier import CompanyProfileClassifierPort
from ia_cumplify.domain.classification import CandidateLabels
from ia_cumplify.domain.company_profile import ClassifiedCompanyProfile
from ia_cumplify.domain.exceptions import InvalidProfileTextError


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
            raise InvalidProfileTextError("The company profile text is empty.")
        if len(content) > self._max_chars:
            raise InvalidProfileTextError(
                f"The company profile text has {len(content)} characters; the limit is {self._max_chars}."
            )
        _require_valid_unicode(content)

        output = self._classifier.classify(content, candidates)
        return ClassifiedCompanyProfile(
            classification=output.classification,
            classifier_version=self._classifier.version,
            usage=output.usage,
        )


def _require_valid_unicode(content: str) -> None:
    """A JSON escape such as \\ud800 gives a lone surrogate, which no request to the model can carry.

    Checked here so it is a 422 of the request, like the length, and not a model failure that the
    backend would retry.
    """
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as exc:
        # from None: the encoding error holds the whole text.
        raise InvalidProfileTextError(
            f"The company profile text has a lone surrogate at position {exc.start}; it is not valid Unicode."
        ) from None
