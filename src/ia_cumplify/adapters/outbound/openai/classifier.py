from collections.abc import Sequence

from openai import OpenAI, OpenAIError

from ia_cumplify.adapters.outbound.openai.llm_schema import (
    LlmArticleClassification,
    LlmLegalBodyClassification,
)
from ia_cumplify.adapters.outbound.openai.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    render_candidate_labels,
)
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import (
    ArticleClassification,
    CandidateLabels,
    ClassifiedArticle,
    ClassifierOutput,
    TokenUsage,
)
from ia_cumplify.domain.exceptions import ClassificationError
from ia_cumplify.domain.legal_body import LegalBody


class OpenAIArticleClassifierAdapter:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        batch_size: int = 25,
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._batch_size = max(1, batch_size)
        self._client = OpenAI(api_key=api_key)

    @property
    def version(self) -> str:
        return f"{PROMPT_VERSION}@{self._model}"

    def classify_many(
        self,
        legal_body: LegalBody,
        all_articles: Sequence[Article],
        targets: Sequence[Article],
        candidates: CandidateLabels | None = None,
    ) -> ClassifierOutput:
        if not targets:
            return ClassifierOutput(articles=(), usage=TokenUsage())

        classified: list[ClassifiedArticle] = []
        usage = TokenUsage()
        for chunk in _chunks(list(targets), self._batch_size):
            chunk_results, chunk_usage = self._classify_chunk(
                legal_body, all_articles, chunk, candidates
            )
            classified.extend(chunk_results)
            usage += chunk_usage
        return ClassifierOutput(articles=tuple(classified), usage=usage)

    def _classify_chunk(
        self,
        legal_body: LegalBody,
        all_articles: Sequence[Article],
        targets: Sequence[Article],
        candidates: CandidateLabels | None,
    ) -> tuple[list[ClassifiedArticle], TokenUsage]:
        # The existing labels go right before the targets, next to where the model writes labels.
        # They are the same for every batch of a legal body, so the shared prefix stays cacheable.
        blocks = [_render_legal_body(legal_body, all_articles)]
        if candidates is not None and not candidates.is_empty():
            blocks.append(render_candidate_labels(candidates))
        blocks.append(_render_targets(targets))
        user_content = "\n\n".join(blocks)

        try:
            completion = self._client.chat.completions.parse(
                model=self._model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format=LlmLegalBodyClassification,
                reasoning_effort=self._reasoning_effort,
            )
        except OpenAIError as exc:
            raise ClassificationError(
                f"OpenAI error while classifying legal body {legal_body.id}: {exc}"
            ) from exc

        message = completion.choices[0].message
        if message.parsed is None:
            refusal = getattr(message, "refusal", None)
            extra = f" Refusal: {refusal}" if refusal else ""
            raise ClassificationError(
                f"Model did not return a valid classification for legal body {legal_body.id}.{extra}"
            )

        return (
            _map_parsed_to_targets(message.parsed, targets),
            _to_usage(getattr(completion, "usage", None)),
        )


def _to_usage(usage: object | None) -> TokenUsage:
    # The call counts even when the provider omits the usage block.
    if usage is None:
        return TokenUsage(llm_calls=1)
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        llm_calls=1,
    )


def _chunks(items: list[Article], size: int) -> list[list[Article]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


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


def _render_targets(targets: Sequence[Article]) -> str:
    ids = ", ".join(article.id for article in targets)
    blocks = [_render_article_block(article) for article in targets]
    return (
        "--- ARTICLES TO CLASSIFY ---\n"
        f"Classify exactly these {len(targets)} article_id values: {ids}\n\n"
        + "\n\n".join(blocks)
        + "\n--- END ARTICLES TO CLASSIFY ---"
    )


def _render_article_block(article: Article) -> str:
    return (
        f"Article ID: {article.id}\n"
        f"Number: {article.number}\n"
        f"Section: {article.section}\n"
        f"Order: {article.order}\n"
        f"Text:\n{article.text}"
    )


def _map_parsed_to_targets(
    parsed: LlmLegalBodyClassification,
    targets: Sequence[Article],
) -> list[ClassifiedArticle]:
    by_id = {item.article_id: item.classification for item in parsed.results}
    missing = [article.id for article in targets if article.id not in by_id]
    if missing:
        raise ClassificationError(
            "Model omitted classifications for article_id(s): " + ", ".join(missing)
        )

    return [
        ClassifiedArticle(
            article_id=article.id,
            number=article.number,
            classification=_to_domain(by_id[article.id]),
        )
        for article in targets
    ]


def _to_domain(parsed: LlmArticleClassification) -> ArticleClassification:
    return ArticleClassification(
        scope=tuple(parsed.scope),
        productive_sector=tuple(parsed.productive_sector),
        territorial_coverage=tuple(parsed.territorial_coverage),
        activity_action=tuple(parsed.activity_action),
        facility_installation_equipment=tuple(parsed.facility_installation_equipment),
    )
