"""DEV-ONLY metrics. Delete this file and every `# DEV-ONLY` comment to remove it."""

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from collections.abc import Sequence

from pydantic import BaseModel

from ia_cumplify.adapters.outbound.openai.classifier import OpenAIArticleClassifierAdapter
from ia_cumplify.domain.article import Article
from ia_cumplify.domain.classification import ArticleClassification
from ia_cumplify.domain.legal_body import LegalBody


class DevMetricsPayload(BaseModel):
    elapsed_ms: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    llm_calls: int


@dataclass
class DevTokenMeter:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    _started_at: float = field(default_factory=perf_counter)

    def add_usage(self, usage: Any) -> None:
        if usage is None:
            return
        self.prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        self.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
        self.llm_calls += 1

    def payload(self) -> DevMetricsPayload:
        return DevMetricsPayload(
            elapsed_ms=round((perf_counter() - self._started_at) * 1000, 2),
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            llm_calls=self.llm_calls,
        )


class MeteredArticleClassifier:
    def __init__(self, inner: OpenAIArticleClassifierAdapter, meter: DevTokenMeter) -> None:
        self._inner = inner
        self._meter = meter

    def classify(
        self,
        legal_body: LegalBody,
        article: Article,
        all_articles: Sequence[Article],
    ) -> ArticleClassification:
        classification, usage = self._inner.classify_with_usage(
            legal_body, article, all_articles
        )
        self._meter.add_usage(usage)
        return classification
