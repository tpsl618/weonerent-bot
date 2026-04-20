import os
import logging
import requests
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

# ── CONFIG ───────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "448609289"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# ── СОСТОЯНИЯ ────────────────────────────────────────────────────────────
STEP_CITY  = 0
STEP_DATES = 1
STEP_CAR   = 2
STEP_NAME  = 3
STEP_PHONE = 4
STEP_DONE  = 5

# ── КЛАВИАТУРЫ ───────────────────────────────────────────────────────────
CAR_INLINE = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🚗 Эконом",  callback_data="car_econom"),
        InlineKeyboardButton("🚙 Комфорт", callback_data="car_comfort"),
    ],
    [
        InlineKeyboardButton("🚕 SUV",     callback_data="car_suv"),
        InlineKeyboardButton("🚌 Минивэн", callback_data="car_minivan"),
    ],
])

PHONE_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("📲 Поделиться номером", request_contact=True)]],
    resize_keyboard=True, one_time_keyboard=True
)
REMOVE = ReplyKeyboardRemove()

# ── ПРОГРЕСС ─────────────────────────────────────────────────────────────
def progress(step):
    total = 5
    filled = step
    bar = "●" * filled + "○" * (total - filled)
    return f"<b>{bar}</b>  {step}/{total}"

# ── ХРАНИЛИЩЕ ────────────────────────────────────────────────────────────
user_data = {}

SYSTEM_PROMPT = """Ты — вежливый помощник WeOneRent, аренда автомобилей в Испании.
Отвечай кратко и дружелюбно на языке клиента (русский или английский).
Не придумывай цены. Депозит зависит от страховки — менеджер расскажет подробнее."""

def call_groq(history):
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "max_tokens": 256,
        "temperature": 0.6
    }
    r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def get_user(chat_id):
    if chat_id not in user_data:
        user_data[chat_id] = {"step": STEP_CITY, "history": []}
    return user_data[chat_id]

# ── КОМАНДЫ ──────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY, "history": []}

    text = (
        "🚘 <b>WeOneRent</b>\n"
        "<i>Аренда автомобилей в Испании</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{progress(0)}\n\n"
        "🏙 <b>В каком городе нужен автомобиль?</b>\n\n"
        "<i>Барселона · Мадрид · Малага · Аликанте\n"
        "Валенсия · Севилья · Тенерифе · и др.</i>\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🌍 <i>Which city do you need a car in?</i>"
    )
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=REMOVE)

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_data[chat_id] = {"step": STEP_CITY, "history": []}
    await update.message.reply_text(
        "🔄 <b>Начнём сначала</b>\nНапиши /start",
        parse_mode="HTML", reply_markup=REMOVE
    )

async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    if update.effective_user.username == "fake_smm":
        OWNER_CHAT_ID = update.effective_chat.id
        await update.message.reply_text(f"✅ Готово. Chat ID: <code>{OWNER_CHAT_ID}</code>", parse_mode="HTML")
    else:
        await update.message.reply_text("❌ Нет доступа.")

# ── ОБРАБОТКА СООБЩЕНИЙ ──────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    ud = get_user(chat_id)

    if update.message.contact:
        text = update.message.contact.phone_number
        if not text.startswith("+"):
            text = "+" + text
    else:
        text = update.message.text

    await process_step(update, context, chat_id, ud, text)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.from_user.id
    ud = get_user(chat_id)

    car_map = {
        "car_econom":  "Эконом",
        "car_comfort": "Комфорт",
        "car_suv":     "SUV",
        "car_minivan": "Минивэн",
    }
    car = car_map.get(query.data, query.data)

    # Редактируем сообщение с кнопками — показываем выбор
    await query.edit_message_text(
        f"{query.message.text}\n\n<b>✔ Выбрано: {car}</b>",
        parse_mode="HTML"
    )

    # Создаём фейковый update с текстом выбора
    ud["car"] = car
    ud["step"] = STEP_NAME

    ud["history"].append({"role": "user", "content": f"Тип авто: {car}"})
    ai = call_groq(ud["history"])
    ud["history"].append({"role": "assistant", "content": ai})

    text = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{progress(3)}\n\n"
        f"{ai}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=REMOVE)

async def process_step(update, context, chat_id, ud, text):
    step = ud["step"]
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        if step == STEP_CITY:
            ud["city"] = text
            ud["step"] = STEP_DATES
            ud["history"].append({"role": "user", "content": f"Город: {text}"})
            ai = call_groq(ud["history"])
            ud["history"].append({"role": "assistant", "content": ai})
            msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{progress(1)}\n\n"
                f"{ai}\n\n"
                f"<i>⚡️ Минимальный срок аренды — 3 суток</i>\n\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=REMOVE)

        elif step == STEP_DATES:
            ud["dates"] = text
            ud["step"] = STEP_CAR
            ud["history"].append({"role": "user", "content": f"Даты: {text}"})
            ai = call_groq(ud["history"])
            ud["history"].append({"role": "assistant", "content": ai})
            msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{progress(2)}\n\n"
                f"{ai}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=CAR_INLINE)

        elif step == STEP_CAR:
            # Если написал текстом (не нажал кнопку)
            ud["car"] = text
            ud["step"] = STEP_NAME
            ud["history"].append({"role": "user", "content": f"Тип авто: {text}"})
            ai = call_groq(ud["history"])
            ud["history"].append({"role": "assistant", "content": ai})
            msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{progress(3)}\n\n"
                f"{ai}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=REMOVE)

        elif step == STEP_NAME:
            ud["name"] = text
            ud["step"] = STEP_PHONE
            ud["history"].append({"role": "user", "content": f"Имя: {text}"})
            ai = call_groq(ud["history"])
            ud["history"].append({"role": "assistant", "content": ai})
            msg = (
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{progress(4)}\n\n"
                f"{ai}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(msg, parse_mode="HTML", reply_markup=PHONE_KEYBOARD)

        elif step == STEP_PHONE:
            ud["phone"] = text
            ud["step"] = STEP_DONE

            summary = (
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"{progress(5)}\n\n"
                "✅ <b>Заявка принята!</b>\n\n"
                f"📍 <b>Город:</b> {ud.get('city','—')}\n"
                f"📅 <b>Даты:</b> {ud.get('dates','—')}\n"
                f"🚗 <b>Авто:</b> {ud.get('car','—')}\n"
                f"👤 <b>Имя:</b> {ud.get('name','—')}\n"
                f"📱 <b>Телефон:</b> {ud.get('phone','—')}\n\n"
                "⏱ Менеджер свяжется с вами\n"
                "в течение <b>15 минут</b> 🙌\n\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await update.message.reply_text(summary, parse_mode="HTML", reply_markup=REMOVE)
            await send_lead(update, context, chat_id, ud)

        elif step == STEP_DONE:
            await update.message.reply_text(
                "✅ <b>Ваша заявка уже принята!</b>\n\n"
                "Менеджер скоро свяжется с вами.\n\n"
                "Для новой заявки → /start",
                parse_mode="HTML", reply_markup=REMOVE
            )

    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text(
            "⚠️ Технический сбой. Напишите нам: @weonerent",
            reply_markup=REMOVE
        )

# ── ОТПРАВКА ЗАЯВКИ ВЛАДЕЛЬЦУ ─────────────────────────────────────────────
async def send_lead(update, context, client_chat_id, ud):
    user = update.effective_user
    username = f"@{user.username}" if user.username else f"ID: {client_chat_id}"

    lead = (
        "🔔 <b>НОВАЯ ЗАЯВКА — WeOneRent</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {user.full_name or '—'}  {username}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>Город:</b> {ud.get('city','—')}\n"
        f"📅 <b>Даты:</b> {ud.get('dates','—')}\n"
        f"🚗 <b>Авто:</b> {ud.get('car','—')}\n"
        f"👤 <b>Имя:</b> {ud.get('name','—')}\n"
        f"📱 <b>Телефон:</b> {ud.get('phone','—')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 tg://user?id={client_chat_id}"
    )
    try:
        await context.bot.send_message(chat_id=OWNER_CHAT_ID, text=lead, parse_mode="HTML")
        logger.info(f"✅ Заявка от {username} → отправлена владельцу")
    except Exception as e:
        logger.error(f"❌ Ошибка отправки заявки: {e}")

# ── MAIN ─────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.CONTACT, handle_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Бот запущен...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
