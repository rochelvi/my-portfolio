import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["BOT_TOKEN"] = "123456:test-token"
os.environ["OWNER_CHAT_ID"] = "100"

import bot
from telegram.error import TelegramError


class RelayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {"threads": {"10": 200, "11": 300}, "greeted": []}
        self.ctx = SimpleNamespace(
            user_data={},
            bot=SimpleNamespace(
                copy_message=AsyncMock(return_value=SimpleNamespace(message_id=21)),
                send_message=AsyncMock(return_value=SimpleNamespace(message_id=20)),
            ),
        )
        self.patcher = patch.object(bot, "load_state", return_value=self.state)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def update(self, owner=100, reply_id=None):
        message = SimpleNamespace(
            message_id=50,
            chat_id=owner,
            reply_to_message=(SimpleNamespace(message_id=reply_id) if reply_id else None),
            reply_text=AsyncMock(),
            date=datetime(2026, 9, 24, 12, 30, tzinfo=timezone.utc),
        )
        return SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(id=owner),
            effective_user=SimpleNamespace(id=owner, username="visitor", full_name="Visitor"),
        )

    def callback(self, message_id=10, owner=100, data="reply"):
        update = self.update(owner)
        update.callback_query = SimpleNamespace(
            data=data,
            message=SimpleNamespace(
                message_id=message_id, text="Visitor (@visitor)\nid: 200", reply_text=AsyncMock()
            ),
            answer=AsyncMock(),
        )
        return update

    async def select(self, message_id=10):
        update = self.callback(message_id)
        await bot.select_recipient(update, self.ctx)
        return update

    async def test_button_copies_multiple_messages_without_reply(self):
        await self.select()
        for message_id in (51, 52, 53):
            update = self.update()
            update.message.message_id = message_id
            await bot.relay_to_visitor(update, self.ctx)
            self.ctx.bot.copy_message.assert_awaited_with(200, 100, message_id)
        self.assertEqual(self.ctx.bot.copy_message.await_count, 3)

    async def test_switch_recipient(self):
        await self.select()
        await self.select(11)
        await bot.relay_to_visitor(self.update(), self.ctx)
        self.ctx.bot.copy_message.assert_awaited_once_with(300, 100, 50)

    async def test_explicit_reply_has_priority(self):
        await self.select()
        await bot.relay_to_visitor(self.update(reply_id=11), self.ctx)
        self.ctx.bot.copy_message.assert_awaited_once_with(300, 100, 50)

    async def test_unknown_reply_does_not_fall_back_to_selected_recipient(self):
        await self.select()
        await bot.relay_to_visitor(self.update(reply_id=999), self.ctx)
        self.ctx.bot.copy_message.assert_not_awaited()

    async def test_no_recipient_does_not_send(self):
        await bot.relay_to_visitor(self.update(), self.ctx)
        self.ctx.bot.copy_message.assert_not_awaited()

    async def test_cancel_command_clears_recipient(self):
        await self.select()
        await bot.cmd_cancel(self.update(), self.ctx)
        await bot.relay_to_visitor(self.update(), self.ctx)
        self.ctx.bot.copy_message.assert_not_awaited()

    async def test_finish_button_and_stale_button(self):
        first = await self.select()
        markup = first.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
        old_data = markup.inline_keyboard[0][0].callback_data
        second = await self.select(11)
        await bot.finish_reply(self.callback(data=old_data), self.ctx)
        await bot.relay_to_visitor(self.update(), self.ctx)
        self.ctx.bot.copy_message.assert_awaited_once_with(300, 100, 50)
        markup = second.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
        await bot.finish_reply(
            self.callback(data=markup.inline_keyboard[0][0].callback_data), self.ctx
        )
        self.ctx.bot.copy_message.reset_mock()
        await bot.relay_to_visitor(self.update(), self.ctx)
        self.ctx.bot.copy_message.assert_not_awaited()

    async def test_unauthorized_buttons_and_sends(self):
        await bot.select_recipient(self.callback(owner=999), self.ctx)
        self.assertEqual(self.ctx.user_data, {})
        await self.select()
        await bot.cmd_cancel(self.update(owner=999), self.ctx)
        await bot.relay_to_visitor(self.update(owner=999), self.ctx)
        self.ctx.bot.copy_message.assert_not_awaited()
        self.assertIn("recipient", self.ctx.user_data)

    async def test_unauthorized_finish_does_not_clear_recipient(self):
        selected = await self.select()
        markup = selected.callback_query.message.reply_text.call_args.kwargs["reply_markup"]
        data = markup.inline_keyboard[0][0].callback_data
        await bot.finish_reply(self.callback(owner=999, data=data), self.ctx)
        self.assertIn("recipient", self.ctx.user_data)

    async def test_start_clears_recipient(self):
        await self.select()
        await bot.cmd_start(self.update(), self.ctx)
        self.assertNotIn("recipient", self.ctx.user_data)

    async def test_real_message_types_use_copy_message(self):
        await self.select()
        contents = [
            {"text": "Text", "entities": [{"type": "bold", "offset": 0, "length": 4}]},
            {"text": "X", "entities": [
                {"type": "custom_emoji", "offset": 0, "length": 1, "custom_emoji_id": "123"}
            ]},
            {"sticker": {
                "file_id": "sticker", "file_unique_id": "s", "type": "regular",
                "width": 100, "height": 100, "is_animated": False, "is_video": False,
            }},
            {"photo": [{"file_id": "photo", "file_unique_id": "p", "width": 100, "height": 100}]},
            {"document": {"file_id": "document", "file_unique_id": "d"}},
            {"voice": {"file_id": "voice", "file_unique_id": "v", "duration": 1}},
        ]
        for content in contents:
            with self.subTest(content=next(iter(content))):
                update = bot.Update.de_json({
                    "update_id": 1,
                    "message": {
                        "message_id": 50, "date": 1,
                        "chat": {"id": 100, "type": "private"},
                        "from": {"id": 100, "is_bot": False, "first_name": "Owner"},
                        **content,
                    },
                }, self.ctx.bot)
                await bot.relay_to_visitor(update, self.ctx)
                self.ctx.bot.copy_message.assert_awaited_with(200, 100, 50)
        self.assertEqual(self.ctx.bot.copy_message.await_count, len(contents))

    def test_polling_receives_callbacks(self):
        with patch.object(bot.Application, "run_polling") as polling:
            bot.main()
        polling.assert_called_once_with(allowed_updates=["message", "callback_query"])

    async def test_expired_button_does_not_select(self):
        await self.select(999)
        self.assertEqual(self.ctx.user_data, {})

    async def test_failure_keeps_recipient_for_retry(self):
        await self.select()
        self.ctx.bot.copy_message.side_effect = TelegramError("Cannot copy")
        update = self.update()
        await bot.relay_to_visitor(update, self.ctx)
        self.assertIn("Не доставлено", update.message.reply_text.call_args.args[0])
        self.ctx.bot.copy_message.side_effect = None
        await bot.relay_to_visitor(update, self.ctx)
        self.ctx.bot.copy_message.assert_awaited_with(200, 100, 50)

    async def test_incoming_format_and_reply_button(self):
        with patch.object(bot, "save_state"):
            await bot.relay_to_owner(self.update(owner=200), self.ctx)
        args = self.ctx.bot.send_message.call_args
        self.assertEqual(args.args[0], 100)
        self.assertTrue(args.args[1].endswith("Visitor (@visitor)\nid: 200 · 2026-09-24 12:30 UTC"))
        button = args.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.text, "Ответить")
        self.assertEqual(button.callback_data, "reply")
        self.ctx.bot.copy_message.assert_awaited_once_with(100, 200, 50)
        self.assertEqual(self.state["threads"]["20"], 200)
        self.assertEqual(self.state["threads"]["21"], 200)


if __name__ == "__main__":
    unittest.main()
