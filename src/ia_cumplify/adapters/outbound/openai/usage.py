from ia_cumplify.domain.classification import TokenUsage


def call_usage(usage: object | None) -> TokenUsage:
    """Tokens of one model call, from the usage block of its completion.

    The call counts even when the provider omits that block. cached_tokens is read from
    prompt_tokens_details, as in the chat.
    """
    if usage is None:
        return TokenUsage(llm_calls=1)
    details = getattr(usage, "prompt_tokens_details", None)
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        llm_calls=1,
        cached_tokens=int(getattr(details, "cached_tokens", 0) or 0),
    )
