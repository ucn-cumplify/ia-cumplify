"""The prompt cache mode of the calls that carry company data ("Caché de prompts" in docs/Chat/api.md).

Agreed on 2026-10-19, option (b): the explicit mode with a single breakpoint at the end of the fixed part of
the prompt. OpenAI then writes to its cache only the prefix that ends there, so the instructions keep their
discount and the data of the request, which comes after the breakpoint, never enters the cache. An explicit
request without any breakpoint does not use the cache at all. Supported from gpt-5.6 on: an older model can
reject the request with a 400.
"""

from typing import Any

PROMPT_CACHE_OPTIONS = {"mode": "explicit"}


def cached_prefix(text: str) -> dict[str, Any]:
    """A text part that ends the cacheable prefix: what comes after it is never cached."""
    return {"type": "text", "text": text, "prompt_cache_breakpoint": {"mode": "explicit"}}
