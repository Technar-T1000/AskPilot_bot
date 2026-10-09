from aiogram import Router, F
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

from config import settings
from database import upsert_user, create_lead

router = Router()


# Состояния машины состояний (FSM) для сбора лида
class LeadForm(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_phone = State()
    waiting_for_problem = State()


# 4 кнопки главного меню
def get_main_keyboard() -> InlineKeyboardMarkup:
    """
    Клавиатура с 4 кнопками по требованию:
    1. Заполнить заявку
    2. Написать в WA
    3. Написать в TG
    4. Открыть сайт
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📝 Заполнить заявку", callback_data="start_lead")
            ],
            [
                InlineKeyboardButton(text="📱 Приложение MAX", url=settings.MAX_APP_URL),
                InlineKeyboardButton(text="✈️ Написать в TG", url=settings.TELEGRAM_SUPPORT_URL)
            ],
            [
                InlineKeyboardButton(text="🌐 Открыть сайт", url=settings.WEBSITE_URL)
            ]
        ]
    )


# Кнопка отправки контакта / отмены
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
    """
    Старт бота (в том числе при переходе из рекламы TG Ads).
    command.args считывает UTM-метку/параметр из ссылки: t.me/bot?start=ad_campaign_1
    """
    await state.clear()
    user = message.from_user
    utm_source = command.args if command.args else None

    # Сохраняем пользователя в Supabase с фиксацией источника перехода
    await upsert_user(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        utm_source=utm_source
    )

    welcome_text = (
        f"Здравствуйте, {user.first_name}!\n\n"
        "Добро пожаловать в наш бот. Мы готовы оперативно помочь вам решить любой вопрос.\n\n"
        "Выберите удобный способ связи или заполните заявку прямо сейчас:"
    )

    await message.answer(welcome_text, reply_markup=get_main_keyboard())


@router.callback_query(F.data == "start_lead")
async def start_lead_survey(callback: CallbackQuery, state: FSMContext):
    """Старт сценария сбора данных: 1. Запрос ФИО."""
    await state.set_state(LeadForm.waiting_for_full_name)
    await callback.message.answer(
        "Шаг 1 из 3: Введите ваше ФИО (Фамилия Имя Отчество):"
    )
    await callback.answer()


@router.message(F.text == "❌ Отмена")
@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext):
    """Отмена заполнения заявки."""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Главное меню:", reply_markup=get_main_keyboard())
        return

    await state.clear()
    await message.answer("Заполнение заявки отменено.", reply_markup=ReplyKeyboardRemove())
    await message.answer("Главное меню:", reply_markup=get_main_keyboard())


@router.message(LeadForm.waiting_for_full_name)
async def process_full_name(message: Message, state: FSMContext):
    """Шаг 1: Получаем ФИО клиента и запрашиваем номер телефона."""
    full_name = message.text.strip()
    if len(full_name) < 2:
        await message.answer("Пожалуйста, введите корректное ФИО:")
        return

    await state.update_data(full_name=full_name)
    await state.set_state(LeadForm.waiting_for_phone)

    await message.answer(
        f"Спасибо, {full_name}!\n\n"
        "Шаг 2 из 3: Укажите ваш контактный номер телефона. "
        "Вы можете нажать кнопку ниже для отправки или написать номер вручную:",
        reply_markup=get_phone_keyboard()
    )


@router.message(LeadForm.waiting_for_phone, F.contact)
@router.message(LeadForm.waiting_for_phone, F.text)
async def process_phone(message: Message, state: FSMContext):
    """Шаг 2: Получаем телефон (кнопкой или текстом) и запрашиваем описание проблемы."""
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
    """
    Шаг 3: Получаем проблему, сохраняем всё в Supabase и отправляем клиенту подтверждение.
    """
    problem = message.text.strip()
    user_data = await state.get_data()

    full_name = user_data.get("full_name")
    phone = user_data.get("phone")
    user = message.from_user

    # Сохраняем лид в Supabase
    lead = await create_lead(
        user_id=user.id,
        telegram_username=user.username,
        full_name=full_name,
        phone=phone,
        problem=problem
    )

    await state.clear()

    if lead:
        response_text = (
            "✅ *Ваша заявка успешно отправлена!*\n\n"
            f"👤 **ФИО**: {full_name}\n"
            f"📞 **Телефон**: {phone}\n"
            f"📝 **Проблема**: {problem}\n\n"
            "Мы уже взяли ваш запрос в обработку и свяжемся с вами в ближайшее время."
        )
    else:
        response_text = (
            "✅ Ваша заявка принята! Мы свяжемся с вами в ближайшее время."
        )

    await message.answer(
        response_text,
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )
