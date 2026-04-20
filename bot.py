import os
import logging
import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ── CONFIG ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "448609289"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── СОСТОЯНИЯ ────────────────────────────────────────────────────────────
STEP_CITY    = 0
STEP_DATES   = 1
STEP_CAR     = 2
STEP_NAME    = 3
STEP_PHONE   = 4
STEP_DONE    = 5

# ── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────
CAR_KEYBOARD = ReplyKeyboardMarkup(
    [["🚗 Эконом", "🚙 Комфорт"], ["🚕 SUV", "🚌 Минивэн"]],
    resize_keyboard=True, one_time_keyboard=True
)
PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Поделиться номером", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)
REMOVE = ReplyKeyboardRemove()

# ── ХРАНИЛИЩЕ ────────────────────────────────────────────────────────────
# user_data[chat_id] = {"step": int, "city": str, "dates": str, ...}
user_data = {}

SYSTEM_PROMPT = """Ты — вежливый помощник WeOneRent, аренда автомобилей в Испании.
Отвечай на языке клиента (русский или английский).
Будь дружелюбным и кратким. Не придумывай цены.
Если спрашивают о депозите: депозит зависит от страховки, менеджер расскажет подробнее.
Если сложный вопрос о ценах/страховке — скажи что переключаешь на менеджера."""

def call_groq(messages):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "max_tokens": 512,
        "temperature": 0.7
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY, "history": []}
    return user_data[chat_id]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY, "history": []}
    await update.message.reply_text(
        "👋 Привет! Я помощник WeOneRent\n"
        "🇪🇸 Аренда автомобилей в Испании\n\n"
        "Помогу оформить заявку за 2 минуты.\n"
        "Менеджер свяжется в течение 15 минут.\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🏙 В каком городе вам нужна машина?\n"
        "(Барселона, Мадрид, Малага, Аликанте...)\n\n"
        "🏙 Which city do you need a car in?",
        reply_markup=REMOVE
    )

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY, "history": []}
    await update.message.reply_text("🔄 Начнём сначала! Напиши /start", reply_markup=REMOVE)

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if update.effective_user.username == "fake_smm":
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(f"✅ Готово. Chat ID: {OWNER_CHAT_ID}")
    else:
        await update.message.reply_text("❌ Нет доступа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ud = get_user(chat_id)

    # Получаем текст (обычный или контакт)
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
            reply = ask_groq(ud, f"Клиент выбрал город: {text}. Спроси на какие даты нужен автомобиль (минимум 3 суток).")
            await update.message.reply_text(reply, reply_markup=REMOVE)

        elif step == STEP_DATES:
            ud["dates"] = text
            ud["step"] = STEP_CAR
            reply = ask_groq(ud, f"Клиент указал даты: {text}. Спроси какой тип авто нужен.")
            await update.message.reply_text(reply, reply_markup=CAR_KEYBOARD)

        elif step == STEP_CAR:
            ud["car"] = text.replace("🚗 ", "").replace("🚙 ", "").replace("🚕 ", "").replace("🚌 ", "")
            ud["step"] = STEP_NAME
            reply = ask_groq(ud, f"Клиент выбрал тип авто: {text}. Спроси имя и фамилию.")
            await update.message.reply_text(reply, reply_markup=REMOVE)

        elif step == STEP_NAME:
            ud["name"] = text
            ud["step"] = STEP_PHONE
            reply = ask_groq(ud, f"Клиент представился: {text}. Попроси номер телефона с кодом страны.")
            await update.message.reply_text(reply, reply_markup=PHONE_KEYBOARD)

        elif step == STEP_PHONE:
            ud["phone"] = text
            ud["step"] = STEP_DONE

            # Итоговое сообщение клиенту
            summary = (
                f"✅ Заявка принята!\n\n"
                f"📍 Город: {ud.get('city', '—')}\n"
                f"📅 Даты: {ud.get('dates', '—')}\n"
                f"🚗 Авто: {ud.get('car', '—')}\n"
                f"👤 Имя: {ud.get('name', '—')}\n"
                f"📱 Телефон: {ud.get('phone', '—')}\n\n"
                f"Менеджер свяжется с вами в течение 15 минут. 🙌"
            )
            await update.message.reply_text(summary, reply_markup=REMOVE)

            # Отправка заявки владельцу — ГАРАНТИРОВАННО
            await send_lead_to_owner(update, context, chat_id, ud)

        elif step == STEP_DONE:
            await update.message.reply_text(
                "✅ Ваша заявка уже принята! Менеджер скоро свяжется.\n\n"
                "Чтобы создать новую заявку, напиши /start",
                reply_markup=REMOVE
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "⚠️ Технический сбой. Напишите нам: @weonerent",
            reply_markup=REMOVE
        )

def ask_groq(ud, prompt):
    """Задаём вопрос через Groq с контекстом разговора."""
    ud["history"].append({"role": "user", "content": prompt})
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + ud["history"]
    reply = call_groq(messages)
    ud["history"].append({"role": "assistant", "content": reply})
    return reply

async def send_lead_to_owner(update, context, client_chat_id, ud):
    """Отправка заявки владельцу — вызывается ВСЕГДА при STEP_DONE."""
    user = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"
    name_tg = user.full_name or "Неизвестно"

    lead = (
        f"🔔 НОВАЯ ЗАЯВКА — WeOneRent\n"
        f"{'━' * 28}\n"
        f"👤 {name_tg} ({username})\n"
        f"{'━' * 28}\n"
        f"📍 Город: {ud.get('city', '—')}\n"
        f"📅 Даты: {ud.get('dates', '—')}\n"
        f"🚗 Авто: {ud.get('car', '—')}\n"
        f"👤 Имя: {ud.get('name', '—')}\n"
        f"📱 Телефон: {ud.get('phone', '—')}\n"
        f"{'━' * 28}\n"
        f"💬 tg://user?id={client_chat_id}"
    )

    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead)
        logger.info(f"✅ Заявка отправлена от {username} владельцу {OWNER_CHAT_ID}")
    except Exception as e:
        logger.error(f"❌ Не удалось отправить заявку: {e}")

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
