import unittest
from pathlib import Path

from support_bot.config import Settings, parse_ids
from support_bot.knowledge import FAQS, match_faq, suggest_department


class KnowledgeTests(unittest.TestCase):
    def test_all_six_exact_questions(self):
        self.assertEqual(len(FAQS), 6)
        for faq in FAQS:
            with self.subTest(question=faq.question):
                self.assertEqual(match_faq(faq.question.upper()), faq)

    def test_common_phrasings(self):
        for question, key in (
            ("Where is my parcel?", "status"),
            ("How to cancel my purchase?", "cancel"),
            ("My item arrived cracked", "damage"),
            ("Tell me about shipping", "delivery"),
            ("How to buy a product?", "order"),
            ("How do I reach support?", "contact"),
        ):
            with self.subTest(question=question):
                self.assertEqual(match_faq(question).key, key)

    def test_errors_and_unknown_questions_do_not_get_wrong_faq(self):
        for question in (
            "My payment failed for my order",
            "website error during delivery checkout",
            "unusual question about warranties",
        ):
            self.assertIsNone(match_faq(question))

    def test_technical_priority_for_mixed_issue(self):
        self.assertEqual(suggest_department("payment error for order"), "programming")
        self.assertEqual(suggest_department("damaged product"), "sales")
        self.assertIsNone(suggest_department("Please help me"))

    def test_id_validation_and_department_access(self):
        self.assertEqual(parse_ids(" 10,20,10, "), frozenset({10, 20}))
        for invalid in ("text", "-100", "0", "10,wrong"):
            with self.assertRaises(ValueError):
                parse_ids(invalid)
        settings = Settings(
            "unused", Path("unused"), frozenset({1}), frozenset({2}), frozenset({3})
        )
        self.assertTrue(settings.can_access(1, "sales"))
        self.assertFalse(settings.can_access(2, "sales"))
        self.assertTrue(settings.can_access(3, "sales"))


if __name__ == "__main__":
    unittest.main()
