"""Runs the real app with uvicorn on 127.0.0.1, for the disconnection tests. It never reads .env.

    python uvicorn_app.py      # prints "PORT <n>" once it listens

TEST_SSE_PING_SECONDS shortens FastAPI's keep-alive ping. The interval is read from fastapi.routing,
which imported it from fastapi.sse: patching fastapi.sse would not change it.
"""

import os
import socket
import sys

from ia_cumplify.config import settings as settings_module

settings_module.Settings.model_config["env_file"] = None

import fastapi.routing  # noqa: E402
import uvicorn  # noqa: E402

if os.environ.get("TEST_SSE_PING_SECONDS"):
    fastapi.routing._PING_INTERVAL = float(os.environ["TEST_SSE_PING_SECONDS"])

from ia_cumplify.adapters.inbound.http.app import app  # noqa: E402
from ia_cumplify.cli import LOG_CONFIG  # noqa: E402


def main() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
    except OSError as exc:
        print(f"NOPORT {type(exc).__name__}", flush=True)
        sys.exit(3)
    print(f"PORT {sock.getsockname()[1]}", flush=True)
    # The service's own logging, as cli.py sets it up; log_level="warning" only silences the access log.
    uvicorn.Server(uvicorn.Config(app, log_level="warning", log_config=LOG_CONFIG)).run(sockets=[sock])


if __name__ == "__main__":
    main()
