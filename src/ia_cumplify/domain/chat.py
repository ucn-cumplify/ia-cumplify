import re
from dataclasses import dataclass
from typing import Literal, get_args

ChatRole = Literal["user", "assistant"]
# Closed list, still to be agreed with the backend and the frontend (docs/Chat/api.md): it fixes
# which citation kinds the widget shows.
PassageKind = Literal["article", "legal_body", "legal_requirement", "vinculation", "obligation"]
Coverage = Literal["answered", "not_covered", "uncited", "refused", "no_passages"]
FinishReason = Literal["stop", "length", "content_filter"]
ProviderErrorCode = Literal["rate_limited", "timeout", "provider_error", "provider_rejected"]
ChatErrorCode = Literal[
    "rate_limited", "timeout", "provider_error", "provider_rejected", "empty_output", "internal"
]

CHAT_ROLES: tuple[str, ...] = get_args(ChatRole)
PASSAGE_KINDS: tuple[str, ...] = get_args(PassageKind)
FINISH_REASONS: tuple[str, ...] = get_args(FinishReason)
PROVIDER_ERROR_CODES: tuple[str, ...] = get_args(ProviderErrorCode)
# Whether the same request may work later. It never obliges the backend to retry.
RETRYABLE_ERROR_CODES = frozenset({"rate_limited", "timeout", "provider_error"})

# Fixed by the contract, unlike the configurable CHAT_* limits.
PASSAGE_ID_MAX_CHARS = 100
PASSAGE_REFERENCE_MAX_CHARS = 200
APP_NAME_MAX_CHARS = 200

# The system prompt asks the model to open with this mark when the passages do not answer the question.
# The output filter and the input neutralization derive their patterns from the same word. A word of
# ASCII letters and underscores; changing it bumps CHAT_PROMPT_VERSION.
NOT_COVERED_WORD = "SIN_RESPALDO"
NOT_COVERED_MARK = f"[{NOT_COVERED_WORD}]"

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def replace_lone_surrogates(text: str) -> str:
    """U+FFFD for each lone surrogate, which keeps every length. Escaped in JSON, System.Text.Json
    cannot read one."""
    return _LONE_SURROGATE.sub("\ufffd", text)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """An earlier message of the conversation. It helps read the question; it is never a source."""

    role: ChatRole
    content: str


@dataclass(frozen=True, slots=True)
class ChatPassage:
    """Something the backend retrieved for this turn. The id is opaque: it only comes back in a citation."""

    id: str
    kind: PassageKind
    reference: str
    text: str


@dataclass(frozen=True, slots=True)
class ChatRequest:
    question: str
    history: tuple[ChatMessage, ...] = ()
    # Most relevant first. Empty is valid: the answer is a fixed refusal without a model call.
    passages: tuple[ChatPassage, ...] = ()
    # Legal Requirements app the chat was opened in. The backend always sends it, since the chat only lives
    # inside these apps; None leaves out the App line.
    app_name: str | None = None


@dataclass(frozen=True, slots=True)
class PromptPassage:
    """A passage as the model sees it: a short key ("P1", "P2"...) instead of the backend id."""

    key: str
    kind: PassageKind
    reference: str
    text: str


@dataclass(frozen=True, slots=True)
class ChatPrompt:
    """What a responder sends to the model. It carries no passage ids, so the model can never cite one."""

    question: str
    history: tuple[ChatMessage, ...]
    passages: tuple[PromptPassage, ...]
    app_name: str | None = None


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """Tokens of the chat call. Its own type: the shared TokenUsage has no cache or reasoning tokens."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Part of prompt_tokens served from the provider's cache.
    cached_tokens: int = 0
    # Part of completion_tokens spent reasoning: billed as output, never shown.
    reasoning_tokens: int = 0
    llm_calls: int = 0


@dataclass(frozen=True, slots=True)
class ChatCitation:
    n: int  # the [n] marker in the text
    id: str  # the passage id the backend sent


@dataclass(frozen=True, slots=True)
class ChatDeltaEvent:
    text: str


@dataclass(frozen=True, slots=True)
class ChatDoneEvent:
    citations: tuple[ChatCitation, ...]
    coverage: Coverage
    citations_dropped: int
    finish_reason: FinishReason
    # None when the provider did not report it: the backend estimates the usage.
    usage: ChatUsage | None
    chat_version: str


@dataclass(frozen=True, slots=True)
class ChatErrorEvent:
    code: ChatErrorCode
    retryable: bool
    usage: ChatUsage | None
    chat_version: str


# What the answer streams: deltas, then exactly one done or error.
ChatEvent = ChatDeltaEvent | ChatDoneEvent | ChatErrorEvent


@dataclass(frozen=True, slots=True)
class ProviderTextEvent:
    text: str


@dataclass(frozen=True, slots=True)
class ProviderRefusalEvent:
    """The provider flagged the answer as a refusal. Its text is dropped: it could quote the request."""


@dataclass(frozen=True, slots=True)
class ProviderFinishEvent:
    # As the provider sent it; normalize_finish_reason maps it to the contract.
    reason: str


@dataclass(frozen=True, slots=True)
class ProviderUsageEvent:
    usage: ChatUsage


# What a responder reads from the provider, in order.
ProviderEvent = ProviderTextEvent | ProviderRefusalEvent | ProviderFinishEvent | ProviderUsageEvent


def normalize_finish_reason(reason: str) -> FinishReason:
    if reason == "length":
        return "length"
    if reason == "content_filter":
        return "content_filter"
    # "stop", plus the tool values ("tool_calls", "function_call"): the chat never offers tools.
    return "stop"


def is_retryable(code: str) -> bool:
    return code in RETRYABLE_ERROR_CODES
