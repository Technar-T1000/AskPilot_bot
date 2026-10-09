import smtplib
import asyncio
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


def _sync_send_email(subject: str, html_body: str):
    """Синхронная отправка письма через SMTP Яндекса (или другого провайдера)."""
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning(
            "SMTP_USER или SMTP_PASSWORD не настроены. Письмо не отправлено. "
            f"Заявка сохранена в базе Supabase."
        )
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.SMTP_USER
        msg["To"] = settings.NOTIFICATION_EMAIL

        html_part = MIMEText(html_body, "html", "utf-8")
        msg.attach(html_part)

        if settings.SMTP_PORT == 465:
            with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [settings.NOTIFICATION_EMAIL], msg.as_string())
        else:
            with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10) as server:
                server.starttls()
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.sendmail(settings.SMTP_USER, [settings.NOTIFICATION_EMAIL], msg.as_string())

        logger.info(f"Уведомление о заявке успешно отправлено на {settings.NOTIFICATION_EMAIL}")
    except Exception as e:
        logger.error(f"Ошибка при отправке email на {settings.NOTIFICATION_EMAIL}: {e}")


async def send_lead_email(
    full_name: str,
    phone: str,
    problem: str,
    telegram_username: Optional[str] = None,
    user_id: Optional[int] = None
):
    """Асинхронная отправка красиво оформленного письма о новом лиде."""
    subject = f"🔥 Новая заявка AskPilot: {full_name}"

    tg_user_str = f"@{telegram_username}" if telegram_username else f"ID: {user_id}"
    tg_link = f"https://t.me/{telegram_username}" if telegram_username else ""

    html_content = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #e0e0e0; border-radius: 8px;">
        <h2 style="color: #2b5797; margin-top: 0;">🚀 Новая заявка от клиента (AskPilot Bot)</h2>
        <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
        
        <p style="font-size: 16px; margin: 8px 0;"><strong>👤 ФИО клиента:</strong> {full_name}</p>
        <p style="font-size: 16px; margin: 8px 0;"><strong>📞 Телефон:</strong> <a href="tel:{phone}" style="color: #1a73e8; text-decoration: none;">{phone}</a></p>
        <p style="font-size: 16px; margin: 8px 0;"><strong>✈️ Telegram:</strong> {f'<a href="{tg_link}">{tg_user_str}</a>' if tg_link else tg_user_str}</p>
        
        <div style="background-color: #f8f9fa; padding: 15px; border-left: 4px solid #2b5797; border-radius: 4px; margin: 15px 0;">
            <p style="margin: 0; font-weight: bold; color: #555;">📝 Суть проблемы / задачи:</p>
            <p style="margin: 8px 0 0 0; font-size: 15px; line-height: 1.4; color: #333;">{problem}</p>
        </div>
        
        <hr style="border: none; border-top: 1px solid #eee; margin: 15px 0;">
        <p style="font-size: 12px; color: #888; margin-bottom: 0;">Заявка сохранена в базе Supabase и доставлена роботом AskPilot.</p>
    </div>
    """

    try:
        await asyncio.to_thread(_sync_send_email, subject, html_content)
    except Exception as e:
        logger.error(f"Не удалось отправить email-уведомление: {e}")


async def send_telegram_alert(bot, message_text: str):
    """Отправка уведомления всем администраторам из списка ADMIN_TELEGRAM_IDS."""
    admin_ids = settings.admin_ids
    if not admin_ids:
        return

    for admin_id in admin_ids:
        try:
            await bot.send_message(
                chat_id=admin_id,
                text=message_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Не удалось отправить уведомление админу {admin_id}: {e}")
