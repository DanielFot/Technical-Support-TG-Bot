"""Run this file in PyCharm or with: python app.py."""

import argparse
import logging
import sqlite3

from telegram import Update
from telegram.ext import Application, MessageHandler, filters

from support_bot.bot import build_application
from support_bot.config import load_settings


def main():
    parser = argparse.ArgumentParser(description="Everything Desk support bot")
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Only show user IDs, to configure staff before launch",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # HTTP client INFO logs include bot tokens in request URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = load_settings(require_staff=not args.setup)
        if args.setup:
            application = Application.builder().token(settings.token).build()

            async def identify(update, context):
                await update.message.reply_text(
                    f"Setup mode. Your Telegram user ID: {update.effective_user.id}\n"
                    "Add authorized IDs to .env, stop setup mode, and run python app.py."
                )

            application.add_handler(MessageHandler(filters.ChatType.PRIVATE, identify))
        else:
            application = build_application(settings)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise SystemExit(f"Configuration error: {exc}") from None
    print("Everything Desk is starting. Press Ctrl+C to stop.")
    application.run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
