import asyncio
import logging
from typing import Optional, Dict, Any
from supabase import create_client, Client
from config import settings

logger = logging.getLogger(__name__)

# Инициализация клиента Supabase
supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)


def _sync_upsert_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    utm_source: Optional[str]
) -> Dict[str, Any]:
    """Синхронная запись пользователя с меткой перехода в Supabase."""
    data = {
        "telegram_id": telegram_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "utm_source": utm_source
    }
    response = supabase.table("users").upsert(data, on_conflict="telegram_id").execute()
    return response.data


def _sync_create_lead(
    user_id: int,
    telegram_username: Optional[str],
    full_name: str,
    phone: str,
    problem: str,
    utm_source: Optional[str]
) -> Dict[str, Any]:
    """Синхронное создание лида с ФИО, телефоном и описанием проблемы."""
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


async def upsert_user(
    telegram_id: int,
    username: Optional[str],
    first_name: Optional[str],
    last_name: Optional[str],
    utm_source: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Асинхронная запись или обновление пользователя."""
    try:
        return await asyncio.to_thread(_sync_upsert_user, telegram_id, username, first_name, last_name, utm_source)
    except Exception as e:
        logger.error(f"Ошибка при сохранении пользователя {telegram_id} в Supabase: {e}")
        return None


async def create_lead(
    user_id: int,
    telegram_username: Optional[str],
    full_name: str,
    phone: str,
    problem: str,
    utm_source: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Асинхронное сохранение лида из TG Ads в Supabase."""
    try:
        return await asyncio.to_thread(_sync_create_lead, user_id, telegram_username, full_name, phone, problem, utm_source)
    except Exception as e:
        logger.error(f"Ошибка при сохранении лида пользователя {user_id} в Supabase: {e}")
        return None
