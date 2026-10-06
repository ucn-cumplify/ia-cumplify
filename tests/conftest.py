import os

import pytest

from ia_cumplify.config import settings as settings_module

# Every variable Settings reads: a value exported in the shell must not reach a test either.
_SETTINGS_ENV = tuple(name.upper() for name in settings_module.Settings.model_fields)

# Importing the app module builds the app, and that reads the settings: without this, collecting a
# test module that imports it would read .env before any fixture runs.
settings_module.Settings.model_config["env_file"] = None


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch):
    """No test reads .env, and an OpenAI client built by mistake can only reach 127.0.0.1.

    Settings reads model_config["env_file"] on every instantiation, so this has the effect of
    Settings(_env_file=None) everywhere; tests set the variables they need with monkeypatch.setenv.
    """
    from ia_cumplify.adapters.inbound.http.dependencies import reset_wiring_cache

    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)
    # No proxy either: loopback traffic must stay on loopback.
    for name in [name for name in os.environ if name.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(settings_module.Settings.model_config, "env_file", None)
    # The OpenAI SDK reads OPENAI_BASE_URL when a client gets no base_url. Port 9 (discard) refuses.
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_MAX_RETRIES", "0")
    # Cached adapters hold settings and clients from another test (and another event loop).
    reset_wiring_cache()
    yield
    reset_wiring_cache()
