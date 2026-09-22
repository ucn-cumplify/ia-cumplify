from openai import OpenAI

from ia_cumplify.adapters.outbound.openai.llm_schema import LlmArticleClassification
from ia_cumplify.adapters.outbound.openai.prompts import SYSTEM_PROMPT
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import ArticleClassification
from ia_cumplify.domain.exceptions import ClassificationError
from ia_cumplify.domain.legal_body import LegalBody


class OpenAIArticleClassifierAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = OpenAI(api_key=api_key)

    def classify(self, legal_body: LegalBody, article: Article) -> ArticleClassification:
        user_content = (
            f"Legal body ID: {legal_body.id}\n"
            f"Legal body title: {legal_body.title}\n"
            f"Article ID: {article.id}\n"
            f"Article number: {article.number}\n"
            f"Section: {article.section}\n\n"
            f"--- ARTICLE TEXT ---\n{article.text}\n--- END ---"
        )

        completion = self._client.chat.completions.parse(
            model=self._model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            response_format=LlmArticleClassification,
            reasoning_effort=self._reasoning_effort,
        )

        message = completion.choices[0].message
        if message.parsed is None:
            raise ClassificationError(
                f"Model did not return a valid classification for article {article.id}"
            )

        return _to_domain(message.parsed)


def _to_domain(parsed: LlmArticleClassification) -> ArticleClassification:
    return ArticleClassification(
        scope=tuple(parsed.scope),
        productive_sector=tuple(parsed.productive_sector),
        territorial_coverage=tuple(parsed.territorial_coverage),
        activity_action=tuple(parsed.activity_action),
        facility_installation_equipment=tuple(parsed.facility_installation_equipment),
    )
