from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nut_name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    manufacturer TEXT NOT NULL DEFAULT '',
    serial TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL DEFAULT '',
    usb_id TEXT NOT NULL DEFAULT '',
    online INTEGER NOT NULL DEFAULT 0,
    last_seen INTEGER,
    last_error TEXT NOT NULL DEFAULT '',
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS energy_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    price REAL NOT NULL CHECK(price >= 0),
    effective_from INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    battery_charge REAL,
    runtime_seconds REAL,
    load_percent REAL,
    input_voltage REAL,
    output_voltage REAL,
    real_power_w REAL,
    status TEXT NOT NULL DEFAULT '',
    price_per_kwh REAL NOT NULL,
    raw_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    details_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS report_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_start INTEGER NOT NULL,
    period_end INTEGER NOT NULL,
    recipient TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_samples_device_ts ON samples(device_id, ts);
CREATE INDEX IF NOT EXISTS idx_samples_ts ON samples(ts);
CREATE INDEX IF NOT EXISTS idx_events_device_start ON events(device_id, started_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_open_event_unique
ON events(device_id, event_type) WHERE ended_at IS NULL;
"""

MAX_INTEGRATION_GAP_SECONDS = 15 * 60


def numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def initialize(self) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(SCHEMA)
            conn.commit()

    def get_setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        now = int(time.time())
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, json.dumps(value), now),
            )
            conn.commit()

    def settings(self) -> dict[str, Any]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key,value FROM settings").fetchall()
        result: dict[str, Any] = {}
        for row in rows:
            try:
                result[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                continue
        return result

    def ensure_energy_price(self, price: float) -> None:
        with self._write_lock, self.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM energy_prices").fetchone()["c"]
            if count == 0:
                conn.execute(
                    "INSERT INTO energy_prices(price,effective_from) VALUES(?,?)",
                    (price, int(time.time())),
                )
                conn.commit()

    def set_energy_price(self, price: float) -> None:
        now = int(time.time())
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO energy_prices(price,effective_from) VALUES(?,?)",
                (price, now),
            )
            conn.commit()

    def current_energy_price(self) -> float:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT price FROM energy_prices WHERE effective_from<=? ORDER BY effective_from DESC LIMIT 1",
                (int(time.time()),),
            ).fetchone()
        return float(row["price"]) if row else 0.0

    def upsert_device(self, nut_name: str, description: str, variables: dict[str, str], ts: int) -> int:
        model = variables.get("device.model") or variables.get("ups.model") or description
        manufacturer = variables.get("device.mfr") or variables.get("ups.mfr") or ""
        serial = variables.get("device.serial") or variables.get("ups.serial") or ""
        driver = variables.get("driver.name", "")
        vendor = variables.get("driver.parameter.vendorid", "")
        product = variables.get("driver.parameter.productid", "")
        usb_id = f"{vendor}:{product}" if vendor and product else ""
        raw = json.dumps(variables, separators=(",", ":"))
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO devices(nut_name,description,model,manufacturer,serial,driver,usb_id,online,last_seen,last_error,raw_json)
                VALUES(?,?,?,?,?,?,?,1,?,'',?)
                ON CONFLICT(nut_name) DO UPDATE SET
                    description=excluded.description, model=excluded.model,
                    manufacturer=excluded.manufacturer, serial=excluded.serial,
                    driver=excluded.driver, usb_id=excluded.usb_id,
                    online=1, last_seen=excluded.last_seen, last_error='', raw_json=excluded.raw_json
                """,
                (nut_name, description, model, manufacturer, serial, driver, usb_id, ts, raw),
            )
            row = conn.execute("SELECT id FROM devices WHERE nut_name=?", (nut_name,)).fetchone()
            conn.commit()
        return int(row["id"])

    def insert_sample(self, device_id: int, variables: dict[str, str], ts: int, price: float) -> None:
        values = (
            device_id,
            ts,
            numeric(variables.get("battery.charge")),
            numeric(variables.get("battery.runtime")),
            numeric(variables.get("ups.load")),
            numeric(variables.get("input.voltage")),
            numeric(variables.get("output.voltage")),
            numeric(variables.get("ups.realpower")),
            variables.get("ups.status", ""),
            price,
            json.dumps(variables, separators=(",", ":")),
        )
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO samples(device_id,ts,battery_charge,runtime_seconds,load_percent,input_voltage,
                    output_voltage,real_power_w,status,price_per_kwh,raw_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            conn.commit()

    def mark_all_offline(self, error: str) -> list[int]:
        with self._write_lock, self.connect() as conn:
            rows = conn.execute("SELECT id FROM devices").fetchall()
            conn.execute("UPDATE devices SET online=0,last_error=?", (error[:500],))
            conn.commit()
        return [int(row["id"]) for row in rows]

    def sync_events(self, device_id: int, active: dict[str, tuple[str, str]], ts: int) -> None:
        with self._write_lock, self.connect() as conn:
            open_rows = conn.execute(
                "SELECT id,event_type FROM events WHERE device_id=? AND ended_at IS NULL", (device_id,)
            ).fetchall()
            open_events = {row["event_type"]: int(row["id"]) for row in open_rows}
            for event_type, event_id in open_events.items():
                if event_type not in active:
                    conn.execute("UPDATE events SET ended_at=? WHERE id=?", (ts, event_id))
            for event_type, (severity, message) in active.items():
                if event_type not in open_events:
                    conn.execute(
                        "INSERT INTO events(device_id,event_type,severity,message,started_at) VALUES(?,?,?,?,?)",
                        (device_id, event_type, severity, message, ts),
                    )
            conn.commit()

    def latest_devices(self) -> list[dict[str, Any]]:
        query = """
        SELECT d.*,
               s.ts AS sample_ts,s.battery_charge,s.runtime_seconds,s.load_percent,s.input_voltage,
               s.output_voltage,s.real_power_w,s.status,s.price_per_kwh,s.raw_json AS sample_raw_json
        FROM devices d
        LEFT JOIN samples s ON s.id=(
            SELECT id FROM samples WHERE device_id=d.id ORDER BY ts DESC,id DESC LIMIT 1
        )
        ORDER BY d.nut_name
        """
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["variables"] = json.loads(item.pop("sample_raw_json") or item["raw_json"])
            except json.JSONDecodeError:
                item["variables"] = {}
            result.append(item)
        return result

    def device_ids(self) -> list[int]:
        with self.connect() as conn:
            return [int(row["id"]) for row in conn.execute("SELECT id FROM devices").fetchall()]

    def sample_count(self) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM samples").fetchone()
        return int(row["c"])

    def history(self, start_ts: int, end_ts: int, device_name: str | None, max_points: int = 500) -> list[dict[str, Any]]:
        duration = max(1, end_ts - start_ts)
        bucket = max(1, math.ceil(duration / max_points))
        params: list[Any] = [bucket, bucket, start_ts, end_ts]
        where = "s.ts BETWEEN ? AND ?"
        if device_name:
            where += " AND d.nut_name=?"
            params.append(device_name)
        query = f"""
        SELECT d.nut_name,
               CAST(s.ts / ? AS INTEGER) * ? AS ts,
               AVG(s.battery_charge) AS battery_charge,
               AVG(s.runtime_seconds) AS runtime_seconds,
               AVG(s.load_percent) AS load_percent,
               AVG(s.input_voltage) AS input_voltage,
               AVG(s.output_voltage) AS output_voltage,
               AVG(s.real_power_w) AS real_power_w
        FROM samples s JOIN devices d ON d.id=s.device_id
        WHERE {where}
        GROUP BY d.nut_name, CAST(s.ts / {bucket} AS INTEGER)
        ORDER BY ts,d.nut_name
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def energy_and_cost(self, start_ts: int, end_ts: int, device_name: str | None = None) -> tuple[float, float, bool]:
        params: list[Any] = [start_ts, end_ts]
        where = "s.ts BETWEEN ? AND ? AND s.real_power_w IS NOT NULL"
        if device_name:
            where += " AND d.nut_name=?"
            params.append(device_name)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT d.nut_name,s.ts,s.real_power_w,s.price_per_kwh
                    FROM samples s JOIN devices d ON d.id=s.device_id
                    WHERE {where} ORDER BY d.nut_name,s.ts""",
                params,
            ).fetchall()
        energy = 0.0
        cost = 0.0
        complete = True
        previous: dict[str, sqlite3.Row] = {}
        for row in rows:
            name = row["nut_name"]
            prev = previous.get(name)
            if prev is not None:
                elapsed = int(row["ts"]) - int(prev["ts"])
                if 0 < elapsed <= MAX_INTEGRATION_GAP_SECONDS:
                    avg_w = (float(prev["real_power_w"]) + float(row["real_power_w"])) / 2
                    kwh = avg_w * (elapsed / 3600) / 1000
                    avg_price = (float(prev["price_per_kwh"]) + float(row["price_per_kwh"])) / 2
                    energy += kwh
                    cost += kwh * avg_price
                elif elapsed > MAX_INTEGRATION_GAP_SECONDS:
                    complete = False
            previous[name] = row
        return energy, cost, complete

    def events(self, start_ts: int, end_ts: int, device_name: str | None = None) -> list[dict[str, Any]]:
        where = "e.started_at<=? AND COALESCE(e.ended_at,?)>=?"
        params: list[Any] = [end_ts, end_ts, start_ts]
        if device_name:
            where += " AND d.nut_name=?"
            params.append(device_name)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT e.*,d.nut_name FROM events e JOIN devices d ON d.id=e.device_id
                    WHERE {where} ORDER BY e.started_at DESC""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def report_summary(self, start_ts: int, end_ts: int) -> dict[str, int]:
        with self.connect() as conn:
            device_count = conn.execute("SELECT COUNT(*) AS c FROM devices").fetchone()["c"]
            rows = conn.execute(
                """SELECT event_type,severity,COUNT(*) AS c FROM events
                    WHERE started_at BETWEEN ? AND ? GROUP BY event_type,severity""",
                (start_ts, end_ts),
            ).fetchall()
        counts = {row["event_type"]: int(row["c"]) for row in rows}
        warning = sum(int(row["c"]) for row in rows if row["severity"] == "warning")
        critical = sum(int(row["c"]) for row in rows if row["severity"] == "critical")
        return {
            "number_of_devices": int(device_count),
            "warning_event": warning,
            "critical_event": critical,
            "utility_power_failed": counts.get("utility_power_failed", 0),
            "enabling_buck": counts.get("enabling_buck", 0),
            "enabling_boost": counts.get("enabling_boost", 0),
            "overload": counts.get("overload", 0),
        }

    def record_report(self, start_ts: int, end_ts: int, recipient: str, status: str, error: str = "") -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO report_runs(period_start,period_end,recipient,status,error,created_at) VALUES(?,?,?,?,?,?)",
                (start_ts, end_ts, recipient, status, error[:1000], int(time.time())),
            )
            conn.commit()

    def prune_samples(self, retention_days: int) -> int:
        cutoff = int(time.time()) - retention_days * 86400
        with self._write_lock, self.connect() as conn:
            cursor = conn.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            conn.commit()
        return int(cursor.rowcount)
