import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Telegram Bot
    BOT_TOKEN: str

    # Supabase credentials
    SUPABASE_URL: str
    SUPABASE_KEY: str = ""
    SUPABASE_SECRET_KEY: str = ""
    SUPABASE_PUBLISHABLE_KEY: str = ""

    def model_post_init(self, __context):
        if not self.SUPABASE_KEY:
            if self.SUPABASE_SECRET_KEY:
                self.SUPABASE_KEY = self.SUPABASE_SECRET_KEY
            elif self.SUPABASE_PUBLISHABLE_KEY:
                self.SUPABASE_KEY = self.SUPABASE_PUBLISHABLE_KEY

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

    # Настройки отправки заявок на Email
    NOTIFICATION_EMAIL: str = "fedserggas@yandex.ru"
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""      # Ваш логин Яндекса (например, fedserggas@yandex.ru)
    SMTP_PASSWORD: str = ""  # Пароль приложения Яндекса (App password)

    # Список Telegram ID администраторов через запятую (например: "12345678,87654321")
    ADMIN_TELEGRAM_IDS: str = ""

    @property
    def admin_ids(self) -> list[int]:
        """Возвращает список числовых ID администраторов."""
        if not self.ADMIN_TELEGRAM_IDS:
            return []
        result = []
        for item in self.ADMIN_TELEGRAM_IDS.split(","):
            cleaned = item.strip()
            if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
                result.append(int(cleaned))
        return result

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
