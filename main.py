import os
import asyncio
import logging
import smtplib
import html
import time
import secrets
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

def _sync_send_email(subject: str, html_body: str, plain_text: str = ""):
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("SMTP_USER или SMTP_PASSWORD не настроены в Render. Email пропущен.")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"AskPilot Bot <{settings.SMTP_USER}>"
        msg["To"] = settings.NOTIFICATION_EMAIL
        if plain_text:
            msg.attach(MIMEText(plain_text, "plain", "utf-8"))
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

async def send_lead_email(
    full_name: str,
    phone: str,
    problem: str,
    telegram_username: Optional[str] = None,
    user_id: Optional[int] = None,
    utm_source: Optional[str] = None
):
    subject = f"🔥 Новая заявка AskPilot: {full_name} ({phone})"
    
    # Формируем ссылку на профиль Telegram
    if telegram_username:
        tg_url = f"https://t.me/{telegram_username}"
        tg_display = f"@{telegram_username}"
    elif user_id:
        tg_url = f"tg://user?id={user_id}"
        tg_display = f"ID: {user_id}"
    else:
        tg_url = ""
        tg_display = "Не указан"

    safe_name = html.escape(full_name)
    safe_phone = html.escape(phone)
    safe_problem = html.escape(problem)
    safe_utm = html.escape(utm_source) if utm_source else ""

    utm_row = f"""
    <tr>
        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; color: #666;"><strong>🎯 Источник (UTM):</strong></td>
        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; color: #333;">{safe_utm}</td>
    </tr>
    """ if utm_source else ""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body style="font-family: Arial, sans-serif; background-color: #f4f6f9; padding: 20px; margin: 0;">
        <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 10px; overflow: hidden; box-shadow: 0 4px 12px rgba(0,0,0,0.08); border: 1px solid #e1e4e8;">
            <div style="background-color: #1a4f8b; color: #ffffff; padding: 20px; text-align: center;">
                <h2 style="margin: 0; font-size: 22px;">⚖️ Юридическая компания «Базис»</h2>
                <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">Новая заявка через бота AskPilot</p>
            </div>
            <div style="padding: 24px;">
                <table style="width: 100%; border-collapse: collapse; font-size: 15px;">
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; width: 150px; color: #666;"><strong>👤 ФИО клиента:</strong></td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; color: #111; font-weight: bold; font-size: 16px;">{safe_name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; color: #666;"><strong>📞 Телефон:</strong></td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0;">
                            <a href="tel:{safe_phone}" style="color: #1a4f8b; font-size: 16px; font-weight: bold; text-decoration: none;">{safe_phone}</a>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0; color: #666;"><strong>✈️ Telegram:</strong></td>
                        <td style="padding: 10px 0; border-bottom: 1px solid #f0f0f0;">
                            {f'<a href="{tg_url}" style="color: #0088cc; font-weight: bold; text-decoration: underline;">{tg_display}</a>' if tg_url else tg_display}
                        </td>
                    </tr>
                    {utm_row}
                </table>

                <div style="margin-top: 20px; padding: 16px; background-color: #f8fafc; border-left: 4px solid #1a4f8b; border-radius: 4px;">
                    <div style="color: #666; font-size: 13px; font-weight: bold; text-transform: uppercase; margin-bottom: 6px;">📝 Описание проблемы / вопроса:</div>
                    <div style="color: #222; font-size: 15px; line-height: 1.5; white-space: pre-wrap;">{safe_problem}</div>
                </div>

                {f'''
                <div style="text-align: center; margin-top: 25px;">
                    <a href="{tg_url}" style="display: inline-block; background-color: #0088cc; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: bold; font-size: 15px;">
                        ✈️ Открыть диалог в Telegram
                    </a>
                </div>
                ''' if tg_url else ''}
            </div>
            <div style="background-color: #f8f9fa; padding: 12px 20px; text-align: center; font-size: 12px; color: #888; border-top: 1px solid #eeeeee;">
                Заявка сохранена в базе Supabase • AskPilot Bot
            </div>
        </div>
    </body>
    </html>
    """

    plain_text = (
        f"НОВАЯ ЗАЯВКА ASKPILOT:\n\n"
        f"ФИО: {full_name}\n"
        f"Телефон: {phone}\n"
        f"Telegram: {tg_display} ({tg_url})\n\n"
        f"Описание проблемы:\n{problem}\n"
    )

    await asyncio.to_thread(_sync_send_email, subject, html_content, plain_text)

async def send_lead_to_website(
    full_name: str,
    phone: str,
    problem: str,
    telegram_username: Optional[str] = None,
    user_id: Optional[int] = None,
    utm_source: Optional[str] = None
) -> bool:
    """Отправка заявки через проверенный доверенный сайт bazislaw.ru (send.php).
    Так как сайт отправляет почту сам через HTTPS, это на 100% обходит блокировку портов Render!"""
    if telegram_username:
        tg_link = f"https://t.me/{telegram_username}"
        tg_display = f"@{telegram_username}"
    elif user_id:
        tg_link = f"tg://user?id={user_id}"
        tg_display = f"ID: {user_id}"
    else:
        tg_link = ""
        tg_display = "Не указан"

    note_parts = [
        f"📝 Вопрос / ситуация: {problem}",
        f"✈️ Telegram клиента: {tg_display} ({tg_link})" if tg_link else f"✈️ Telegram: {tg_display}"
    ]
    if utm_source:
        note_parts.append(f"🎯 Источник рекламы (UTM): {utm_source}")
    note_parts.append("🤖 [Заявка создана автоматически через Telegram-бот AskPilot]")

    full_message = "\n\n".join(note_parts)

    payload = {
        "form_schema": "bazislaw-v2",
        "name": full_name,
        "phone": phone,
        "message": full_message,
        "company": "",  # honeypot ловушка против спам-ботов, должна быть пустой
        "consent": "1",
        "analytics_ajax": "1"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AskPilot-Bot/2.0 (Bazis Law Lead Forwarder)",
        "Referer": settings.WEBSITE_URL.rstrip('/') + "/",
        "Origin": settings.WEBSITE_URL.rstrip('/')
    }

    send_url = f"{settings.WEBSITE_URL.rstrip('/')}/send.php"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(send_url, data=payload, headers=headers)
            if resp.status_code == 200:
                result = resp.json()
                if result.get("success"):
                    logger.info(f"Заявка '{full_name}' успешно отправлена через сайт {send_url}! Lead ID: {result.get('lead_id')}")
                    return True
                else:
                    logger.warning(f"Сайт вернул отказ при отправке заявки: {result.get('message')}")
            else:
                logger.warning(f"Сайт {send_url} вернул HTTP {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Ошибка при отправке заявки через сайт: {e}")
    return False

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

# Защита от флуда и спама: хранит время последней заявки пользователя
user_last_submission: Dict[int, float] = {}

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
    if utm_source:
        await state.update_data(utm_source=utm_source)

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
    user = message.from_user

    # Защита от флуда: не чаще 1 заявки в 30 секунд от одного пользователя
    now = time.time()
    if now - user_last_submission.get(user.id, 0) < 30:
        await message.answer(
            "⚠️ Вы уже недавно отправили заявку. Мы уже получили её и свяжемся с вами в ближайшее время.",
            reply_markup=get_main_keyboard()
        )
        await state.clear()
        return
    if len(user_last_submission) > 5000:
        user_last_submission.clear()
    user_last_submission[user.id] = now

    user_data = await state.get_data()
    full_name = user_data.get("full_name")
    phone = user_data.get("phone")
    utm_source = user_data.get("utm_source")

    # 1. Сохраняем в Supabase
    lead = await create_lead(
        user_id=user.id,
        telegram_username=user.username,
        full_name=full_name,
        phone=phone,
        problem=problem,
        utm_source=utm_source
    )

    # 2. Отправляем заявку через проверенный сайт компании bazislaw.ru (send.php)
    # Это на 100% доставляет заявку на почту, гарантированно обходя сетевые блокировки Render!
    asyncio.create_task(
        send_lead_to_website(
            full_name=full_name,
            phone=phone,
            problem=problem,
            telegram_username=user.username,
            user_id=user.id,
            utm_source=utm_source
        )
    )

    # 3. Отправляем на Email напрямую (если в Render настроен SMTP)
    asyncio.create_task(
        send_lead_email(
            full_name=full_name,
            phone=phone,
            problem=problem,
            telegram_username=user.username,
            user_id=user.id,
            utm_source=utm_source
        )
    )

    # 4. Отправляем всем администраторам в Telegram (с экранированием HTML против сбоев разметки)
    safe_name = html.escape(full_name)
    safe_phone = html.escape(phone)
    safe_problem = html.escape(problem)
    safe_utm = html.escape(utm_source) if utm_source else ""
    tg_mention = f"@{user.username}" if user.username else f'<a href="tg://user?id={user.id}">{html.escape(user.full_name or str(user.id))}</a>'

    admin_alert_text = (
        f"🔥 <b>Новая заявка AskPilot!</b>\n\n"
        f"👤 <b>ФИО:</b> {safe_name}\n"
        f"📞 <b>Телефон:</b> {safe_phone}\n"
        f"💬 <b>Telegram:</b> {tg_mention}\n"
        f"📝 <b>Проблема:</b> {safe_problem}"
    )
    if safe_utm:
        admin_alert_text += f"\n🎯 <b>Источник (UTM):</b> {safe_utm}"

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

async def setup_webhook():
    """Подключение вебхука к Telegram."""
    try:
        await bot.set_webhook(
            url=settings.webhook_url,
            secret_token=settings.SECRET_TOKEN,
            drop_pending_updates=True,
            allowed_updates=dp.resolve_used_update_types()
        )
        logger.info(f"Webhook успешно подключен к Telegram: {settings.webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Ошибка установки Webhook: {e}")
        return False

async def delayed_webhook_ensure():
    """Через 25 секунд после старта повторно привязывает вебхук.
    Это на 100% защищает от сброса вебхука старым контейнером Render."""
    try:
        await asyncio.sleep(25)
        logger.info("Повторная контрольная привязка Webhook...")
        await setup_webhook()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"Ошибка в delayed_webhook_ensure: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Запуск AskPilot сервиса...")
    await setup_webhook()
    ping_task = asyncio.create_task(keep_alive_task())
    ensure_task = asyncio.create_task(delayed_webhook_ensure())
    yield
    logger.info("Остановка AskPilot сервиса...")
    ping_task.cancel()
    ensure_task.cancel()
    try:
        await ping_task
    except asyncio.CancelledError:
        pass
    try:
        await ensure_task
    except asyncio.CancelledError:
        pass
    # ВНИМАНИЕ: НЕ вызываем delete_webhook() на выходе,
    # чтобы при перезапуске старый процесс не удалял вебхук нового процесса!
    try:
        await bot.session.close()
        logger.info("Сессия бота закрыта.")
    except Exception as e:
        logger.warning(f"Ошибка закрытия сессии: {e}")

app = FastAPI(title="AskPilot Bot API", lifespan=lifespan)

@app.get("/")
async def root():
    return {"app": "AskPilot Bot", "status": "online"}

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "AskPilot_bot"}

@app.get("/set-webhook")
async def manual_set_webhook():
    """Эндпоинт для мгновенной перепривязки вебхука прямо через браузер."""
    success = await setup_webhook()
    return {
        "status": "success" if success else "error",
        "webhook_url": settings.webhook_url
    }

@app.get("/test-email")
async def test_email_endpoint():
    """Тестирование отправки письма с возвратом детального статуса или ошибки прямо в браузере."""
    if not settings.SMTP_USER:
        return {
            "status": "error",
            "message": "В Render не настроена переменная SMTP_USER. Добавьте в Environment: SMTP_USER = fedserggas@yandex.ru"
        }
    if not settings.SMTP_PASSWORD:
        return {
            "status": "error",
            "message": "В Render не настроена переменная SMTP_PASSWORD. Добавьте в Environment 16-значный пароль приложения Яндекса."
        }
    
    def _test_send():
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            msg = MIMEText(
                "Это тестовое письмо от AskPilot Bot!\n\n"
                "Если вы получили это письмо, значит отправка заявок из бота настроена и работает идеально!",
                "plain",
                "utf-8"
            )
            msg["Subject"] = "🧪 Тест почты AskPilot Bot"
            msg["From"] = settings.SMTP_USER
            msg["To"] = settings.NOTIFICATION_EMAIL
            server.sendmail(settings.SMTP_USER, [settings.NOTIFICATION_EMAIL], msg.as_string())
    
    try:
        await asyncio.to_thread(_test_send)
        return {
            "status": "success",
            "message": f"Тестовое письмо успешно отправлено с {settings.SMTP_USER} на {settings.NOTIFICATION_EMAIL}! Проверьте папку 'Входящие' и 'Спам'."
        }
    except smtplib.SMTPAuthenticationError as e:
        return {
            "status": "error",
            "error_type": "SMTPAuthenticationError (Ошибка авторизации)",
            "details": str(e),
            "hint": "Яндекс отклонил пароль. Убедитесь, что в Render указан 16-значный Пароль приложения, созданный в id.yandex.ru/security -> Пароли приложений -> Почта."
        }
    except Exception as e:
        return {
            "status": "error",
            "error_type": type(e).__name__,
            "details": str(e)
        }

@app.get("/test-site-lead")
async def test_site_lead_endpoint():
    """Тестирование автоматической отправки заявки через сайт компании bazislaw.ru."""
    success = await send_lead_to_website(
        full_name="Тестовый Заявитель (AskPilot Bot)",
        phone="+79991234567",
        problem="Тестовая проверка: заявка из бота успешно отправлена через сайт компании bazislaw.ru!",
        telegram_username="AskPilotTester"
    )
    return {
        "status": "success" if success else "error",
        "message": (
            "✅ Заявка успешно отправлена через сайт bazislaw.ru (send.php)! "
            "Сайт сам отправил письмо. Проверьте вашу почту!"
        ) if success else "❌ Сайт bazislaw.ru вернул ошибку при приеме заявки. Проверьте логи."
    }

@app.post(settings.WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)
):
    if settings.SECRET_TOKEN:
        if not x_telegram_bot_api_secret_token or not secrets.compare_digest(x_telegram_bot_api_secret_token, settings.SECRET_TOKEN):
            logger.warning("Отклонен неавторизованный запрос к вебхуку (отсутствует или не совпадает секретный токен).")
            return Response(status_code=status.HTTP_403_FORBIDDEN, content="Forbidden")
    data = await request.json()
    update = types.Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return Response(status_code=status.HTTP_200_OK)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("main:app", host=settings.HOST, port=port, reload=True)
