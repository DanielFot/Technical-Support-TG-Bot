import sqlite3
import tempfile
import unittest
from pathlib import Path

from support_bot.storage import Store


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "nested" / "support.db"
        self.store = Store(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, key="source:1", user_id=10, department="programming"):
        return self.store.create_ticket(
            key,
            user_id,
            department,
            {"kind": "text", "body": "Payment failed"},
            {20, 30},
        )

    def test_ticket_history_and_outbox_survive_restart(self):
        ticket_id = self.create()
        restarted = Store(self.path)
        self.assertEqual(restarted.get_ticket(ticket_id)["department"], "programming")
        self.assertEqual(restarted.history(ticket_id)[0]["body"], "Payment failed")
        self.assertEqual(len(restarted.pending()), 2)

    def test_duplicate_update_creates_only_one_ticket_and_delivery(self):
        first = self.create()
        self.assertEqual(first, self.create())
        self.assertEqual(len(self.store.list_tickets(user_id=10)), 1)
        self.assertEqual(len(self.store.history(first)), 1)
        self.assertEqual(len(self.store.pending()), 2)

    def test_atomic_rollback_when_notification_is_invalid(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_ticket("bad", 10, "sales", {"kind": "text", "body": "Test"}, {None})
        self.assertEqual(self.store.list_tickets(user_id=10), [])
        self.assertEqual(self.store.pending(), [])

    def test_sql_injection_is_saved_as_plain_text(self):
        ticket_id = self.store.create_ticket(
            "sql",
            10,
            "sales",
            {"kind": "text", "body": "'); DROP TABLE specialist_requests; --"},
            set(),
        )
        self.assertEqual(self.store.get_ticket(ticket_id)["user_id"], 10)
        self.assertIn("DROP TABLE", self.store.history(ticket_id)[0]["body"])

    def test_staff_reply_and_closed_ticket_guard(self):
        ticket_id = self.create()
        self.store.add_message(
            ticket_id,
            "reply:1",
            20,
            "staff",
            {"kind": "text", "body": "Try again"},
            {10},
        )
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "in_progress")
        self.store.set_status(ticket_id, "closed", 20, "close:1", {10})
        with self.assertRaises(ValueError):
            self.store.add_message(
                ticket_id,
                "reply:2",
                10,
                "customer",
                {"kind": "text", "body": "More"},
                {20},
            )
        self.assertEqual(len(self.store.history(ticket_id)), 3)

    def test_only_five_active_tickets_and_closed_ticket_does_not_count(self):
        ids = [self.create(f"request:{number}") for number in range(5)]
        with self.assertRaises(ValueError):
            self.create("sixth")
        self.store.set_status(ids[0], "closed", 20, "resolved", {10})
        self.assertGreater(self.create("sixth"), ids[-1])

    def test_queue_filters_departments_and_paginates(self):
        for number in range(10):
            self.create(str(number), user_id=number + 100, department="sales")
        self.create("technical", user_id=300)
        self.assertEqual(len(self.store.list_tickets(departments=("sales",))), 8)
        self.assertEqual(len(self.store.list_tickets(departments=("sales",), offset=8)), 2)
        self.assertEqual(len(self.store.list_tickets(departments=("programming",))), 1)

    def test_delivery_ack_and_fair_retry_order(self):
        self.create()
        first = self.store.pending()[0]["delivery_id"]
        self.store.mark_attempted(first)
        self.assertNotEqual(self.store.pending(limit=1)[0]["delivery_id"], first)
        self.store.mark_delivered(first)
        self.assertEqual(len(self.store.pending()), 1)

    def test_replayed_reply_cannot_change_a_later_closed_status(self):
        ticket_id = self.create()
        payload = {"kind": "text", "body": "Reply"}
        self.store.add_message(ticket_id, "reply", 20, "staff", payload, {10})
        self.store.set_status(ticket_id, "closed", 20, "close", {10})
        self.store.add_message(ticket_id, "reply", 20, "staff", payload, {10})
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "closed")
        self.assertEqual(len(self.store.history(ticket_id)), 3)

    def test_replayed_status_update_cannot_override_later_status(self):
        ticket_id = self.create()
        self.store.set_status(ticket_id, "closed", 20, "close", {10})
        self.store.set_status(ticket_id, "open", 10, "reopen", {20})
        self.store.set_status(ticket_id, "closed", 20, "close", {10})
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "open")
        self.assertEqual(len(self.store.history(ticket_id)), 3)

    def test_reopen_respects_active_limit(self):
        ticket_id = self.create()
        self.store.set_status(ticket_id, "closed", 20, "close", {10})
        for number in range(5):
            self.create(f"other:{number}")
        with self.assertRaisesRegex(ValueError, "five active"):
            self.store.set_status(ticket_id, "open", 10, "reopen", {20})
        self.assertEqual(self.store.get_ticket(ticket_id)["status"], "closed")


if __name__ == "__main__":
    unittest.main()
