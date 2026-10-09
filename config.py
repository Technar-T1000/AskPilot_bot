import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram Bot
    BOT_TOKEN: str

    # Supabase credentials
    SUPABASE_URL: str
    SUPABASE_KEY: str

    # Webhook & Host Settings (Render)
    WEBHOOK_BASE_URL: str = "https://askpilot-bot.onrender.com"
    WEBHOOK_PATH: str = "/webhook"
    SECRET_TOKEN: str = "webhook-secret-token"

    # Server settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Интервал самопинга для предотвращения засыпания на Render (840 сек = 14 минут)
    PING_INTERVAL_SECONDS: int = 840

    # Ссылки для кнопок быстрого перехода
    MAX_APP_URL: str = "https://max.ru/u/f9LHodD0cOL6l-6QMFdUEZxEMAvmjgiz0m9IZLT_QELNVMmYCTr5i9PGaTw"
    TELEGRAM_SUPPORT_URL: str = "https://t.me/Advokaaty"
    WEBSITE_URL: str = "https://bazislaw.ru/"

    @property
    def webhook_url(self) -> str:
        return f"{self.WEBHOOK_BASE_URL.rstrip('/')}{self.WEBHOOK_PATH}"

    @property
    def health_url(self) -> str:
        return f"{self.WEBHOOK_BASE_URL.rstrip('/')}/health"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()
