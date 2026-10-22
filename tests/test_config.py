import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support_bot.config import ROOT, load_settings

TOKEN = "123456:OFFLINE_TEST_TOKEN_NOT_A_REAL_CREDENTIAL"


class ConfigTests(unittest.TestCase):
    def load(self, env, **kwargs):
        with (
            patch.dict(os.environ, env, clear=True),
            patch("support_bot.config.load_dotenv"),
        ):
            return load_settings(**kwargs)

    def test_missing_and_invalid_token_fail_clearly(self):
        for token in ("", "paste-token-here", "123:short"):
            with self.assertRaisesRegex(ValueError, "BOT_TOKEN"):
                self.load({"BOT_TOKEN": token})

    def test_both_departments_required_but_admin_is_fallback(self):
        with self.assertRaisesRegex(ValueError, "both department"):
            self.load({"BOT_TOKEN": TOKEN, "SALES_IDS": "30"})
        settings = self.load({"BOT_TOKEN": TOKEN, "ADMIN_IDS": "90"})
        self.assertEqual(settings.recipients("sales"), {90})
        self.assertEqual(settings.recipients("programming"), {90})

    def test_setup_can_run_before_staff_ids_are_known(self):
        settings = self.load({"BOT_TOKEN": TOKEN}, require_staff=False)
        self.assertEqual(settings.admins, frozenset())

    def test_relative_path_is_independent_of_pycharm_working_directory(self):
        settings = self.load(
            {"BOT_TOKEN": TOKEN, "ADMIN_IDS": "90", "DATABASE_PATH": "data/test.db"}
        )
        self.assertEqual(settings.database_path, ROOT / "data/test.db")
        with tempfile.TemporaryDirectory() as directory:
            absolute = Path(directory) / "outside.db"
            settings = self.load(
                {"BOT_TOKEN": TOKEN, "ADMIN_IDS": "90", "DATABASE_PATH": str(absolute)}
            )
            self.assertEqual(settings.database_path, absolute)


if __name__ == "__main__":
    unittest.main()
