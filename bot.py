import os
import logging
import pytz
from datetime import datetime
from telegram import (
    Update,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)
from datetime import time as dtime
from itertools import cycle
from content import SCHEDULED_POSTS, LIFEHACKS

BOT_TOKEN      = os.environ["BOT_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID  = int(os.environ.get("OWNER_CHAT_ID", "448609289"))
CHANNEL_ID     = os.environ.get("CHANNEL_ID", "@weonerent")
ADMIN_USERNAME = "fake_smm"

MSK = pytz.timezone("Europe/Moscow")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Шаги заявки ────────────────────────────────────────────────
STEP_CITY  = 0
STEP_DATES = 1
STEP_CAR   = 2
STEP_NAME  = 3
STEP_PHONE = 4
STEP_DONE  = 5

# Шаги ручной публикации
STEP_POST_TEXT    = "post_text"
STEP_POST_BUTTONS = "post_buttons"

# ─── Клавиатуры для заявки ──────────────────────────────────────
CAR_KEYBOARD = ReplyKeyboardMarkup(
    [["Эконом", "Комфорт"], ["SUV", "Минивэн"]],
    resize_keyboard=True, one_time_keyboard=True
)
PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Отправить мой номер", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)
REMOVE = ReplyKeyboardRemove()

# ─── Inline кнопки ──────────────────────────────────────────────
SUBSCRIBE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 Наш канал", url="https://t.me/weonerent"),
     InlineKeyboardButton("🌐 Сайт", url="https://weonerent.es")]
])

KEYBOARDS = {
    "full": InlineKeyboardMarkup([
        [InlineKeyboardButton("✈️ Оставить заявку", url="https://t.me/weonerent_ai_bot")],
        [InlineKeyboardButton("🌐 Наш сайт", url="https://weonerent.es")]
    ]),
    "soft": InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Узнать стоимость", url="https://t.me/weonerent_ai_bot"),
         InlineKeyboardButton("🌐 Сайт", url="https://weonerent.es")]
    ]),
    "promo": InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Забронировать сейчас", url="https://t.me/weonerent_ai_bot")],
        [InlineKeyboardButton("📋 Подробнее на сайте", url="https://weonerent.es")]
    ]),
}

BUTTON_TYPE_KEYBOARD = ReplyKeyboardMarkup(
    [["📌 Стандарт", "💬 Мягкий CTA"], ["🔥 Акция", "❌ Без кнопок"]],
    resize_keyboard=True, one_time_keyboard=True
)
BUTTON_MAP = {
    "📌 Стандарт":   KEYBOARDS["full"],
    "💬 Мягкий CTA": KEYBOARDS["soft"],
    "🔥 Акция":      KEYBOARDS["promo"],
    "❌ Без кнопок": None,
}

# ─── Калькулятор цен ────────────────────────────────────────────
PRICES = {
    "барселона":  {"эконом": 32, "комфорт": 45, "suv": 58, "минивэн": 65},
    "малага":     {"эконом": 25, "комфорт": 38, "suv": 52, "минивэн": 60},
    "аликанте":   {"эконом": 22, "комфорт": 35, "suv": 48, "минивэн": 58},
    "тенерифе":   {"эконом": 28, "комфорт": 42, "suv": 55, "минивэн": 68},
    "мадрид":     {"эконом": 35, "комфорт": 50, "suv": 65, "минивэн": 75},
    "валенсия":   {"эконом": 24, "комфорт": 37, "suv": 50, "минивэн": 60},
    "севилья":    {"эконом": 26, "комфорт": 39, "suv": 53, "минивэн": 62},
    "гран-канария": {"эконом": 27, "комфорт": 40, "suv": 54, "минивэн": 65},
}

CITY_ALIASES = {
    "bcn": "барселона", "barcelona": "барселона",
    "malaga": "малага", "málaga": "малага",
    "alicante": "аликанте", "alicant": "аликанте",
    "tenerife": "тенерифе", "тф": "тенерифе",
    "madrid": "мадрид", "мск": "мадрид",
    "valencia": "валенсия",
    "seville": "севилья", "sevilla": "севилья",
    "gran canaria": "гран-канария", "гк": "гран-канария",
}

CAR_ALIASES = {
    "econom": "эконом", "economy": "эконом", "small": "эконом",
    "comfort": "комфорт", "medium": "комфорт",
    "suv": "suv", "джип": "suv", "внедорожник": "suv",
    "minivan": "минивэн", "van": "минивэн", "минивен": "минивэн",
}

def calc_price(city_raw: str, car_raw: str, days: int):
    city = city_raw.lower().strip()
    car  = car_raw.lower().strip()
    city = CITY_ALIASES.get(city, city)
    car  = CAR_ALIASES.get(car, car)
    if city not in PRICES:
        return None, None, None
    if car not in PRICES[city]:
        car = "эконом"
    base   = PRICES[city][car]
    # Скидка за длительность
    if days >= 14:
        disc, disc_pct = int(base * days * 0.15), 15
    elif days >= 7:
        disc, disc_pct = int(base * days * 0.10), 10
    else:
        disc, disc_pct = 0, 0
    total = base * days - disc
    return base, total, disc_pct

# ─── Данные пользователей и ротация лайфхаков ───────────────────
user_data = {}
lifehack_cycle = cycle(LIFEHACKS)   # бесконечная ротация

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY}
    return user_data[chat_id]

def is_admin(update: Update) -> bool:
    return update.effective_user.username == ADMIN_USERNAME

# ─── Автопостинг: callback для запланированного поста ───────────
async def auto_publish(context: ContextTypes.DEFAULT_TYPE):
    post = context.job.data
    try:
        if post.get("type") == "poll":
            await context.bot.send_poll(
                chat_id=CHANNEL_ID,
                question=post["question"],
                options=post["options"],
                is_anonymous=True,
            )
        else:
            keyboard = KEYBOARDS.get(post.get("buttons")) if post.get("buttons") else None
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=post["text"],
                reply_markup=keyboard,
            )
        logger.info(f"Auto-published post at {datetime.now(MSK).strftime('%d.%m %H:%M')}")
    except Exception as e:
        logger.error(f"Auto-publish error: {e}")
        try:
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"❌ Ошибка автопостинга: {e}"
            )
        except Exception:
            pass

# ─── Callback для ротации лайфхаков ─────────────────────────────
async def publish_lifehack(context: ContextTypes.DEFAULT_TYPE):
    text = next(lifehack_cycle)
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=text,
            reply_markup=KEYBOARDS["soft"],
        )
        logger.info(f"Lifehack published at {datetime.now(MSK).strftime('%d.%m %H:%M')}")
    except Exception as e:
        logger.error(f"Lifehack publish error: {e}")

# ─── Планировщик: запускается при старте бота ───────────────────
def schedule_all_posts(app):
    now = datetime.now(MSK)

    # Разовые посты (Недели 1–8)
    count = 0
    for post in SCHEDULED_POSTS:
        when = post["when"]
        if when > now:
            app.job_queue.run_once(
                auto_publish,
                when=when,
                data=post,
                name=f"post_{when.strftime('%d%m_%H%M')}",
            )
            count += 1

    # Еженедельный отчёт — каждый понедельник в 08:00 МСК
    app.job_queue.run_repeating(
        send_weekly_report,
        interval=60 * 60 * 24 * 7,
        first=dtime(8, 0, tzinfo=MSK),
        name="weekly_report",
    )

    # Вечная ротация лайфхаков — каждый пн 09:30 и чт 19:30 МСК
    # Запускается начиная с 23 июня (после окончания основного расписания)
    rotation_start = MSK.localize(datetime(2026, 6, 23, 0, 0))
    if now < rotation_start:
        delay = (rotation_start - now).total_seconds()
    else:
        delay = 0

    app.job_queue.run_repeating(
        publish_lifehack,
        interval=60 * 60 * 24 * 7 / 2,   # дважды в неделю (каждые 3.5 дня)
        first=rotation_start if now < rotation_start else now,
        name="lifehack_rotation",
    )

    logger.info(f"Запланировано {count} постов + вечная ротация лайфхаков")
    return count

# ─── Команды ────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY}
    await update.message.reply_text(
        "Добрый день! WeOneRent — аренда автомобилей в Испании.\n\n"
        "Отвечайте коротко — оформим заявку за пару минут.\n\n"
        "В каком городе нужен автомобиль?",
        reply_markup=REMOVE
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text="Пока оформляем заявку — подписывайтесь на канал. "
             "Там лайфхаки, маршруты и актуальные цены.",
        reply_markup=SUBSCRIBE_KEYBOARD
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY}
    await update.message.reply_text(
        "Начнём заново.\n\nВ каком городе нужен автомобиль?",
        reply_markup=REMOVE
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if not is_admin(update):
        await update.message.reply_text("Нет доступа.")
        return
    OWNER_CHAT_ID = update.effective_chat.id

    now   = datetime.now(MSK)
    total = len(SCHEDULED_POSTS)
    done  = sum(1 for p in SCHEDULED_POSTS if p["when"] <= now)
    left  = total - done

    next_post = next((p for p in SCHEDULED_POSTS if p["when"] > now), None)
    next_info = (
        next_post["when"].strftime("%d.%m %H:%M") + " МСК"
        if next_post else "— все опубликованы"
    )

    await update.message.reply_text(
        f"✅ Chat ID обновлён: {OWNER_CHAT_ID}\n\n"
        f"📅 Посты: опубликовано {done}/{total}, осталось {left}\n"
        f"⏭ Следующий пост: {next_info}\n\n"
        f"Команды:\n"
        f"/post — опубликовать в канал вручную\n"
        f"/status — статус расписания\n"
        f"/cancel — выйти из режима публикации"
    )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    now   = datetime.now(MSK)
    lines = [f"📅 Расписание постов (сейчас {now.strftime('%d.%m %H:%M')} МСК)\n"]
    for p in SCHEDULED_POSTS:
        mark = "✅" if p["when"] <= now else "⏳"
        kind = "📊 Опрос" if p.get("type") == "poll" else "📝 Пост"
        when = p["when"].strftime("%d.%m %H:%M")
        lines.append(f"{mark} {when} — {kind}")
    await update.message.reply_text("\n".join(lines))

async def post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_POST_TEXT}
    await update.message.reply_text(
        "📝 Ручная публикация в канал\n\nОтправь текст поста.\n\n/cancel — отмена",
        reply_markup=REMOVE
    )

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY}
    await update.message.reply_text("Отменено.", reply_markup=REMOVE)

FAQ_TEXT = (
    "❓ Часто задаваемые вопросы\n\n"
    "1. Какой минимальный срок аренды?\n"
    "→ 3 суток\n\n"
    "2. Нужна ли кредитная карта?\n"
    "→ Карта нужна для залога €500–1500. Подойдёт Wise или Revolut. "
    "У нас можно обсудить альтернативы.\n\n"
    "3. Примут ли права из СНГ?\n"
    "→ Да. Рекомендуем иметь МВУ (международное удостоверение).\n\n"
    "4. Есть ли доставка авто?\n"
    "→ Да, во всех наших городах. Доп. стоимость €15–30.\n\n"
    "5. Что включает страховка?\n"
    "→ Базовая CDW с франшизой. Можно доплатить за SCDW (без франшизы).\n\n"
    "6. Можно взять авто в одном городе, сдать в другом?\n"
    "→ Да, one-way аренда. Доплата €50–200 в зависимости от маршрута.\n\n"
    "7. Есть ли детское кресло?\n"
    "→ Да. Для подписчиков канала @weonerent — бесплатно.\n\n"
    "8. Как быстро ответит менеджер?\n"
    "→ В течение 15 минут в рабочее время.\n\n"
    "9. Можно отменить бронь?\n"
    "→ Да, бесплатно за 48 часов до выезда.\n\n"
    "10. В каких городах работаете?\n"
    "→ Барселона, Мадрид, Малага, Аликанте, Валенсия, "
    "Севилья, Тенерифе, Гран-Канария.\n\n"
    "Остался вопрос? Напишите — ответим 👇"
)

async def faq_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(FAQ_TEXT, reply_markup=KEYBOARDS["soft"])

async def price_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Быстрый расчёт цены: /price Барселона SUV 7"""
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Формат: /price Город Класс Дней\n\n"
            "Пример: /price Барселона SUV 7\n\n"
            "Города: Барселона, Малага, Аликанте, Тенерифе, Мадрид, Валенсия, Севилья, Гран-Канария\n"
            "Классы: Эконом, Комфорт, SUV, Минивэн"
        )
        return
    city_raw, car_raw = args[0], args[1]
    try:
        days = int(args[2])
    except ValueError:
        await update.message.reply_text("Количество дней должно быть числом. Пример: /price Малага Эконом 5")
        return

    base, total, disc_pct = calc_price(city_raw, car_raw, days)
    if base is None:
        await update.message.reply_text(
            f"Город «{city_raw}» не найден.\n\n"
            "Доступные: Барселона, Малага, Аликанте, Тенерифе, Мадрид, Валенсия, Севилья, Гран-Канария"
        )
        return

    city_name = city_raw.capitalize()
    car_name  = car_raw.capitalize()
    disc_text = f"\n💚 Скидка {disc_pct}% за длительность уже включена" if disc_pct else ""

    await update.message.reply_text(
        f"💰 Расчёт стоимости\n\n"
        f"📍 {city_name} · {car_name} · {days} дн.\n\n"
        f"Цена от: €{base}/сутки\n"
        f"Итого: от €{total}{disc_text}\n\n"
        f"Точная цена зависит от дат и наличия авто.\n"
        f"Оставьте заявку — менеджер пришлёт финальную стоимость.",
        reply_markup=KEYBOARDS["full"]
    )

# ─── Обработка сообщений ────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ud = get_user(chat_id)

    if update.message.contact:
        phone = update.message.contact.phone_number
        text = f"+{phone}" if not phone.startswith("+") else phone
    else:
        text = update.message.text

    step = ud.get("step")

    # ── Ручной постинг ──
    if step == STEP_POST_TEXT and is_admin(update):
        ud["post_text"] = text
        ud["step"] = STEP_POST_BUTTONS
        await update.message.reply_text(
            "Выбери тип кнопок:",
            reply_markup=BUTTON_TYPE_KEYBOARD
        )
        return

    if step == STEP_POST_BUTTONS and is_admin(update):
        keyboard  = BUTTON_MAP.get(text)
        post_text = ud.get("post_text", "")
        try:
            await context.bot.send_message(
                chat_id=CHANNEL_ID,
                text=post_text,
                reply_markup=keyboard
            )
            await update.message.reply_text(
                f"✅ Опубликовано в {CHANNEL_ID}", reply_markup=REMOVE
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Ошибка: {e}\n\nУбедись что бот — администратор {CHANNEL_ID}",
                reply_markup=REMOVE
            )
        user_data[chat_id] = {"step": STEP_CITY}
        return

    # ── Обычная заявка ──
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    try:
        if step == STEP_CITY:
            ud["city"] = text
            ud["step"] = STEP_DATES
            track_start()
            # Планируем напоминание через 2 часа если не завершит
            context.job_queue.run_once(
                remind_abandoned,
                when=60 * 60 * 2,
                data={"chat_id": chat_id},
                name=f"remind_{chat_id}",
            )
            await update.message.reply_text(
                "Минимальный срок аренды — 3 суток.\n\n"
                "На какой срок хотите взять автомобиль?\n"
                "Укажите даты: с ... по ...",
                reply_markup=REMOVE
            )
        elif step == STEP_DATES:
            ud["dates"] = text
            ud["step"] = STEP_CAR
            await update.message.reply_text(
                "Какой тип автомобиля вам нужен?",
                reply_markup=CAR_KEYBOARD
            )
        elif step == STEP_CAR:
            ud["car"] = text
            ud["step"] = STEP_NAME
            await update.message.reply_text(
                "Ваше имя и фамилия?",
                reply_markup=REMOVE
            )
        elif step == STEP_NAME:
            ud["name"] = text
            ud["step"] = STEP_PHONE
            await update.message.reply_text(
                "Ваш номер телефона с кодом страны.\n\n"
                "Можете нажать кнопку ниже или написать вручную.",
                reply_markup=PHONE_KEYBOARD
            )
        elif step == STEP_PHONE:
            ud["phone"] = text
            ud["step"] = STEP_DONE
            summary = (
                "Заявка принята.\n\n"
                f"Город: {ud.get('city', '—')}\n"
                f"Даты: {ud.get('dates', '—')}\n"
                f"Автомобиль: {ud.get('car', '—')}\n"
                f"Имя: {ud.get('name', '—')}\n"
                f"Телефон: {ud.get('phone', '—')}\n\n"
                "Менеджер свяжется с вами в течение 15 минут."
            )
            await update.message.reply_text(summary, reply_markup=REMOVE)
            track_lead(ud.get("city", "unknown"))
            await send_lead(update, context, chat_id, ud)
            await update.message.reply_text(
                "В нашем канале — лайфхаки про аренду авто, маршруты и актуальные цены.",
                reply_markup=SUBSCRIBE_KEYBOARD
            )
        elif step == STEP_DONE:
            await update.message.reply_text(
                "Ваша заявка уже принята. Менеджер скоро свяжется.\n\n"
                "Для новой заявки — /start",
                reply_markup=REMOVE
            )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "Технический сбой. Напишите нам напрямую: @weonerent",
            reply_markup=REMOVE
        )

# ─── Отправка лида ──────────────────────────────────────────────
# ─── Еженедельный отчёт ─────────────────────────────────────────
weekly_stats = {"leads": 0, "started": 0, "cities": {}}

def track_lead(city: str):
    weekly_stats["leads"] += 1
    c = city.lower().strip()
    weekly_stats["cities"][c] = weekly_stats["cities"].get(c, 0) + 1

def track_start():
    weekly_stats["started"] += 1

async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    stats = weekly_stats.copy()
    weekly_stats["leads"]   = 0
    weekly_stats["started"] = 0
    weekly_stats["cities"]  = {}

    cities_text = "\n".join(
        f"  {c.capitalize()}: {n}" for c, n in
        sorted(stats["cities"].items(), key=lambda x: -x[1])
    ) or "  —"

    conversion = (
        f"{round(stats['leads'] / stats['started'] * 100)}%"
        if stats["started"] > 0 else "—"
    )

    report = (
        f"📊 Еженедельный отчёт WeOneRent\n"
        f"{'─' * 25}\n"
        f"Начали заявку: {stats['started']}\n"
        f"Завершили заявку: {stats['leads']}\n"
        f"Конверсия: {conversion}\n"
        f"{'─' * 25}\n"
        f"По городам:\n{cities_text}\n"
        f"{'─' * 25}\n"
        f"Следующий пост в канале: смотри /status"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=report)
    except Exception as e:
        logger.error(f"Weekly report error: {e}")

async def remind_abandoned(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание если человек остановился на середине заявки"""
    chat_id = context.job.data["chat_id"]
    ud = user_data.get(chat_id, {})
    step = ud.get("step")
    # Отправляем напоминание только если заявка не завершена
    if step not in (None, STEP_CITY, STEP_DONE):
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Вы начали оформлять заявку но не закончили.\n\n"
                     "Продолжить — просто напишите следующий ответ.\n"
                     "Начать заново — /start",
                reply_markup=KEYBOARDS["soft"]
            )
        except Exception:
            pass

async def send_lead(update, context, client_chat_id, ud):
    user     = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"
    lead = (
        f"НОВАЯ ЗАЯВКА — WeOneRent\n"
        f"{'─' * 25}\n"
        f"Клиент: {user.full_name or '—'} ({username})\n"
        f"{'─' * 25}\n"
        f"Город: {ud.get('city', '—')}\n"
        f"Даты: {ud.get('dates', '—')}\n"
        f"Авто: {ud.get('car', '—')}\n"
        f"Имя: {ud.get('name', '—')}\n"
        f"Телефон: {ud.get('phone', '—')}\n"
        f"{'─' * 25}\n"
        f"Написать: tg://user?id={client_chat_id}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead)
        logger.info(f"Заявка отправлена от {username}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

# ─── Запуск ─────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Планируем все посты при старте
    schedule_all_posts(app)

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("reset",   reset))
    app.add_handler(CommandHandler("admin",   admin_cmd))
    app.add_handler(CommandHandler("post",    post_cmd))
    app.add_handler(CommandHandler("status",  status_cmd))
    app.add_handler(CommandHandler("cancel",  cancel_cmd))
    app.add_handler(CommandHandler("price",   price_cmd))
    app.add_handler(CommandHandler("faq",     faq_cmd))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен. Автопостинг активен.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
