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
    CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes
)
from telegram.constants import ChatMemberStatus
from datetime import time as dtime
from itertools import cycle
from content import SCHEDULED_POSTS, LIFEHACKS

BOT_TOKEN      = os.environ["BOT_TOKEN"]
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY", "")   # резерв для AI-функций
OWNER_CHAT_ID  = int(os.environ.get("OWNER_CHAT_ID", "448609289"))
CHANNEL_ID        = os.environ.get("CHANNEL_ID", "@weonerent")
DISCUSSION_GROUP  = os.environ.get("DISCUSSION_GROUP", "")   # ID группы обсуждения
ADMIN_USERNAME    = os.environ.get("ADMIN_USERNAME", "")   # Telegram username без @

def get_manager_url() -> str:
    return f"https://t.me/{ADMIN_USERNAME}" if ADMIN_USERNAME else "https://t.me/weonerent"

TZ = pytz.timezone("Europe/Madrid")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def is_working_hours() -> bool:
    """Рабочее время менеджера: 9:00–20:00 по Мадриду, ежедневно"""
    hour = datetime.now(TZ).hour
    return 9 <= hour < 20

# ─── Шаги заявки ────────────────────────────────────────────────
STEP_CITY  = 0
STEP_DATES = 1
STEP_CAR   = 2
STEP_NAME  = 3
STEP_PHONE        = 4
STEP_DONE         = 5
STEP_DATES_DETAIL = "dates_detail"   # ввод точных дат текстом

# Шаги ручной публикации
STEP_POST_TEXT    = "post_text"
STEP_POST_BUTTONS = "post_buttons"

# ─── Клавиатуры для заявки ──────────────────────────────────────
DATES_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📅 3–4 дня",    "📅 5–7 дней"],
        ["📅 1–2 недели", "📅 Больше 2 недель"],
        ["✏️ Указать точные даты"],
    ],
    resize_keyboard=True, one_time_keyboard=True
)

# Маппинг кнопок → примерное кол-во дней для расчёта цены
DATES_BUTTON_MAP = {
    "📅 3–4 дня":         3,
    "📅 5–7 дней":        5,
    "📅 1–2 недели":     10,
    "📅 Больше 2 недель": 16,
}

CITY_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🏙 Барселона",  "🏛 Мадрид"],
        ["☀️ Малага",     "🌴 Аликанте"],
        ["🏖 Валенсия",   "🎭 Севилья"],
        ["🌊 Торревьеха", "🏄 Гандия"],
        ["⛵ Дения",      "🌴 Марбелья"],
    ],
    resize_keyboard=True, one_time_keyboard=True
)

# Маппинг кнопок → ключи для PRICES
CITY_BUTTON_MAP = {
    "🏙 Барселона":  "барселона",
    "🏛 Мадрид":     "мадрид",
    "☀️ Малага":     "малага",
    "🌴 Аликанте":   "аликанте",
    "🏖 Валенсия":   "валенсия",
    "🎭 Севилья":    "севилья",
    "🌊 Торревьеха": "торревьеха",
    "🏄 Гандия":     "гандия",
    "⛵ Дения":      "дения",
    "🌴 Марбелья":   "марбелья",
}

CAR_KEYBOARD = ReplyKeyboardMarkup(
    [["Эконом", "Комфорт", "SUV"]],
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

# ─── Главное меню (строится динамически, чтобы URL менеджера всегда актуален) ──
def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗 Оставить заявку",    callback_data="menu_apply")],
        [InlineKeyboardButton("💰 Узнать стоимость",   callback_data="menu_price"),
         InlineKeyboardButton("ℹ️ О сервисе",           callback_data="menu_faq")],
        [InlineKeyboardButton("📞 Написать менеджеру", url=get_manager_url())],
    ])

MAIN_MENU = build_main_menu()   # статическая копия — обновляется при старте

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
    "барселона":   {"эконом": 32, "комфорт": 45, "suv": 58},
    "малага":      {"эконом": 25, "комфорт": 38, "suv": 52},
    "аликанте":    {"эконом": 22, "комфорт": 35, "suv": 48},
    "мадрид":      {"эконом": 35, "комфорт": 50, "suv": 65},
    "валенсия":    {"эконом": 24, "комфорт": 37, "suv": 50},
    "севилья":     {"эконом": 26, "комфорт": 39, "suv": 53},
    "торревьеха":  {"эконом": 20, "комфорт": 32, "suv": 45},
    "гандия":      {"эконом": 22, "комфорт": 35, "suv": 48},
    "дения":       {"эконом": 22, "комфорт": 35, "suv": 48},
    "марбелья":    {"эконом": 28, "комфорт": 42, "suv": 55},
}

CITY_ALIASES = {
    "bcn": "барселона", "barcelona": "барселона",
    "malaga": "малага", "málaga": "малага",
    "alicante": "аликанте", "alicant": "аликанте",
    "madrid": "мадрид",
    "valencia": "валенсия",
    "seville": "севилья", "sevilla": "севилья",
    "torrevieja": "торревьеха", "торрева": "торревьеха",
    "gandia": "гандия", "gandía": "гандия",
    "denia": "дения", "dénia": "дения",
    "marbella": "марбелья",
}

CAR_ALIASES = {
    "econom": "эконом", "economy": "эконом", "small": "эконом",
    "comfort": "комфорт", "medium": "комфорт",
    "suv": "suv", "джип": "suv", "внедорожник": "suv",
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
    base  = PRICES[city][car]
    total = base * days
    return base, total

# ─── Данные пользователей и ротация лайфхаков ───────────────────
user_data = {}
lifehack_cycle = cycle(LIFEHACKS)   # бесконечная ротация

# ─── Парсер дат → количество дней ──────────────────────────────
import re as _re
from datetime import date as _date

_MONTHS = {
    'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4,
    'май': 5, 'мая': 5, 'июн': 6, 'июл': 7,
    'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12,
}

def parse_days(text: str) -> int:
    """
    Вычисляет количество дней из произвольного текста.
    Поддерживает форматы:
      - "7 дней / 7 дня / 7 суток"
      - "неделя / две недели / месяц"
      - "с 10 по 17 июля"            → 7
      - "10.06 – 11.07"              → 31
      - "10/06 - 11/07"              → 31
      - "10 06 11 07"                → 31
      - "10 июня – 11 июля"          → 31
      - "с 10.06.2026 по 11.07.2026" → 31
    """
    t = text.lower().strip()

    # Слова-ключи
    if _re.search(r'полмесяц|пол\s*месяц', t):
        return 15
    if _re.search(r'дв[ае]\s*недел|2\s*недел', t):
        return 14
    if _re.search(r'\bнедел', t):
        return 7
    if _re.search(r'\bмесяц', t):
        return 30

    # "N дней / дня / суток / ночей"
    m = _re.search(r'(\d+)\s*(?:дней|дня|день|суток|ноч\w+)', t)
    if m:
        return int(m.group(1))

    # Пробуем найти две даты вида DD.MM или DD/MM или DD-MM или DD MM
    # Разделители: . / – -  (пробел не используем — съедает соседнюю дату)
    dp = r'(\d{1,2})[./\-](\d{1,2})(?:[./\-]\d{4})?'
    pairs = _re.findall(dp, t)
    if len(pairs) >= 2:
        d1, mo1 = int(pairs[0][0]), int(pairs[0][1])
        d2, mo2 = int(pairs[1][0]), int(pairs[1][1])
        if (1 <= d1 <= 31 and 1 <= mo1 <= 12 and
                1 <= d2 <= 31 and 1 <= mo2 <= 12):
            try:
                yr = datetime.now(TZ).year
                dt1 = _date(yr, mo1, d1)
                dt2 = _date(yr, mo2, d2)
                if dt2 <= dt1:          # переход через новый год
                    dt2 = _date(yr + 1, mo2, d2)
                diff = (dt2 - dt1).days
                if 1 <= diff <= 365:
                    return diff
            except ValueError:
                pass

    # Названия месяцев: "10 июня – 11 июля"
    mon_pat = r'(\d{1,2})\s*(' + '|'.join(_MONTHS) + r')\w*'
    named = _re.findall(mon_pat, t)
    if len(named) >= 2:
        d1, mk1 = int(named[0][0]), named[0][1]
        d2, mk2 = int(named[1][0]), named[1][1]
        mo1, mo2 = _MONTHS.get(mk1), _MONTHS.get(mk2)
        if mo1 and mo2:
            try:
                yr = datetime.now(TZ).year
                dt1 = _date(yr, mo1, d1)
                dt2 = _date(yr, mo2, d2)
                if dt2 <= dt1:
                    dt2 = _date(yr + 1, mo2, d2)
                diff = (dt2 - dt1).days
                if 1 <= diff <= 365:
                    return diff
            except ValueError:
                pass

    # Четыре числа подряд = DD MM DD MM (голосовой/нестандартный ввод)
    nums = _re.findall(r'\d+', t)
    if len(nums) == 4:
        d1, mo1, d2, mo2 = (int(x) for x in nums)
        if 1<=d1<=31 and 1<=mo1<=12 and 1<=d2<=31 and 1<=mo2<=12:
            try:
                yr = datetime.now(TZ).year
                dt1 = _date(yr, mo1, d1)
                dt2 = _date(yr, mo2, d2)
                if dt2 <= dt1: dt2 = _date(yr+1, mo2, d2)
                diff = (dt2 - dt1).days
                if 1 <= diff <= 365: return diff
            except ValueError: pass

    # Два числа — диапазон внутри одного месяца: "с 10 по 17"
    if len(nums) == 2:
        a, b = int(nums[0]), int(nums[1])
        if 1 <= a <= 31 and 1 <= b <= 31 and b > a:
            return b - a

    return 0

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY}
    return user_data[chat_id]

def is_admin(update: Update) -> bool:
    user = update.effective_user
    # Проверка по username (если задан) или по OWNER_CHAT_ID
    if ADMIN_USERNAME and user.username == ADMIN_USERNAME:
        return True
    if update.effective_chat.id == OWNER_CHAT_ID:
        return True
    return False

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
        logger.info(f"Auto-published post at {datetime.now(TZ).strftime('%d.%m %H:%M')}")
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
        logger.info(f"Lifehack published at {datetime.now(TZ).strftime('%d.%m %H:%M')}")
    except Exception as e:
        logger.error(f"Lifehack publish error: {e}")

# ─── Планировщик: запускается при старте бота ───────────────────
def schedule_all_posts(app):
    now = datetime.now(TZ)

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

    # Еженедельный отчёт — каждый понедельник в 08:00 по Мадриду
    app.job_queue.run_repeating(
        send_weekly_report,
        interval=60 * 60 * 24 * 7,
        first=dtime(8, 0, tzinfo=TZ),
        name="weekly_report",
    )

    # Вечная ротация лайфхаков — каждый пн 09:30 и чт 19:30 по Мадриду
    # Запускается начиная с 23 июня (после окончания основного расписания)
    rotation_start = TZ.localize(datetime(2026, 6, 23, 0, 0))
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
def get_welcome_text() -> str:
    if is_working_hours():
        timing = "Менеджер ответит в течение 5 минут."
    else:
        timing = "Сейчас нерабочее время (9:00–20:00 по Мадриду) — менеджер ответит по возможности."
    return (
        "Привет! 👋 Помогу подобрать и забронировать авто в Испании.\n\n"
        f"{timing}\n\n"
        "Чем могу помочь?"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": None}   # ждём выбора в меню
    await update.message.reply_text(
        get_welcome_text(),
        reply_markup=build_main_menu()
    )

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вспомогательная функция — показать главное меню (из любого места)"""
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": None}
    await context.bot.send_message(
        chat_id=chat_id,
        text="Чем ещё могу помочь?",
        reply_markup=build_main_menu()
    )

# ─── Обработчики кнопок главного меню ───────────────────────────
async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data == "menu_apply":
        user_data[chat_id] = {"step": STEP_CITY}
        await query.message.reply_text(
            "Отлично, оформляем заявку! 🚗\n\n"
            "Шаг 1 из 4 — выберите город:",
            reply_markup=CITY_KEYBOARD
        )

    elif data == "menu_price":
        user_data[chat_id] = {"step": None}
        await query.message.reply_text(
            "💰 Ориентировочные цены на аренду авто:\n\n"
            "🏙 Барселона — от €32/сутки\n"
            "🏛 Мадрид — от €35/сутки\n"
            "☀️ Малага — от €25/сутки\n"
            "🌴 Аликанте — от €22/сутки\n"
            "🏖 Валенсия — от €24/сутки\n"
            "🎭 Севилья — от €26/сутки\n"
            "🌊 Торревьеха — от €20/сутки\n"
            "🏄 Гандия — от €22/сутки\n"
            "⛵ Дения — от €22/сутки\n"
            "🌴 Марбелья — от €28/сутки\n\n"
            "Цены за сутки, от 3 дней.\n"
            "\n"
            "🛡 В аренду включена базовая страховка (гражданская ответственность).\n\n"
            "Дополнительные пакеты:\n"
            "• Расширенная страховка — €20/сутки: колёса, стёкла + 1 бесплатный вызов эвакуатора\n"
            "• Максимальное покрытие — €30/сутки: колёса, стёкла, эвакуатор, ассистанс + "
            "покрытие при ДТП (франшиза €500)\n\n"
            "Хотите точный расчёт под ваши даты?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚗 Оформить заявку", callback_data="menu_apply")],
                [InlineKeyboardButton("📞 Написать менеджеру", url=get_manager_url())],
            ])
        )

    elif data == "menu_faq":
        user_data[chat_id] = {"step": None}
        await query.message.reply_text(
            FAQ_TEXT,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚗 Оставить заявку", callback_data="menu_apply")],
                [InlineKeyboardButton("📞 Спросить менеджера", url=get_manager_url())],
            ])
        )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": None}
    await update.message.reply_text(
        "Хорошо, начнём сначала! 😊\n\nЧем могу помочь?",
        reply_markup=build_main_menu()
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if not is_admin(update):
        await update.message.reply_text("Нет доступа.")
        return
    OWNER_CHAT_ID = update.effective_chat.id

    now   = datetime.now(TZ)
    total = len(SCHEDULED_POSTS)
    done  = sum(1 for p in SCHEDULED_POSTS if p["when"] <= now)
    left  = total - done

    next_post = next((p for p in SCHEDULED_POSTS if p["when"] > now), None)
    next_info = (
        next_post["when"].strftime("%d.%m %H:%M") + " (Мадрид)"
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
    now   = datetime.now(TZ)
    lines = [f"📅 Расписание постов (сейчас {now.strftime('%d.%m %H:%M')} Мадрид)\n"]
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
    "ℹ️ Всё о сервисе WeOneRent\n\n"

    "1. Какой минимальный срок аренды?\n"
    "→ 3 суток\n\n"

    "2. Какие карты принимаете?\n"
    "→ Принимаем все карты — кредитные и дебетовые, включая Wise и Revolut. "
    "Альтернативные варианты оплаты тоже можем обсудить индивидуально.\n\n"

    "3. Примут ли права из СНГ?\n"
    "→ Да. Рекомендуем иметь МВУ (международное водительское удостоверение).\n\n"

    "4. Есть ли доставка авто?\n"
    "→ Доставка по Аликанте и в аэропорт в рабочее время — бесплатно.\n"
    "Доставка по Аликанте и в аэропорт вне рабочего времени — €50 за одну точку.\n"
    "Доставка в другие города и регионы Испании — обсуждается индивидуально.\n\n"

    "5. Что включает страховка?\n"
    "→ В аренду включена базовая страховка (гражданская ответственность "
    "перед третьими лицами).\n\n"
    "Дополнительные пакеты:\n"
    "• Расширенная страховка — €20/сутки: колёса, стёкла + 1 бесплатный вызов эвакуатора\n"
    "• Максимальное покрытие — €30/сутки: колёса, стёкла, эвакуатор, ассистанс + "
    "покрытие при ДТП (франшиза €500)\n\n"

    "6. Можно взять авто в одном городе, сдать в другом?\n"
    "→ Да. Условия обсуждаются индивидуально.\n\n"

    "7. Есть ли детское кресло?\n"
    "→ Да. Для подписчиков канала @weonerent — бесплатно.\n\n"

    "8. Как быстро ответит менеджер?\n"
    "→ В течение 5 минут в рабочее время (9:00–20:00 по Мадриду).\n\n"

    "9. Можно отменить бронь?\n"
    "→ Да, бесплатно за 48 часов до выезда.\n\n"

    "10. В каких городах работаете?\n"
    "→ Барселона, Мадрид, Малага, Аликанте, Валенсия, Севилья, "
    "Торревьеха, Гандия, Дения, Марбелья.\n\n"

    "11. Способы оплаты:\n"
    "💳 Банковская карта — Visa, Mastercard (в т.ч. Wise и Revolut)\n"
    "🏦 Банковский перевод — SEPA и SWIFT\n"
    "💵 Наличные — EUR, USD (при получении автомобиля)\n"
    "₿ Криптовалюта — BTC, ETH, USDT (TRC20/ERC20), USDC\n"
    "📱 Wise / Revolut — прямой перевод без комиссий\n\n"

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
            "Города: Барселона, Мадрид, Малага, Аликанте, Валенсия, Севилья, Торревьеха, Гандия, Дения, Марбелья\n"
            "Классы: Эконом, Комфорт, SUV, Минивэн"
        )
        return
    city_raw, car_raw = args[0], args[1]
    try:
        days = int(args[2])
    except ValueError:
        await update.message.reply_text("Количество дней должно быть числом. Пример: /price Малага Эконом 5")
        return

    result = calc_price(city_raw, car_raw, days)
    if result[0] is None:
        await update.message.reply_text(
            f"Город «{city_raw}» не найден.\n\n"
            "Доступные: Барселона, Мадрид, Малага, Аликанте, Валенсия, Севилья, Торревьеха, Гандия, Дения, Марбелья"
        )
        return

    base, total = result
    city_name = city_raw.capitalize()
    car_name  = car_raw.capitalize()

    await update.message.reply_text(
        f"💰 Расчёт стоимости\n\n"
        f"📍 {city_name} · {car_name} · {days} дн.\n\n"
        f"Цена от: €{base}/сутки\n"
        f"Итого: от €{total}\n\n"
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

    # ── Если пользователь ещё не выбрал действие — показываем меню ──
    if step is None:
        await update.message.reply_text(
            "Выберите нужный пункт 👇",
            reply_markup=MAIN_MENU
        )
        return

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
            # Нормализуем: кнопка «🏙 Барселона» → «Барселона»
            city_clean = CITY_BUTTON_MAP.get(text, text)
            city_display = city_clean.capitalize()

            # Проверяем — город из нашего списка?
            city_key = CITY_ALIASES.get(city_clean.lower(), city_clean.lower())
            if city_key not in PRICES:
                await update.message.reply_text(
                    "Мы пока не работаем в этом городе. "
                    "Выберите город из списка 👇",
                    reply_markup=CITY_KEYBOARD
                )
                return

            ud["city"]     = city_display
            ud["city_key"] = city_key
            ud["step"]     = STEP_DATES
            track_start()

            # Напоминание через 2 часа если не завершит
            context.job_queue.run_once(
                remind_abandoned,
                when=60 * 60 * 2,
                data={"chat_id": chat_id},
                name=f"remind_{chat_id}",
            )

            await update.message.reply_text(
                f"✅ Город: {city_display}\n\n"
                "Шаг 2 из 4 — на какой срок нужен автомобиль?",
                reply_markup=DATES_KEYBOARD
            )
        elif step == STEP_DATES:
            if text == "✏️ Указать точные даты":
                # Пользователь хочет ввести даты вручную
                ud["step"] = STEP_DATES_DETAIL
                await update.message.reply_text(
                    "Введите даты или срок аренды.\n\n"
                    "Например: с 10 по 17 июля  или  7 дней",
                    reply_markup=REMOVE
                )
                return

            # Кнопка с диапазоном дней
            days_estimate = DATES_BUTTON_MAP.get(text)
            if days_estimate:
                ud["dates"]         = text.replace("📅 ", "")
                ud["days_estimate"] = days_estimate
            else:
                # Свободный ввод (на случай если обошли клавиатуру)
                ud["dates"] = text
                ud["days_estimate"] = 0

            ud["step"] = STEP_CAR
            await update.message.reply_text(
                f"✅ Срок: {ud['dates']}\n\n"
                "Шаг 3 из 4 — выберите тип автомобиля:",
                reply_markup=CAR_KEYBOARD
            )

        elif step == STEP_DATES_DETAIL:
            # Ввод точных дат текстом — используем умный парсер
            ud["dates"]        = text
            ud["days_estimate"] = parse_days(text)
            ud["step"]         = STEP_CAR

            days = ud["days_estimate"]
            days_text = f"{days} дней" if days else "уточним с менеджером"

            await update.message.reply_text(
                f"✅ Даты: {text} ({days_text})\n\n"
                "Шаг 3 из 4 — выберите тип автомобиля:",
                reply_markup=CAR_KEYBOARD
            )
        elif step == STEP_CAR:
            ud["car"] = text
            ud["step"] = STEP_NAME
            # Считаем примерную стоимость из сохранённого days_estimate
            total_hint = ""
            try:
                days     = ud.get("days_estimate", 0)
                city_key = ud.get("city_key", "")
                car_key  = CAR_ALIASES.get(text.lower().strip(), text.lower().strip())
                if 3 <= days <= 60 and city_key:
                    base, total = calc_price(city_key, car_key, days)
                    if total:
                        total_hint = f"\n\n💰 Примерная стоимость: от €{total} за {days} дней"
            except Exception:
                pass

            await update.message.reply_text(
                f"✅ Автомобиль: {text}{total_hint}\n\n"
                "Шаг 4 из 4 — как вас зовут?\n\nВведите имя и фамилию:",
                reply_markup=REMOVE
            )
        elif step == STEP_NAME:
            ud["name"] = text
            ud["step"] = STEP_PHONE
            manager_handle = f"@{ADMIN_USERNAME}" if ADMIN_USERNAME else "@weonerent"
            await update.message.reply_text(
                "Отлично! Последний шаг — номер телефона.\n\n"
                "Нажмите кнопку ниже или введите вручную с кодом страны.\n"
                "Например: +34 612 345 678\n\n"
                f"💬 Или напишите менеджеру напрямую {manager_handle} — он поможет без формы.",
                reply_markup=PHONE_KEYBOARD
            )
        elif step == STEP_PHONE:
            ud["phone"] = text
            ud["step"]  = STEP_DONE

            city    = ud.get("city", "—")
            dates   = ud.get("dates", "—")
            car     = ud.get("car", "—")
            name    = ud.get("name", "—")
            phone   = ud.get("phone", "—")

            # Оценка стоимости для итогового сообщения
            price_line = ""
            days = ud.get("days_estimate", 0)
            city_key = ud.get("city_key", "")
            car_key  = CAR_ALIASES.get(car.lower().strip(), car.lower().strip())
            if 3 <= days <= 60 and city_key:
                try:
                    _, total = calc_price(city_key, car_key, days)
                    if total:
                        price_line = f"\n💰 Ориентировочно: от €{total}"
                except Exception:
                    pass

            if is_working_hours():
                response_note = "Менеджер свяжется с вами в течение 5 минут."
            else:
                response_note = "Менеджер ответит по возможности — сейчас нерабочее время (9:00–20:00 по Мадриду)."

            summary = (
                "🎉 Заявка оформлена!\n\n"
                "Вот что мы передали менеджеру:\n"
                "─────────────────────\n"
                f"📍 Город: {city}\n"
                f"📅 Срок: {dates}\n"
                f"🚗 Автомобиль: {car}\n"
                f"👤 Имя: {name}\n"
                f"📞 Телефон: {phone}"
                f"{price_line}\n"
                "─────────────────────\n\n"
                f"{response_note} "
                "Если что-то изменится — просто напишите ему напрямую."
            )
            await update.message.reply_text(summary, reply_markup=REMOVE)
            track_lead(city)
            await send_lead(update, context, chat_id, ud)

            # Предлагаем канал с пояснением зачем
            await update.message.reply_text(
                "Пока ждёте ответа — подпишитесь на наш канал 👇\n\n"
                "Там маршруты по Испании, лайфхаки об аренде авто "
                "и актуальные цены каждую неделю.",
                reply_markup=SUBSCRIBE_KEYBOARD
            )

        elif step == STEP_DONE:
            # После завершения заявки — возвращаем в меню
            user_data[chat_id] = {"step": None}
            await update.message.reply_text(
                "Ваша заявка уже принята 👍\n\nЧем ещё могу помочь?",
                reply_markup=build_main_menu()
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

async def welcome_new_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Приветствие новых участников группы обсуждения"""
    result = update.chat_member
    # Только когда статус меняется на "участник" (не бот, не повторный вход)
    old_status = result.old_chat_member.status
    new_status = result.new_chat_member.status
    user       = result.new_chat_member.user

    if user.is_bot:
        return
    if old_status in (ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
        return
    if new_status != ChatMemberStatus.MEMBER:
        return

    name = user.first_name or "Привет"
    await context.bot.send_message(
        chat_id=result.chat.id,
        text=(
            f"👋 {name}, добро пожаловать в чат WeOneRent!\n\n"
            "Здесь можно задавать вопросы про аренду авто в Испании, "
            "обсуждать маршруты и делиться опытом.\n\n"
            "Если нужна аренда — оформите заявку прямо сейчас, "
            "менеджер ответит в течение 5 минут в рабочее время 👇"
        ),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✈️ Оставить заявку", url="https://t.me/weonerent_ai_bot")],
            [InlineKeyboardButton("📢 Канал @weonerent", url="https://t.me/weonerent")],
        ])
    )

async def remind_abandoned(context: ContextTypes.DEFAULT_TYPE):
    """Напоминание если человек остановился на середине заявки"""
    chat_id = context.job.data["chat_id"]
    ud = user_data.get(chat_id, {})
    step = ud.get("step")
    # Отправляем напоминание только если заявка не завершена
    if step not in (None, STEP_CITY, STEP_DONE):
        city = ud.get("city", "")
        city_part = f" в {city}" if city else ""
        if is_working_hours():
            timing_note = "⏱ Менеджер сейчас онлайн — ответит в течение 5 минут."
        else:
            timing_note = "🕘 Менеджер ответит в рабочее время (9:00–20:00 по Мадриду)."
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"👋 Вы начали оформлять заявку на аренду авто{city_part}, "
                    f"но не завершили её.\n\n"
                    f"{timing_note}\n\n"
                    "Продолжить — просто напишите следующий ответ.\n"
                    "Начать заново — /start"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚗 Продолжить заявку", callback_data="menu_apply")],
                    [InlineKeyboardButton("📞 Написать менеджеру", url=get_manager_url())],
                ])
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
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_"))
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен. Автопостинг активен.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
