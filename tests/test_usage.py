"""call_usage, which the classifier, the company profile and the applicability reasons share."""

from types import SimpleNamespace

import pytest
from openai.types import CompletionUsage

from ia_cumplify.adapters.outbound.openai.usage import call_usage
from ia_cumplify.domain.classification import TokenUsage

REPORTED = {
    "prompt_tokens": 1000,
    "completion_tokens": 100,
    "total_tokens": 1100,
    "prompt_tokens_details": {"cached_tokens": 640},
}


@pytest.mark.parametrize(
    ("value", "count"),
    [
        (12, 12),
        (0, 0),
        (None, 0),
        (-5, 0),
        (True, 0),
        (12.7, 0),
        (float("inf"), 0),
        (float("nan"), 0),
        ("12", 0),
        ("abc", 0),
        ([1], 0),
        ({"tokens": 1}, 0),
    ],
)
def test_each_count_is_a_positive_integer_or_zero(value: object, count: int) -> None:
    # The SDK builds the usage block without validating it. Before, int() raised a TypeError, a ValueError
    # or an OverflowError (Infinity) and failed an answer the provider had already billed.
    usage = SimpleNamespace(
        prompt_tokens=value,
        completion_tokens=value,
        total_tokens=value,
        prompt_tokens_details=SimpleNamespace(cached_tokens=value),
    )
    assert call_usage(usage) == TokenUsage(
        prompt_tokens=count,
        completion_tokens=count,
        total_tokens=count,
        llm_calls=1,
        cached_tokens=count,
    )


def test_the_block_reads_the_same_from_the_sdk_or_from_its_json() -> None:
    expected = TokenUsage(
        prompt_tokens=1000,
        completion_tokens=100,
        total_tokens=1100,
        llm_calls=1,
        cached_tokens=640,
    )
    assert call_usage(CompletionUsage.model_validate(REPORTED)) == expected
    assert call_usage(REPORTED) == expected


@pytest.mark.parametrize(
    "block",
    [None, {}, [REPORTED], "1100", 1100, {"prompt_tokens_details": "x"}],
)
def test_without_a_usage_block_only_the_call_counts(block: object) -> None:
    assert call_usage(block) == TokenUsage(llm_calls=1)
