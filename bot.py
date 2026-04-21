import os
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

BOT_TOKEN     = os.environ["BOT_TOKEN"]
GROQ_API_KEY  = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "448609289"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

STEP_CITY  = 0
STEP_DATES = 1
STEP_CAR   = 2
STEP_NAME  = 3
STEP_PHONE = 4
STEP_DONE  = 5

CAR_KEYBOARD = ReplyKeyboardMarkup(
    [["Эконом", "Комфорт"], ["SUV", "Минивэн"]],
    resize_keyboard=True, one_time_keyboard=True
)
PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("Отправить мой номер", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)
REMOVE = ReplyKeyboardRemove()

CHANNEL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("Подписаться на канал", url="https://t.me/weonerent")]
])

user_data = {}

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY}
    return user_data[chat_id]

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
        text="Пока оформляем заявку — подписывайтесь на наш канал. Там лайфхаки, маршруты и актуальные цены.",
        reply_markup=CHANNEL_KEYBOARD
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY}
    await update.message.reply_text("Начнём заново.\n\nВ каком городе нужен автомобиль?", reply_markup=REMOVE)

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if update.effective_user.username == "fake_smm":
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(f"Готово. Chat ID: {OWNER_CHAT_ID}")
    else:
        await update.message.reply_text("Нет доступа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ud = get_user(chat_id)

    if update.message.contact:
        phone = update.message.contact.phone_number
        text = f"+{phone}" if not phone.startswith("+") else phone
    else:
        text = update.message.text

    step = ud["step"]
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
                reply_markup=CHANNEL_KEYBOARD
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

async def send_lead(update, context, client_chat_id, ud):
    user = update.effective_user
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

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
