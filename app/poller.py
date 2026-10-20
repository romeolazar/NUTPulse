from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .config import AppConfig
from .database import Database
from .nut_client import NutClient, NutClientError

logger = logging.getLogger("nutpulse.poller")


EVENT_MAP: dict[str, tuple[str, str, str]] = {
    "OB": ("utility_power_failed", "warning", "Utility Power Failed"),
    "LB": ("low_battery", "critical", "Low Battery"),
    "RB": ("replace_battery", "critical", "Replace Battery"),
    "BOOST": ("enabling_boost", "warning", "Enabling Boost"),
    "TRIM": ("enabling_buck", "warning", "Enabling Buck"),
    "OVER": ("overload", "critical", "Overload"),
}


@dataclass
class PollerState:
    running: bool = False
    last_success: int | None = None
    last_error: str = ""
    consecutive_errors: int = 0
    devices_seen: int = 0


class NutPoller:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config
        self.state = PollerState()
        self._stop = asyncio.Event()

    def connection_settings(self) -> tuple[str, int, int]:
        host = str(self.db.get_setting("nut_host", self.config.nut_host))
        port = int(self.db.get_setting("nut_port", self.config.nut_port))
        interval = max(2, int(self.db.get_setting("poll_interval", self.config.nut_poll_interval)))
        return host, port, interval

    async def test_connection(self, host: str | None = None, port: int | None = None) -> dict[str, Any]:
        current_host, current_port, _ = self.connection_settings()
        client = NutClient(host or current_host, port or current_port)
        devices = await client.list_ups()
        return {
            "ok": True,
            "host": host or current_host,
            "port": port or current_port,
            "devices": [{"name": item.name, "description": item.description} for item in devices],
        }

    async def poll_once(self) -> None:
        host, port, _ = self.connection_settings()
        client = NutClient(host, port)
        now = int(time.time())
        devices = await client.list_ups()
        if not devices:
            raise NutClientError("NUT server returned no UPS devices")
        price = self.db.current_energy_price()
        seen = 0
        for ups in devices:
            variables = await client.list_vars(ups.name)
            device_id = self.db.upsert_device(ups.name, ups.description, variables, now)
            self.db.insert_sample(device_id, variables, now, price)
            tokens = set(variables.get("ups.status", "").split())
            active: dict[str, tuple[str, str]] = {}
            for token, (event_type, severity, message) in EVENT_MAP.items():
                if token in tokens:
                    active[event_type] = (severity, message)
            self.db.sync_events(device_id, active, now)
            seen += 1
        self.state.last_success = now
        self.state.last_error = ""
        self.state.consecutive_errors = 0
        self.state.devices_seen = seen

    async def run(self) -> None:
        self.state.running = True
        prune_counter = 0
        while not self._stop.is_set():
            _, _, interval = self.connection_settings()
            try:
                await self.poll_once()
            except Exception as exc:  # polling must survive malformed or unreachable devices
                self.state.consecutive_errors += 1
                self.state.last_error = str(exc)
                logger.warning("NUT polling failed: %s", exc)
                if self.state.consecutive_errors >= 3:
                    now = int(time.time())
                    for device_id in self.db.mark_all_offline(str(exc)):
                        self.db.sync_events(
                            device_id,
                            {"communication_lost": ("critical", "Communication Lost")},
                            now,
                        )
            prune_counter += 1
            if prune_counter >= max(1, 3600 // interval):
                self.db.prune_samples(self.config.retention_days)
                prune_counter = 0
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
        self.state.running = False

    def stop(self) -> None:
        self._stop.set()
