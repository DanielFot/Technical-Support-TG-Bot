"""Async Telegram handlers, with SQLite persistence before specialist delivery."""

import asyncio
import html
import logging
import secrets
import sqlite3
from contextlib import suppress

from telegram import BotCommand, Update
from telegram.constants import ChatType, ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, MessageHandler, filters

from .config import Settings
from .interface import (
    DEPARTMENTS,
    PRIVACY,
    WELCOME,
    departments,
    faq_menu,
    keyboard,
    menu,
    ticket_actions,
)
from .knowledge import (
    FAQ_BY_KEY,
    FAQS,
    GUIDES,
    find_guide,
    match_faq,
    suggest_department,
)
from .storage import Store

LOG = logging.getLogger(__name__)
COMMANDS = [
    BotCommand("start", "Open Everything Desk"),
    BotCommand("faq", "Browse quick answers"),
    BotCommand("support", "Start a specialist request"),
    BotCommand("tickets", "View your tickets"),
    BotCommand("cancel", "Cancel the current draft or reply"),
    BotCommand("help", "How to use the desk"),
    BotCommand("privacy", "How support data is stored"),
    BotCommand("whoami", "Show your Telegram user ID"),
]


def escaped(value):
    return html.escape(str(value))


def read_ticket_id(value):
    if not value.isascii() or not value.isdecimal() or len(value) > 10 or int(value) <= 0:
        raise ValueError("Use a positive ticket number, for example /ticket 12.")
    return int(value)


class Desk:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self.delivery_lock = asyncio.Lock()
        self.worker = None

    async def say(self, update, text, reply_markup=None):
        await update.effective_message.reply_text(
            text, parse_mode=ParseMode.HTML, reply_markup=reply_markup
        )

    def reset(self, context):
        for key in ("draft", "department", "manual_department", "writing"):
            context.user_data.pop(key, None)

    def accessible(self, user_id, ticket):
        return ticket and (
            ticket["user_id"] == user_id or self.settings.can_access(user_id, ticket["department"])
        )

    async def command(self, update, context):
        if update.effective_chat.type != ChatType.PRIVATE:
            await self.say(update, "Please open a private chat with this bot for support.")
            return
        command = update.message.text.split()[0].split("@")[0][1:].lower()
        args = update.message.text.split()[1:]
        user_id = update.effective_user.id
        staff = self.settings.is_staff(user_id)
        if command in {"start", "cancel"}:
            self.reset(context)
            prefix = (
                "Draft/reply cancelled. Saved tickets are still available.\n\n"
                if command == "cancel"
                else ""
            )
            await self.say(update, prefix + WELCOME, menu(staff))
        elif command == "faq":
            self.reset(context)
            await self.say(
                update,
                "<b>THE QUICK-ANSWER SHELF</b>\nChoose a question.",
                faq_menu(FAQS),
            )
        elif command == "support":
            self.reset(context)
            await self.say(
                update,
                "<b>FIND THE RIGHT SPECIALIST</b>\nWhich team can help?",
                departments(),
            )
        elif command == "privacy":
            await self.say(update, PRIVACY, menu(staff))
        elif command == "whoami":
            await self.say(
                update,
                f"Your Telegram user ID: <code>{user_id}</code>\nAdministrators can use this ID in .env.",
            )
        elif command in {"tickets", "queue"}:
            await self.list_tickets(update, context, "mine" if command == "tickets" else "queue", 0)
        elif command in {"ticket", "reply", "close", "reopen"}:
            try:
                if not args:
                    raise ValueError(
                        f"Usage: /{command} TICKET_NUMBER"
                        + (" your message" if command == "reply" else "")
                    )
                ticket_id = read_ticket_id(args[0])
                if command == "ticket":
                    await self.show_ticket(update, context, ticket_id)
                elif command == "reply":
                    ticket = self.store.get_ticket(ticket_id)
                    if not self.accessible(user_id, ticket):
                        await self.say(update, "Ticket unavailable or access denied.")
                        return
                    body = update.message.text.split(maxsplit=2)
                    if len(body) < 3:
                        await self.begin_reply(update, context, ticket_id)
                    else:
                        await self.save_reply(
                            update, context, ticket, {"kind": "text", "body": body[2]}
                        )
                else:
                    await self.change_status(update, context, ticket_id, command)
            except ValueError as exc:
                await self.say(update, escaped(exc))
        elif command == "help":
            text = (
                "<b>EVERYTHING DESK / FIELD GUIDE</b>\n\n"
                "Browse /faq or type a question. If you need a person, choose /support, "
                "pick a team, send a description and confirm the preview.\n\n"
                "Text, voice notes and photos are welcome. Voice notes are delivered to "
                "a person without transcription. For a damaged item, include a photo.\n\n"
                "Use /tickets to track requests, read recent history and add messages. "
                "Resolved tickets can be reopened. /cancel leaves the current draft or reply.\n\n"
                "Please share one issue per ticket. Do not send passwords or card details. "
                "We show help instructions, not live order tracking or refunds.\n\n"
                "Each draft holds one message or attachment. Add more after submitting. "
                "Unsubmitted drafts reset when the bot restarts."
            )
            if staff:
                text += (
                    "\n\n<b>TEAM TOOLS</b>\n/queue - department inbox\n/ticket 12 - ticket and recent history"
                    "\n/reply 12 message - send a reply\n/reply 12 - send a photo or voice reply next"
                    "\n/close 12 - resolve\n/reopen 12 - reopen\n"
                    "Your permissions come from the administrator's configuration."
                )
            await self.say(update, text, menu(staff))
        else:
            await self.say(
                update,
                "Unknown command. Choose a desk option or use /help.",
                menu(staff),
            )

    async def callback(self, update, context):
        query = update.callback_query
        await query.answer()
        if not query.message or query.message.chat.type != ChatType.PRIVATE:
            return
        data = query.data or ""
        user_id = update.effective_user.id
        staff = self.settings.is_staff(user_id)
        try:
            if data == "home":
                self.reset(context)
                await self.say(update, WELCOME, menu(staff))
            elif data == "privacy":
                await self.say(update, PRIVACY, menu(staff))
            elif data == "faq":
                self.reset(context)
                await self.say(
                    update,
                    "<b>THE QUICK-ANSWER SHELF</b>\nChoose a question.",
                    faq_menu(FAQS),
                )
            elif data.startswith("faq:"):
                faq = FAQ_BY_KEY.get(data[4:])
                if faq:
                    self.reset(context)
                    await self.answer_faq(update, faq)
            elif data == "guides":
                self.reset(context)
                await self.say(
                    update,
                    "<b>THE FIX-IT SHELF</b>\nTry these steps before opening a ticket.",
                    keyboard(
                        [[(title, f"guide:{key}")] for key, (title, _) in GUIDES.items()]
                        + [[("Quick answers", "faq")]]
                    ),
                )
            elif data.startswith("guide:") and data[6:] in GUIDES:
                self.reset(context)
                title, body = GUIDES[data[6:]]
                await self.say(
                    update,
                    f"<b>{title}</b>\n\n{body}",
                    keyboard(
                        [
                            [("Talk to programming", "dept:programming")],
                            [("More fixes", "guides")],
                        ]
                    ),
                )
            elif data == "new":
                self.reset(context)
                await self.say(
                    update,
                    "<b>FIND THE RIGHT SPECIALIST</b>\nWhich team can help?",
                    departments(),
                )
            elif data.startswith("dept:"):
                department = data[5:]
                if department not in DEPARTMENTS:
                    return
                context.user_data.pop("writing", None)
                context.user_data["department"] = department
                context.user_data["manual_department"] = True
                if "draft" in context.user_data:
                    await self.preview(update, context)
                else:
                    await self.say(
                        update,
                        f"<b>{DEPARTMENTS[department]}</b>\n\nDescribe one issue, send a voice note or attach a photo with a caption. "
                        "Include an order reference if relevant. Do not include payment card details.\n\n/cancel to leave.",
                    )
            elif data == "choose":
                await self.say(update, "Choose the team for this request.", departments())
            elif data.startswith("submit:"):
                draft = context.user_data.get("draft")
                if not draft or draft.get("preview_key") != data[7:]:
                    await self.say(
                        update,
                        "That preview has expired or was already submitted. Use /tickets or /support.",
                    )
                    return
                department = context.user_data.get("department")
                if department not in DEPARTMENTS:
                    await self.say(update, "Please select a team first.", departments())
                    return
                ticket_id = self.store.create_ticket(
                    draft["source_key"],
                    user_id,
                    department,
                    draft["payload"],
                    self.settings.recipients(department),
                )
                self.reset(context)
                await self.say(
                    update,
                    f"<b>REQUEST SAVED / #{ticket_id:04d}</b>\n\nTeam: {DEPARTMENTS[department]}\nStatus: open\n\n"
                    "Your request is saved in the team's inbox. Notifications will be retried if delivery is unavailable. "
                    "Replies arrive here; you can track this ticket below.",
                    keyboard(
                        [
                            [("Open my ticket", f"ticket:{ticket_id}")],
                            [("Back to desk", "home")],
                        ]
                    ),
                )
                await self.flush(context.bot)
            elif data.startswith("list:"):
                _, kind, page = data.split(":")
                if kind in {"mine", "queue", "closed"} and page.isdigit() and len(page) < 8:
                    await self.list_tickets(update, context, kind, int(page))
            else:
                action, value = data.split(":", 1)
                ticket_id = read_ticket_id(value)
                if action == "ticket":
                    await self.show_ticket(update, context, ticket_id)
                elif action == "write":
                    await self.begin_reply(update, context, ticket_id)
                elif action in {"close", "reopen"}:
                    await self.change_status(update, context, ticket_id, action)
        except ValueError as exc:
            await self.say(update, escaped(exc))

    async def answer_faq(self, update, faq):
        await self.say(
            update,
            f"<b>{escaped(faq.question)}</b>\n\n{escaped(faq.answer)}",
            keyboard(
                [
                    [("I still need a specialist", "new")],
                    [("More quick answers", "faq")],
                    [("Back to desk", "home")],
                ]
            ),
        )

    @staticmethod
    def payload(message):
        if message.voice:
            return {
                "kind": "voice",
                "body": message.caption or "Voice note (no automatic transcription).",
                "file_id": message.voice.file_id,
            }
        if message.photo:
            return {
                "kind": "photo",
                "body": message.caption or "Photo attachment.",
                "file_id": message.photo[-1].file_id,
            }
        if message.text:
            return {"kind": "text", "body": message.text}
        return None

    async def message(self, update, context):
        payload = self.payload(update.message)
        if not payload:
            await self.say(
                update,
                "Please send text, a Telegram voice note, or a photo. Use /support to start.",
            )
            return
        if len(payload["body"]) > 3000:
            await self.say(
                update,
                "Please shorten your message to 3,000 characters. You can add more messages after submitting.",
            )
            return
        if "writing" in context.user_data:
            ticket = self.store.get_ticket(context.user_data["writing"])
            if not self.accessible(update.effective_user.id, ticket):
                self.reset(context)
                await self.say(update, "Ticket unavailable or access denied.")
                return
            try:
                await self.save_reply(update, context, ticket, payload)
            except ValueError as exc:
                await self.say(update, escaped(exc))
            return
        if not context.user_data.get("manual_department"):
            context.user_data.pop("department", None)
        if "department" not in context.user_data and payload["kind"] == "text":
            faq = match_faq(payload["body"])
            if faq:
                self.reset(context)
                await self.answer_faq(update, faq)
                return
            if payload["body"].strip().lower() in {"hi", "hello", "hey"}:
                self.reset(context)
                await self.say(
                    update,
                    WELCOME,
                    menu(self.settings.is_staff(update.effective_user.id)),
                )
                return
        context.user_data["draft"] = {
            "message_id": update.message.message_id,
            "source_key": f"{update.effective_chat.id}:{update.message.message_id}",
            "payload": payload,
        }
        if "department" not in context.user_data:
            department = suggest_department(payload["body"])
            if department:
                context.user_data["department"] = department
        guide = find_guide(payload["body"]) if payload["kind"] == "text" else None
        if guide:
            title, body = GUIDES[guide]
            await self.say(
                update,
                f"<b>QUICK FIX / {title}</b>\n\n{body}\n\nStill need help? Review your request below.",
            )
        await self.preview(update, context)

    async def preview(self, update, context):
        draft = context.user_data["draft"]
        draft["preview_key"] = secrets.token_hex(6)
        department = context.user_data.get("department")
        team = DEPARTMENTS.get(department, "Please choose a team")
        text = f"<b>YOUR REQUEST / PREVIEW</b>\n\nTeam: {team}\nFormat: {draft['payload']['kind']}\n\n{escaped(draft['payload']['body'][:1200])}"
        text += "\n\nConfirm to save and share this request with a specialist. Sending another message replaces this draft. /cancel discards it."
        rows = []
        if department:
            rows.append([("Send request", f"submit:{draft['preview_key']}")])
        rows.extend(
            [
                [
                    (
                        "Choose a different team" if department else "Choose a team",
                        "choose",
                    )
                ],
                [("Discard draft", "home")],
            ]
        )
        await self.say(update, text, keyboard(rows))

    async def list_tickets(self, update, context, kind, page):
        self.reset(context)
        user_id = update.effective_user.id
        if kind != "mine" and not self.settings.is_staff(user_id):
            await self.say(update, "This command is for authorized specialists.")
            return
        allowed = tuple(key for key in DEPARTMENTS if self.settings.can_access(user_id, key))
        tickets = self.store.list_tickets(
            user_id=user_id if kind == "mine" else None,
            departments=allowed,
            offset=page * 8,
            closed=kind == "closed",
        )
        heading = (
            "MY TICKETS"
            if kind == "mine"
            else ("RESOLVED INBOX" if kind == "closed" else "TEAM INBOX")
        )
        text = f"<b>{heading}</b> / PAGE {page + 1}\n\n"
        if not tickets:
            text += "No tickets on this page."
        else:
            text += "Choose a ticket to read its latest messages.\n"
        rows = []
        for ticket in tickets:
            label = f"#{ticket['id']:04d} / {DEPARTMENTS[ticket['department']]} / {ticket['status'].replace('_', ' ')}"
            rows.append([(label, f"ticket:{ticket['id']}")])
        navigation = []
        if page:
            navigation.append(("Newer", f"list:{kind}:{page - 1}"))
        if len(tickets) == 8:
            navigation.append(("Older", f"list:{kind}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        if kind != "mine":
            rows.append(
                [
                    (
                        "Active inbox" if kind == "closed" else "Resolved inbox",
                        "list:queue:0" if kind == "closed" else "list:closed:0",
                    )
                ]
            )
        rows.append([("Back to desk", "home")])
        await self.say(update, text, keyboard(rows))

    async def show_ticket(self, update, context, ticket_id):
        ticket = self.store.get_ticket(ticket_id)
        user_id = update.effective_user.id
        if not self.accessible(user_id, ticket):
            await self.say(update, "Ticket unavailable or access denied.")
            return
        staff = (
            self.settings.can_access(user_id, ticket["department"]) and ticket["user_id"] != user_id
        )
        text = (
            f"<b>TICKET #{ticket_id:04d}</b>\n{DEPARTMENTS[ticket['department']]} / {ticket['status'].replace('_', ' ')}"
            f"\nCreated: {ticket['created_at']}\n\n<b>RECENT HISTORY</b> / last 8 entries\n"
        )
        for item in self.store.history(ticket_id):
            text += f"\n<b>{item['role'].title()}</b> / {item['created_at']}\n{escaped(item['body'][:230])}\n"
            if item["kind"] != "text":
                text += f"[{item['kind']} attachment]\n"
        if self.store.pending_for_ticket(ticket_id):
            text += (
                "\nSome notifications are awaiting delivery. The saved history is available here.\n"
            )
        await self.say(update, text, ticket_actions(ticket, staff))
        # Actual attachment playback, rather than a file ID customers cannot use.
        for item in self.store.history(ticket_id, limit=3):
            if item["file_id"]:
                await self.send_media(
                    context.bot,
                    user_id,
                    item["kind"],
                    item["file_id"],
                    f"Ticket #{ticket_id:04d} / {item['role']}",
                )

    async def begin_reply(self, update, context, ticket_id):
        ticket = self.store.get_ticket(ticket_id)
        if not self.accessible(update.effective_user.id, ticket):
            await self.say(update, "Ticket unavailable or access denied.")
            return
        if ticket["status"] == "closed":
            await self.say(
                update,
                "Reopen this ticket before adding a message.",
                ticket_actions(ticket),
            )
            return
        self.reset(context)
        context.user_data["writing"] = ticket_id
        await self.say(
            update,
            f"<b>REPLY / #{ticket_id:04d}</b>\nSend text, a voice note or a photo. Your next message will be saved and sent.\n/cancel to leave.",
        )

    async def save_reply(self, update, context, ticket, payload):
        if len(payload["body"]) > 3000:
            raise ValueError("Please shorten your reply to 3,000 characters.")
        user_id = update.effective_user.id
        customer = ticket["user_id"] == user_id
        recipients = (
            self.settings.recipients(ticket["department"]) - {user_id}
            if customer
            else {ticket["user_id"]}
        )
        self.store.add_message(
            ticket["id"],
            f"{update.effective_chat.id}:{update.message.message_id}",
            user_id,
            "customer" if customer else "staff",
            payload,
            recipients,
        )
        self.reset(context)
        await self.say(
            update,
            f"Message saved to ticket #{ticket['id']:04d}. Delivery is queued.",
            keyboard(
                [
                    [("Open ticket", f"ticket:{ticket['id']}")],
                    [("Back to desk", "home")],
                ]
            ),
        )
        await self.flush(context.bot)

    async def change_status(self, update, context, ticket_id, action):
        ticket = self.store.get_ticket(ticket_id)
        user_id = update.effective_user.id
        if not self.accessible(user_id, ticket):
            await self.say(update, "Ticket unavailable or access denied.")
            return
        if action == "close" and not self.settings.can_access(user_id, ticket["department"]):
            await self.say(
                update,
                "Only the assigned department's specialists can resolve tickets.",
            )
            return
        if action == "reopen" and ticket["status"] != "closed":
            await self.say(update, "This ticket is already active.")
            return
        recipients = (self.settings.recipients(ticket["department"]) | {ticket["user_id"]}) - {
            user_id
        }
        source = f"status:{update.update_id}"
        self.store.set_status(
            ticket_id,
            "closed" if action == "close" else "open",
            user_id,
            source,
            recipients,
        )
        await self.show_ticket(update, context, ticket_id)
        await self.flush(context.bot)

    @staticmethod
    async def send_media(bot, recipient, kind, file_id, caption):
        if kind == "voice":
            await bot.send_voice(recipient, file_id, caption=caption)
        elif kind == "photo":
            await bot.send_photo(recipient, file_id, caption=caption)

    async def flush(self, bot):
        async with self.delivery_lock:
            for item in self.store.pending():
                ticket = self.store.get_ticket(item["ticket_id"])
                recipient = item["recipient_id"]
                # Revoking staff permissions also stops pending ticket disclosures.
                if recipient != ticket["user_id"] and not self.settings.can_access(
                    recipient, ticket["department"]
                ):
                    self.store.mark_delivered(item["delivery_id"])
                    continue
                caption = f"Ticket #{item['ticket_id']:04d} / {DEPARTMENTS[item['department']]} / {item['role']}"
                self.store.mark_attempted(item["delivery_id"])
                try:
                    if item["kind"] == "text":
                        await bot.send_message(
                            recipient,
                            f"<b>{caption}</b>\n\n{escaped(item['body'])}",
                            parse_mode=ParseMode.HTML,
                            reply_markup=keyboard(
                                [[("Open ticket", f"ticket:{item['ticket_id']}")]]
                            ),
                        )
                    else:
                        await self.send_media(
                            bot,
                            recipient,
                            item["kind"],
                            item["file_id"],
                            f"{caption}\n{item['body'][:800]}\n/ticket {item['ticket_id']}",
                        )
                    self.store.mark_delivered(item["delivery_id"])
                except TelegramError as exc:
                    # Do not log payloads, tokens or exception URLs.
                    LOG.warning(
                        "Notification %s retained: %s",
                        item["delivery_id"],
                        type(exc).__name__,
                    )

    async def retry_worker(self, bot):
        while True:
            try:
                await self.flush(bot)
            except (sqlite3.Error, OSError) as exc:
                LOG.error("Outbox retry failed: %s", type(exc).__name__)
            await asyncio.sleep(60)

    async def post_init(self, application):
        try:
            await application.bot.set_my_commands(COMMANDS)
        except TelegramError as exc:
            LOG.warning("Command menu setup failed: %s", type(exc).__name__)
        self.worker = asyncio.create_task(self.retry_worker(application.bot))

    async def post_stop(self, application):
        if self.worker:
            self.worker.cancel()
            with suppress(asyncio.CancelledError):
                await self.worker

    async def error(self, update, context):
        LOG.error("Update failed: %s", type(context.error).__name__)
        if (
            isinstance(update, Update)
            and update.effective_message
            and update.effective_chat.type == ChatType.PRIVATE
        ):
            with suppress(TelegramError):
                await self.say(
                    update,
                    "The desk could not finish that action. Check /tickets before resending a request. Use /cancel if you need to start again.",
                )


def build_application(settings: Settings, store: Store | None = None, *, bot=None) -> Application:
    desk = Desk(settings, store if store is not None else Store(settings.database_path))
    builder = (
        Application.builder()
        .concurrent_updates(False)
        .post_init(desk.post_init)
        .post_stop(desk.post_stop)
    )
    if bot is None:
        builder.token(settings.token)
    else:
        builder.bot(bot)
    application = builder.build()
    application.bot_data["desk"] = desk
    application.add_handler(CallbackQueryHandler(desk.callback))
    application.add_handler(MessageHandler(filters.COMMAND, desk.command))
    application.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND, desk.message)
    )
    application.add_error_handler(desk.error)
    return application
