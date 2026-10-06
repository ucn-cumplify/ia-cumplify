"""CHT-013: the CHAT_* settings, their defaults and .env.example match the configuration table of
docs/Chat/api.md. .env.example holds placeholders only: no client is built from it."""

from pathlib import Path

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
