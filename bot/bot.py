"""Релей-бот: посетители сайта пишут боту, бот пересылает владельцу.

Переменные окружения:
  BOT_TOKEN      — токен от @BotFather
  OWNER_CHAT_ID  — числовой Telegram id владельца (узнать у @userinfobot)
  DATA_DIR       — каталог для threads.json (по умолчанию ./data)

Схема работы: на каждое сообщение посетителя бот шлёт владельцу «шапку»
(имя, @username, id, время) и копию самого сообщения. Оба id сообщений
запоминаются в threads.json, поэтому reply владельца на любое из них
уходит отправителю — так работает двусторонняя переписка без публикации
личного аккаунта.
"""

import json
import logging
import os
import secrets
import tempfile
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"])
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
STATE_FILE = DATA_DIR / "threads.json"
MAX_THREADS = 5000

WELCOME = (
    "Привет! Это бот для связи с Даниилом.\n\n"
    "Просто напишите сообщение — я передам его, а ответ придёт вам в этот чат."
)
FIRST_SENT = "Сообщение передано — ответ придёт вам в этот чат."

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("relay-bot")


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"threads": {}, "greeted": []}


def save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # атомарная запись: сначала во временный файл, потом rename —
    # иначе при падении контейнера останется обрезанный JSON
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def remember(state: dict, owner_msg_id: int, chat_id: int) -> None:
    threads = state["threads"]
    threads[str(owner_msg_id)] = chat_id
    # файл не должен расти бесконечно: храним последние MAX_THREADS записей
    if len(threads) > MAX_THREADS:
        for key in list(threads)[: len(threads) - MAX_THREADS]:
            del threads[key]
    save_state(state)


def is_owner(update: Update) -> bool:
    return (
        update.effective_user is not None
        and update.effective_user.id == OWNER_CHAT_ID
        and update.effective_chat is not None
        and update.effective_chat.id == OWNER_CHAT_ID
    )


def finish_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Завершить ответ", callback_data=f"finish:{token}")]
    ])


async def select_recipient(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Кнопка доступна только владельцу.", show_alert=True)
        return
    target = load_state()["threads"].get(str(query.message.message_id))
    if target is None:
        await query.answer("Сообщение слишком старое: получатель не найден.", show_alert=True)
        return
    await query.answer()
    ctx.user_data.pop("recipient", None)
    token = secrets.token_hex(8)
    await query.message.reply_text(
        f"Режим ответа:\n{query.message.text}\n\n"
        f"Все следующие сообщения отправлю пользователю id: {target}. "
        "Жду текст, стикер, фото, голосовое или файл. "
        "Можно отправлять несколько сообщений подряд.\n"
        "Чтобы остановиться, нажмите «Завершить ответ» или /cancel.",
        reply_markup=finish_keyboard(token),
    )
    ctx.user_data["recipient"] = {"chat_id": target, "token": token}


async def finish_reply(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not is_owner(update):
        await query.answer("Кнопка доступна только владельцу.", show_alert=True)
        return
    recipient = ctx.user_data.get("recipient")
    if not recipient or query.data != f"finish:{recipient['token']}":
        await query.answer("Этот режим ответа уже завершён или выбран другой получатель.")
        return
    ctx.user_data.pop("recipient", None)
    await query.answer("Режим ответа завершён.")
    await query.message.reply_text("Ответ завершён. Для нового ответа выберите «Ответить».")


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        ctx.user_data.pop("recipient", None)
        await update.message.reply_text("Режим ответа выключен. Сообщения больше не отправляются.")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if is_owner(update):
        ctx.user_data.pop("recipient", None)
        await update.message.reply_text(
            "Это ваш бот-приёмник. Нажмите «Ответить» под шапкой сообщения "
            "и отправляйте текст, стикеры, фото или файлы без reply. "
            "Режим действует до кнопки «Завершить ответ» или /cancel.\n"
            "Сейчас режим ответа выключен. Обычный reply также работает. "
            "Отправить текст по id: /send <id> <текст>."
        )
    else:
        await update.message.reply_text(WELCOME)


async def cmd_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != OWNER_CHAT_ID:
        return
    args = ctx.args or []
    if len(args) < 2 or not args[0].lstrip("-").isdigit():
        await update.message.reply_text("Формат: /send <id> <текст>")
        return
    try:
        await ctx.bot.send_message(int(args[0]), " ".join(args[1:]))
        await update.message.reply_text("✓ Доставлено")
    except TelegramError as e:
        await update.message.reply_text(f"Не доставлено: {e.message}")


async def relay_to_owner(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    user = update.effective_user
    state = load_state()

    username = f"@{user.username}" if user.username else "—"
    header = (
        f"📩 {user.full_name} ({username})\n"
        f"id: {msg.chat_id} · {msg.date:%Y-%m-%d %H:%M} UTC"
    )
    sent = await ctx.bot.send_message(
        OWNER_CHAT_ID, header,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Ответить", callback_data="reply")]
        ]),
    )
    remember(state, sent.message_id, msg.chat_id)
    copied = await ctx.bot.copy_message(OWNER_CHAT_ID, msg.chat_id, msg.message_id)
    remember(state, copied.message_id, msg.chat_id)

    # подтверждение посетителю — только один раз, чтобы не шуметь
    if msg.chat_id not in state["greeted"]:
        state["greeted"].append(msg.chat_id)
        save_state(state)
        await msg.reply_text(FIRST_SENT)


async def relay_to_visitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_owner(update):
        return
    msg = update.message
    replied = msg.reply_to_message
    recipient = ctx.user_data.get("recipient")
    target = (
        load_state()["threads"].get(str(replied.message_id))
        if replied else recipient["chat_id"] if recipient else None
    )
    if target is None:
        await msg.reply_text(
            "Получатель не выбран или сообщение слишком старое. "
            "Нажмите «Ответить» под шапкой сообщения посетителя "
            "или сделайте reply на его сообщение."
        )
        return
    try:
        await ctx.bot.copy_message(target, OWNER_CHAT_ID, msg.message_id)
    except TelegramError as e:
        await msg.reply_text(f"Не доставлено: {e.message}. Можно повторить отправку.")
        return
    await msg.reply_text(
        f"Доставлено пользователю id: {target}.",
        reply_markup=finish_keyboard(recipient["token"]) if recipient else None,
    )


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("send", cmd_send))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(select_recipient, pattern=r"^reply$"))
    app.add_handler(CallbackQueryHandler(finish_reply, pattern=r"^finish:[0-9a-f]{16}$"))
    # в группе обрабатывает только первый подошедший handler,
    # поэтому ветка владельца должна стоять раньше общей
    app.add_handler(
        MessageHandler(
            filters.Chat(OWNER_CHAT_ID) & filters.User(OWNER_CHAT_ID)
            & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            relay_to_visitor,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.Chat(OWNER_CHAT_ID)
            & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            relay_to_owner,
        )
    )
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
