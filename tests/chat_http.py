"""Helpers for the HTTP tests of POST /api/v1/chat: request bodies, and a reader of the response that
checks the stream rules of the contract the way the backend reads it (SSE blocks, data as JSON)."""

import json
import re
from dataclasses import dataclass, field

import pytest

SERVICE_KEY = "test-service-key"
HEADERS = {"X-API-Key": SERVICE_KEY}
SECRET = "SECRETO-de-la-empresa"

DONE_KEYS = ["citations", "coverage", "citations_dropped", "finish_reason", "usage", "chat_version", "dev_metrics"]
ERROR_KEYS = ["code", "retryable", "usage", "chat_version"]
USAGE_KEYS = ["prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens", "reasoning_tokens", "llm_calls"]
ZERO_USAGE = dict.fromkeys(USAGE_KEYS, 0)

_MARKER = re.compile(r"\[(\d+)\]")
# What the citation filter treats as a bracketed number.
_ANY_NUMBER = re.compile(r"\[\s*\d+\s*\]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def _strings(value: object) -> list[str]:
    """Every string inside a decoded JSON value, keys included."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for key, item in value.items() for text in (key, *_strings(item))]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def configure(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """The service key plus the given variables (names in lower case, as Settings fields)."""
    monkeypatch.setenv("SERVICE_API_KEY", SERVICE_KEY)
    for name, value in env.items():
        monkeypatch.setenv(name.upper(), value)


def passage(number: int = 1, **changes: object) -> dict:
    return {
        "id": f"id-{number}",
        "kind": "article",
        "reference": f"Ley 16.744, art. {number}",
        "text": f"Texto del pasaje {number}.",
        **changes,
    }


def chat_body(**changes: object) -> dict:
    return {"question": "¿Qué exige la norma?", "passages": [passage(1), passage(2)], **changes}


@dataclass
class SseEvent:
    name: str | None
    data: object
    raw: str


@dataclass
class ChatReply:
    body: str
    events: list[SseEvent]
    comments: list[str] = field(default_factory=list)

    @property
    def deltas(self) -> list[str]:
        return [event.data["text"] for event in self.events if event.name == "delta"]  # type: ignore[index]

    @property
    def text(self) -> str:
        return "".join(self.deltas)

    @property
    def final(self) -> SseEvent:
        return self.events[-1]

    @property
    def done(self) -> dict:
        assert self.final.name == "done", self.final
        return self.final.data  # type: ignore[return-value]

    @property
    def error(self) -> dict:
        assert self.final.name == "error", self.final
        return self.final.data  # type: ignore[return-value]


def parse_sse(body: str) -> tuple[list[SseEvent], list[str]]:
    """Events and comments of a text/event-stream body. Each data field must be one line of JSON."""
    events: list[SseEvent] = []
    comments: list[str] = []
    assert body.endswith("\n\n"), "every event ends with a blank line"
    for block in body.split("\n\n"):
        if not block:
            continue
        name: str | None = None
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                comments.append(line[1:].strip())
                continue
            key, _, value = line.partition(": ")
            # No id: and no retry: a cut stream cannot be resumed.
            assert key in ("event", "data"), line
            if key == "event":
                name = value
            else:
                data.append(value)
        if name is None and not data:
            continue
        assert len(data) == 1, block
        events.append(SseEvent(name=name, data=json.loads(data[0]), raw=data[0]))
    return events, comments


def read_reply(response) -> ChatReply:
    """The chat stream of a 200 response, after checking every rule the backend relies on."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    events, comments = parse_sse(response.text)
    reply = ChatReply(body=response.text, events=events, comments=comments)
    assert_chat_events(reply)
    return reply


def assert_chat_events(reply: ChatReply) -> None:
    assert reply.events, "a stream always ends with a final event"
    *body, final = reply.events
    assert all(event.name == "delta" for event in body), [event.name for event in reply.events]
    assert final.name in ("done", "error"), final.name
    for event in body:
        assert list(event.data) == ["text"] and event.data["text"], event  # type: ignore[arg-type,index]
    # A lone surrogate, escaped in the JSON, is unreadable for System.Text.Json. json.loads joins a
    # valid pair (an emoji) into one character, so what is left in a decoded string is a lone one.
    assert not any(_LONE_SURROGATE.search(text) for event in reply.events for text in _strings(event.data))
    # A bracketed number never comes split between two deltas, and every one is a marker written "[n]".
    assert sum(len(_ANY_NUMBER.findall(delta)) for delta in reply.deltas) == len(_ANY_NUMBER.findall(reply.text))
    assert all(re.fullmatch(r"\[[0-9]+\]", number) for number in _ANY_NUMBER.findall(reply.text)), reply.text
    data = final.data
    assert isinstance(data, dict)
    if final.name == "error":
        assert list(data) == ERROR_KEYS, data
        assert data["code"] in ("rate_limited", "timeout", "provider_error", "provider_rejected", "empty_output", "internal")
        assert data["retryable"] is (data["code"] in ("rate_limited", "timeout", "provider_error"))
    else:
        assert list(data) == DONE_KEYS, data
        assert body, "done always comes after at least one delta"
        numbers = [citation["n"] for citation in data["citations"]]
        assert numbers == list(range(1, len(numbers) + 1))
        assert all(list(citation) == ["n", "id"] for citation in data["citations"])
        ids = [citation["id"] for citation in data["citations"]]
        assert len(ids) == len(set(ids))
        # Every [n] in the text has its citation, and every citation its [n].
        assert {int(n) for n in _MARKER.findall(reply.text)} == set(numbers)
        # Numbered by first appearance: 1, 2, 3...
        assert list(dict.fromkeys(int(n) for n in _MARKER.findall(reply.text))) == numbers
        assert data["finish_reason"] in ("stop", "length", "content_filter")
    if data["usage"] is not None:
        assert list(data["usage"]) == USAGE_KEYS, data["usage"]
