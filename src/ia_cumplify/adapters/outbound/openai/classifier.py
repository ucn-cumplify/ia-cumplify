from collections.abc import Sequence

from openai import OpenAI, OpenAIError

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

    def classify(
        self,
        legal_body: LegalBody,
        article: Article,
        all_articles: Sequence[Article],
    ) -> ArticleClassification:
        classification, _usage = self.classify_with_usage(legal_body, article, all_articles)
        return classification

    # DEV-ONLY: used by inbound/http/dev_metrics.py
    def classify_with_usage(
        self,
        legal_body: LegalBody,
        article: Article,
        all_articles: Sequence[Article],
    ) -> tuple[ArticleClassification, object | None]:
        user_content = (
            f"{_render_legal_body(legal_body, all_articles)}\n\n"
            f"{_render_target_article(article)}"
        )

        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=LlmArticleClassification,
                reasoning_effort=self._reasoning_effort,
            )
        except OpenAIError as exc:
            raise ClassificationError(
                f"OpenAI error for article {article.id}: {exc}"
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refusal = getattr(message, "refusal", None)
            extra = f" Refusal: {refusal}" if refusal else ""
            raise ClassificationError(
                f"Model did not return a valid classification for article {article.id}.{extra}"
            )

        return _to_domain(message.parsed), getattr(completion, "usage", None)


def _render_legal_body(legal_body: LegalBody, articles: Sequence[Article]) -> str:
    header_lines = [
        "--- FULL LEGAL BODY (base context) ---",
        f"ID: {legal_body.id}",
        f"Title: {legal_body.title}",
        f"Type: {legal_body.type}",
    ]
    if legal_body.summary:
        header_lines.append(f"Summary:\n{legal_body.summary}")

    article_blocks = [_render_article_block(item) for item in articles]
    body = "\n\n".join(article_blocks) if article_blocks else "(no articles)"
    return "\n".join(header_lines) + "\n\n" + body + "\n--- END FULL LEGAL BODY ---"


def _render_target_article(article: Article) -> str:
    return (
        "--- TARGET ARTICLE TO CLASSIFY ---\n"
        f"{_render_article_block(article)}\n"
        "--- END TARGET ARTICLE ---"
    )


def _render_article_block(article: Article) -> str:
    return (
        f"Article ID: {article.id}\n"
        f"Number: {article.number}\n"
        f"Section: {article.section}\n"
        f"Order: {article.order}\n"
        f"Text:\n{article.text}"
    )


def _to_domain(parsed: LlmArticleClassification) -> ArticleClassification:
    return ArticleClassification(
        scope=tuple(parsed.scope),
        productive_sector=tuple(parsed.productive_sector),
        territorial_coverage=tuple(parsed.territorial_coverage),
        activity_action=tuple(parsed.activity_action),
        facility_installation_equipment=tuple(parsed.facility_installation_equipment),
    )
