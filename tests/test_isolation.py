import os

from ia_cumplify.config.settings import Settings, get_settings


def test_settings_come_neither_from_dotenv_nor_from_the_shell() -> None:
    assert Settings.model_config["env_file"] is None
    settings = get_settings()
    assert (settings.service_api_key, settings.openai_api_key, settings.database_url) == ("", "", "")
    assert settings.openai_max_retries == 0
    assert os.environ["OPENAI_BASE_URL"].startswith("http://127.0.0.1:")
