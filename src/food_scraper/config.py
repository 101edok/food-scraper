from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RECON_DIR = PROJECT_ROOT / "recon"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_dsn: str = Field(
        default="mysql+asyncmy://lenta:lenta@127.0.0.1:3306/lenta",
        alias="MYSQL_DSN",
    )
    city_subdomain: str = Field(default="moscow", alias="CITY_SUBDOMAIN")
    delivery_mode: str = Field(default="pickup", alias="DELIVERY_MODE")
    concurrency: int = Field(default=8, alias="CONCURRENCY")
    rps: float = Field(default=5.0, alias="RPS")
    device_id: str = Field(
        default="11111111-1111-1111-1111-111111111111", alias="DEVICE_ID"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    proxy_url: str | None = Field(default=None, alias="PROXY_URL")
    user_agent: str | None = Field(default=None, alias="USER_AGENT")
    # Алерты о прогонах и качестве данных в Telegram; без них — только в лог
    alert_telegram_token: str | None = Field(default=None, alias="ALERT_TELEGRAM_TOKEN")
    alert_telegram_chat_id: str | None = Field(default=None, alias="ALERT_TELEGRAM_CHAT_ID")

    @property
    def base_url(self) -> str:
        return f"https://{self.city_subdomain}.lenta.com"

    @property
    def x_domain(self) -> str:
        return f"{self.city_subdomain}.lenta.com"


def get_settings() -> Settings:
    return Settings()
