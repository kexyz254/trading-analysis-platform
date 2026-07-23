from __future__ import annotations

import unittest
from pathlib import Path

from services.umil.app.authority import AUTHORITY, AuthorityContract
from services.umil.app.config import Settings, assert_secret_free_environment


class AuthorityAndConfigTest(unittest.TestCase):
    def test_authority_is_permanently_fail_closed(self):
        AUTHORITY.validate_fail_closed()
        self.assertTrue(AUTHORITY.read_only)
        self.assertFalse(AUTHORITY.can_trade)
        self.assertFalse(AUTHORITY.can_influence_ats)
        with self.assertRaises(RuntimeError):
            AuthorityContract(can_trade=True).validate_fail_closed()

    def test_trading_credentials_are_rejected_from_process(self):
        with self.assertRaisesRegex(RuntimeError, "BINANCE_API_SECRET"):
            assert_secret_free_environment({"BINANCE_API_SECRET": "present"})

    def test_settings_cannot_point_at_ats_databases_or_port(self):
        with self.assertRaises(ValueError):
            Settings(database_path=Path("trading_system.db"))
        with self.assertRaises(ValueError):
            Settings(database_path=Path("umil.db"), bind_port=8000)

    def test_only_umil_environment_names_configure_service(self):
        settings = Settings.from_environment({
            "UMIL_DATABASE_PATH": "/tmp/test-umil.db",
            "UMIL_QUOTE_ASSETS": "USDT,FDUSD",
            "UMIL_AUTO_START": "false",
        })
        self.assertEqual(settings.quote_assets, ("USDT", "FDUSD"))
        self.assertFalse(settings.auto_start)


if __name__ == "__main__":
    unittest.main()
