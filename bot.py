import os
import json
import logging
import requests as _requests
import pytz
import threading as _threading_http
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
from telegram import (
    Update,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ChatMemberHandler, filters, ContextTypes,
    PicklePersistence
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

# ─── Facebook Conversions API ────────────────────────────────────
FB_PIXEL_ID      = os.environ.get("FB_PIXEL_ID", "787631537198771")
FB_ACCESS_TOKEN  = os.environ.get("FB_ACCESS_TOKEN", "")   # из Events Manager → Настройки

MANAGER_FALLBACK    = "weonerent"   # дефолт если ADMIN_USERNAME не задан в env
GOOGLE_SCRIPT_URL   = os.environ.get("GOOGLE_SCRIPT_URL", "")  # URL Google Apps Script вебхука

def get_manager_url() -> str:
    username = ADMIN_USERNAME or MANAGER_FALLBACK
    return f"https://t.me/{username}"

def get_manager_handle() -> str:
    username = ADMIN_USERNAME or MANAGER_FALLBACK
    return f"@{username}"

# ─── UTM-метки ───────────────────────────────────────────────────
SITE_URL      = "https://weonerent.es"
UTM_BOT       = "?utm_source=telegram&utm_medium=bot&utm_campaign=weonerent_bot"
UTM_FINAL     = "?utm_source=telegram&utm_medium=bot&utm_campaign=weonerent_bot&utm_content=final"
UTM_FAQ       = "?utm_source=telegram&utm_medium=bot&utm_campaign=weonerent_bot&utm_content=faq"
UTM_PRICE     = "?utm_source=telegram&utm_medium=bot&utm_campaign=weonerent_bot&utm_content=price"
UTM_FOLLOWUP  = "?utm_source=telegram&utm_medium=bot&utm_campaign=weonerent_bot&utm_content=followup"

def site_url(utm: str = UTM_BOT) -> str:
    return SITE_URL + utm

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
    [
        [KeyboardButton("📱 Отправить мой номер", request_contact=True)],
        [KeyboardButton("💬 Напишу сам — без звонка")],
    ],
    resize_keyboard=True, one_time_keyboard=True
)
TELEGRAM_CONTACT_MARKER = "💬 Напишу сам — без звонка"
REMOVE = ReplyKeyboardRemove()

# ─── Inline кнопки ──────────────────────────────────────────────
SUBSCRIBE_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 Наш канал", url="https://t.me/weonerent"),
     InlineKeyboardButton("🌐 Сайт", url=site_url())]
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
        [InlineKeyboardButton("🌐 Наш сайт", url=site_url())]
    ]),
    "soft": InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Узнать стоимость", url="https://t.me/weonerent_ai_bot"),
         InlineKeyboardButton("🌐 Сайт", url=site_url())]
    ]),
    "promo": InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Забронировать сейчас", url="https://t.me/weonerent_ai_bot")],
        [InlineKeyboardButton("📋 Подробнее на сайте", url=site_url(UTM_FAQ))]
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

# ─── Google Sheets (через Apps Script webhook) ───────────────────
# ─── Очередь неотправленных лидов (retry) ───────────────────────
import threading as _threading
_failed_leads_queue: list = []
_queue_lock = _threading.Lock()

def _post_to_sheets_with_retry(payload: dict, max_attempts: int = 3) -> bool:
    """POST в Apps Script с экспоненциальным backoff. True = успех."""
    import time as _time
    for attempt in range(max_attempts):
        try:
            resp = _requests.post(GOOGLE_SCRIPT_URL, json=payload, timeout=15)
            if resp.status_code == 200:
                return True
            logger.warning(f"Sheets attempt {attempt+1}: HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"Sheets attempt {attempt+1}: {e}")
        if attempt < max_attempts - 1:
            _time.sleep(2 ** attempt)   # 1s → 2s перед следующей попыткой
    return False

def _sha256(value: str) -> str:
    """SHA-256 хеш строки (нижний регистр, без пробелов) — требование FB."""
    import hashlib
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()

def send_fb_lead_event(phone: str = "", name: str = "", city: str = "", event_id: str = ""):
    """
    Отправляет серверное событие Lead в Facebook Conversions API.
    Документация: https://developers.facebook.com/docs/marketing-api/conversions-api
    """
    if not FB_ACCESS_TOKEN:
        logger.debug("FB_ACCESS_TOKEN не задан — пропускаем Conversions API")
        return

    import time as _time

    # Нормализация телефона: только цифры, добавить 7 если 10 цифр (RU)
    phone_clean = "".join(c for c in phone if c.isdigit())
    if len(phone_clean) == 10:
        phone_clean = "7" + phone_clean  # российский формат

    # Собираем user_data (только заполненные поля)
    user_data_fb: dict = {}
    if phone_clean:
        user_data_fb["ph"] = [_sha256(phone_clean)]
    if name:
        parts = name.strip().split(maxsplit=1)
        user_data_fb["fn"] = [_sha256(parts[0])]
        if len(parts) > 1:
            user_data_fb["ln"] = [_sha256(parts[1])]

    payload = {
        "data": [{
            "event_name":       "Lead",
            "event_time":       int(_time.time()),
            "event_id":         event_id or f"bot_lead_{int(_time.time())}",
            "event_source_url": "https://t.me/weonerent_ai_bot",
            "action_source":    "other",            # бот ≠ сайт
            "user_data":        user_data_fb,
            "custom_data": {
                "city":     city,
                "currency": "EUR",
                "content_name": "Telegram Bot Lead",
            },
        }],
        "access_token": FB_ACCESS_TOKEN,
    }

    try:
        url = f"https://graph.facebook.com/v21.0/{FB_PIXEL_ID}/events"
        resp = _requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            events_received = resp.json().get("events_received", 0)
            logger.info(f"FB Conversions API: Lead отправлен, events_received={events_received}")
        else:
            logger.warning(f"FB Conversions API error {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"FB Conversions API exception: {e}")

def append_lead_to_sheets(ud: dict, user, chat_id: int, price_est: str = ""):
    """Отправляет лид в Google Sheets. При неудаче — кладёт в очередь retry."""
    if not GOOGLE_SCRIPT_URL:
        return
    username = f"@{user.username}" if user.username else f"tg://user?id={chat_id}"
    payload = {
        "date":     datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),
        "name":     ud.get("name", "—"),
        "phone":    ud.get("phone", "—"),
        "city":     ud.get("city", "—"),
        "dates":    ud.get("dates", "—"),
        "car":      ud.get("car", "—"),
        "price":    price_est,
        "telegram": username,
        "chat_id":  str(chat_id),
        "status":   "Новый",
        "source":   ud.get("source", "organic"),
    }
    if _post_to_sheets_with_retry(payload):
        logger.info(f"Lead saved to Sheets: {ud.get('name')} / {ud.get('city')}")
    else:
        logger.error(f"Sheets: все попытки исчерпаны, лид в очереди retry")
        with _queue_lock:
            _failed_leads_queue.append(payload)

    # ─── Facebook Conversions API (серверный пиксель) ───────────
    import threading as _fb_thread
    _fb_thread.Thread(
        target=send_fb_lead_event,
        kwargs={
            "phone":    ud.get("phone", ""),
            "name":     ud.get("name", ""),
            "city":     ud.get("city", ""),
            "event_id": f"lead_{chat_id}_{int(__import__('time').time())}",
        },
        daemon=True,
    ).start()

async def flush_failed_leads(context: ContextTypes.DEFAULT_TYPE):
    """Job: каждые 10 минут повторяет попытку отправить неудавшиеся лиды."""
    with _queue_lock:
        queue_copy = list(_failed_leads_queue)
    if not queue_copy:
        return
    recovered = []
    for payload in queue_copy:
        if _post_to_sheets_with_retry(payload, max_attempts=2):
            recovered.append(payload)
            logger.info(f"Recovered lead: {payload.get('name')}")
    if recovered:
        with _queue_lock:
            for p in recovered:
                if p in _failed_leads_queue:
                    _failed_leads_queue.remove(p)
        try:
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"✅ Восстановлено {len(recovered)} лидов в Google Sheets"
            )
        except Exception:
            pass

def get_sheets_stats_this_week() -> dict:
    """Запрашивает статистику за текущую неделю из Apps Script."""
    result = {"total": 0, "cities": {}}
    if not GOOGLE_SCRIPT_URL:
        return result
    try:
        now = datetime.now(TZ)
        week_start = (now.replace(hour=0, minute=0, second=0, microsecond=0)
                      - timedelta(days=now.weekday()))
        resp = _requests.get(
            GOOGLE_SCRIPT_URL,
            params={"action": "stats", "since": week_start.strftime("%d.%m.%Y")},
            timeout=10
        )
        if resp.status_code == 200:
            data = resp.json()
            result["total"]  = data.get("total", 0)
            result["cities"] = data.get("cities", {})
    except Exception as e:
        logger.error(f"Sheets stats error: {e}")
    return result

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
        return None, None
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
    # app.user_data — defaultdict(dict), автоматически создаёт запись
    if not user_data.get(chat_id):
        user_data[chat_id] = {"step": STEP_CITY}
    return user_data[chat_id]

def is_valid_phone(text: str) -> bool:
    """Проверяет что номер телефона содержит от 7 до 15 цифр."""
    import re as _re2
    digits = _re2.sub(r'\D', '', text)
    return 7 <= len(digits) <= 15

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
    if not app.job_queue.get_jobs_by_name("weekly_report"):
        app.job_queue.run_repeating(
            send_weekly_report,
            interval=60 * 60 * 24 * 7,
            first=dtime(8, 0, tzinfo=TZ),
            name="weekly_report",
        )

    # Вечная ротация лайфхаков — каждый пн 09:30 и чт 19:30 по Мадриду
    # Запускается начиная с 23 июня (после окончания основного расписания)
    rotation_start = TZ.localize(datetime(2026, 6, 23, 0, 0))

    if not app.job_queue.get_jobs_by_name("lifehack_rotation"):
        app.job_queue.run_repeating(
            publish_lifehack,
            interval=60 * 60 * 24 * 7 / 2,   # дважды в неделю (каждые 3.5 дня)
            first=rotation_start if now < rotation_start else now,
            name="lifehack_rotation",
        )

    # Retry неотправленных лидов — каждые 10 минут
    if not app.job_queue.get_jobs_by_name("flush_leads"):
        app.job_queue.run_repeating(
            flush_failed_leads,
            interval=60 * 10,
            first=60,
            name="flush_leads",
        )

    logger.info(f"Запланировано {count} постов + вечная ротация лайфхаков")
    return count

# ─── Команды ────────────────────────────────────────────────────
def get_welcome_text() -> str:
    if is_working_hours():
        timing = "На связи, отвечу быстро 🙌"
    else:
        timing = "Сейчас не рабочее время, но напишите — отвечу как только появлюсь онлайн."
    return (
        f"Привет! Меня зовут Алекс, я менеджер WeOneRent 👋\n\n"
        f"Помогу подобрать авто в Испании и оформить всё за пару минут. {timing}\n\n"
        "Чем могу помочь?"
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ud = user_data.get(chat_id, {})
    is_new = not ud.get("gdpr_accepted")

    # Трекинг источника: /start fb_ads → source="fb_ads"
    source = context.args[0] if context.args else ud.get("source", "organic")
    user_data[chat_id] = {"step": None, "gdpr_accepted": True, "source": source}
    logger.info(f"User {chat_id} started bot, source={source}")

    if is_new:
        # Первый запуск — GDPR уведомление (RGPD Art. 13)
        await update.message.reply_text(
            "👋 <b>Добро пожаловать в WeOneRent!</b>\n\n"
            "Перед началом — коротко об обработке данных:\n\n"
            "📋 <b>Что мы собираем:</b> имя, телефон (или Telegram), город и даты аренды.\n"
            "🎯 <b>Зачем:</b> только для оформления аренды авто. Никакого спама.\n"
            "🗑 <b>Удаление:</b> напишите на hello@weonerent.es — удалим в течение 30 дней.\n\n"
            "Продолжая, вы соглашаетесь с нашей "
            "<a href='https://weonerent.es/privacy-policy'>Политикой конфиденциальности</a>.\n\n"
            "<i>WeOneRent SL · CIF B22809552 · Alicante, España</i>",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

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
        ud = user_data.get(chat_id, {})
        current_step = ud.get("step")

        # Если есть незавершённая заявка — продолжаем с текущего шага
        if current_step not in (None, STEP_DONE) and current_step is not None:
            step_prompts = {
                STEP_CITY:         ("Продолжаем! Выберите город:", CITY_KEYBOARD),
                STEP_DATES:        ("Продолжаем! На какой срок нужен автомобиль?", DATES_KEYBOARD),
                STEP_DATES_DETAIL: ("Введите даты или срок аренды.\n\nНапример: с 10 по 17 июля  или  7 дней", REMOVE),
                STEP_CAR:          ("Продолжаем! Выберите тип автомобиля:", CAR_KEYBOARD),
                STEP_NAME:         ("Продолжаем! Как вас зовут?\n\nВведите имя и фамилию:", REMOVE),
                STEP_PHONE:        ("Продолжаем! Отправьте номер телефона или введите вручную:", PHONE_KEYBOARD),
            }
            prompt, kb = step_prompts.get(current_step, ("Продолжаем заявку:", CITY_KEYBOARD))
            await query.message.reply_text(prompt, reply_markup=kb)
        else:
            # Начинаем новую заявку
            user_data[chat_id] = {"step": STEP_CITY}
            await query.message.reply_text(
                "Отлично, оформляем! 🚗\n\nВ каком городе нужен автомобиль?",
                reply_markup=CITY_KEYBOARD
            )

    elif data.startswith("resume_"):
        # Возврат по кнопке из напоминания — пробуем восстановить шаг
        ud = user_data.get(chat_id, {})
        current_step = ud.get("step")

        if current_step not in (None, STEP_DONE) and current_step is not None:
            step_prompts = {
                STEP_CITY:         ("Продолжаем! Выберите город:", CITY_KEYBOARD),
                STEP_DATES:        ("Продолжаем! На какой срок нужен автомобиль?", DATES_KEYBOARD),
                STEP_DATES_DETAIL: ("Введите даты или срок аренды.\n\nНапример: с 10 по 17 июля  или  7 дней", REMOVE),
                STEP_CAR:          ("Продолжаем! Выберите тип автомобиля:", CAR_KEYBOARD),
                STEP_NAME:         ("Продолжаем! Как вас зовут?\n\nВведите имя и фамилию:", REMOVE),
                STEP_PHONE:        ("Продолжаем! Отправьте номер телефона или введите вручную:", PHONE_KEYBOARD),
            }
            prompt, kb = step_prompts.get(current_step, ("Продолжаем заявку:", CITY_KEYBOARD))
            await query.message.reply_text(prompt, reply_markup=kb)
        else:
            # Данные не сохранились (рестарт бота) — начинаем заново честно
            user_data[chat_id] = {"step": STEP_CITY}
            await query.message.reply_text(
                "К сожалению, данные заявки не сохранились — давайте начнём заново 🙌\n\n"
                "В каком городе нужен автомобиль?",
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
    user_data[chat_id] = {"step": None}
    await update.message.reply_text(
        "Отменено. Чем могу помочь?",
        reply_markup=build_main_menu()
    )

async def sheetstest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Диагностика подключения к Google Sheets — только для админа."""
    if not is_admin(update):
        return

    lines = ["🔍 Диагностика Google Sheets\n"]
    lines.append(f"GOOGLE_SCRIPT_URL: {'✅ задан' if GOOGLE_SCRIPT_URL else '❌ не задан'}\n")

    if not GOOGLE_SCRIPT_URL:
        lines.append("⛔ Добавь переменную GOOGLE_SCRIPT_URL в Railway.")
        lines.append("Инструкция — смотри последнее сообщение в чате.")
        await update.message.reply_text("\n".join(lines))
        return

    # Тестовая запись
    try:
        payload = {
            "date": datetime.now(TZ).strftime("%d.%m.%Y %H:%M"),
            "name": "ТЕСТ", "phone": "+34 000 000 000",
            "city": "Барселона", "dates": "7 дней",
            "car": "Эконом", "price": "от €224",
            "telegram": "@sheetstest", "chat_id": "0", "status": "Тест",
        }
        resp = _requests.post(GOOGLE_SCRIPT_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            lines.append("✅ Тестовая строка записана в таблицу!")
            lines.append("🎉 Всё работает — удали строку «ТЕСТ» из таблицы.")
        else:
            lines.append(f"❌ Сервер ответил {resp.status_code}")
            lines.append(f"Текст ответа: {resp.text[:300]}")
    except Exception as e:
        lines.append(f"❌ Ошибка соединения: {e}")
        lines.append("\nПроверь что URL скопирован полностью и Apps Script задеплоен.")

    await update.message.reply_text("\n".join(lines))

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

async def privacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """RGPD — права пользователя и информация об обработке данных."""
    chat_id = update.effective_chat.id
    await update.message.reply_text(
        "🔒 <b>Ваши данные и ваши права</b>\n\n"
        "<b>Что мы храним о вас:</b>\n"
        "• Имя и контакт (телефон или Telegram)\n"
        "• Город и даты запрошенной аренды\n"
        "• Ваш Telegram ID для связи\n\n"
        "<b>Ваши права (RGPD/GDPR):</b>\n"
        "✅ Получить копию ваших данных\n"
        "✅ Потребовать исправления\n"
        "✅ Потребовать удаления («право на забвение»)\n"
        "✅ Возразить против обработки\n\n"
        "<b>Как воспользоваться:</b>\n"
        "Напишите на <a href='mailto:hello@weonerent.es'>hello@weonerent.es</a>\n"
        "Тема: «RGPD — удаление данных» или «RGPD — копия данных»\n"
        "Ответим в течение 30 дней.\n\n"
        "📄 <a href='https://weonerent.es/privacy-policy'>Полная политика конфиденциальности</a>\n\n"
        "<i>WeOneRent SL · CIF B22809552 · Alicante, España</i>",
        parse_mode="HTML",
        disable_web_page_preview=True
    )

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
            track_step("step_dates")

            # Напоминание через 2 часа если не завершит
            context.job_queue.run_once(
                remind_abandoned,
                when=60 * 60 * 2,
                data={"chat_id": chat_id},
                name=f"remind_{chat_id}",
            )

            await update.message.reply_text(
                f"✅ Город: {city_display}\n\nНа какой срок нужен автомобиль?",
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
            track_step("step_car")
            city_key = ud.get("city_key", "")
            prices = PRICES.get(city_key, {})
            ep = prices.get("эконом", 22)
            cp = prices.get("комфорт", 35)
            sp = prices.get("suv", 48)
            await update.message.reply_text(
                f"✅ Срок: {ud['dates']}\n\nКакой тип автомобиля?\n\n"
                f"🟢 Эконом — от €{ep}/сутки (Seat Ibiza, VW Polo)\n"
                f"🔵 Комфорт — от €{cp}/сутки (Seat Leon, Skoda Octavia)\n"
                f"🔴 SUV — от €{sp}/сутки (Seat Ateca, Kia Sportage)",
                reply_markup=CAR_KEYBOARD
            )

        elif step == STEP_DATES_DETAIL:
            # Ввод точных дат текстом — используем умный парсер
            ud["dates"]        = text
            ud["days_estimate"] = parse_days(text)
            ud["step"]         = STEP_CAR

            days = ud["days_estimate"]
            days_text = f"{days} дней" if days else "уточним с менеджером"

            city_key = ud.get("city_key", "")
            prices = PRICES.get(city_key, {})
            ep = prices.get("эконом", 22)
            cp = prices.get("комфорт", 35)
            sp = prices.get("suv", 48)
            await update.message.reply_text(
                f"✅ Даты: {text} ({days_text})\n\nКакой тип автомобиля?\n\n"
                f"🟢 Эконом — от €{ep}/сутки (Seat Ibiza, VW Polo)\n"
                f"🔵 Комфорт — от €{cp}/сутки (Seat Leon, Skoda Octavia)\n"
                f"🔴 SUV — от €{sp}/сутки (Seat Ateca, Kia Sportage)",
                reply_markup=CAR_KEYBOARD
            )
        elif step == STEP_CAR:
            ud["car"] = text
            ud["step"] = STEP_NAME
            track_step("step_name")
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
                f"✅ Автомобиль: {text}{total_hint}\n\nКак вас зовут? Введите имя и фамилию:",
                reply_markup=REMOVE
            )
        elif step == STEP_NAME:
            ud["name"] = text
            ud["step"] = STEP_PHONE
            track_step("step_phone")
            name_first = text.strip().split()[0] if text.strip() else text
            await update.message.reply_text(
                f"Почти готово, {name_first}! 🎉\n\n"
                "Последний шаг — как вам удобнее получить подтверждение?\n\n"
                "📞 <b>Телефон</b> — менеджер позвонит один раз, чтобы подтвердить детали. Никакого спама.\n\n"
                "💬 <b>Без звонка</b> — менеджер напишет вам прямо здесь, в Telegram.",
                parse_mode="HTML",
                reply_markup=PHONE_KEYBOARD
            )
        elif step == STEP_PHONE:
            # Пользователь выбрал "Без звонка" — используем Telegram как контакт
            telegram_no_call = (text == TELEGRAM_CONTACT_MARKER)
            if telegram_no_call:
                user = update.effective_user
                tg_handle = f"@{user.username}" if user.username else f"tg://user?id={chat_id}"
                phone_value = f"Telegram: {tg_handle}"
            else:
                phone_value = None

            # Валидация номера (пропускаем если пришёл контакт или выбрал "без звонка")
            if not telegram_no_call and not update.message.contact and not is_valid_phone(text):
                await update.message.reply_text(
                    "Не похоже на номер телефона 🙈\n\n"
                    "Введите с кодом страны, например:\n"
                    "+34 612 345 678  или  +7 999 123 45 67\n\n"
                    "Или нажмите «💬 Напишу сам» — менеджер напишет вам в Telegram.",
                    reply_markup=PHONE_KEYBOARD
                )
                return

            # Защита от дублей — не отправляем заявку дважды
            if ud.get("lead_sent"):
                await update.message.reply_text(
                    "Ваша заявка уже принята 👍 Менеджер скоро свяжется.",
                    reply_markup=build_main_menu()
                )
                return

            ud["phone"] = phone_value if phone_value else text
            ud["step"]  = STEP_DONE
            ud["lead_sent"] = True

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

            # Разный текст в зависимости от способа связи
            is_tg_contact = phone.startswith("Telegram:")
            if is_working_hours():
                if is_tg_contact:
                    response_note = "Менеджер напишет вам здесь в Telegram в течение 5 минут."
                else:
                    response_note = "Менеджер позвонит вам в течение 5 минут."
            else:
                if is_tg_contact:
                    response_note = "Менеджер напишет вам в Telegram — сейчас нерабочее время (9:00–20:00 по Мадриду)."
                else:
                    response_note = "Менеджер позвонит в рабочее время (9:00–20:00 по Мадриду)."

            # Строка контакта в сводке
            contact_label = "💬 Telegram" if is_tg_contact else "📞 Телефон"
            contact_value = phone.replace("Telegram: ", "") if is_tg_contact else phone

            summary = (
                "🎉 Заявка принята!\n\n"
                "Вот что мы передали менеджеру:\n"
                "─────────────────────\n"
                f"📍 Город: {city}\n"
                f"📅 Срок: {dates}\n"
                f"🚗 Автомобиль: {car}\n"
                f"👤 Имя: {name}\n"
                f"{contact_label}: {contact_value}"
                f"{price_line}\n"
                "─────────────────────\n\n"
                f"{response_note}\n\n"
                "Пока ждёте — в нашем канале маршруты по Испании, "
                "лайфхаки об аренде и актуальные цены 👇"
            )
            final_keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Наш канал @weonerent", url="https://t.me/weonerent")],
                [InlineKeyboardButton("🌐 Сайт WeOneRent", url=site_url(UTM_FINAL)),
                 InlineKeyboardButton("💬 Написать менеджеру", url=get_manager_url())],
            ])
            track_lead(city)
            await send_lead(update, context, chat_id, ud)
            await update.message.reply_text(summary, reply_markup=final_keyboard)

            # Реферальная программа
            await update.message.reply_text(
                "🎁 <b>Порекомендуйте нас другу — получите скидку €15</b> на следующую аренду!\n\n"
                "Поделитесь этим сообщением с другом, который едет в Испанию.\n"
                "Пусть он назовёт ваш промокод при бронировании:\n\n"
                f"<code>WOR-{chat_id % 9000 + 1000}</code>\n\n"
                "Скидка активируется автоматически после его первой аренды 🚗",
                parse_mode="HTML"
            )

            # Follow-up через 2 часа если менеджер не позвонил
            context.job_queue.run_once(
                followup_after_lead,
                when=60 * 60 * 2,
                data={"chat_id": chat_id, "name": name},
                name=f"followup_{chat_id}",
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
            f"Технический сбой. Напишите мне напрямую: {get_manager_handle()}",
            reply_markup=REMOVE
        )

# ─── Отправка лида ──────────────────────────────────────────────
# ─── Еженедельный отчёт ─────────────────────────────────────────
weekly_stats = {
    "leads": 0, "started": 0, "cities": {},
    "step_dates": 0, "step_car": 0, "step_name": 0, "step_phone": 0,
}

def track_lead(city: str):
    weekly_stats["leads"] += 1
    c = city.lower().strip()
    weekly_stats["cities"][c] = weekly_stats["cities"].get(c, 0) + 1

def track_start():
    weekly_stats["started"] += 1

def track_step(step_name: str):
    """Трекинг прохождения шага воронки."""
    if step_name in weekly_stats:
        weekly_stats[step_name] += 1

async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    stats = weekly_stats.copy()
    weekly_stats["leads"]      = 0
    weekly_stats["started"]    = 0
    weekly_stats["cities"]     = {}
    weekly_stats["step_dates"] = 0
    weekly_stats["step_car"]   = 0
    weekly_stats["step_name"]  = 0
    weekly_stats["step_phone"] = 0

    # Данные из Google Sheets (приоритет)
    sheets_stats = get_sheets_stats_this_week()
    total_leads  = sheets_stats["total"] if sheets_stats["total"] > 0 else stats["leads"]
    cities_data  = sheets_stats["cities"] if sheets_stats["cities"] else stats["cities"]

    cities_text = "\n".join(
        f"  {c.capitalize()}: {n}" for c, n in
        sorted(cities_data.items(), key=lambda x: -x[1])
    ) or "  —"

    # Воронка — считаем drop-off на каждом шаге
    s  = stats["started"]
    d  = stats["step_dates"]
    c  = stats["step_car"]
    n  = stats["step_name"]
    p  = stats["step_phone"]
    l  = stats["leads"]

    def pct(part, whole):
        return f"{round(part / whole * 100)}%" if whole > 0 else "—"

    funnel_text = (
        f"  /start → город:    {s} чел.\n"
        f"  → даты:            {d} ({pct(d, s)} от старта)\n"
        f"  → тип авто:        {c} ({pct(c, s)} от старта)\n"
        f"  → имя:             {n} ({pct(n, s)} от старта)\n"
        f"  → телефон:         {p} ({pct(p, s)} от старта)\n"
        f"  → заявка готова:   {l} ({pct(l, s)} конверсия)"
    )

    sheets_note = (
        "📋 Лиды из Google Sheets"
        if sheets_stats["total"] > 0 else
        "⚠️ Sheets не подключён — данные из памяти"
    )

    report = (
        f"📊 Еженедельный отчёт WeOneRent\n"
        f"{'─' * 28}\n"
        f"🔻 Воронка за неделю:\n{funnel_text}\n"
        f"{'─' * 28}\n"
        f"📍 По городам:\n{cities_text}\n"
        f"{'─' * 28}\n"
        f"{sheets_note}\n"
        f"Следующий пост: /status"
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

async def followup_after_lead(context: ContextTypes.DEFAULT_TYPE):
    """Follow-up через 2 часа после отправки заявки."""
    chat_id = context.job.data["chat_id"]
    _name_parts = context.job.data.get("name", "").split()
    name = _name_parts[0] if _name_parts else "Привет"
    ud      = user_data.get(chat_id, {})

    # Не отправляем если уже идёт новая заявка
    if ud.get("step") not in (STEP_DONE, None):
        return

    if is_working_hours():
        text = (
            f"{name}, менеджер уже обрабатывает вашу заявку! 🚗\n\n"
            "Если не получили звонок — напишите напрямую, ответим сразу:"
        )
    else:
        text = (
            f"{name}, ваша заявка принята и ждёт менеджера.\n\n"
            "Сейчас нерабочее время (9:00–20:00 по Мадриду).\n"
            "Менеджер свяжется утром — или напишите сами:"
        )

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Написать менеджеру", url=get_manager_url())],
                [InlineKeyboardButton("🌐 Сайт WeOneRent", url=site_url(UTM_FOLLOWUP))],
            ])
        )
    except Exception as e:
        logger.error(f"followup_after_lead error: {e}")

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
                    [InlineKeyboardButton("🚗 Продолжить заявку", callback_data=f"resume_{step}")],
                    [InlineKeyboardButton("📞 Написать менеджеру", url=get_manager_url())],
                ])
            )
        except Exception:
            pass

async def send_lead(update, context, client_chat_id, ud):
    user     = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"

    # Считаем ориентировочную стоимость для Sheets
    price_est = ""
    days     = ud.get("days_estimate", 0)
    city_key = ud.get("city_key", "")
    car_key  = CAR_ALIASES.get(ud.get("car", "").lower().strip(), ud.get("car", "").lower().strip())
    if 3 <= days <= 60 and city_key:
        try:
            _, total = calc_price(city_key, car_key, days)
            if total:
                price_est = f"от €{total}"
        except Exception:
            pass

    lead = (
        f"🆕 НОВАЯ ЗАЯВКА — WeOneRent\n"
        f"{'─' * 25}\n"
        f"Клиент: {user.full_name or '—'} ({username})\n"
        f"{'─' * 25}\n"
        f"Город: {ud.get('city', '—')}\n"
        f"Даты: {ud.get('dates', '—')}\n"
        f"Авто: {ud.get('car', '—')}\n"
        f"Имя: {ud.get('name', '—')}\n"
        f"Телефон: {ud.get('phone', '—')}\n"
        + (f"Стоимость: {price_est}\n" if price_est else "") +
        f"{'─' * 25}\n"
        f"Написать: tg://user?id={client_chat_id}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead)
        logger.info(f"Заявка отправлена от {username}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

    # Сохраняем в Google Sheets (отдельно, чтобы ошибка Sheets не ломала Telegram)
    try:
        append_lead_to_sheets(ud, user, client_chat_id, price_est)
    except Exception as e:
        logger.error(f"Sheets lead error: {e}")

# ─── Уведомление при старте ─────────────────────────────────────
async def post_init(application):
    """Отправляет уведомление владельцу при каждом запуске бота."""
    now = datetime.now(TZ).strftime("%d.%m %H:%M")
    try:
        await application.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"✅ Бот WeOneRent запущен\n🕐 {now} (Мадрид)\n🚀 Готов принимать заявки"
        )
    except Exception as e:
        logger.error(f"post_init notify error: {e}")

# ─── Health Check HTTP сервер ────────────────────────────────────
_bot_started_at = datetime.now(pytz.timezone("Europe/Madrid")).strftime("%d.%m %H:%M")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            body = json.dumps({
                "status": "ok",
                "bot": "WeOneRent",
                "started": _bot_started_at
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # не спамить логи

def _run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info(f"Health check сервер запущен на порту {port}")
    server.serve_forever()

def start_health_server():
    t = _threading_http.Thread(target=_run_health_server, daemon=True)
    t.start()

# ─── Ежедневный heartbeat ────────────────────────────────────────
async def daily_heartbeat(context):
    """Каждый день в 10:00 по Мадриду шлёт отчёт владельцу."""
    now = datetime.now(TZ)
    uptime_hours = "—"
    leads_today = weekly_stats.get("leads", 0)
    started_today = weekly_stats.get("started", 0)

    text = (
        f"✅ <b>Бот WeOneRent работает</b>\n"
        f"🕐 {now.strftime('%d.%m %H:%M')} (Мадрид)\n\n"
        f"📊 Сегодня:\n"
        f"  • Начали заявку: {started_today}\n"
        f"  • Заявок готово: {leads_today}\n\n"
        f"🟢 Всё в порядке"
    )
    try:
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Heartbeat error: {e}")

# ─── Запуск ─────────────────────────────────────────────────────
def _build_persistence() -> PicklePersistence:
    """Создаёт PicklePersistence. При повреждённом файле — удаляет и создаёт заново."""
    pickle_path = "/data/bot_persistence"
    os.makedirs("/data", exist_ok=True)   # создаём /data если нет
    for attempt in range(2):
        try:
            p = PicklePersistence(filepath=pickle_path, update_interval=30)
            logger.info(f"PicklePersistence загружен: {pickle_path}")
            return p
        except Exception as e:
            logger.error(f"PicklePersistence попытка {attempt+1} не удалась: {e}")
            if attempt == 0:
                # Удаляем повреждённый файл и пробуем снова
                for suffix in ["", ".pkl", ".db", "-conversations", "-user_data", "-chat_data", "-bot_data"]:
                    try:
                        os.remove(pickle_path + suffix)
                        logger.warning(f"Удалён повреждённый файл: {pickle_path + suffix}")
                    except FileNotFoundError:
                        pass
                    except Exception as ex:
                        logger.warning(f"Не удалось удалить {pickle_path + suffix}: {ex}")
    # Финальный фоллбэк — in-memory (без персистентности)
    logger.warning("PicklePersistence недоступен — работаем без сохранения сессий")
    return None

def main():
    global user_data
    persistence = _build_persistence()
    builder = Application.builder().token(BOT_TOKEN).post_init(post_init)
    if persistence:
        builder = builder.persistence(persistence)
    app = builder.build()
    # Подключаем наш user_data к PTB — теперь PicklePersistence сохраняет его автоматически
    user_data = app._user_data  # mutable internal dict (app.user_data — read-only proxy)

    # Планируем все посты при старте
    schedule_all_posts(app)

    # Ежедневный heartbeat в 10:00 по Мадриду
    app.job_queue.run_daily(
        daily_heartbeat,
        time=dtime(hour=10, minute=0, tzinfo=TZ),
        name="daily_heartbeat"
    )

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("reset",   reset))
    app.add_handler(CommandHandler("admin",   admin_cmd))
    app.add_handler(CommandHandler("post",    post_cmd))
    app.add_handler(CommandHandler("status",  status_cmd))
    app.add_handler(CommandHandler("cancel",     cancel_cmd))
    app.add_handler(CommandHandler("price",      price_cmd))
    app.add_handler(CommandHandler("faq",        faq_cmd))
    app.add_handler(CommandHandler("privacy",    privacy_cmd))
    app.add_handler(CommandHandler("sheetstest", sheetstest_cmd))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern="^menu_|^resume_"))
    app.add_handler(ChatMemberHandler(welcome_new_member, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # ─── Error handler — логирует ВСЕ ошибки хендлеров ───────────
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        logger.error(f"PTB error: {context.error}", exc_info=context.error)
        try:
            # Уведомляем владельца об ошибке
            await context.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text=f"⚠️ Ошибка бота:\n<code>{context.error}</code>",
                parse_mode="HTML"
            )
        except Exception:
            pass

    app.add_error_handler(error_handler)

    start_health_server()
    logger.info("Бот запущен. Автопостинг активен.")
    # drop_pending_updates=False — не теряем команды при рестарте
    app.run_polling(drop_pending_updates=False, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
