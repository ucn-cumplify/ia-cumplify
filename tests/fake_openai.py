"""A fake OpenAI chat completions endpoint on 127.0.0.1 for the tests. Nothing leaves the loopback.

It speaks raw HTTP/1.1 over asyncio, so a test controls every byte, every delay and how the connection
ends, and it records each request and the moment the client closes the connection.

The first word of the request's last message (the question) names the scenario; an unknown name gets
"quick". In-process (running()), tests add their own scenarios. As a script, for the disconnection
tests, it serves BUILTIN_SCENARIOS and appends its records to a JSON-lines file:

    python fake_openai.py LOG_FILE     # prints "PORT <n>" once it listens on 127.0.0.1
"""

import asyncio
import contextlib
import itertools
import json
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field

MODEL = "fake-model"


def chunk(
    content: str | None = None, *, refusal: str | None = None, finish: str | None = None, role: str | None = None
) -> dict:
    delta: dict = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    if refusal is not None:
        delta["refusal"] = refusal
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": MODEL,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        "usage": None,
    }


def usage_chunk(prompt: int = 1200, completion: int = 300, cached: int = 640, reasoning: int = 96) -> dict:
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": MODEL,
        "choices": [],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached},
            "completion_tokens_details": {"reasoning_tokens": reasoning},
        },
    }


def answer(*texts: str, finish: str = "stop", usage: bool = True) -> tuple[dict, ...]:
    """What OpenAI streams for a normal answer: the role, the texts, finish_reason and the usage."""
    events = [chunk(role="assistant", content=""), *(chunk(text) for text in texts), chunk(finish=finish)]
    if usage:
        events.append(usage_chunk())
    return tuple(events)


@dataclass(frozen=True)
class Scenario:
    # A dict goes out as "data: <json>", a str as "data: <str>" and bytes as they are.
    events: Sequence[dict | str | bytes] = ()
    status: int = 200
    # JSON body of a non-200 answer.
    error: dict | None = None
    headers: Sequence[tuple[str, str]] = ()
    headers_delay: float = 0.0
    # Seconds between writes, and events per write.
    delay: float = 0.0
    per_write: int = 1
    # "done": [DONE] and the end of the chunked body. "eof": only the end of the body. "abort": the
    # connection is reset. "hang": nothing else is sent until the client closes. A non-200 answer sends
    # its whole body with "done" and "eof", and only half of it with "abort" and "hang".
    ending: str = "done"


BUILTIN_SCENARIOS: dict[str, Scenario | Sequence[Scenario]] = {
    "quick": Scenario(events=answer("Según el pasaje [P", "1], debe constituirse un comité.")),
    # About 16 s of tokens unless the client closes first.
    "burst": Scenario(events=answer(*(f"t{i} " for i in range(40000))), per_write=5, delay=0.002),
    "slow": Scenario(events=answer(*(f"t{i} " for i in range(40))), delay=0.5),
    "late": Scenario(events=answer(*(f"t{i} " for i in range(5))), headers_delay=3.0, delay=0.2),
}


@dataclass
class FakeOpenAI:
    scenarios: dict[str, Scenario | Sequence[Scenario]] = field(default_factory=dict)
    log_path: str | None = None
    records: list[dict] = field(default_factory=list)
    port: int = 0

    def __post_init__(self) -> None:
        self.scenarios = {**BUILTIN_SCENARIOS, **self.scenarios}
        self._cids = itertools.count(1)
        self._served: dict[str, int] = {}
        self._tasks: set[asyncio.Task] = set()
        self._server: asyncio.Server | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def requests(self, name: str | None = None) -> list[dict]:
        return [r for r in self.records if r["ev"] == "request" and (name is None or r["scenario"] == name)]

    def events_of(self, cid: int) -> list[dict]:
        return [r for r in self.records if r["cid"] == cid]

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _record(self, cid: int, ev: str, **fields: object) -> None:
        record = {"cid": cid, "ev": ev, "t": time.time(), **fields}
        self.records.append(record)
        if self.log_path:
            with open(self.log_path, "a", encoding="utf-8") as log:
                log.write(json.dumps(record) + "\n")

    def _scenario(self, body: object) -> tuple[str, Scenario]:
        name = "quick"
        messages = body.get("messages") if isinstance(body, dict) else None
        if messages and isinstance(messages[-1].get("content"), str) and messages[-1]["content"].split():
            name = messages[-1]["content"].split()[0]
        if name not in self.scenarios:
            name = "quick"
        scenario = self.scenarios[name]
        if isinstance(scenario, Scenario):
            return name, scenario
        # A sequence: one scenario per request with that name, the last one repeated.
        served = self._served.get(name, 0)
        self._served[name] = served + 1
        return name, scenario[min(served, len(scenario) - 1)]

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        cid = next(self._cids)
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
            raw = await reader.readexactly(int(headers.get("content-length", "0")))
            body = json.loads(raw) if raw else None
        except (asyncio.IncompleteReadError, ConnectionError, ValueError):
            writer.close()
            self._tasks.discard(task)
            return
        name, scenario = self._scenario(body)
        self._record(cid, "request", method=method, path=path, headers=headers, body=body, scenario=name)
        eof = asyncio.Event()
        watcher = asyncio.create_task(self._watch(reader, cid, eof))
        try:
            await self._respond(writer, scenario, cid, eof)
        except (ConnectionError, OSError) as exc:
            self._record(cid, "write_error", error=type(exc).__name__)
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(watcher, 1.0)
            watcher.cancel()
            self._tasks.discard(task)

    async def _watch(self, reader: asyncio.StreamReader, cid: int, eof: asyncio.Event) -> None:
        """Records when the client closes its side: an SDK client sends nothing after the request."""
        try:
            while await reader.read(65536):
                pass
            self._record(cid, "eof")
        except (ConnectionError, OSError) as exc:
            self._record(cid, "eof", error=type(exc).__name__)
        eof.set()

    async def _wait(self, eof: asyncio.Event, seconds: float) -> bool:
        """Sleeps, unless the client closes first. True when it did."""
        # asyncio.TimeoutError is the builtin TimeoutError only since 3.11.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(eof.wait(), seconds)
        return eof.is_set()

    async def _respond(self, writer: asyncio.StreamWriter, scenario: Scenario, cid: int, eof: asyncio.Event) -> None:
        if scenario.headers_delay and await self._wait(eof, scenario.headers_delay):
            self._record(cid, "stopped_after_eof", sent=0, headers_sent=False)
            return
        extra = "".join(f"{key}: {value}\r\n" for key, value in scenario.headers)
        if scenario.status != 200:
            body = json.dumps(scenario.error or {"error": {"message": "fake error", "type": "server_error"}}).encode()
            sent = body if scenario.ending in ("done", "eof") else body[: len(body) // 2]
            writer.write(
                f"HTTP/1.1 {scenario.status} Fake\r\ncontent-type: application/json\r\n"
                f"content-length: {len(body)}\r\nconnection: close\r\n{extra}\r\n".encode()
                + sent
            )
            await writer.drain()
            self._record(cid, "error_sent", status=scenario.status)
            if scenario.ending == "abort":
                writer.transport.abort()
                self._record(cid, "aborted", sent=0)
            elif scenario.ending == "hang":
                await eof.wait()
                self._record(cid, "stopped_after_eof", sent=0, headers_sent=True)
            return
        writer.write(
            "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n"
            f"connection: close\r\nx-request-id: req_fake\r\n{extra}\r\n".encode()
        )
        await writer.drain()
        self._record(cid, "headers_sent")
        payloads = [_sse(event) for event in scenario.events]
        sent = 0
        for start in range(0, len(payloads), max(1, scenario.per_write)):
            if eof.is_set() or (start and scenario.delay and await self._wait(eof, scenario.delay)):
                self._record(cid, "stopped_after_eof", sent=sent, headers_sent=True)
                return
            batch = payloads[start : start + max(1, scenario.per_write)]
            writer.write(b"".join(_http_chunk(payload) for payload in batch))
            await writer.drain()
            sent += len(batch)
        if scenario.ending == "abort":
            writer.transport.abort()
            self._record(cid, "aborted", sent=sent)
        elif scenario.ending == "hang":
            await eof.wait()
            self._record(cid, "stopped_after_eof", sent=sent, headers_sent=True)
        else:
            tail = _http_chunk(_sse("[DONE]")) if scenario.ending == "done" else b""
            writer.write(tail + b"0\r\n\r\n")
            await writer.drain()
            self._record(cid, "complete", sent=sent)


def _sse(event: dict | str | bytes) -> bytes:
    if isinstance(event, bytes):
        return event
    data = event if isinstance(event, str) else json.dumps(event)
    return b"data: " + data.encode("utf-8", "surrogatepass") + b"\n\n"


def _http_chunk(data: bytes) -> bytes:
    return f"{len(data):x}\r\n".encode() + data + b"\r\n"


@contextlib.contextmanager
def running(scenarios: dict[str, Scenario | Sequence[Scenario]] | None = None) -> Iterator[FakeOpenAI]:
    """The fake server on its own event loop, in a daemon thread of the test process."""
    fake = FakeOpenAI(scenarios=dict(scenarios or {}))
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name="fake-openai", daemon=True)
    thread.start()
    try:
        asyncio.run_coroutine_threadsafe(fake.start(), loop).result(timeout=5)
        yield fake
    finally:
        with contextlib.suppress(Exception):
            asyncio.run_coroutine_threadsafe(fake.stop(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


async def _serve_forever(log_path: str) -> None:
    fake = FakeOpenAI(log_path=log_path)
    port = await fake.start()
    print(f"PORT {port}", flush=True)
    assert fake._server is not None
    await fake._server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(_serve_forever(sys.argv[1]))
    except OSError as exc:  # no free port on 127.0.0.1
        print(f"NOPORT {type(exc).__name__}", flush=True)
        sys.exit(3)
