from pathlib import Path

from pydantic import AnyHttpUrl, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "meat_bot.db"
DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
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
    one_c_timeout: int = Field(default=10, validation_alias="ONE_C_TIMEOUT", ge=1)
    mock_mode: bool = Field(default=False, validation_alias="MOCK_MODE")
    mock_mode_on_1c_failure: bool = Field(
        default=True,
        validation_alias="MOCK_MODE_ON_1C_FAILURE",
    )
    admin_ids: str = Field(default="", validation_alias="ADMIN_IDS")
    support_contact: str = Field(default="+380000000000", validation_alias="SUPPORT_CONTACT")
    database_url: str = Field(
        default=f"sqlite+aiosqlite:///{DEFAULT_DATABASE_PATH.as_posix()}",
        validation_alias="DATABASE_URL",
    )
    backup_dir: str = Field(default=str(DEFAULT_BACKUP_DIR), validation_alias="BACKUP_DIR")
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


settings = Settings()


def get_settings() -> Settings:
    return settings


__all__ = ["Settings", "settings", "get_settings", "PROJECT_ROOT"]
