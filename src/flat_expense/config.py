from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")
    database_url: str = "postgresql+psycopg://flat@db/flat"
    api_token: SecretStr = Field(min_length=32)
    allowed_telegram_user_ids: str
    telegram_bot_token: SecretStr | None = None
    telegram_api_root: str = "https://api.telegram.org"
    api_base_url: str = "http://api:8000"
    web_enabled: bool = False
    web_owner_id: int | None = Field(default=None, gt=0)
    web_public: bool = False
    web_password_hash: SecretStr | None = None
    web_allowed_hosts: str = "localhost,127.0.0.1,::1"

    @field_validator("web_owner_id", mode="before")
    @classmethod
    def optional_web_owner(cls, value):
        return None if value == "" else value

    @field_validator("web_password_hash", mode="before")
    @classmethod
    def optional_web_password_hash(cls, value):
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_web_owner(self):
        if self.web_enabled:
            if self.web_owner_id is None and len(self.allowed_ids) == 1:
                self.web_owner_id = next(iter(self.allowed_ids))
            if self.web_owner_id not in self.allowed_ids:
                raise ValueError("Для веб-интерфейса выберите WEB_OWNER_ID из разрешённых ID.")
        if self.web_public and self.web_password_hash is None:
            raise ValueError("Для публичного веб-интерфейса задайте WEB_PASSWORD_HASH.")
        return self

    @field_validator("web_allowed_hosts")
    @classmethod
    def validate_web_hosts(cls, value: str) -> str:
        hosts = [host.strip().lower() for host in value.split(",") if host.strip()]
        if not hosts or any(
            "/" in host or " " in host or (":" in host and host != "::1") for host in hosts
        ):
            raise ValueError("WEB_ALLOWED_HOSTS: укажите имена или IP через запятую, без портов.")
        return ",".join(dict.fromkeys(hosts))

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

    @property
    def allowed_web_hosts(self) -> set[str]:
        return set(self.web_allowed_hosts.split(","))


@lru_cache
def settings() -> Settings:
    return Settings()
