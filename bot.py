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

# ── LOGGING ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── KEYBOARDS ────────────────────────────────────────────────────────────
CAR_TYPE_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🚗 Эконом", "🚙 Комфорт"],
        ["🚕 SUV", "🚌 Минивэн"],
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📱 Отправить мой номер", request_contact=True)]],
    resize_keyboard=True,
    one_time_keyboard=True
)

REMOVE_KEYBOARD = ReplyKeyboardRemove()

SYSTEM_PROMPT = """Ты — AI-агент компании WeOneRent, аренда автомобилей в Испании.
Твоя задача: вежливо и дружелюбно собрать заявку от клиента.

Языки: русский и английский. Отвечай на том языке на котором пишет клиент.

Задавай вопросы строго по порядку, по одному за раз:
1. Спроси город (где нужна машина)
2. Спроси даты (минимальный срок — 3 суток)
3. Спроси тип авто — ОБЯЗАТЕЛЬНО добавь маркер [SHOW_CAR_TYPES] в конце сообщения
4. Спроси имя и фамилию
5. Спроси телефон — ОБЯЗАТЕЛЬНО добавь маркер [SHOW_PHONE] в конце сообщения

После получения всех данных — выведи итоговую сводку заявки и напиши что менеджер свяжется в течение 15 минут.
В конце сообщения с итоговой сводкой добавь маркер: [ЗАЯВКА ГОТОВА]

Важно:
- Не придумывай цены — говори что менеджер озвучит точную стоимость
- Если спрашивают о депозите — отвечай: "Депозит зависит от выбранного вида страховки. Менеджер подробно расскажет обо всех условиях."
- Если спрашивают о доставке — говори что доставка авто возможна в любую точку
- Если клиент задаёт сложные вопросы о страховке, условиях или ценах — говори что переключаешь на менеджера и добавляй маркер [ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]
- Будь кратким, не пиши длинных абзацев
- Задавай строго по одному вопросу за раз
- НЕ включай маркеры [SHOW_CAR_TYPES], [SHOW_PHONE], [ЗАЯВКА ГОТОВА], [ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА] в текст для клиента"""

# Хранилище истории сообщений
user_histories = {}

def call_groq(messages):
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
        "👋 *Привет! Я помощник WeOneRent*\n"
        "🇪🇸 Аренда автомобилей в Испании\n\n"
        "🚗 Помогу оформить заявку за 2 минуты\n"
        "📞 Менеджер свяжется в течение 15 минут\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🏙 *В каком городе вам нужна машина?*\n\n"
        "_(Барселона, Мадрид, Малага, Аликанте...)_\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👋 *Hi! I'm WeOneRent assistant*\n"
        "🇪🇸 Car rental in Spain\n\n"
        "🏙 *Which city do you need a car in?*",
        parse_mode="Markdown",
        reply_markup=REMOVE_KEYBOARD
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if update.effective_user.username == "fake_smm":
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(
            f"✅ Зарегистрирован как владелец.\n"
            f"Chat ID: `{OWNER_CHAT_ID}`\n"
            f"Заявки будут приходить сюда.",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("❌ Нет доступа.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    # Обработка контакта (кнопка телефона)
    if update.message.contact:
        phone = update.message.contact.phone_number
        user_message = f"+{phone}" if not phone.startswith("+") else phone
    else:
        user_message = update.message.text

    if chat_id not in user_histories:
        user_histories[chat_id] = []

    user_histories[chat_id].append({"role": "user", "content": user_message})
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + user_histories[chat_id]
        assistant_message = call_groq(messages)
        user_histories[chat_id].append({"role": "assistant", "content": assistant_message})

        # Очищаем маркеры из текста для клиента
        clean_message = (
            assistant_message
            .replace("[ЗАЯВКА ГОТОВА]", "")
            .replace("[ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]", "")
            .replace("[SHOW_CAR_TYPES]", "")
            .replace("[SHOW_PHONE]", "")
            .strip()
        )

        # Определяем клавиатуру
        if "[SHOW_CAR_TYPES]" in assistant_message:
            keyboard = CAR_TYPE_KEYBOARD
        elif "[SHOW_PHONE]" in assistant_message:
            keyboard = PHONE_KEYBOARD
        elif "[ЗАЯВКА ГОТОВА]" in assistant_message:
            keyboard = REMOVE_KEYBOARD
        else:
            keyboard = None

        if keyboard is not None:
            await update.message.reply_text(clean_message, reply_markup=keyboard, parse_mode="Markdown")
        else:
            await update.message.reply_text(clean_message, parse_mode="Markdown")

        if "[ЗАЯВКА ГОТОВА]" in assistant_message:
            await send_lead_to_owner(update, context, chat_id, clean_message)

        if "[ПЕРЕКЛЮЧИТЬ НА МЕНЕДЖЕРА]" in assistant_message:
            await notify_manager(update, context, chat_id)

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "⚠️ Технический сбой. Напишите нам напрямую: @weonerent",
            reply_markup=REMOVE_KEYBOARD
        )

async def send_lead_to_owner(update, context, client_chat_id, summary):
    global OWNER_CHAT_ID
    if not OWNER_CHAT_ID:
        return

    user = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"
    name = user.full_name or "Неизвестно"

    lead_message = (
        f"🔔 *НОВАЯ ЗАЯВКА — WeOneRent*\n"
        f"{'━' * 25}\n"
        f"👤 {name}\n"
        f"📱 {username}\n"
        f"{'━' * 25}\n\n"
        f"{summary}\n\n"
        f"{'━' * 25}\n"
        f"💬 [Написать клиенту](tg://user?id={client_chat_id})"
    )

    try:
        await context.bot.send_message(
            chat_id=OWNER_CHAT_ID,
            text=lead_message,
            parse_mode="Markdown"
        )
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
            text=f"⚡️ *Клиент {username} задал сложный вопрос* — требуется менеджер.\n"
                 f"💬 [Написать клиенту](tg://user?id={client_chat_id})",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка уведомления: {e}")

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_histories[chat_id] = []
    await update.message.reply_text(
        "🔄 Начнём сначала!\nНажми /start",
        reply_markup=REMOVE_KEYBOARD
    )

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
