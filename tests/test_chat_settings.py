"""CHT-013: the CHAT_* settings, their defaults and .env.example match the configuration table of
docs/Chat/api.md. .env.example holds placeholders only: no client is built from it.

CHT-014: CHAT_FAKE_RESPONDER runs only with APP_ENV=development (sprint 4, step 2.14)."""

import subprocess
import sys
from pathlib import Path

import pytest
from chat_http import SECRET
from pydantic import ValidationError

from ia_cumplify.config.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# The configuration table of docs/Chat/api.md.
CONTRACT_DEFAULTS = {
    "chat_model": "",
    "chat_reasoning_effort": "",
    "chat_max_completion_tokens": 4000,
    "chat_timeout_seconds": 60,
    "chat_max_retries": 0,
    "chat_question_max_chars": 2000,
    "chat_history_max_messages": 6,
    "chat_history_max_chars": 8000,
    "chat_max_passages": 12,
    "chat_passage_max_chars": 6000,
    "chat_passages_max_total_chars": 48000,
    "chat_fake_responder": False,
}


def _chat_fields(settings: Settings) -> dict[str, object]:
    return {name: getattr(settings, name) for name in Settings.model_fields if name.startswith("chat_")}


def test_defaults_are_the_contract_ones() -> None:
    assert _chat_fields(Settings()) == CONTRACT_DEFAULTS


def test_env_example_documents_every_chat_variable_with_its_default() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    variables = [index for index, line in enumerate(lines) if line.startswith("CHAT_")]
    assert sorted(lines[index].split("=", 1)[0] for index in variables) == sorted(
        name.upper() for name in CONTRACT_DEFAULTS
    )
    # Each one with its own line of explanation right above it.
    assert all(lines[index - 1].startswith("# ") for index in variables)
    # A .env copied from the example keeps the contract defaults.
    assert _chat_fields(Settings(_env_file=ENV_EXAMPLE)) == CONTRACT_DEFAULTS


class TestFakeResponderOnlyInDevelopment:
    REFUSAL = "CHAT_FAKE_RESPONDER=true runs only with APP_ENV=development"

    @pytest.mark.parametrize("app_env", [None, "production", "staging", ""])
    def test_any_other_environment_stops_the_settings(self, monkeypatch, app_env: str | None) -> None:
        monkeypatch.setenv("CHAT_FAKE_RESPONDER", "true")
        monkeypatch.setenv("OPENAI_API_KEY", SECRET)
        monkeypatch.setenv("SERVICE_API_KEY", SECRET)
        if app_env is not None:
            monkeypatch.setenv("APP_ENV", app_env)

        with pytest.raises(ValidationError) as raised:
            Settings()

        assert self.REFUSAL in str(raised.value)
        # The error names the variables, never the values read. Without hide_input_in_errors, pydantic adds
        # input_value with every setting read, and its cut still shows the start of SERVICE_API_KEY.
        assert "input_value" not in str(raised.value)
        assert SECRET not in str(raised.value)

    def test_development_runs_it(self, monkeypatch) -> None:
        monkeypatch.setenv("CHAT_FAKE_RESPONDER", "true")
        monkeypatch.setenv("APP_ENV", "development")
        assert Settings().chat_fake_responder is True

    def test_off_it_starts_in_any_environment(self, monkeypatch) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        assert Settings().chat_fake_responder is False

    def test_the_process_ends_with_the_error(self, tmp_path: Path) -> None:
        """Importing the app is the first thing uvicorn does (ia-cumplify-serve): it fails before listening.

        An empty working directory, so no .env is read, and only these variables.
        """
        variables = {
            "CHAT_FAKE_RESPONDER": "true",
            "APP_ENV": "production",
            "OPENAI_API_KEY": SECRET,
            "SERVICE_API_KEY": SECRET,
        }
        done = subprocess.run(
            [sys.executable, "-c", "import ia_cumplify.adapters.inbound.http.app"],
            cwd=tmp_path,
            env=variables,
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert done.returncode != 0
        assert self.REFUSAL in done.stderr
        assert "input_value" not in done.stderr
        assert SECRET not in done.stdout + done.stderr
