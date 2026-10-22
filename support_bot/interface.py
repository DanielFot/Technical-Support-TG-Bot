"""Telegram-native shop interface: clear headings, compact cards, no emoji."""

from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup

DEPARTMENTS = {"programming": "Programming team", "sales": "Sales team"}
WELCOME = (
    "<b>EVERYTHING DESK</b>\n"
    "<i>We Sell Everything In The World</i>\n\n"
    "A little help for everything you buy.\n\n"
    "<b>01 / Quick answers</b>\nOrdering, delivery, returns and more.\n\n"
    "<b>02 / Specialist help</b>\nWebsite or payment trouble? Programming.\nProduct or order questions? Sales.\n\n"
    "<b>03 / Your tickets</b>\nFollow your request and continue the conversation.\n\n"
    "Choose below, or describe what you need."
)
PRIVACY = (
    "<b>YOUR SUPPORT DATA</b>\n\n"
    "FAQ browsing and ordinary FAQ questions are not saved to our database. "
    "When you confirm a support request, we store your Telegram user ID, "
    "ticket details, messages, attachment file IDs, status and UTC timestamps "
    "in the store's SQLite database. Authorized specialists receive the ticket.\n\n"
    "Voice notes and photos are relayed through Telegram; there is no automatic "
    "transcription or local media download. Telegram also processes your messages.\n\n"
    "Never send passwords, verification codes or full payment card details. "
    "Ask the store's administrator if you need your support records removed."
)


def keyboard(rows):
    return InlineKeyboardMarkup(
        [[Button(label, callback_data=data) for label, data in row] for row in rows]
    )


def menu(staff=False):
    rows = [
        [("Quick answers", "faq")],
        [("Talk to a specialist", "new")],
        [("My tickets", "list:mine:0")],
    ]
    if staff:
        rows.append([("Team inbox", "list:queue:0")])
    rows.append([("Your data", "privacy")])
    return keyboard(rows)


def departments():
    return keyboard(
        [
            [("Website / payment", "dept:programming")],
            [("Products / orders", "dept:sales")],
            [("Back to desk", "home")],
        ]
    )


def faq_menu(faqs):
    return keyboard(
        [[(faq.label, f"faq:{faq.key}")] for faq in faqs]
        + [[("Fix a common issue", "guides")], [("Back to desk", "home")]]
    )


def ticket_actions(ticket, staff=False):
    ticket_id = ticket["id"]
    rows = []
    if ticket["status"] == "closed":
        rows.append([("Reopen ticket", f"reopen:{ticket_id}")])
    else:
        rows.append([("Reply to ticket" if staff else "Add a message", f"write:{ticket_id}")])
        if staff:
            rows.append([("Mark resolved", f"close:{ticket_id}")])
    rows.extend([[("Refresh", f"ticket:{ticket_id}")], [("Back to desk", "home")]])
    return keyboard(rows)
