"""The other side of POST /api/v1/action-plans, as backend-cumplify implements it (PR #225, step 2B.10).

backend_body() builds the request the way IaCumplifyClient.ProposeActionPlanAsync serializes it with
System.Text.Json: snake_case keys in that order, Guids in "D" format and nulls sent as null.

read_like_backend() is a port of ActionPlanResponseReader.Read: what the backend would store from a 200. A
task this service returns and the reader drops would be a task the model paid for and the user never sees, so
the tests check that every task survives it. If the reader changes in the backend, this port has to change
with it.
"""

import json
import re
import uuid
from dataclasses import dataclass, field

PROMPT_VERSION = "action-plan-v1"
PROPOSAL_VERSION_MAX_LENGTH = 200
MODEL_MAX_LENGTH = 100
NAME_MAX_LENGTH = 255
DESCRIPTION_MAX_LENGTH = 2000
PRIORITIES = ("alta", "media", "baja")

# JsonElement.TryGetGuid only reads the "D" format.
_GUID_D = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_INT32_MAX = 2**31 - 1


def fragment(document_id: str, chunk_index: int, text: str, locator: str | None = None) -> dict:
    return {"document_id": document_id, "chunk_index": chunk_index, "locator": locator, "text": text}


def finding(
    finding_id: str,
    description: str,
    fragments: list[dict],
    *,
    requirement: str | None = None,
    missing: str | None = None,
) -> dict:
    return {
        "id": finding_id,
        "description": description,
        "requirement": requirement,
        "missing": missing,
        "fragments": fragments,
    }


def backend_body(findings: list[dict]) -> dict:
    return {"prompt_version": PROMPT_VERSION, "findings": findings}


@dataclass
class ReadTask:
    name: str
    description: str | None
    priority: str | None
    document_id: uuid.UUID
    chunk_index: int
    locator: str | None
    citation: str


@dataclass
class ReadFinding:
    finding_id: uuid.UUID
    unsupported: bool
    tasks: list[ReadTask] = field(default_factory=list)


@dataclass
class Reading:
    can_store: bool
    proposal_version: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cached_tokens: int
    llm_calls: int
    findings: list[ReadFinding]


def read_like_backend(payload: str, sent_findings: list[dict]) -> Reading:
    """ActionPlanResponseReader.Read over the response body and the findings that were sent."""
    try:
        root = json.loads(payload)
    except json.JSONDecodeError:
        return Reading(False, "", "", 0, 0, 0, 0, 1, [])
    usage = root.get("usage") if isinstance(root, dict) and isinstance(root.get("usage"), dict) else None
    calls = _count(usage, "llm_calls")
    proposal_version = _text(root, "proposal_version")
    model = _text(root, "model")
    findings_value = root.get("findings") if isinstance(root, dict) else None
    findings_ok = not (isinstance(root, dict) and "findings" in root) or isinstance(findings_value, list)
    can_store = (
        _fit(proposal_version, PROPOSAL_VERSION_MAX_LENGTH) is not None
        and _fit(model, MODEL_MAX_LENGTH) is not None
        and findings_ok
    )
    returned = _index(findings_value)
    return Reading(
        can_store=can_store,
        proposal_version=_fit(proposal_version, PROPOSAL_VERSION_MAX_LENGTH) or "",
        model=_fit(model, MODEL_MAX_LENGTH) or "",
        prompt_tokens=_count(usage, "prompt_tokens"),
        completion_tokens=_count(usage, "completion_tokens"),
        total_tokens=_count(usage, "total_tokens"),
        cached_tokens=_count(usage, "cached_tokens"),
        llm_calls=calls if calls >= 1 else 1,
        findings=[_read_finding(sent, returned) for sent in sent_findings] if can_store else [],
    )


def _index(findings: object) -> dict[uuid.UUID, dict]:
    returned: dict[uuid.UUID, dict] = {}
    if not isinstance(findings, list):
        return returned
    for item in findings:
        if not isinstance(item, dict):
            continue
        finding_id = _guid(item, "id")
        if finding_id is None or finding_id in returned:
            continue
        returned[finding_id] = item
    return returned


def _read_finding(sent: dict, returned: dict[uuid.UUID, dict]) -> ReadFinding:
    sent_id = uuid.UUID(sent["id"])
    item = returned.get(sent_id)
    if item is None:
        return ReadFinding(sent_id, True)
    fragments: dict[tuple[uuid.UUID, int], dict] = {}
    for sent_fragment in sent.get("fragments") or []:
        text = sent_fragment.get("text")
        if sent_fragment["chunk_index"] < 0 or not text or not text.strip():
            continue
        fragments.setdefault((uuid.UUID(sent_fragment["document_id"]), sent_fragment["chunk_index"]), sent_fragment)
    tasks = []
    if isinstance(item.get("tasks"), list):
        for task in item["tasks"]:
            kept = _task(task, fragments)
            if kept is not None:
                tasks.append(kept)
    return ReadFinding(sent_id, not tasks, tasks)


def _task(task: object, fragments: dict[tuple[uuid.UUID, int], dict]) -> ReadTask | None:
    if not isinstance(task, dict):
        return None
    name = _fit(_text(task, "name"), NAME_MAX_LENGTH)
    if name is None:
        return None
    citation = task.get("citation")
    if not isinstance(citation, dict):
        return None
    document_id = _guid(citation, "document_id")
    chunk_index = citation.get("chunk_index")
    if (
        document_id is None
        or not isinstance(chunk_index, int)
        or isinstance(chunk_index, bool)
        or not 0 <= chunk_index <= _INT32_MAX
    ):
        return None
    sent_fragment = fragments.get((document_id, chunk_index))
    if sent_fragment is None:
        return None
    quote = _text(citation, "quote")
    if quote is None or not _contains_quote(sent_fragment["text"], quote):
        return None
    priority = _text(task, "priority")
    priority = priority.lower() if priority is not None else None
    locator = sent_fragment.get("locator")
    return ReadTask(
        name=name,
        description=_fit(_text(task, "description"), DESCRIPTION_MAX_LENGTH),
        priority=priority if priority in PRIORITIES else None,
        document_id=document_id,
        chunk_index=chunk_index,
        locator=locator.strip() if locator and locator.strip() else None,
        citation=quote,
    )


def _contains_quote(fragment_text: str, quote: str) -> bool:
    compact_quote = _compact(quote)
    return bool(compact_quote) and compact_quote in _compact(fragment_text)


def _compact(text: str) -> str:
    # char.IsWhiteSpace and char.ToLowerInvariant, character by character.
    return "".join(character.lower() for character in text if not character.isspace())


def _count(usage: dict | None, name: str) -> int:
    if usage is None:
        return 0
    value = usage.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= _INT32_MAX:
        return 0
    return value


def _text(root: object, name: str) -> str | None:
    if not isinstance(root, dict) or not isinstance(root.get(name), str):
        return None
    text = root[name].strip()
    return text or None


def _fit(value: str | None, maximum: int) -> str | None:
    # string.Length counts UTF-16 code units.
    return None if value is None or len(value.encode("utf-16-le")) // 2 > maximum else value


def _guid(root: dict, name: str) -> uuid.UUID | None:
    value = root.get(name)
    if not isinstance(value, str) or _GUID_D.fullmatch(value) is None:
        return None
    parsed = uuid.UUID(value)
    return None if parsed.int == 0 else parsed
