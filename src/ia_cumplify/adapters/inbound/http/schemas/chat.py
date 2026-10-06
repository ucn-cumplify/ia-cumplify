from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from ia_cumplify.domain.chat import (
    APP_NAME_MAX_CHARS,
    PASSAGE_ID_MAX_CHARS,
    PASSAGE_REFERENCE_MAX_CHARS,
    ChatMessage,
    ChatPassage,
    ChatRequest,
    ChatRole,
    PassageKind,
    replace_lone_surrogates,
)

# A single line: no line break of str.splitlines anywhere. A pattern, unlike a validator, keeps the
# value out of the 422.
_ONE_LINE = "^[^\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]+$"


# Any string constraint makes pydantic-core read the value as UTF-8, so a lone surrogate fails with
# string_unicode. The id keeps that 422: it goes back unchanged, and System.Text.Json cannot read a
# lone surrogate. reference and app_name only reach the model: like the free text, they get U+FFFD.
def _replace_lone_surrogates(value: object) -> object:
    # Before any constraint; anything but a str goes on to the usual errors. The length is kept.
    return replace_lone_surrogates(value) if isinstance(value, str) else value


# Never trimmed: the id goes back unchanged in the citations.
PassageId = Annotated[
    str, StringConstraints(min_length=1, max_length=PASSAGE_ID_MAX_CHARS, pattern=_ONE_LINE)
]
# Trimmed before measuring and before the line check: a line break at the end is just dropped.
PassageReference = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=PASSAGE_REFERENCE_MAX_CHARS, pattern=_ONE_LINE
    ),
]
AppName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=APP_NAME_MAX_CHARS, pattern=_ONE_LINE),
]

# The configurable CHAT_* limits are checked by the use case, after the schema: they need the
# settings, and a missing OPENAI_API_KEY must answer 503 before any of them.


class ChatHistoryMessageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ChatRole = Field(..., description="user or assistant")
    content: str = Field(..., description="Trimmed; an empty message is dropped before counting")


class ChatPassageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: PassageId = Field(
        ..., description="Opaque backend id, unique in the request. Not trimmed: citations return it unchanged"
    )
    kind: PassageKind = Field(..., description="Kind of source, from a closed list")
    reference: PassageReference = Field(
        ..., description="Readable reference, e.g. 'Ley 16.744, art. 66'. The model names the source with it"
    )
    text: str = Field(..., description="Trimmed, between 1 and CHAT_PASSAGE_MAX_CHARS characters")

    @field_validator("reference", mode="before")
    @classmethod
    def _reference_without_lone_surrogates(cls, value: object) -> object:
        return _replace_lone_surrogates(value)


class ChatContextBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_name: AppName | None = Field(
        default=None, description="Legal Requirements app the chat was opened from; null or absent outside an app"
    )

    @field_validator("app_name", mode="before")
    @classmethod
    def _app_name_without_lone_surrogates(cls, value: object) -> object:
        return _replace_lone_surrogates(value)


class ChatRequestBody(BaseModel):
    """POST /api/v1/chat. Optional fields accept null, as System.Text.Json sends them by default."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(..., description="Trimmed, between 1 and CHAT_QUESTION_MAX_CHARS characters")
    history: list[ChatHistoryMessageBody] | None = Field(
        default=None,
        description=(
            "Earlier messages, oldest first, without the current question. Up to CHAT_HISTORY_MAX_MESSAGES "
            "messages and CHAT_HISTORY_MAX_CHARS characters"
        ),
    )
    passages: list[ChatPassageBody] = Field(
        ..., description="What the backend retrieved for this turn, most relevant first. May be empty"
    )
    context: ChatContextBody | None = Field(default=None, description="Context of the conversation")

    @field_validator("passages")
    @classmethod
    def _ids_are_unique(cls, passages: list[ChatPassageBody]) -> list[ChatPassageBody]:
        ids = [passage.id for passage in passages]
        if len(set(ids)) != len(ids):
            raise ValueError("Passage ids must be unique.")
        return passages

    def to_domain(self) -> ChatRequest:
        return ChatRequest(
            question=self.question,
            history=tuple(ChatMessage(role=message.role, content=message.content) for message in self.history or ()),
            passages=tuple(
                ChatPassage(id=passage.id, kind=passage.kind, reference=passage.reference, text=passage.text)
                for passage in self.passages
            ),
            app_name=self.context.app_name if self.context is not None else None,
        )
