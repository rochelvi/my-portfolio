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
import tempfile
from pathlib import Path

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
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


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id == OWNER_CHAT_ID:
        await update.message.reply_text(
            "Это ваш бот-приёмник. Чтобы ответить посетителю, сделайте reply "
            "на его сообщение. Начать диалог первым: /send <id> <текст>."
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
    sent = await ctx.bot.send_message(OWNER_CHAT_ID, header)
    remember(state, sent.message_id, msg.chat_id)
    copied = await ctx.bot.copy_message(OWNER_CHAT_ID, msg.chat_id, msg.message_id)
    remember(state, copied.message_id, msg.chat_id)

    # подтверждение посетителю — только один раз, чтобы не шуметь
    if msg.chat_id not in state["greeted"]:
        state["greeted"].append(msg.chat_id)
        save_state(state)
        await msg.reply_text(FIRST_SENT)


async def relay_to_visitor(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.message
    replied = msg.reply_to_message
    if not replied:
        await msg.reply_text(
            "Чтобы ответить посетителю, сделайте reply на его сообщение "
            "(или начните диалог командой /send <id> <текст>)."
        )
        return
    target = load_state()["threads"].get(str(replied.message_id))
    if target is None:
        await msg.reply_text(
            "Не могу найти получателя: это не пересланное ботом сообщение "
            "или оно слишком старое."
        )
        return
    try:
        await ctx.bot.copy_message(target, OWNER_CHAT_ID, msg.message_id)
        await msg.reply_text("✓ Доставлено")
    except TelegramError as e:
        await msg.reply_text(f"Не доставлено: {e.message}")


def main() -> None:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("send", cmd_send))
    # в группе обрабатывает только первый подошедший handler,
    # поэтому ветка владельца должна стоять раньше общей
    app.add_handler(
        MessageHandler(filters.Chat(OWNER_CHAT_ID) & ~filters.COMMAND, relay_to_visitor)
    )
    app.add_handler(
        MessageHandler(~filters.COMMAND & ~filters.StatusUpdate.ALL, relay_to_owner)
    )
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
