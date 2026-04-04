from functools import lru_cache

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(validation_alias="BOT_TOKEN", min_length=10)
    one_c_base_url: AnyHttpUrl | None = Field(
        default=None,
        validation_alias="ONE_C_BASE_URL",
    )
    one_c_username: str | None = Field(default=None, validation_alias="ONE_C_USERNAME")
    one_c_password: SecretStr | None = Field(
        default=None,
        validation_alias="ONE_C_PASSWORD",
    )
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
