from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app.config import AppConfig
from app.database import Database
from app.nut_client import NutClientError, NutUps
from app.poller import NutPoller

APC = {"ups.load": "18", "ups.realpower.nominal": "390", "ups.status": "OL", "device.model": "APC"}
CYBERPOWER = {"ups.realpower": "127", "ups.load": "12", "ups.status": "OL", "device.model": "CP"}


class FakeNutClient:
    """Stands in for NutClient; `failing` names the UPS devices whose variables cannot be read."""

    failing: set[str] = set()
    data = {"apc": APC, "cyberpower": CYBERPOWER}

    def __init__(self, host: str, port: int = 3493, timeout: float = 5.0):
        pass

    async def list_ups(self) -> list[NutUps]:
        return [NutUps(name, name) for name in self.data]

    async def list_vars(self, ups_name: str) -> dict[str, str]:
        if ups_name in self.failing:
            raise NutClientError("ERR DATA-STALE")
        return dict(self.data[ups_name])


class PollerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"DATA_DIR": self.temp.name}):
            config = AppConfig.from_env()
        self.db = Database(Path(self.temp.name) / "test.db")
        self.db.initialize()
        self.db.ensure_energy_price(1.0)
        self.db.set_setting("nut_host", "nut.example")
        self.poller = NutPoller(self.db, config)
        FakeNutClient.failing = set()
        patcher = mock.patch("app.poller.NutClient", FakeNutClient)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.temp.cleanup)

    def poll(self) -> None:
        asyncio.run(self.poller.poll_once())

    def samples(self, name: str) -> int:
        device_id = self.db.device_id(name)
        if device_id is None:
            return 0
        with self.db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM samples WHERE device_id=?", (device_id,)).fetchone()[0]

    def test_poll_records_every_device_with_estimated_power(self) -> None:
        self.poll()
        devices = {item["nut_name"]: item for item in self.db.latest_devices()}
        self.assertAlmostEqual(devices["apc"]["real_power_w"], 70.2)
        self.assertEqual(devices["apc"]["power_estimated"], 1)
        self.assertEqual(devices["cyberpower"]["real_power_w"], 127.0)
        self.assertEqual(devices["cyberpower"]["power_estimated"], 0)

    def test_one_failing_ups_does_not_stop_the_others(self) -> None:
        FakeNutClient.failing = {"apc"}
        self.poll()
        self.assertEqual(self.samples("cyberpower"), 1)
        self.assertEqual(self.samples("apc"), 0)
        self.assertIsNotNone(self.poller.state.last_success)
        self.assertEqual(self.poller.state.devices_seen, 1)
        self.assertIn("apc", self.poller.state.last_error)

    def test_failing_ups_goes_offline_only_after_three_failures(self) -> None:
        self.poll()  # both devices known
        FakeNutClient.failing = {"apc"}
        for _ in range(2):
            self.poll()
        self.assertEqual({d["nut_name"]: d["online"] for d in self.db.latest_devices()}, {"apc": 1, "cyberpower": 1})
        self.poll()
        online = {d["nut_name"]: d["online"] for d in self.db.latest_devices()}
        self.assertEqual(online, {"apc": 0, "cyberpower": 1})
        events = self.db.events(0, int(time.time()) + 10)
        self.assertEqual([(e["nut_name"], e["event_type"]) for e in events], [("apc", "communication_lost")])

        FakeNutClient.failing = set()
        self.poll()
        self.assertEqual({d["nut_name"]: d["online"] for d in self.db.latest_devices()}["apc"], 1)
        self.assertIsNotNone(self.db.events(0, int(time.time()) + 10)[0]["ended_at"])
        self.assertEqual(self.poller.state.last_error, "")

    def test_poll_fails_when_no_device_can_be_read(self) -> None:
        FakeNutClient.failing = {"apc", "cyberpower"}
        with self.assertRaises(NutClientError):
            self.poll()

    def test_maintenance_rolls_up_and_prunes(self) -> None:
        device_id = self.db.upsert_device("apc", "apc", APC, 0)
        old = int(time.time()) - 200 * 86400
        for ts in range(old, old + 7200, 60):
            self.db.insert_sample(device_id, APC, ts, 1.0)
        self.poller.run_maintenance()
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0], 0)
            self.assertGreaterEqual(conn.execute("SELECT COUNT(*) FROM samples_hourly").fetchone()[0], 2)
        self.assertGreater(self.db.energy_summary(old - 3600, old + 8000)["energy_kwh"], 0)


if __name__ == "__main__":
    unittest.main()
