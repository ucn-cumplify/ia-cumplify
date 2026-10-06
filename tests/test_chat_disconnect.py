"""CHT-011: when the backend disconnects, ia closes the provider call within milliseconds.

The app (uvicorn, FastAPI and the openai SDK, unchanged) and the fake OpenAI run as real servers in
their own processes on 127.0.0.1. In the test process, the test's own allocations would trigger the
garbage collection that also closes a broken implementation, and the burst case would prove nothing.

They run by default. Deselect them with -m "not disconnect"; they skip themselves when no port of
127.0.0.1 can be bound.
"""

import http.client
import json
import os
import queue
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from chat_http import SECRET, SERVICE_KEY, parse_sse, passage

from ia_cumplify.config.settings import Settings

pytestmark = pytest.mark.disconnect

HERE = Path(__file__).parent
# Measured: 1 to 4 ms. A close left to the garbage collector takes seconds, or never comes.
CLOSE_BUDGET_MS = 500.0
PING_SECONDS = 0.2
STARTUP_SECONDS = 30.0


@dataclass
class Servers:
    port: int
    log: Path
    stderr: Path

    def records(self, question: str) -> list[dict]:
        """What the fake OpenAI recorded for the request that carried this question."""
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line.strip()]
        cid = next(
            (r["cid"] for r in records if r["ev"] == "request" and r["body"]["messages"][-1]["content"] == question),
            None,
        )
        return [r for r in records if r["cid"] == cid] if cid is not None else []

    def wait(self, question: str, done: Callable[[list[dict]], bool], timeout: float = 10.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while True:
            records = self.records(question)
            if done(records) or time.monotonic() > deadline:
                return records
            time.sleep(0.01)


def _first(records: list[dict], event: str) -> dict | None:
    return next((record for record in records if record["ev"] == event), None)


def _clean_env(**extra: str) -> dict[str, str]:
    """The test environment without anything that could reach a real service or a real .env value."""
    settings = {name.upper() for name in Settings.model_fields}
    env = {
        name: value
        for name, value in os.environ.items()
        if name.upper() not in settings
        and not name.upper().startswith(("OPENAI_", "CHAT_"))
        and not name.lower().endswith("_proxy")
    }
    env.update(PYTHONDONTWRITEBYTECODE="1", **extra)
    return env


def _start(script: str, *args: str, env: dict[str, str], cwd: Path, stderr) -> tuple[subprocess.Popen, int]:
    process = subprocess.Popen(
        [sys.executable, str(HERE / script), *args], cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=stderr, text=True
    )
    stdout = process.stdout
    assert stdout is not None
    # select() takes only sockets on Windows, so a thread reads the first line. If the child dies,
    # readline() returns "" at once; if it hangs, kill() below closes the pipe and the thread ends.
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: lines.put(stdout.readline()), daemon=True).start()
    try:
        line = lines.get(timeout=STARTUP_SECONDS)
    except queue.Empty:
        line = ""
    if line.startswith("PORT "):
        return process, int(line.split()[1])
    process.kill()
    process.wait()
    if line.startswith("NOPORT"):
        pytest.skip("no free port on 127.0.0.1")
    raise RuntimeError(f"{script} did not start")


def _loopback_available() -> bool:
    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
        return True
    except OSError:
        return False


def _chat_body(question: str) -> bytes:
    return json.dumps({"question": question, "passages": [passage(1)]}).encode()


def _complete_answer(port: int, question: str) -> tuple[int, str]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(
            "POST",
            "/api/v1/chat",
            body=_chat_body(question),
            headers={"Content-Type": "application/json", "X-API-Key": SERVICE_KEY},
        )
        response = connection.getresponse()
        # http.client fails here if the chunked body lacks its terminator.
        return response.status, response.read().decode()
    finally:
        connection.close()


def _open_chat(port: int, question: str) -> socket.socket:
    body = _chat_body(question)
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    sock.sendall(
        (
            "POST /api/v1/chat HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
            f"X-API-Key: {SERVICE_KEY}\r\nContent-Length: {len(body)}\r\n\r\n"
        ).encode()
        + body
    )
    return sock


def _healthy(port: int) -> bool:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request("GET", "/health")
        return connection.getresponse().status == 200
    finally:
        connection.close()


@pytest.fixture(scope="module")
def servers(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Servers]:
    if not _loopback_available():
        pytest.skip("no free port on 127.0.0.1")
    # A directory without .env; the app launcher also turns the .env reading off.
    workdir = tmp_path_factory.mktemp("chat-disconnect")
    log, stderr_path = workdir / "fake_openai.jsonl", workdir / "uvicorn.stderr"
    processes: list[subprocess.Popen] = []
    with open(stderr_path, "w", encoding="utf-8") as stderr:
        try:
            fake, fake_port = _start("fake_openai.py", str(log), env=_clean_env(), cwd=workdir, stderr=subprocess.DEVNULL)
            processes.append(fake)
            app_env = _clean_env(
                SERVICE_API_KEY=SERVICE_KEY,
                OPENAI_API_KEY="sk-test-fake",
                OPENAI_BASE_URL=f"http://127.0.0.1:{fake_port}/v1",
                NO_PROXY="*",
                TEST_SSE_PING_SECONDS=str(PING_SECONDS),
            )
            app, app_port = _start("uvicorn_app.py", env=app_env, cwd=workdir, stderr=stderr)
            processes.append(app)
            # The first request of the process builds the adapter: the timed tests must not pay it.
            status, _ = _complete_answer(app_port, f"quick {uuid.uuid4().hex}")
            assert status == 200
            yield Servers(port=app_port, log=log, stderr=stderr_path)
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _read_until(sock: socket.socket, enough: Callable[[bytes], bool]) -> bytes:
    received = b""
    while not enough(received):
        data = sock.recv(65536)
        if not data:
            break
        received += data
    return received


def _disconnect_after_two_deltas(servers: Servers, scenario: str) -> tuple[float, list[dict]]:
    question = f"{scenario} {uuid.uuid4().hex}"
    sock = _open_chat(servers.port, question)
    received = _read_until(sock, lambda data: data.count(b"event: delta") >= 2)
    assert received.startswith(b"HTTP/1.1 200"), received[:200]
    closed_at = time.time()
    sock.close()
    records = servers.wait(question, lambda rs: _first(rs, "stopped_after_eof") is not None or _first(rs, "complete") is not None)
    return closed_at, records


def _close_ms(closed_at: float, records: list[dict]) -> float:
    assert _first(records, "complete") is None, "the provider sent the whole answer: nobody closed its connection"
    eof = _first(records, "eof")
    assert eof is not None, records
    return (eof["t"] - closed_at) * 1000


@pytest.mark.parametrize("attempt", [1, 2])
def test_burst(servers: Servers, attempt: int) -> None:
    """The case that fails without the request-scoped close: FastAPI's producer is blocked sending and
    the generator stays suspended in its yield."""
    closed_at, records = _disconnect_after_two_deltas(servers, "burst")
    elapsed = _close_ms(closed_at, records)
    print(f"burst {attempt}: the provider saw the close after {elapsed:.1f} ms")
    assert elapsed < CLOSE_BUDGET_MS
    stopped = _first(records, "stopped_after_eof")
    assert stopped is not None and stopped["sent"] < 40000
    assert _healthy(servers.port)


def test_slow_cadence(servers: Servers) -> None:
    closed_at, records = _disconnect_after_two_deltas(servers, "slow")
    elapsed = _close_ms(closed_at, records)
    print(f"slow: the provider saw the close after {elapsed:.1f} ms")
    assert elapsed < CLOSE_BUDGET_MS
    assert _healthy(servers.port)


def test_before_the_provider_sends_its_headers(servers: Servers) -> None:
    question = f"late {uuid.uuid4().hex}"
    sent_at = time.time()
    sock = _open_chat(servers.port, question)
    sock.settimeout(0.05)
    received, first_byte_at = b"", None
    while time.time() - sent_at < 1.0:
        try:
            data = sock.recv(65536)
        except TimeoutError:
            continue
        if not data:
            break
        first_byte_at = first_byte_at or time.time()
        received += data
    closed_at = time.time()
    sock.close()
    # ia answered at once, and pinged while the provider was silent.
    assert first_byte_at is not None and (first_byte_at - sent_at) * 1000 < CLOSE_BUDGET_MS
    head = received.split(b"\r\n\r\n", 1)[0].lower()
    assert head.startswith(b"http/1.1 200") and b"content-type: text/event-stream; charset=utf-8" in head
    assert received.count(b": ping") >= 2
    assert b"event:" not in received
    records = servers.wait(question, lambda rs: _first(rs, "stopped_after_eof") is not None)
    elapsed = _close_ms(closed_at, records)
    print(
        f"before the headers: first byte after {(first_byte_at - sent_at) * 1000:.1f} ms, "
        f"{received.count(b': ping')} pings; the provider saw the close after {elapsed:.1f} ms"
    )
    assert elapsed < CLOSE_BUDGET_MS
    assert _first(records, "headers_sent") is None
    assert _healthy(servers.port)


def test_a_complete_answer_through_uvicorn(servers: Servers) -> None:
    status, body = _complete_answer(servers.port, f"quick {uuid.uuid4().hex}")
    assert status == 200
    events, _ = parse_sse(body)
    assert [event.name for event in events][-1] == "done"
    assert all(event.name == "delta" for event in events[:-1])
    done = events[-1].data
    assert isinstance(done, dict)
    assert done["citations"] == [{"n": 1, "id": "id-1"}]
    assert (done["coverage"], done["finish_reason"], done["chat_version"]) == ("answered", "stop", "chat-v1@gpt-5.6-luna")
    assert done["usage"] == {
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "cached_tokens": 640,
        "reasoning_tokens": 96,
        "llm_calls": 1,
    }


def test_the_service_log_reaches_stderr_without_content(servers: Servers) -> None:
    """caplog brings its own handler: only the real stderr shows that the INFO records survive (CHT-012)."""
    question = f"log {SECRET} {uuid.uuid4().hex}"
    status, _ = _complete_answer(servers.port, question)
    assert status == 200
    connection = http.client.HTTPConnection("127.0.0.1", servers.port, timeout=10)
    try:
        connection.request(
            "POST",
            "/api/v1/chat",
            body=json.dumps({"question": question}).encode(),
            headers={"Content-Type": "application/json", "X-API-Key": SERVICE_KEY},
        )
        assert connection.getresponse().status == 422
    finally:
        connection.close()
    stderr = servers.stderr.read_text(encoding="utf-8")
    # The fixture's warm-up answer also logs a done line: the length tells this one apart.
    assert f"Chat answer done: 1 passages (article 1), question of {len(question)} characters" in stderr
    assert "Request validation failed on POST /api/v1/chat: [('missing', ('body', 'passages'))]" in stderr
    assert SECRET not in stderr
