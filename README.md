# 🚀 AskPilot Bot (Сбор лидов из TG Ads + Supabase + Render)

Telegram-бот для конвертации трафика из **Telegram Ads** в реальные заявки. Бот опрашивает клиента, сохраняет данные в облачную базу данных **Supabase** и предоставляет кнопки для быстрой связи.

---

## 🎯 Возможности бота

1. **4 главные кнопки**:
   * 📝 **Заполнить заявку** — запускает пошаговый опрос клиента.
   * 📱 **Приложение MAX** — ссылка на приложение MAX (`MAX_APP_URL`).
   * ✈️ **Написать в TG** — ссылка на личный диалог с менеджером в Telegram (`TELEGRAM_SUPPORT_URL`).
   * 🌐 **Открыть сайт** — ссылка на официальный сайт компании (`WEBSITE_URL`).

2. **Пошаговый сценарий лидогенерации (FSM)**:
   * **Шаг 1**: Сбор **ФИО** клиента.
   * **Шаг 2**: Сбор **контактного номера телефона** (можно ввести вручную или отправить одной кнопкой `📱 Отправить свой номер`).
   * **Шаг 3**: Сбор **сути проблемы / задачи** клиента.

3. **Интеграция с Supabase**:
   * При старте бот фиксирует профиль пользователя и UTM-метку (если клиент пришел из объявления TG Ads по ссылке вида `https://t.me/bot?start=campaign_id`).
   * Заявка мгновенно попадает в таблицу `leads` с полями: ФИО, телефон, проблема, Telegram ID, @username, статус (`new`) и датой.

4. **Работа на Render без засыпания**:
   * Эндпоинт `GET /health` + встроенный самопинг + файл GitHub Actions [.github/workflows/keep_alive.yml](file:///D:/Work_ru/AskPilot_bot/.github/workflows/keep_alive.yml) каждые 14 минут.

---

## ⚙️ Пошаговый запуск

### 1. Настройка базы в Supabase
1. Зайдите в проект на [supabase.com](https://supabase.com).
2. Откройте **SQL Editor** -> **New query**.
3. Вставьте и выполните код из файла [supabase_schema.sql](file:///D:/Work_ru/AskPilot_bot/supabase_schema.sql).
4. В **Project Settings -> API** скопируйте `Project URL` и `anon public key`.

### 2. Заполнение настроек (.env)
Скопируйте `.env.example` в `.env` и укажите ваши реальные значения:
* `BOT_TOKEN`: токен от [@BotFather](https://t.me/BotFather)
* `SUPABASE_URL` и `SUPABASE_KEY`
* `WHATSAPP_URL`: ссылка на WhatsApp (например, `https://wa.me/79991234567`)
* `TELEGRAM_SUPPORT_URL`: ссылка на менеджера (например, `https://t.me/manager_tg`)
* `WEBSITE_URL`: адрес вашего сайта
* `WEBHOOK_BASE_URL`: URL сервиса на Render (например, `https://askpilot-bot.onrender.com`)

### 3. Загрузка в GitHub
```bash
cd D:\Work_ru\AskPilot_bot
git init
git add .
git commit -m "AskPilot TG Ads bot with Supabase integration"
git branch -M main
git remote add origin https://github.com/Technar-T1000/AskPilot_bot.git
git push -u origin main
```

### 4. Развертывание на Render.com
1. Создайте **New Web Service** на [Render.com](https://dashboard.render.com).
2. Выберите ваш репозиторий.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Вставьте переменные окружения в разделе **Environment Variables**.
6. Нажмите **Deploy**.
