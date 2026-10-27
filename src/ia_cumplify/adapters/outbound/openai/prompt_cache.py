"""The prompt cache mode of every call to chat completions ("Caché de prompts" in docs/Chat/api.md).

Agreed on 2026-10-19, option (b): the explicit mode with a single breakpoint at the end of the fixed part of
the prompt. OpenAI then writes to its cache only the prefix that ends there, so the instructions keep their
discount and the data of the request, which comes after the breakpoint, never enters the cache. An explicit
request without any breakpoint does not use the cache at all. Supported from gpt-5.6 on: an older model can
reject the request with a 400.

The six calls use it. The breakpoint ends the system prompt in the chat, the evidence review, the action plan
proposal, the classification and the applicability reasons. In the company profile it goes after EXISTING
LABELS or, without labels, at the end of the system prompt. The classification and the applicability reasons
joined on 2026-10-26 (step 2.4 of the sprint 4 plan): until then, in the implicit mode, OpenAI chose the
prefix, and it could hold the law, the labels or the matched values of the request.
"""

from typing import Any

PROMPT_CACHE_OPTIONS = {"mode": "explicit"}


def cached_prefix(text: str) -> dict[str, Any]:
    """A text part that ends the cacheable prefix: what comes after it is never cached."""
    return {"type": "text", "text": text, "prompt_cache_breakpoint": {"mode": "explicit"}}
