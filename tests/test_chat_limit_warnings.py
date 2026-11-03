"""CHT-050 to CHT-054: the startup warning for a CHAT_* request limit set below its default (sprint 4,
step 1.5 of backend-cumplify). The backend mirrors each default as a constant and checks them against
tests/contracts/chat-limits.json: below a default, a request the backend builds within its own limit gets 422
here. The warning does not stop the service, and a value at or above the default is compatible.

Each test builds a new app and runs its lifespan with TestClient, as the service starts."""

import logging
from dataclasses import fields

import pytest
from chat_http import HEADERS, SECRET, chat_body, configure, passage
from fastapi.testclient import TestClient
from test_chat_settings import CONTRACT_DEFAULTS

from ia_cumplify.adapters.inbound.http.app import chat_limits_below_default, create_app
from ia_cumplify.application.use_cases.answer_chat import ChatLimits
from ia_cumplify.config.settings import Settings

# The six request limits of the configuration table of docs/Chat/api.md, with their defaults.
LIMITS = {
    name: CONTRACT_DEFAULTS[name]
    for name in (
        "chat_question_max_chars",
        "chat_history_max_messages",
        "chat_history_max_chars",
        "chat_max_passages",
        "chat_passage_max_chars",
        "chat_passages_max_total_chars",
    )
}
WARNING = "is below its default"


def start(monkeypatch, caplog, **env: str) -> list[logging.LogRecord]:
    """Starts a new app with the given variables and returns the limit warnings it logged. The app answers
    after its lifespan: a request without the key gets 401, before any limit or provider."""
    configure(monkeypatch, **env)
    caplog.set_level(logging.WARNING, logger="ia_cumplify.adapters.inbound.http.app")
    with TestClient(create_app()) as client:
        assert client.post("/api/v1/chat", json=chat_body()).status_code == 401
    return [record for record in caplog.records if WARNING in record.getMessage()]


def message(name: str, value: int) -> str:
    return (
        f"{name.upper()}={value} is below its default {LIMITS[name]}, which the backend mirrors as a constant: "
        "a chat request the backend builds within its own limit can get 422 here."
    )


class TestNoWarning:
    def test_defaults_start_without_a_limit_warning(self, monkeypatch, caplog) -> None:
        # CHT-050: nothing configured, so every limit is its default.
        assert start(monkeypatch, caplog) == []

    @pytest.mark.parametrize("name", LIMITS)
    @pytest.mark.parametrize("delta", [0, 1, 1000])
    def test_a_limit_at_or_above_its_default_is_compatible(self, monkeypatch, caplog, name: str, delta: int) -> None:
        # CHT-052: equal or higher is not an incompatibility; the backend stays within it.
        assert start(monkeypatch, caplog, **{name: str(LIMITS[name] + delta)}) == []


class TestWarning:
    @pytest.mark.parametrize("name", LIMITS)
    def test_one_below_its_default_warns_once_with_the_variable_and_both_values(
        self, monkeypatch, caplog, name: str
    ) -> None:
        # CHT-051
        records = start(monkeypatch, caplog, **{name: str(LIMITS[name] - 1)})

        assert [(record.levelno, record.getMessage()) for record in records] == [
            (logging.WARNING, message(name, LIMITS[name] - 1))
        ]

    @pytest.mark.parametrize(("name", "value"), [("chat_max_passages", 0), ("chat_question_max_chars", -1)])
    def test_zero_and_negative_values_warn_and_the_service_still_starts(
        self, monkeypatch, caplog, name: str, value: int
    ) -> None:
        # CHT-051. Settings accepts them (no ge=1, decision D3 of step 1.5); start() checks the app answers.
        records = start(monkeypatch, caplog, **{name: str(value)})

        assert [record.getMessage() for record in records] == [message(name, value)]

    def test_several_below_their_default_warn_once_each_in_the_order_of_chat_limits(self, monkeypatch, caplog) -> None:
        # CHT-051
        records = start(monkeypatch, caplog, chat_passages_max_total_chars="100", chat_question_max_chars="10")

        assert [record.getMessage() for record in records] == [
            message("chat_question_max_chars", 10),
            message("chat_passages_max_total_chars", 100),
        ]


class TestBehaviourBelowTheDefault:
    """What the warning announces, observed through HTTP with the fake responder (no OpenAI)."""

    def test_zero_passages_rejects_any_request_with_passages(self, monkeypatch) -> None:
        configure(monkeypatch, chat_fake_responder="true", app_env="development", chat_max_passages="0")
        with TestClient(create_app()) as client:
            response = client.post("/api/v1/chat", headers=HEADERS, json=chat_body(passages=[passage(1)]))

        assert response.status_code == 422
        assert response.json() == {"detail": "The request has 1 passages; the limit is 0."}

    def test_a_negative_question_limit_rejects_every_question(self, monkeypatch) -> None:
        configure(monkeypatch, chat_fake_responder="true", app_env="development", chat_question_max_chars="-1")
        with TestClient(create_app()) as client:
            response = client.post("/api/v1/chat", headers=HEADERS, json=chat_body(question="¿Qué exige?"))

        assert response.status_code == 422
        assert response.json() == {"detail": "The question has 11 characters; the limit is -1."}


class TestThresholds:
    def test_the_thresholds_are_the_six_limits_of_chat_limits_and_their_settings_defaults(self) -> None:
        # CHT-053. tests/contracts/chat-limits.json repeats these defaults; test_contracts.py checks it where the
        # contract examples exist (backend-cumplify step 1.4).
        names = [f"chat_{limit.name}" for limit in fields(ChatLimits)]

        assert names == list(LIMITS)
        assert {name: Settings.model_fields[name].default for name in names} == LIMITS

    def test_the_helper_reports_only_what_is_below(self, monkeypatch) -> None:
        # CHT-053: the same comparison the lifespan logs.
        monkeypatch.setenv("CHAT_MAX_PASSAGES", "11")
        monkeypatch.setenv("CHAT_HISTORY_MAX_CHARS", "8001")

        assert chat_limits_below_default(Settings()) == [("CHAT_MAX_PASSAGES", 11, 12)]


class TestPrivacy:
    def test_the_warning_logs_no_key_or_other_setting(self, monkeypatch, caplog) -> None:
        # CHT-054: the secrets set, a limit below its default, and every record of the start.
        configure(monkeypatch, openai_api_key=SECRET, chat_max_passages="11")
        monkeypatch.setenv("SERVICE_API_KEY", SECRET)
        caplog.set_level(logging.DEBUG)
        with TestClient(create_app()) as client:
            client.post("/api/v1/chat", json=chat_body())

        warnings = [record for record in caplog.records if WARNING in record.getMessage()]
        assert len(warnings) == 1
        assert SECRET not in caplog.text
        # Only the variable name and two integers reach the record.
        assert warnings[0].args == ("CHAT_MAX_PASSAGES", 11, 12)

    def test_a_non_integer_value_stops_the_settings_without_showing_it(self, monkeypatch) -> None:
        # Existing pydantic behavior (no new restriction): an invalid value fails at startup, naming the field.
        monkeypatch.setenv("CHAT_MAX_PASSAGES", SECRET)

        with pytest.raises(ValueError) as raised:
            Settings()

        assert "chat_max_passages" in str(raised.value)
        assert SECRET not in str(raised.value)
