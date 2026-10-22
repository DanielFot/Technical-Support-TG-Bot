"""Read configuration without putting credentials in source control."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def parse_ids(value: str) -> frozenset[int]:
    try:
        ids = frozenset(int(part.strip()) for part in value.split(",") if part.strip())
    except ValueError as exc:
        raise ValueError("Staff IDs must be comma-separated numeric user IDs.") from exc
    if any(user_id <= 0 for user_id in ids):
        raise ValueError("Staff IDs must be positive Telegram user IDs, not group IDs.")
    return ids


@dataclass(frozen=True)
class Settings:
    token: str
    database_path: Path
    admins: frozenset[int] = frozenset()
    programming: frozenset[int] = frozenset()
    sales: frozenset[int] = frozenset()

    def recipients(self, department: str) -> frozenset[int]:
        return self.admins | (self.programming if department == "programming" else self.sales)

    def is_staff(self, user_id: int) -> bool:
        return user_id in self.admins | self.programming | self.sales

    def can_access(self, user_id: int, department: str) -> bool:
        return user_id in self.recipients(department)


def load_settings(*, require_staff: bool = True) -> Settings:
    load_dotenv(ROOT / ".env")
    token = os.getenv("BOT_TOKEN", "").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        raise ValueError("Set BOT_TOKEN in .env to the token supplied by BotFather.")
    database_path = Path(os.getenv("DATABASE_PATH", "data/support.db"))
    settings = Settings(
        token,
        ROOT / database_path,
        parse_ids(os.getenv("ADMIN_IDS", "")),
        parse_ids(os.getenv("PROGRAMMING_IDS", "")),
        parse_ids(os.getenv("SALES_IDS", "")),
    )
    if require_staff and (
        not settings.recipients("programming") or not settings.recipients("sales")
    ):
        raise ValueError("Configure both department IDs, or ADMIN_IDS as a fallback for both.")
    return settings
