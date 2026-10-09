import os
import asyncio
import logging
import smtplib
import httpx
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

from pydantic_settings import BaseSettings, SettingsConfigDict
from fastapi import FastAPI, Request, Response, status, Header
from supabase import create_client, Client
from aiogram import Bot, Dispatcher, Router, F, types
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==============================================================================
# 1. КОНФИГУРАЦИЯ И НАСТРОЙКИ (SETTINGS)
# ==============================================================================

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
    PING_INTERVAL_SECONDS: int = 840  # 14 минут (Render засыпает через 15)

    # 4 ссылки для кнопок
    MAX_APP_URL: str = "https://max.ru/u/f9LHodD0cOL6l-6QMFdUEZxEMAvmjgiz0m9IZLT_QELNVMmYCTr5i9PGaTw"
    TELEGRAM_SUPPORT_URL: str = "https://t.me/Advokaaty"
    WEBSITE_URL: str = "https://bazislaw.ru/"

    # Настройки отправки заявок на Email
    NOTIFICATION_EMAIL: str = "fedserggas@yandex.ru"
    SMTP_HOST: str = "smtp.yandex.ru"
    SMTP_PORT: int = 465
    SMTP_USER: str = ""      # Логин (fedserggas@yandex.ru)
    SMTP_PASSWORD: str = ""  # 16-значный пароль приложения Яндекса

    # Telegram ID администраторов через запятую (например: "12345678,87654321")
    ADMIN_TELEGRAM_IDS: str = ""

    @property
    def admin_ids(self) -> List[int]:
        if not self.ADMIN_TELEGRAM_IDS:
            return []
        res = []
        for item in self.ADMIN_TELEGRAM_IDS.split(","):
            cleaned = item.strip()
            if cleaned.isdigit() or (cleaned.startswith("-") and cleaned[1:].isdigit()):
                res.append(int(cleaned))
        return res

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

# Логирование
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logger = logging.getLogger("askpilot_bot")

# ==============================================================================
# 2. БАЗА ДАННЫХ (SUPABASE)
# ==============================================================================

supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

def _sync_upsert_user(telegram_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str], utm_source: Optional[str]) -> Dict[str, Any]:
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "utm_source": utm_source
    }
    response = supabase.table("users").upsert(data, on_conflict="telegram_id").execute()
    return response.data

def _sync_create_lead(user_id: int, telegram_username: Optional[str], full_name: str, phone: str, problem: str, utm_source: Optional[str]) -> Dict[str, Any]:
    data = {
        "user_id": user_id,
        "telegram_username": telegram_username,
        "full_name": full_name,
        "phone": phone,
        "problem": problem,
        "utm_source": utm_source,
        "status": "new"
    }
    response = supabase.table("leads").insert(data).execute()
    return response.data

async def upsert_user(telegram_id: int, username: Optional[str], first_name: Optional[str], last_name: Optional[str], utm_source: Optional[str] = None):
    try:
        return await asyncio.to_thread(_sync_upsert_user, telegram_id, username, first_name, last_name, utm_source)
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя {telegram_id} в Supabase: {e}")
        return None

async def create_lead(user_id: int, telegram_username: Optional[str], full_name: str, phone: str, problem: str, utm_source: Optional[str] = None):
    try:
        return await asyncio.to_thread(_sync_create_lead, user_id, telegram_username, full_name, phone, problem, utm_source)
    except Exception as e:
        logger.error(f"Ошибка сохранения лида в Supabase: {e}")
        return None

# ==============================================================================
# 3. УВЕДОМЛЕНИЯ (EMAIL + TELEGRAM АДМИНИСТРАТОРЫ)
# ==============================================================================

def _sync_send_email(subject: str, html_body: str):
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP_USER или SMTP_PASSWORD не настроены в Render. Email пропущен.")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_USER
        msg["To"] = settings.NOTIFICATION_EMAIL
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [settings.NOTIFICATION_EMAIL], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [settings.NOTIFICATION_EMAIL], msg.as_string())
        logger.info(f"Письмо успешно отправлено на {settings.NOTIFICATION_EMAIL}")
    except Exception as e:
        logger.error(f"Ошибка отправки email: {e}")

async def send_lead_email(full_name: str, phone: str, problem: str, telegram_username: Optional[str] = None, user_id: Optional[int] = None):
    subject = f"🔥 Новая заявка AskPilot: {full_name}"
    tg_user_str = f"@{telegram_username}" if telegram_username else f"ID: {user_id}"
    tg_link = f"https://t.me/{telegram_username}" if telegram_username else ""

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
        <h2 style="color: #2b5797; margin-top: 0;">🚀 Новая заявка AskPilot Bot</h2>
        <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
        <p><strong>👤 ФИО:</strong> {full_name}</p>
        <p><strong>📞 Телефон:</strong> <a href="tel:{phone}">{phone}</a></p>
        <p><strong>✈️ Telegram:</strong> {f'<a href="{tg_link}">{tg_user_str}</a>' if tg_link else tg_user_str}</p>
        <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #2b5797; margin: 15px 0;">
            <p style="margin: 0; font-weight: bold;">📝 Проблема / задача:</p>
            <p style="margin: 8px 0 0 0;">{problem}</p>
        </div>
        <p style="font-size: 12px; color: #888;">Заявка сохранена в базе Supabase.</p>
    </div>
    """
    await asyncio.to_thread(_sync_send_email, subject, html_content)

async def send_telegram_alert(bot: Bot, message_text: str):
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(chat_id=admin_id, text=message_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Ошибка отправки админу {admin_id}: {e}")

# ==============================================================================
# 4. ЛОГИКА БОТА И FSM-СЦЕНАРИЙ (HANDLERS)
# ==============================================================================

router = Router()

class LeadForm(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_phone = State()
    waiting_for_problem = State()

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Заполнить заявку", callback_data="start_lead")],
            [
                InlineKeyboardButton(text="📱 Приложение MAX", url=settings.MAX_APP_URL),
                InlineKeyboardButton(text="✈️ Написать в TG", url=settings.TELEGRAM_SUPPORT_URL)
            ],
            [InlineKeyboardButton(text="🌐 Открыть сайт", url=settings.WEBSITE_URL)]
        ]
    )

def get_phone_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить свой номер", request_contact=True)],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext):
    await state.clear()
    user = message.from_user
    utm_source = command.args if command.args else None

    await upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        utm_source=utm_source
    )

    welcome_text = (
        f"Здравствуйте, {user.first_name}!\n\n"
        "Добро пожаловать в **AskPilot** 🚀\n"
        "Мы готовы оперативно помочь вам решить любой вопрос.\n\n"
        "Выберите удобный способ связи или заполните заявку прямо сейчас:"
    )
    await message.answer(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

@router.callback_query(F.data == "start_lead")
async def start_lead_survey(callback: CallbackQuery, state: FSMContext):
    await state.set_state(LeadForm.waiting_for_full_name)
    await callback.message.answer("Шаг 1 из 3: Введите ваше ФИО (Фамилия Имя Отчество):")
    await callback.answer()

@router.message(F.text == "❌ Отмена")
@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Заполнение заявки отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Главное меню:", reply_markup=get_main_keyboard())

@router.message(LeadForm.waiting_for_full_name)
async def process_full_name(message: Message, state: FSMContext):
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Пожалуйста, введите корректное ФИО:")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(LeadForm.waiting_for_phone)
    await message.answer(
        f"Спасибо, {full_name}!\n\n"
        "Шаг 2 из 3: Укажите ваш контактный номер телефона "
        "(кнопкой ниже или текстом вручную):",
        reply_markup=get_phone_keyboard()
    )

@router.message(LeadForm.waiting_for_phone, F.contact)
@router.message(LeadForm.waiting_for_phone, F.text)
async def process_phone(message: Message, state: FSMContext):
    if message.contact:
        phone = message.contact.phone_number
    else:
        phone = message.text.strip()
        if len(phone) < 5:
            await message.answer("Пожалуйста, введите корректный номер телефона:")
            return

    await state.update_data(phone=phone)
    await state.set_state(LeadForm.waiting_for_problem)
    await message.answer(
        "Шаг 3 из 3: Опишите, пожалуйста, какая у вас проблема или с каким вопросом требуется помочь?",
        reply_markup=ReplyKeyboardRemove()
    )

@router.message(LeadForm.waiting_for_problem)
async def process_problem(message: Message, state: FSMContext):
    problem = message.text.strip()
    user_data = await state.get_data()
    full_name = user_data.get("full_name")
    phone = user_data.get("phone")
    user = message.from_user

    # 1. Сохраняем в Supabase
    lead = await create_lead(
        user_id=user.id,
        telegram_username=user.username,
        full_name=full_name,
        phone=phone,
        problem=problem
    )

    # 2. Отправляем на Email в фоне
    asyncio.create_task(
        send_lead_email(
            full_name=full_name,
            phone=phone,
            problem=problem,
            telegram_username=user.username,
            user_id=user.id
        )
    )

    # 3. Отправляем всем администраторам в Telegram
    admin_alert_text = (
        f"🔥 <b>Новая заявка AskPilot!</b>\n\n"
        f"👤 <b>ФИО:</b> {full_name}\n"
        f"📞 <b>Телефон:</b> {phone}\n"
        f"💬 <b>Telegram:</b> @{user.username or user.id}\n"
        f"📝 <b>Проблема:</b> {problem}"
    )
    asyncio.create_task(send_telegram_alert(message.bot, admin_alert_text))

    await state.clear()

    response_text = (
        "✅ *Ваша заявка успешно отправлена!*\n\n"
        f"👤 **ФИО**: {full_name}\n"
        f"📞 **Телефон**: {phone}\n"
        f"📝 **Проблема**: {problem}\n\n"
        "Мы уже взяли ваш запрос в обработку и свяжемся с вами в ближайшее время."
    )
    await message.answer(response_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

# ==============================================================================
# 5. СЕРВЕР FASTAPI, WEBHOOK И KEEP-ALIVE
# ==============================================================================

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
dp.include_router(router)

async def keep_alive_task():
    logger.info(f"Запущен фоновый мониторинг Keep-Alive. Интервал: {settings.PING_INTERVAL_SECONDS} сек.")
    await asyncio.sleep(10)
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                url = settings.health_url
                res = await client.get(url)
                logger.info(f"Keep-Alive пинг -> {url} (HTTP {res.status_code})")
            except Exception as e:
                logger.warning(f"Keep-Alive предупреждение: {e}")
            await asyncio.sleep(settings.PING_INTERVAL_SECONDS)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск AskPilot сервиса...")
    try:
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.SECRET_TOKEN,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
        logger.info(f"Webhook успешно подключен к Telegram: {settings.webhook_url}")
    except Exception as e:
        logger.error(f"Ошибка установки Webhook: {e}")

    ping_task = asyncio.create_task(keep_alive_task())
    yield
    logger.info("Остановка AskPilot сервиса...")
    ping_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    try:
        await bot.delete_webhook()
        await bot.session.close()
    except Exception as e:
        logger.warning(f"Ошибка закрытия сессии: {e}")

app = FastAPI(title="AskPilot Bot API", lifespan=lifespan)

@app.get("/")
async def root():
    return {"app": "AskPilot Bot", "status": "online"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "AskPilot_bot"}

@app.post(settings.WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=None)
):
    if settings.SECRET_TOKEN and x_telegram_bot_api_secret_token != settings.SECRET_TOKEN:
        return Response(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    data = await request.json()
    update = types.Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=status.HTTP_200_OK)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("main:app", host=settings.HOST, port=port, reload=True)
