import copy

import uvicorn
from uvicorn.config import LOGGING_CONFIG

# uvicorn configures only its own loggers: without this, the service's INFO records (the summary of each
# chat answer and the 422 trace, CHT-012) are dropped and its warnings come out without a level.
# deepcopy: LOGGING_CONFIG is uvicorn's shared default.
LOG_CONFIG = copy.deepcopy(LOGGING_CONFIG)
LOG_CONFIG["loggers"]["ia_cumplify"] = {"handlers": ["default"], "level": "INFO", "propagate": False}


def _run(reload: bool) -> None:
    uvicorn.run(
        "ia_cumplify.adapters.inbound.http.app:app",
        host="0.0.0.0",
        port=8000,
        reload=reload,
        log_config=LOG_CONFIG,
    )


def main() -> None:
    """Local development (uv run ia-cumplify): reloads on code changes."""
    _run(reload=True)


def serve() -> None:
    """Container (ia-cumplify-serve): the same logging, without the reloader process."""
    _run(reload=False)
