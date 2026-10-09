import os
import asyncio
import logging
import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status, Header
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage

from config import settings
from handlers import router

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("askpilot_bot")

# Инициализация бота и диспетчера aiogram
bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)


async def keep_alive_task():
    """
    Фоновая задача самопинга: отправляет GET-запрос на /health каждые N минут.
    Предотвращает засыпание инстанса на бесплатном тарифе Render.
    """
    logger.info(f"Запущен фоновый мониторинг Keep-Alive. Интервал: {settings.PING_INTERVAL_SECONDS} сек.")
    await asyncio.sleep(10)  # Небольшая пауза для гарантированного запуска сервера

    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                url = settings.health_url
                response = await client.get(url)
                logger.info(f"Keep-Alive пинг отправлен на {url} -> HTTP {response.status_code}")
            except Exception as e:
                logger.warning(f"Keep-Alive: не удалось выполнить запрос к {settings.health_url} ({e})")

            await asyncio.sleep(settings.PING_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Жизненный цикл FastAPI: регистрация Webhook при старте и корректное закрытие."""
    logger.info("Запуск AskPilot сервиса...")

    # Устанавливаем Webhook в Telegram
    try:
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.SECRET_TOKEN,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
        logger.info(f"Webhook успешно подключен к Telegram: {settings.webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка при установке Webhook: {e}")

    # Запускаем фоновый процесс Keep-Alive
    ping_task = asyncio.create_task(keep_alive_task())

    yield  # Сервер работает

    # Корректная остановка
    logger.info("Остановка AskPilot сервиса...")
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass

    try:
        await bot.delete_webhook()
        await bot.session.close()
        logger.info("Webhook отключен, сессия бота завершена.")
    except Exception as e:
        logger.warning(f"Ошибка завершения сессии бота: {e}")


# Создание приложения FastAPI
app = FastAPI(title="AskPilot Bot API", lifespan=lifespan)


@app.get("/")
async def root():
    """Главный эндпоинт статуса сервиса."""
    return {"app": "AskPilot Bot", "status": "online"}


@app.get("/health")
async def health_check():
    """
    Эндпоинт для проверки активности сервиса (Keep-Alive).
    Сюда поступают внешние запросы для предотвращения сна на Render.
    """
    return {"status": "ok", "service": "AskPilot_bot"}


@app.post(settings.WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=None)
):
    """Прием и обработка входящих сообщений от серверов Telegram."""
    if settings.SECRET_TOKEN and x_telegram_bot_api_secret_token != settings.SECRET_TOKEN:
        logger.warning("Отклонен неавторизованный запрос к вебхуку.")
        return Response(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")

    data = await request.json()
    update = types.Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)

    return Response(status_code=status.HTTP_200_OK)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("main:app", host=settings.HOST, port=port, reload=True)
