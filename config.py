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
    one_c_x_bot_token: SecretStr | None = Field(
        default=None,
        validation_alias="ONE_C_X_BOT_TOKEN",
    )
    mock_mode_on_1c_failure: bool = Field(
        default=True,
        validation_alias="MOCK_MODE_ON_1C_FAILURE",
    )
    admin_ids: str = Field(default="", validation_alias="ADMIN_IDS")
    support_contact: str = Field(default="+380000000000", validation_alias="SUPPORT_CONTACT")
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/meat_bot.db",
        validation_alias="DATABASE_URL",
    )
    backup_dir: str = Field(default="./backups", validation_alias="BACKUP_DIR")
    backup_retention_days: int = Field(default=30, validation_alias="BACKUP_RETENTION_DAYS")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")

    @property
    def admin_id_set(self) -> set[int]:
        result: set[int] = set()
        for raw in self.admin_ids.split(","):
            candidate = raw.strip()
            if not candidate:
                continue
            try:
                result.add(int(candidate))
            except ValueError:
                continue
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
