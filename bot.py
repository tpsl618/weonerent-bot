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

# Шаги режима публикации (для админа)
STEP_POST_TEXT        = "post_text"
STEP_POST_BUTTONS     = "post_buttons"
STEP_SCHEDULE_WHEN    = "schedule_when"
STEP_SCHEDULE_TEXT    = "schedule_text"
STEP_SCHEDULE_BUTTONS = "schedule_buttons"

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

POST_KEYBOARD_FULL = InlineKeyboardMarkup([
    [InlineKeyboardButton("✈️ Оставить заявку", url="https://t.me/weonerent_ai_bot")],
    [InlineKeyboardButton("🌐 Наш сайт", url="https://weonerent.es")]
])

POST_KEYBOARD_SOFT = InlineKeyboardMarkup([
    [InlineKeyboardButton("💬 Узнать стоимость", url="https://t.me/weonerent_ai_bot"),
     InlineKeyboardButton("🌐 Сайт", url="https://weonerent.es")]
])

POST_KEYBOARD_PROMO = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔥 Забронировать сейчас", url="https://t.me/weonerent_ai_bot")],
    [InlineKeyboardButton("📋 Подробнее на сайте", url="https://weonerent.es")]
])

BUTTON_MAP = {
    "📌 Стандарт":   POST_KEYBOARD_FULL,
    "💬 Мягкий CTA": POST_KEYBOARD_SOFT,
    "🔥 Акция":      POST_KEYBOARD_PROMO,
    "❌ Без кнопок": None,
}

BUTTON_TYPE_KEYBOARD = ReplyKeyboardMarkup(
    [["📌 Стандарт", "💬 Мягкий CTA"], ["🔥 Акция", "❌ Без кнопок"]],
    resize_keyboard=True, one_time_keyboard=True
)

# ─── Данные пользователей ───────────────────────────────────────
user_data = {}
scheduled_jobs = {}   # name -> описание для /jobs

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY}
    return user_data[chat_id]

def is_admin(update: Update) -> bool:
    return update.effective_user.username == ADMIN_USERNAME

# ─── Callback для запланированного поста ────────────────────────
async def publish_scheduled(context: ContextTypes.DEFAULT_TYPE):
    job  = context.job
    data = job.data
    try:
        await context.bot.send_message(
            chat_id=CHANNEL_ID,
            text=data["text"],
            reply_markup=data["keyboard"]
        )
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"✅ Пост опубликован в {CHANNEL_ID}\n\n«{data['text'][:80]}…»"
        )
        scheduled_jobs.pop(job.name, None)
        logger.info(f"Scheduled post published: {job.name}")
    except Exception as e:
        logger.error(f"Scheduled post error: {e}")
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"❌ Ошибка публикации запланированного поста: {e}"
        )

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
        text="Пока оформляем заявку — подписывайтесь на канал. Там лайфхаки, маршруты и актуальные цены.",
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
    if is_admin(update):
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(
            f"✅ Готово. Chat ID: {OWNER_CHAT_ID}\n\n"
            "Команды:\n"
            "/post — опубликовать сейчас\n"
            "/schedule — запланировать пост\n"
            "/jobs — список запланированных постов\n"
            "/cancel — выйти из режима публикации"
        )
    else:
        await update.message.reply_text("Нет доступа.")

async def post_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_POST_TEXT}
    await update.message.reply_text(
        "📝 Публикация сейчас\n\n"
        "Отправь текст поста.\n\n/cancel — отмена",
        reply_markup=REMOVE
    )

async def schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_SCHEDULE_WHEN}
    await update.message.reply_text(
        "⏰ Планировщик\n\n"
        "Укажи дату и время публикации (МСК):\n\n"
        "Формат: ДД.ММ ЧЧ:ММ\n"
        "Пример: 28.04 09:30\n\n"
        "/cancel — отмена",
        reply_markup=REMOVE
    )

async def jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not scheduled_jobs:
        await update.message.reply_text("Нет запланированных постов.")
        return
    lines = ["📅 Запланированные посты:\n"]
    for name, info in scheduled_jobs.items():
        lines.append(f"• {info['when_str']} МСК — «{info['preview']}»")
    await update.message.reply_text("\n".join(lines))

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

    # ── /post: ввод текста ──
    if step == STEP_POST_TEXT and is_admin(update):
        ud["post_text"] = text
        ud["step"] = STEP_POST_BUTTONS
        await update.message.reply_text(
            "Выбери тип кнопок:",
            reply_markup=BUTTON_TYPE_KEYBOARD
        )
        return

    # ── /post: выбор кнопок → публикуем сразу ──
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
                f"✅ Пост опубликован в {CHANNEL_ID}",
                reply_markup=REMOVE
            )
        except Exception as e:
            await update.message.reply_text(
                f"❌ Ошибка: {e}\n\nУбедись что бот — администратор канала {CHANNEL_ID}",
                reply_markup=REMOVE
            )
        user_data[chat_id] = {"step": STEP_CITY}
        return

    # ── /schedule: ввод даты/времени ──
    if step == STEP_SCHEDULE_WHEN and is_admin(update):
        try:
            year = datetime.now(MSK).year
            when_naive = datetime.strptime(f"{text} {year}", "%d.%m %H:%M %Y")
            when_msk   = MSK.localize(when_naive)
            if when_msk <= datetime.now(MSK):
                await update.message.reply_text(
                    "❌ Время уже прошло. Укажи будущее время:"
                )
                return
            ud["schedule_when"]     = when_msk
            ud["schedule_when_str"] = text
            ud["step"] = STEP_SCHEDULE_TEXT
            await update.message.reply_text(
                f"✅ Время: {text} МСК\n\nТеперь отправь текст поста:",
                reply_markup=REMOVE
            )
        except ValueError:
            await update.message.reply_text(
                "❌ Неверный формат. Пример: 28.04 09:30"
            )
        return

    # ── /schedule: ввод текста ──
    if step == STEP_SCHEDULE_TEXT and is_admin(update):
        ud["schedule_text"] = text
        ud["step"] = STEP_SCHEDULE_BUTTONS
        await update.message.reply_text(
            "Выбери тип кнопок:",
            reply_markup=BUTTON_TYPE_KEYBOARD
        )
        return

    # ── /schedule: выбор кнопок → создаём задачу ──
    if step == STEP_SCHEDULE_BUTTONS and is_admin(update):
        keyboard  = BUTTON_MAP.get(text)
        post_text = ud.get("schedule_text", "")
        when_msk  = ud.get("schedule_when")
        when_str  = ud.get("schedule_when_str", "")

        job_name = f"post_{when_str.replace(' ', '_').replace(':', '-')}"
        context.job_queue.run_once(
            publish_scheduled,
            when=when_msk,
            data={"text": post_text, "keyboard": keyboard},
            name=job_name
        )
        scheduled_jobs[job_name] = {
            "when_str": when_str,
            "preview":  post_text[:50]
        }
        await update.message.reply_text(
            f"✅ Пост запланирован на {when_str} МСК\n\n"
            f"«{post_text[:80]}{'…' if len(post_text) > 80 else ''}»\n\n"
            f"Список постов: /jobs",
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
                "В нашем канале — лайфхаки про аренду авто, маршруты по Испании и актуальные цены.",
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
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("reset",    reset))
    app.add_handler(CommandHandler("admin",    admin_cmd))
    app.add_handler(CommandHandler("post",     post_cmd))
    app.add_handler(CommandHandler("schedule", schedule_cmd))
    app.add_handler(CommandHandler("jobs",     jobs_cmd))
    app.add_handler(CommandHandler("cancel",   cancel_cmd))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
