from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    database_url: str = "postgresql+psycopg://flat@db/flat"
    api_token: SecretStr = Field(min_length=32)
    allowed_telegram_user_ids: str
    telegram_bot_token: SecretStr | None = None
    telegram_api_root: str = "https://api.telegram.org"
    api_base_url: str = "http://api:8000"

    @field_validator("telegram_api_root")
    @classmethod
    def validate_telegram_api_root(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.path
            or url.query
            or url.fragment
        ):
            raise ValueError("TELEGRAM_API_ROOT: нужен HTTPS-адрес без пути, пароля и параметров.")
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError("Некорректный порт Telegram-релея.")
        return value

    @field_validator("allowed_telegram_user_ids")
    @classmethod
    def validate_allowed_ids(cls, value: str) -> str:
        parts = [part.strip() for part in value.split(",")]
        if not parts or any(
            not part.isascii() or not part.isdecimal() or int(part) <= 0 for part in parts
        ):
            raise ValueError("Укажите положительные Telegram user ID через запятую.")
        return ",".join(str(int(part)) for part in parts)

    @property
    def allowed_ids(self) -> set[int]:
        return {int(x.strip()) for x in self.allowed_telegram_user_ids.split(",") if x.strip()}


@lru_cache
def settings() -> Settings:
    return Settings()
