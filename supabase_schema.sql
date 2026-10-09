-- ========================================================
-- SQL-скрипт для Supabase (AskPilot Bot / TG Ads Lead Gen)
-- Выполните в: Supabase Dashboard -> SQL Editor -> New Query -> Run
-- ========================================================

-- 1. Таблица пользователей
CREATE TABLE IF NOT EXISTS public.users (
    telegram_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    utm_source TEXT, -- Метка кампании из TG Ads (deep link /start <метка>)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_username ON public.users (username);

-- 2. Таблица лидов и заявок с рекламы TG Ads
CREATE TABLE IF NOT EXISTS public.leads (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT REFERENCES public.users(telegram_id) ON DELETE SET NULL,
    telegram_username TEXT,
    full_name TEXT NOT NULL,         -- ФИО клиента
    phone TEXT NOT NULL,             -- Контактный номер телефона
    problem TEXT NOT NULL,           -- Какая проблема / задача
    utm_source TEXT,                 -- Кампания / объявление TG Ads
    status TEXT DEFAULT 'new' NOT NULL, -- 'new', 'in_progress', 'completed', 'cancelled'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Индексы для быстрой фильтрации заявок
CREATE INDEX IF NOT EXISTS idx_leads_user_id ON public.leads (user_id);
CREATE INDEX IF NOT EXISTS idx_leads_status ON public.leads (status);
CREATE INDEX IF NOT EXISTS idx_leads_created_at ON public.leads (created_at DESC);

COMMENT ON TABLE public.users IS 'Пользователи, перешедшие в бота (в т.ч. из TG Ads)';
COMMENT ON TABLE public.leads IS 'Лиды с заполненными ФИО, телефоном и описанием проблемы';
