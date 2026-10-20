from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.auth import hash_password, verify_password
from app.database import Database
from app.reporting import previous_seven_days, render_report


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.db.initialize()
        self.db.ensure_energy_price(1.2)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_energy_is_integrated_per_device(self) -> None:
        variables = {
            "device.model": "Test UPS",
            "ups.realpower": "1000",
            "ups.status": "OL",
        }
        device_id = self.db.upsert_device("ups0", "Test", variables, 1000)
        self.db.insert_sample(device_id, variables, 1000, 1.2)
        self.db.insert_sample(device_id, variables, 1060, 1.2)
        energy, cost, complete = self.db.energy_and_cost(900, 1100)
        self.assertAlmostEqual(energy, 1 / 60)
        self.assertAlmostEqual(cost, 0.02)
        self.assertTrue(complete)

    def test_event_open_and_close(self) -> None:
        device_id = self.db.upsert_device("ups0", "Test", {}, 1000)
        self.db.sync_events(device_id, {"overload": ("critical", "Overload")}, 1000)
        self.db.sync_events(device_id, {}, 1060)
        events = self.db.events(900, 1200)
        self.assertEqual(events[0]["event_type"], "overload")
        self.assertEqual(events[0]["ended_at"], 1060)

    def test_energy_marks_long_gaps_incomplete(self) -> None:
        variables = {"device.model": "Test UPS", "ups.realpower": "100", "ups.status": "OL"}
        device_id = self.db.upsert_device("ups0", "Test", variables, 1000)
        self.db.insert_sample(device_id, variables, 1000, 1.2)
        self.db.insert_sample(device_id, variables, 2000, 1.2)
        _, _, complete = self.db.energy_and_cost(900, 2100)
        self.assertFalse(complete)

    def test_initial_admin_can_only_be_created_once(self) -> None:
        password_hash = hash_password("correct-horse-battery-staple")
        self.assertTrue(self.db.create_initial_admin("admin", password_hash, "session-secret"))
        self.assertFalse(self.db.create_initial_admin("other", password_hash, "other-secret"))
        self.assertEqual(self.db.get_setting("auth_username"), "admin")
        self.assertTrue(verify_password("correct-horse-battery-staple", self.db.get_setting("auth_password_hash")))


class AuthenticationTests(unittest.TestCase):
    def test_password_hash_round_trip(self) -> None:
        encoded = hash_password("a-strong-password")
        self.assertTrue(verify_password("a-strong-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertNotIn("a-strong-password", encoded)


class ReportTests(unittest.TestCase):
    def test_previous_seven_days(self) -> None:
        now = datetime(2026, 10, 20, 12, 0, tzinfo=ZoneInfo("Europe/Bucharest"))
        start, end, start_label, end_label = previous_seven_days(now)
        self.assertEqual(end - start, 7 * 86400)
        self.assertEqual(start_label, "2026-10-13")
        self.assertEqual(end_label, "2026-10-20")

    def test_report_contains_requested_fields(self) -> None:
        text, html = render_report({"number_of_devices": 2, "overload": 1}, "2026-10-13", "2026-10-20")
        self.assertIn("Number of Devices: 2", text)
        self.assertIn("Overload: 1", text)
        self.assertIn("Last 7 Days Event Summary", html)


if __name__ == "__main__":
    unittest.main()
