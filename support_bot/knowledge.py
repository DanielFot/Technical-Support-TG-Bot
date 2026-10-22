"""The six supplied FAQs and conservative, transparent keyword routing."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class FAQ:
    key: str
    label: str
    question: str
    answer: str


FAQS = (
    FAQ(
        "order",
        "Place an order",
        "How do I place an order?",
        'To place an order, please select the item you are interested in and click the "Add to cart" button. Then, go to your cart and follow the instructions to complete your purchase.',
    ),
    FAQ(
        "status",
        "Track my order",
        "How can I check the status of my order?",
        'You can check the status of your order by logging into your account on our website and navigating to the "My orders" section. Your current order status will be displayed there.',
    ),
    FAQ(
        "cancel",
        "Cancel an order",
        "How do I cancel an order?",
        "If you wish to cancel an order, please contact our customer support team as soon as possible. We will do our best to assist you with the cancellation before the order is shipped.",
    ),
    FAQ(
        "damage",
        "Damaged item",
        "What should I do if an item arrives damaged?",
        "If you receive a damaged item, please contact our customer support team immediately and provide photographs of the damage. We will assist you with an exchange or a return.",
    ),
    FAQ(
        "contact",
        "Contact support",
        "How can I contact your technical support team?",
        "You can reach our technical support team by calling the number provided on our website or by messaging us via our chatbot.",
    ),
    FAQ(
        "delivery",
        "Delivery information",
        "Where can I find information about delivery?",
        "You can find delivery information on the checkout page of our website. It lists the available delivery methods and estimated timeframes.",
    ),
)
FAQ_BY_KEY = {faq.key: faq for faq in FAQS}

GUIDES = {
    "website": (
        "Website not working",
        "Refresh the page once and check your internet connection. Try a private browser window or a different browser. If the issue remains, send the page address, device/browser and exact error to the programming team. Remove private information from screenshots.",
    ),
    "payment": (
        "Payment error",
        "Check your bank or payment app for a pending or completed charge before trying again. Do not repeatedly submit payment or share card numbers, security codes or verification codes. If you were charged without an order confirmation, send the order reference (if available), time and displayed error to the programming team.",
    ),
    "login": (
        "Cannot sign in",
        "Check that you are using the email address for your store account. Use the website's password-reset option if needed. Never send your password or a reset/verification code here. If signing in still fails, send the displayed error and your browser/device to the programming team.",
    ),
}


def find_guide(text: str) -> str | None:
    tokens = words(text)
    if tokens & {"payment", "charged", "card"}:
        return "payment"
    if tokens & {"login", "password"} or {"sign", "in"} <= tokens:
        return "login"
    if tokens & {"website", "site", "crash", "bug"}:
        return "website"
    return None


def words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def match_faq(text: str) -> FAQ | None:
    normalized = " ".join(re.findall(r"[a-z0-9]+", text.lower()))
    for faq in FAQS:
        if normalized == " ".join(re.findall(r"[a-z0-9]+", faq.question.lower())):
            return faq
    tokens = words(text)
    # Problems should reach specialists rather than an irrelevant shopping FAQ.
    if tokens & {
        "error",
        "failed",
        "failing",
        "crash",
        "crashing",
        "broken",
        "bug",
        "charged",
    }:
        return None
    if tokens & {"damaged", "damage", "smashed", "cracked"}:
        key = "damage"
    elif tokens & {"cancel", "cancellation"} and tokens & {"order", "purchase"}:
        key = "cancel"
    elif tokens & {"track", "tracking", "status", "where"} and tokens & {
        "order",
        "parcel",
        "package",
    }:
        key = "status"
    elif tokens & {"delivery", "shipping", "timeframes"}:
        key = "delivery"
    elif tokens & {"place", "buy", "purchase"} and tokens & {
        "order",
        "item",
        "product",
        "how",
    }:
        key = "order"
    elif tokens & {"contact", "reach", "phone"} and tokens & {"support", "team", "you"}:
        key = "contact"
    else:
        return None
    return FAQ_BY_KEY[key]


def suggest_department(text: str) -> str | None:
    tokens = words(text)
    technical = tokens & {
        "website",
        "site",
        "payment",
        "checkout",
        "login",
        "password",
        "bug",
        "error",
        "crash",
        "charged",
        "card",
    }
    sales = tokens & {
        "product",
        "item",
        "size",
        "stock",
        "return",
        "refund",
        "damaged",
        "damage",
        "delivery",
        "order",
        "cancel",
        "exchange",
    }
    if technical:
        return "programming"
    if sales:
        return "sales"
    return None
