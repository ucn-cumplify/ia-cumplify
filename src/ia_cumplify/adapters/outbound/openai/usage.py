from collections.abc import Mapping

from ia_cumplify.domain.classification import TokenUsage


def call_usage(usage: object | None) -> TokenUsage:
    """Tokens of one model call, from the usage block of its completion: the SDK's object or, when the SDK
    could not parse the body, the block of its JSON.

    The call counts even when the provider omits that block. cached_tokens is read from
    prompt_tokens_details, as in the chat. Each count is read on its own, with the rule of the chat: one
    that is missing or is not a positive integer counts 0. The SDK builds the block without validating it,
    and a malformed count must not fail an answer the provider already billed.
    """
    if usage is None:
        return TokenUsage(llm_calls=1)
    details = _field(usage, "prompt_tokens_details")
    return TokenUsage(
        prompt_tokens=_count(_field(usage, "prompt_tokens")),
        completion_tokens=_count(_field(usage, "completion_tokens")),
        total_tokens=_count(_field(usage, "total_tokens")),
        llm_calls=1,
        cached_tokens=_count(_field(details, "cached_tokens")),
    )


def _field(block: object, name: str) -> object:
    if isinstance(block, Mapping):
        return block.get(name)
    return getattr(block, name, None)


def _count(value: object) -> int:
    # The same rule as chat_responder._count: bool is an int in Python, but never a count.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 0
