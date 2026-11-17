from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.auth import constant_time_equals, hash_password, verify_password
from app.database import Database, power_watts
from app.periods import month_windows
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


HOUR = 3600
APC = {"ups.load": "18", "ups.realpower.nominal": "390", "ups.status": "OL"}


class PowerEstimateTests(unittest.TestCase):
    def test_reported_real_power_wins(self) -> None:
        self.assertEqual(power_watts({"ups.realpower": "127", **APC}), (127.0, False))

    def test_zero_reported_power_is_not_replaced_by_an_estimate(self) -> None:
        self.assertEqual(power_watts({"ups.realpower": "0", **APC}), (0.0, False))

    def test_estimates_from_load_and_nominal_power(self) -> None:
        watts, estimated = power_watts(APC)
        self.assertAlmostEqual(watts, 70.2)
        self.assertTrue(estimated)

    def test_no_estimate_without_nominal_power(self) -> None:
        self.assertEqual(power_watts({"ups.load": "18"}), (None, False))
        self.assertEqual(power_watts({"ups.load": "18", "ups.realpower.nominal": "0"}), (None, False))
        self.assertEqual(power_watts({"ups.load": "", "ups.realpower.nominal": "390"}), (None, False))


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.db.initialize()
        self.db.ensure_energy_price(1.0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fill(self, name: str, variables: dict[str, str], start: int, end: int, step: int = 60) -> int:
        device_id = self.db.upsert_device(name, name, variables, start)
        for ts in range(start, end + 1, step):
            self.db.insert_sample(device_id, variables, ts, 1.0)
        return device_id

    def test_estimated_power_is_stored_and_flagged(self) -> None:
        self.fill("apc", APC, 0, 120)
        self.fill("cp", {"ups.realpower": "100", "ups.status": "OL"}, 0, 120)
        devices = {item["nut_name"]: item for item in self.db.latest_devices()}
        self.assertAlmostEqual(devices["apc"]["real_power_w"], 70.2)
        self.assertEqual(devices["apc"]["power_estimated"], 1)
        self.assertEqual(devices["cp"]["power_estimated"], 0)
        self.assertTrue(self.db.energy_summary(0, 120, "apc")["estimated"])
        self.assertFalse(self.db.energy_summary(0, 120, "cp")["estimated"])

    def test_samples_do_not_store_raw_variables(self) -> None:
        self.fill("apc", {**APC, "ups.firmware": "x" * 500}, 0, 60)
        with self.db.connect() as conn:
            sizes = [row[0] for row in conn.execute("SELECT length(raw_json) FROM samples")]
        self.assertTrue(all(size <= 2 for size in sizes))
        self.assertIn("ups.firmware", self.db.latest_devices()[0]["variables"])

    def test_rollup_matches_raw_totals_and_survives_pruning(self) -> None:
        start = 10 * HOUR + 30
        end = 40 * HOUR + 30
        self.fill("apc", {"ups.realpower": "200", "ups.status": "OL"}, start, end, step=30)
        now = 50 * HOUR
        raw = self.db.energy_summary(start, end)
        self.assertGreater(self.db.rollup_hourly(now), 0)
        self.assertEqual(self.db.rollup_hourly(now), 0)  # nothing new to roll up
        self.assertAlmostEqual(self.db.energy_summary(start, end)["energy_kwh"], raw["energy_kwh"])
        deleted = self.db.prune_samples(0)
        self.assertGreater(deleted, 0)
        with self.db.connect() as conn:
            oldest = conn.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
        self.assertIsNone(oldest)  # everything was rolled up, so the raw samples can go
        pruned = self.db.energy_summary(start, end)
        self.assertAlmostEqual(pruned["energy_kwh"], raw["energy_kwh"], places=6)
        self.assertAlmostEqual(pruned["cost"], raw["cost"], places=6)
        self.assertAlmostEqual(raw["energy_kwh"], 0.2 * 30)

    def test_recent_raw_data_is_added_to_rolled_up_data(self) -> None:
        self.fill("apc", {"ups.realpower": "1000", "ups.status": "OL"}, 0, 10 * HOUR, step=60)
        everything = self.db.energy_summary(0, 10 * HOUR)["energy_kwh"]
        self.db.rollup_hourly(5 * HOUR + 123)  # hours 0-4 rolled up, the rest stays raw
        self.assertAlmostEqual(self.db.energy_summary(0, 10 * HOUR)["energy_kwh"], everything)
        self.db.prune_samples(0)
        self.assertAlmostEqual(self.db.energy_summary(0, 10 * HOUR)["energy_kwh"], everything)

    def test_prune_never_deletes_samples_that_were_not_rolled_up(self) -> None:
        self.fill("apc", APC, 0, 3 * HOUR)
        self.assertEqual(self.db.prune_samples(0), 0)  # nothing rolled up yet
        self.db.rollup_hourly(2 * HOUR)
        self.db.prune_samples(0)
        with self.db.connect() as conn:
            remaining = conn.execute("SELECT MIN(ts) FROM samples").fetchone()[0]
        self.assertEqual(remaining, 2 * HOUR - 15 * 60)

    def test_rollup_flags_gaps_and_estimates(self) -> None:
        device_id = self.db.upsert_device("apc", "apc", APC, 0)
        for ts in (0, 60, 2 * HOUR, 2 * HOUR + 60):
            self.db.insert_sample(device_id, APC, ts, 1.0)
        self.db.rollup_hourly(4 * HOUR)
        summary = self.db.energy_summary(0, 3 * HOUR)
        self.assertFalse(summary["complete"])
        self.assertTrue(summary["estimated"])

    def test_hourly_history_is_available_after_pruning(self) -> None:
        self.fill("apc", {"ups.realpower": "50", "ups.status": "OL"}, 0, 5 * HOUR)
        self.db.rollup_hourly(6 * HOUR)
        self.db.prune_samples(0)
        series = self.db.history_hourly(0, 6 * HOUR, None)
        self.assertEqual(len(series), 6)
        self.assertAlmostEqual(series[0]["real_power_w"], 50)

    def test_migration_estimates_old_power_and_shrinks_samples(self) -> None:
        path = Path(self.temp.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE devices (id INTEGER PRIMARY KEY AUTOINCREMENT, nut_name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '', manufacturer TEXT NOT NULL DEFAULT '',
                serial TEXT NOT NULL DEFAULT '', driver TEXT NOT NULL DEFAULT '', usb_id TEXT NOT NULL DEFAULT '',
                online INTEGER NOT NULL DEFAULT 0, last_seen INTEGER, last_error TEXT NOT NULL DEFAULT '',
                raw_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE samples (id INTEGER PRIMARY KEY AUTOINCREMENT, device_id INTEGER NOT NULL, ts INTEGER NOT NULL,
                battery_charge REAL, runtime_seconds REAL, load_percent REAL, input_voltage REAL, output_voltage REAL,
                real_power_w REAL, status TEXT NOT NULL DEFAULT '', price_per_kwh REAL NOT NULL,
                raw_json TEXT NOT NULL DEFAULT '{}');
            INSERT INTO devices(nut_name) VALUES ('apc'), ('cp');
            """
        )
        rows = [
            (1, 100, None, APC),                                    # estimated from load and nominal power
            (1, 160, None, {"ups.load": "18"}),                      # no nominal power: stays empty
            (2, 100, 127.0, {"ups.realpower": "127", "ups.load": "12", "ups.realpower.nominal": "1000"}),
        ]
        for device_id, ts, power, variables in rows:
            conn.execute(
                "INSERT INTO samples(device_id,ts,real_power_w,price_per_kwh,raw_json) VALUES(?,?,?,?,?)",
                (device_id, ts, power, 1.0, json.dumps(variables)),
            )
        conn.commit()
        conn.close()

        db = Database(path)
        db.initialize()
        db.initialize()  # migrating twice is harmless
        with db.connect() as check:
            result = [tuple(row) for row in check.execute(
                "SELECT device_id,ts,real_power_w,power_estimated,raw_json FROM samples ORDER BY id")]
            self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertAlmostEqual(result[0][2], 70.2)
        self.assertEqual(result[0][3], 1)
        self.assertIsNone(result[1][2])
        self.assertEqual(result[2][2:4], (127.0, 0))
        self.assertTrue(all(row[4] == "{}" for row in result))


class AuthenticationTests(unittest.TestCase):
    def test_constant_time_equals_handles_non_ascii(self) -> None:
        self.assertTrue(constant_time_equals("pa55wörd", "pa55wörd"))
        self.assertFalse(constant_time_equals("pa55wörd", "pa55word"))
        self.assertFalse(constant_time_equals("", "x"))

    def test_password_hash_round_trip(self) -> None:
        encoded = hash_password("a-strong-password")
        self.assertTrue(verify_password("a-strong-password", encoded))
        self.assertFalse(verify_password("wrong-password", encoded))
        self.assertNotIn("a-strong-password", encoded)


class PeriodTests(unittest.TestCase):
    def test_last_twelve_calendar_months(self) -> None:
        now = int(datetime(2026, 2, 10, 12, 0, tzinfo=ZoneInfo("Europe/Bucharest")).timestamp())
        windows = month_windows(now, "Europe/Bucharest")
        self.assertEqual([w[0] for w in windows][0], "2025-03")
        self.assertEqual([w[0] for w in windows][-1], "2026-02")
        self.assertEqual(len(windows), 12)
        self.assertEqual(windows[-1][2], now)  # the current month ends now
        for (_, _, end), (_, start, _) in zip(windows, windows[1:]):
            self.assertEqual(end, start)  # months are contiguous, including across DST changes


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
