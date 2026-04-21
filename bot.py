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
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен. Автопостинг активен.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
