import os
import logging
import requests
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes
)

# ── CONFIG ──────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "448609289"))

# ── LOGGING ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

SYSTEM_PROMPT = """Ты — AI-агент компании WeOneRent, аренда автомобилей в Испании.
Твоя задача: вежливо и дружелюбно собрать заявку от клиента.

Языки: русский и английский. Отвечай на том языке на котором пишет клиент.

Задавай вопросы строго по порядку, по одному за раз:
1. "В каком городе вам нужна машина?"
2. "На какие даты вам нужен автомобиль? Уточняю — минимальный срок аренды 3 суток."
3. "Какой тип авто вам нужен? (эконом, комфорт, SUV, минивэн)"
4. "Ваше имя и фамилия?"
5. "Ваш номер телефона с кодом страны (обязательно)?"

После получения всех данных — выведи итоговую сводку заявки и напиши что менеджер свяжется в течение 15 минут.
В конце сообщения с итоговой сводкой добавь маркер: [ЗАЯВКА ГОТОВА]

Важно:
- Не придумывай цены — говори что менеджер озвучит точную стоимость
- Если спрашивают о депозите — отвечай: "Депозит зависит от выбранного вида страховки. У нас есть разные варианты страхования. Менеджер подробно расскажет обо всех условиях и подберёт оптимальный вариант для вас."
- Если спрашивают о доставке — говори что доставка авто возможна в любую точку
- Если клиент задаёт сложные вопросы о страховке, условиях или ценах — говори что переключаешь на менеджера и добавляй маркер [ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]
- Будь кратким, не пиши длинных абзацев
- Задавай строго по одному вопросу за раз"""

# Хранилище истории сообщений
user_histories = {}

def call_groq(messages):
    """Вызов Groq API через requests — без зависимостей от openai/groq SDK."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7
    }
    resp = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    await update.message.reply_text(
        "👋 Привет! Я помощник WeOneRent — аренда автомобилей в Испании.\n\n"
        "🚗 Помогу оформить заявку за пару минут.\n"
        "📞 Менеджер свяжется с вами в течение 15 минут.\n\n"
        "В каком городе вам нужна машина?\n\n"
        "---\n"
        "👋 Hi! I'm WeOneRent assistant — car rental in Spain.\n"
        "Which city do you need a car in?"
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if update.effective_user.username == "fake_smm":
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(
            f"✅ Зарегистрирован как владелец.\n"
            f"Chat ID: {OWNER_CHAT_ID}\n"
            f"Заявки будут приходить сюда."
        )
    else:
        await update.message.reply_text("❌ Нет доступа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_message = update.message.text

    if chat_id not in user_histories:
        user_histories[chat_id] = []

    user_histories[chat_id].append({"role": "user", "content": user_message})
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[chat_id]
        assistant_message = call_groq(messages)
        user_histories[chat_id].append({"role": "assistant", "content": assistant_message})

        clean_message = assistant_message.replace("[ЗАЯВКА ГОТОВА]", "").replace("[ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]", "").strip()
        await update.message.reply_text(clean_message)

        if "[ЗАЯВКА ГОТОВА]" in assistant_message:
            await send_lead_to_owner(update, context, chat_id, clean_message)

        if "[ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]" in assistant_message:
            await notify_manager(update, context, chat_id)

    except Exception as e:
        logger.error(f"Ошибка Groq API: {e}")
        await update.message.reply_text(
            "⚠️ Технический сбой. Напишите нам напрямую: @weonerent"
        )

async def send_lead_to_owner(update, context, client_chat_id, summary):
    global OWNER_CHAT_ID
    if not OWNER_CHAT_ID:
        logger.warning("OWNER_CHAT_ID не установлен — запусти /admin")
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"
    name = user.full_name or "Неизвестно"

    lead_message = (
        f"🔔 НОВАЯ ЗАЯВКА — WeOneRent Bot\n"
        f"{'─' * 30}\n"
        f"👤 {name}\n"
        f"📱 {username}\n"
        f"{'─' * 30}\n"
        f"{summary}\n"
        f"{'─' * 30}\n"
        f"💬 Написать: tg://user?id={client_chat_id}"
    )

    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead_message)
        logger.info(f"Заявка отправлена от {username}")
    except Exception as e:
        logger.error(f"Не удалось отправить заявку: {e}")

async def notify_manager(update, context, client_chat_id):
    global OWNER_CHAT_ID
    if not OWNER_CHAT_ID:
        return
    user = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"
    try:
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=f"⚡️ Клиент {username} задал сложный вопрос — требуется менеджер.\n"
                 f"💬 Написать: tg://user?id={client_chat_id}"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    await update.message.reply_text("🔄 Начнём сначала. Напиши /start")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
