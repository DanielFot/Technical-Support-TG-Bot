"""Exercise real PTB Update dispatch against an in-memory Telegram API fake."""

import json
import tempfile
import unittest
from pathlib import Path

from telegram import Update
from telegram.ext import ExtBot
from telegram.request import BaseRequest

from support_bot.bot import build_application
from support_bot.config import Settings
from support_bot.knowledge import FAQS
from support_bot.storage import Store


class TelegramAPI(BaseRequest):
    def __init__(self):
        self.calls = []
        self.blocked = set()

    @property
    def read_timeout(self):
        return 5

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **kwargs):
        operation = url.rsplit("/", 1)[-1]
        parameters = request_data.parameters if request_data else {}
        self.calls.append((operation, parameters))
        if parameters.get("chat_id") in self.blocked:
            return 403, json.dumps(
                {
                    "ok": False,
                    "error_code": 403,
                    "description": "Forbidden: bot was blocked",
                }
            ).encode()
        if operation == "getMe":
            result = {
                "id": 999,
                "is_bot": True,
                "first_name": "Everything Desk",
                "username": "everything_test_bot",
            }
        elif operation in {"sendMessage", "sendVoice", "sendPhoto"}:
            result = {
                "message_id": len(self.calls),
                "date": 1700000000,
                "chat": {"id": parameters["chat_id"], "type": "private"},
                "text": parameters.get("text", ""),
            }
        else:
            result = True
        return 200, json.dumps({"ok": True, "result": result}).encode()


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # SQLite fsyncs on Windows can exceed asyncio debug's 100 ms warning.
        import asyncio

        asyncio.get_running_loop().slow_callback_duration = 1
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "support.db")
        self.settings = Settings(
            "123456:TEST_TOKEN_FOR_OFFLINE_TESTS_ONLY",
            self.store.path,
            frozenset({90}),
            frozenset({20}),
            frozenset({30}),
        )
        self.api = TelegramAPI()
        bot = ExtBot(self.settings.token, request=self.api, get_updates_request=self.api)
        self.app = build_application(self.settings, self.store, bot=bot)
        self.errors = []

        async def record_error(update, context):
            self.errors.append(context.error)

        self.app.add_error_handler(record_error)
        await self.app.initialize()
        self.serial = 0

    async def asyncTearDown(self):
        await self.app.shutdown()
        self.temp.cleanup()
        self.assertEqual(self.errors, [], f"Unexpected handler errors: {self.errors}")

    async def send(self, text=None, user=10, *, callback=None, media=None, group=False):
        self.serial += 1
        sender = {"id": user, "first_name": "Test", "is_bot": False}
        message = {
            "message_id": self.serial,
            "date": 1700000000,
            "from": sender,
            "chat": {
                "id": -50 if group else user,
                "type": "group" if group else "private",
            },
        }
        if text is not None:
            message["text"] = text
            if text.startswith("/"):
                message["entities"] = [
                    {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}
                ]
        if media == "voice":
            message["voice"] = {
                "file_id": "voice-file",
                "file_unique_id": "voice-unique",
                "duration": 5,
            }
        if media == "photo":
            message["photo"] = [
                {
                    "file_id": "photo-file",
                    "file_unique_id": "photo-unique",
                    "width": 800,
                    "height": 600,
                }
            ]
        if callback:
            message["from"] = {"id": 999, "first_name": "Desk", "is_bot": True}
            message["text"] = "Menu"
            raw = {
                "update_id": self.serial,
                "callback_query": {
                    "id": str(self.serial),
                    "from": sender,
                    "chat_instance": "test-instance",
                    "data": callback,
                    "message": message,
                },
            }
        else:
            raw = {"update_id": self.serial, "message": message}
        update = Update.de_json(raw, self.app.bot)
        await self.app.process_update(update)
        return self.serial

    def text_for(self, user=10):
        return "\n".join(
            parameters.get("text", "")
            for operation, parameters in self.api.calls
            if operation == "sendMessage" and parameters.get("chat_id") == user
        )

    async def submit(self, user=10):
        draft = self.app.user_data[user]["draft"]
        await self.send(user=user, callback=f"submit:{draft['preview_key']}")
        return self.store.list_tickets(user_id=user)[0]["id"]

    async def create(self, text="The payment failed", user=10):
        await self.send(text, user)
        return await self.submit(user)

    async def test_welcome_and_all_faq_buttons_without_database_requests(self):
        await self.send("/start")
        self.assertIn("EVERYTHING DESK", self.text_for())
        for faq in FAQS:
            await self.send(callback=f"faq:{faq.key}")
            self.assertIn(faq.answer.replace('"', "&quot;"), self.text_for())
        self.assertEqual(self.store.list_tickets(user_id=10), [])

    async def test_plain_text_faq_is_automatic(self):
        await self.send("Where is my order?")
        self.assertIn("My orders", self.text_for())
        self.assertNotIn("draft", self.app.user_data[10])

    async def test_confirmed_technical_request_routes_only_to_programmers_and_admin(
        self,
    ):
        await self.send("My payment failed")
        self.assertEqual(self.store.list_tickets(user_id=10), [])
        ticket_id = await self.submit()
        self.assertEqual(self.store.get_ticket(ticket_id)["department"], "programming")
        self.assertIn("My payment failed", self.text_for(20))
        self.assertIn("My payment failed", self.text_for(90))
        self.assertEqual(self.text_for(30), "")

    async def test_voice_and_photo_reach_sales_and_are_persisted(self):
        for media, file_id in (("voice", "voice-file"), ("photo", "photo-file")):
            await self.send(callback="new")
            await self.send(callback="dept:sales")
            await self.send(media=media)
            ticket_id = await self.submit()
            self.assertEqual(self.store.history(ticket_id)[0]["file_id"], file_id)
            operation = "sendVoice" if media == "voice" else "sendPhoto"
            self.assertTrue(
                any(
                    name == operation and params["chat_id"] == 30 for name, params in self.api.calls
                )
            )

    async def test_unknown_text_requires_team_choice(self):
        await self.send("I have an unusual question")
        self.assertNotIn("department", self.app.user_data[10])
        await self.send(callback="dept:sales")
        self.assertEqual(self.store.get_ticket(await self.submit())["department"], "sales")

    async def test_cancel_and_expired_preview_do_not_create_tickets(self):
        await self.send("payment error")
        old = self.app.user_data[10]["draft"]["preview_key"]
        await self.send("/cancel")
        await self.send(callback=f"submit:{old}")
        self.assertEqual(self.store.list_tickets(user_id=10), [])

    async def test_old_preview_invalid_after_changing_team(self):
        await self.send("payment error")
        old = self.app.user_data[10]["draft"]["preview_key"]
        await self.send(callback="dept:sales")
        await self.send(callback=f"submit:{old}")
        self.assertEqual(self.store.list_tickets(user_id=10), [])
        self.assertEqual(self.store.get_ticket(await self.submit())["department"], "sales")

    async def test_duplicate_confirmation_does_not_duplicate_ticket(self):
        await self.send("payment error")
        key = self.app.user_data[10]["draft"]["preview_key"]
        await self.submit()
        await self.send(callback=f"submit:{key}")
        self.assertEqual(len(self.store.list_tickets(user_id=10)), 1)

    async def test_other_customers_and_wrong_department_cannot_read_reply_or_close(
        self,
    ):
        ticket_id = await self.create()
        for user in (11, 30):
            for command in (
                f"/ticket {ticket_id}",
                f"/reply {ticket_id} Intrusion",
                f"/close {ticket_id}",
            ):
                await self.send(command, user)
            await self.send(user=user, callback=f"write:{ticket_id}")
            self.assertIn("access denied", self.text_for(user))
            self.assertNotIn("The payment failed", self.text_for(user))
        self.assertEqual(len(self.store.history(ticket_id)), 1)
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "open")

    async def test_staff_reply_customer_followup_resolution_and_reopen(self):
        ticket_id = await self.create()
        await self.send(f"/reply {ticket_id} Please try checkout again", 20)
        self.assertIn("Please try checkout again", self.text_for(10))
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "in_progress")
        await self.send(callback=f"write:{ticket_id}")
        await self.send("It still fails")
        self.assertIn("It still fails", self.text_for(20))
        await self.send(f"/close {ticket_id}", 20)
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "closed")
        await self.send(callback=f"reopen:{ticket_id}")
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "open")

    async def test_staff_can_send_voice_reply(self):
        ticket_id = await self.create()
        await self.send(f"/reply {ticket_id}", 20)
        await self.send(user=20, media="voice")
        self.assertEqual(self.store.history(ticket_id)[-1]["role"], "staff")
        self.assertTrue(
            any(op == "sendVoice" and params["chat_id"] == 10 for op, params in self.api.calls)
        )

    async def test_blocked_staff_delivery_persists_and_retries_after_restart(self):
        self.api.blocked.add(20)
        ticket_id = await self.create()
        self.assertEqual(self.store.pending_for_ticket(ticket_id), 1)
        restarted = Store(self.store.path)
        self.assertEqual(restarted.get_ticket(ticket_id)["status"], "open")
        self.api.blocked.clear()
        await self.app.bot_data["desk"].flush(self.app.bot)
        self.assertEqual(self.store.pending_for_ticket(ticket_id), 0)
        self.assertIn("The payment failed", self.text_for(20))

    async def test_queue_is_restricted_and_department_filtered(self):
        await self.create()
        await self.send("/queue", 11)
        self.assertIn("authorized specialists", self.text_for(11))
        await self.send("/queue", 30)
        self.assertIn("No tickets", self.text_for(30))
        await self.send("/queue", 20)
        self.assertIn("TEAM INBOX", self.text_for(20))

    async def test_bad_ids_unknown_commands_and_unsupported_media(self):
        await self.send("/ticket nope")
        await self.send("/something")
        await self.send()
        self.assertIn("positive ticket number", self.text_for())
        self.assertIn("Unknown command", self.text_for())
        self.assertIn("Please send text", self.text_for())

    async def test_group_support_does_not_save_private_data(self):
        await self.send("/support", group=True)
        await self.send("payment error", group=True)
        self.assertEqual(self.store.list_tickets(user_id=10), [])

    async def test_long_input_and_html_are_handled(self):
        await self.send("x" * 3001)
        self.assertNotIn("draft", self.app.user_data[10])
        await self.send("payment error <script>alert(1)</script>")
        await self.submit()
        self.assertIn("&lt;script&gt;", self.text_for(20))

    async def test_startup_menu_and_background_worker_shutdown(self):
        desk = self.app.bot_data["desk"]
        await desk.post_init(self.app)
        self.assertIsNotNone(desk.worker)
        await desk.post_stop(self.app)
        self.assertTrue(desk.worker.cancelled())

    async def test_replacing_draft_refreshes_automatic_department(self):
        await self.send("payment error")
        self.assertEqual(self.app.user_data[10]["department"], "programming")
        await self.send("Need a product exchange")
        self.assertEqual(self.app.user_data[10]["department"], "sales")

    async def test_customer_cannot_close_ticket(self):
        ticket_id = await self.create()
        await self.send(f"/close {ticket_id}")
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "open")
        self.assertIn("Only the assigned", self.text_for())

    async def test_revoked_staff_does_not_receive_pending_private_data(self):
        self.api.blocked.add(20)
        ticket_id = await self.create()
        self.api.blocked.clear()
        self.api.calls.clear()
        desk = self.app.bot_data["desk"]
        desk.settings = Settings(
            self.settings.token,
            self.store.path,
            frozenset({90}),
            frozenset({21}),
            frozenset({30}),
        )
        await desk.flush(self.app.bot)
        self.assertEqual(self.store.pending_for_ticket(ticket_id), 0)
        self.assertNotIn("The payment failed", self.text_for(20))

    async def test_resolved_ticket_rejects_customer_reply_until_reopened(self):
        ticket_id = await self.create()
        await self.send(f"/close {ticket_id}", 20)
        before = len(self.store.history(ticket_id))
        await self.send(f"/reply {ticket_id} extra")
        self.assertEqual(len(self.store.history(ticket_id)), before)
        self.assertIn("closed", self.text_for())

    async def test_manual_department_choice_is_kept_for_new_draft_text(self):
        await self.send(callback="dept:sales")
        await self.send("Payment issue that sales asked me to send")
        self.assertEqual(self.app.user_data[10]["department"], "sales")

    async def test_troubleshooting_buttons_and_automatic_payment_guide(self):
        await self.send(callback="guides")
        await self.send(callback="guide:login")
        self.assertIn("password-reset", self.text_for())
        await self.send("My payment failed")
        self.assertIn("Do not repeatedly submit payment", self.text_for())
        self.assertEqual(self.store.list_tickets(user_id=10), [])


if __name__ == "__main__":
    unittest.main()
