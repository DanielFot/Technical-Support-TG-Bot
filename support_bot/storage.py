"""SQLite ticket history and a durable notification outbox.

Every method owns a short transaction; no connection survives a network await.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS specialist_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL,
    department TEXT NOT NULL CHECK(department IN ('programming', 'sales')),
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'in_progress', 'closed')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES specialist_requests(id),
    source_key TEXT NOT NULL UNIQUE,
    sender_id INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('customer', 'staff', 'system')),
    kind TEXT NOT NULL CHECK(kind IN ('text', 'voice', 'photo')),
    body TEXT NOT NULL DEFAULT '',
    file_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS notification_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL REFERENCES ticket_messages(id),
    recipient_id INTEGER NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0 CHECK(delivered IN (0, 1)),
    attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE(message_id, recipient_id)
);
CREATE INDEX IF NOT EXISTS idx_ticket_owner ON specialist_requests(user_id, id);
CREATE INDEX IF NOT EXISTS idx_queue ON specialist_requests(department, status, id);
CREATE INDEX IF NOT EXISTS idx_history ON ticket_messages(ticket_id, id);
CREATE INDEX IF NOT EXISTS idx_outbox ON notification_outbox(delivered, id);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _message(db, ticket_id, source_key, sender_id, role, payload, recipients):
        cursor = db.execute(
            "INSERT OR IGNORE INTO ticket_messages(ticket_id, source_key, sender_id, role, kind, body, file_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ticket_id,
                source_key,
                sender_id,
                role,
                payload["kind"],
                payload.get("body", ""),
                payload.get("file_id"),
            ),
        )
        if cursor.rowcount:
            db.executemany(
                "INSERT INTO notification_outbox(message_id, recipient_id) VALUES (?, ?)",
                [(cursor.lastrowid, recipient) for recipient in recipients],
            )

    def create_ticket(self, source_key, user_id, department, payload, recipients):
        with self.connect() as db:
            # Duplicate Telegram updates cannot create duplicate requests.
            existing = db.execute(
                "SELECT id FROM specialist_requests WHERE source_key=?", (source_key,)
            ).fetchone()
            if existing:
                return existing["id"]
            if (
                db.execute(
                    "SELECT COUNT(*) FROM specialist_requests WHERE user_id=? AND status!='closed'",
                    (user_id,),
                ).fetchone()[0]
                >= 5
            ):
                raise ValueError(
                    "You already have five active tickets. Open My tickets to add a message to an existing request."
                )
            cursor = db.execute(
                "INSERT INTO specialist_requests(source_key, user_id, department) VALUES (?, ?, ?)",
                (source_key, user_id, department),
            )
            ticket_id = cursor.lastrowid
            self._message(db, ticket_id, source_key, user_id, "customer", payload, recipients)
            return ticket_id

    def get_ticket(self, ticket_id):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM specialist_requests WHERE id=?", (ticket_id,)
            ).fetchone()

    def list_tickets(self, *, user_id=None, departments=(), offset=0, closed=False):
        with self.connect() as db:
            if user_id is not None:
                return db.execute(
                    "SELECT * FROM specialist_requests WHERE user_id=? ORDER BY id DESC LIMIT 8 OFFSET ?",
                    (user_id, offset),
                ).fetchall()
            if not departments:
                return []
            placeholders = ",".join("?" for _ in departments)
            return db.execute(
                f"SELECT * FROM specialist_requests WHERE department IN ({placeholders}) AND status {'=' if closed else '!='} 'closed' ORDER BY id DESC LIMIT 8 OFFSET ?",
                (*departments, offset),
            ).fetchall()

    def history(self, ticket_id, limit=8):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM (SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY id DESC LIMIT ?) ORDER BY id",
                (ticket_id, limit),
            ).fetchall()

    def add_message(self, ticket_id, source_key, sender_id, role, payload, recipients):
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM ticket_messages WHERE source_key=?", (source_key,)
            ).fetchone():
                return
            ticket = db.execute(
                "SELECT status FROM specialist_requests WHERE id=?", (ticket_id,)
            ).fetchone()
            if not ticket or ticket["status"] == "closed":
                raise ValueError(
                    "This ticket is closed. Open its ticket card and choose Reopen first."
                )
            self._message(db, ticket_id, source_key, sender_id, role, payload, recipients)
            db.execute(
                "UPDATE specialist_requests SET status=CASE WHEN ?='staff' THEN 'in_progress' ELSE status END, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                (role, ticket_id),
            )

    def set_status(self, ticket_id, status, sender_id, source_key, recipients):
        with self.connect() as db:
            if db.execute(
                "SELECT 1 FROM ticket_messages WHERE source_key=?", (source_key,)
            ).fetchone():
                return
            ticket = db.execute(
                "SELECT status, user_id FROM specialist_requests WHERE id=?", (ticket_id,)
            ).fetchone()
            if ticket and ticket["status"] != status:
                if status == "open" and ticket["status"] == "closed":
                    active = db.execute(
                        "SELECT COUNT(*) FROM specialist_requests WHERE user_id=? AND status!='closed'",
                        (ticket["user_id"],),
                    ).fetchone()[0]
                    if active >= 5:
                        raise ValueError(
                            "This customer already has five active tickets. Please resolve one first."
                        )
                db.execute(
                    "UPDATE specialist_requests SET status=?, updated_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id=?",
                    (status, ticket_id),
                )
                self._message(
                    db,
                    ticket_id,
                    source_key,
                    sender_id,
                    "system",
                    {
                        "kind": "text",
                        "body": f"Ticket status changed to {status.replace('_', ' ')}.",
                    },
                    recipients,
                )

    def pending(self, limit=50):
        with self.connect() as db:
            return db.execute(
                "SELECT o.id AS delivery_id, o.recipient_id, m.*, t.department, t.status FROM notification_outbox o JOIN ticket_messages m ON m.id=o.message_id JOIN specialist_requests t ON t.id=m.ticket_id WHERE o.delivered=0 ORDER BY o.attempts, o.id LIMIT ?",
                (limit,),
            ).fetchall()

    def mark_attempted(self, delivery_id):
        with self.connect() as db:
            db.execute(
                "UPDATE notification_outbox SET attempts=attempts+1 WHERE id=?",
                (delivery_id,),
            )

    def mark_delivered(self, delivery_id):
        with self.connect() as db:
            db.execute("UPDATE notification_outbox SET delivered=1 WHERE id=?", (delivery_id,))

    def pending_for_ticket(self, ticket_id):
        with self.connect() as db:
            return db.execute(
                "SELECT COUNT(*) FROM notification_outbox o JOIN ticket_messages m ON m.id=o.message_id WHERE m.ticket_id=? AND o.delivered=0",
                (ticket_id,),
            ).fetchone()[0]
