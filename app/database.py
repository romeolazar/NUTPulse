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
    raw_json TEXT NOT NULL DEFAULT '{}',
    power_estimated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS samples_hourly (
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    hour_ts INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    battery_charge REAL,
    runtime_seconds REAL,
    load_percent REAL,
    input_voltage REAL,
    output_voltage REAL,
    real_power_w REAL,
    energy_kwh REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    incomplete INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, hour_ts)
) WITHOUT ROWID;

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
HOUR_SECONDS = 3600
ROLLUP_WATERMARK_KEY = "rollup_watermark"
SAMPLE_METRICS = (
    "battery_charge",
    "runtime_seconds",
    "load_percent",
    "input_voltage",
    "output_voltage",
    "real_power_w",
)


def numeric(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def power_watts(variables: dict[str, str]) -> tuple[float | None, bool]:
    """Return (watts, estimated). UPS models without ups.realpower fall back to load% of nominal power."""
    real = numeric(variables.get("ups.realpower"))
    if real is not None:
        return real, False
    load = numeric(variables.get("ups.load"))
    nominal = numeric(variables.get("ups.realpower.nominal"))
    if load is not None and nominal is not None and load >= 0 and nominal > 0:
        return nominal * load / 100, True
    return None, False


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
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(samples)")}
            if "power_estimated" not in columns:
                conn.execute("ALTER TABLE samples ADD COLUMN power_estimated INTEGER NOT NULL DEFAULT 0")
            conn.commit()
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._migrate_v1(conn)
            conn.execute("PRAGMA user_version = 1")
            conn.commit()

    def _migrate_v1(self, conn: sqlite3.Connection) -> None:
        """Estimate power for older samples, then stop storing every NUT variable in every sample.

        The latest variables are kept on the device row, so per-sample raw_json only wasted space.
        """
        conn.execute(
            """
            UPDATE samples SET
                real_power_w = CAST(json_extract(raw_json, '$."ups.load"') AS REAL)
                             * CAST(json_extract(raw_json, '$."ups.realpower.nominal"') AS REAL) / 100.0,
                power_estimated = 1
            WHERE real_power_w IS NULL
              AND COALESCE(json_extract(raw_json, '$."ups.load"'), '') != ''
              AND COALESCE(json_extract(raw_json, '$."ups.realpower.nominal"'), '') != ''
              AND CAST(json_extract(raw_json, '$."ups.realpower.nominal"') AS REAL) > 0
            """
        )
        slimmed = conn.execute("UPDATE samples SET raw_json='{}' WHERE raw_json != '{}'").rowcount
        conn.commit()
        if slimmed:
            conn.execute("VACUUM")

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

    def create_initial_admin(self, username: str, password_hash: str, auth_secret: str) -> bool:
        """Create the first administrator exactly once."""
        now = int(time.time())
        values = {
            "auth_username": username,
            "auth_password_hash": password_hash,
            "auth_secret": auth_secret,
        }
        with self._write_lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT key FROM settings WHERE key IN ('auth_username','auth_password_hash','auth_secret') LIMIT 1"
            ).fetchone()
            if existing is not None:
                conn.rollback()
                return False
            conn.executemany(
                "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?)",
                [(key, json.dumps(value), now) for key, value in values.items()],
            )
            conn.commit()
        return True

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
        power, estimated = power_watts(variables)
        values = (
            device_id,
            ts,
            numeric(variables.get("battery.charge")),
            numeric(variables.get("battery.runtime")),
            numeric(variables.get("ups.load")),
            numeric(variables.get("input.voltage")),
            numeric(variables.get("output.voltage")),
            power,
            variables.get("ups.status", ""),
            price,
            "{}",
            int(estimated),
        )
        with self._write_lock, self.connect() as conn:
            conn.execute(
                """
                INSERT INTO samples(device_id,ts,battery_charge,runtime_seconds,load_percent,input_voltage,
                    output_voltage,real_power_w,status,price_per_kwh,raw_json,power_estimated)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
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

    def mark_device_offline(self, device_id: int, error: str) -> None:
        with self._write_lock, self.connect() as conn:
            conn.execute("UPDATE devices SET online=0,last_error=? WHERE id=?", (error[:500], device_id))
            conn.commit()

    def device_id(self, nut_name: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute("SELECT id FROM devices WHERE nut_name=?", (nut_name,)).fetchone()
        return int(row["id"]) if row else None

    def device_names(self) -> list[str]:
        with self.connect() as conn:
            return [row["nut_name"] for row in conn.execute("SELECT nut_name FROM devices ORDER BY nut_name")]

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
               s.output_voltage,s.real_power_w,s.power_estimated,s.status,s.price_per_kwh
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
                item["variables"] = json.loads(item["raw_json"] or "{}")
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

    def _rollup_watermark(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (ROLLUP_WATERMARK_KEY,)).fetchone()
        if row is None:
            return 0
        try:
            return int(json.loads(row["value"]))
        except (ValueError, TypeError):
            return 0

    def rollup_hourly(self, now: int | None = None, chunk_hours: int = 24 * 7) -> int:
        """Aggregate every completed hour of raw samples into samples_hourly.

        Hourly rows are kept forever, so long-term consumption survives raw-sample pruning.
        Returns the number of hourly rows written.
        """
        now = int(time.time()) if now is None else now
        end_ts = now - now % HOUR_SECONDS
        written = 0
        with self._write_lock, self.connect() as conn:
            watermark = self._rollup_watermark(conn)
            if watermark == 0:
                first = conn.execute("SELECT MIN(ts) AS t FROM samples").fetchone()["t"]
                if first is None:
                    return 0
                watermark = int(first) - int(first) % HOUR_SECONDS
            while watermark < end_ts:
                chunk_end = min(end_ts, watermark + chunk_hours * HOUR_SECONDS)
                written += self._rollup_chunk(conn, watermark, chunk_end)
                conn.execute(
                    "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (ROLLUP_WATERMARK_KEY, json.dumps(chunk_end), int(time.time())),
                )
                conn.commit()
                watermark = chunk_end
        return written

    def _rollup_chunk(self, conn: sqlite3.Connection, start_ts: int, end_ts: int) -> int:
        written = 0
        for device in conn.execute("SELECT id FROM devices").fetchall():
            device_id = int(device["id"])
            previous = conn.execute(
                """SELECT ts,real_power_w,price_per_kwh,power_estimated FROM samples
                   WHERE device_id=? AND ts<? AND real_power_w IS NOT NULL ORDER BY ts DESC LIMIT 1""",
                (device_id, start_ts),
            ).fetchone()
            rows = conn.execute(
                f"""SELECT ts,{",".join(SAMPLE_METRICS)},price_per_kwh,power_estimated FROM samples
                    WHERE device_id=? AND ts>=? AND ts<? ORDER BY ts""",
                (device_id, start_ts, end_ts),
            ).fetchall()
            buckets: dict[int, dict[str, Any]] = {}
            for row in rows:
                hour = int(row["ts"]) - int(row["ts"]) % HOUR_SECONDS
                bucket = buckets.setdefault(
                    hour,
                    {
                        "count": 0,
                        "sums": dict.fromkeys(SAMPLE_METRICS, 0.0),
                        "counts": dict.fromkeys(SAMPLE_METRICS, 0),
                        "energy": 0.0,
                        "cost": 0.0,
                        "estimated": 0,
                        "incomplete": 0,
                    },
                )
                bucket["count"] += 1
                for metric in SAMPLE_METRICS:
                    if row[metric] is not None:
                        bucket["sums"][metric] += float(row[metric])
                        bucket["counts"][metric] += 1
                if row["real_power_w"] is None:
                    continue
                if previous is not None:
                    elapsed = int(row["ts"]) - int(previous["ts"])
                    if 0 < elapsed <= MAX_INTEGRATION_GAP_SECONDS:
                        avg_w = (float(previous["real_power_w"]) + float(row["real_power_w"])) / 2
                        kwh = avg_w * (elapsed / 3600) / 1000
                        avg_price = (float(previous["price_per_kwh"]) + float(row["price_per_kwh"])) / 2
                        bucket["energy"] += kwh
                        bucket["cost"] += kwh * avg_price
                        if previous["power_estimated"] or row["power_estimated"]:
                            bucket["estimated"] = 1
                    elif elapsed > MAX_INTEGRATION_GAP_SECONDS:
                        bucket["incomplete"] = 1
                previous = row
            for hour, bucket in buckets.items():
                averages = [
                    bucket["sums"][metric] / bucket["counts"][metric] if bucket["counts"][metric] else None
                    for metric in SAMPLE_METRICS
                ]
                conn.execute(
                    f"""INSERT OR REPLACE INTO samples_hourly(device_id,hour_ts,sample_count,
                            {",".join(SAMPLE_METRICS)},energy_kwh,cost,estimated,incomplete)
                        VALUES(?,?,?,{",".join("?" * len(SAMPLE_METRICS))},?,?,?,?)""",
                    (
                        device_id, hour, bucket["count"], *averages,
                        bucket["energy"], bucket["cost"], bucket["estimated"], bucket["incomplete"],
                    ),
                )
                written += 1
        return written

    def energy_summary(self, start_ts: int, end_ts: int, device_name: str | None = None) -> dict[str, Any]:
        """Energy and cost for a period, combining hourly rollups (old data) with raw samples (recent data)."""
        energy = 0.0
        cost = 0.0
        complete = True
        estimated = False
        with self.connect() as conn:
            watermark = self._rollup_watermark(conn)
            if watermark and start_ts < watermark:
                params: list[Any] = [start_ts, min(end_ts, watermark)]
                where = "h.hour_ts + 3600 > ? AND h.hour_ts < ?"
                if device_name:
                    where += " AND d.nut_name=?"
                    params.append(device_name)
                row = conn.execute(
                    f"""SELECT COALESCE(SUM(h.energy_kwh),0) AS e, COALESCE(SUM(h.cost),0) AS c,
                               COALESCE(MAX(h.estimated),0) AS est, COALESCE(MAX(h.incomplete),0) AS inc
                        FROM samples_hourly h JOIN devices d ON d.id=h.device_id WHERE {where}""",
                    params,
                ).fetchone()
                energy += float(row["e"])
                cost += float(row["c"])
                estimated = bool(row["est"])
                complete = not row["inc"]
            raw_from = max(start_ts, watermark) if watermark else start_ts
            if raw_from <= end_ts:
                # The interval that crosses the watermark belongs to the first unrolled hour.
                query_from = watermark - MAX_INTEGRATION_GAP_SECONDS if watermark and start_ts <= watermark else raw_from
                params = [query_from, end_ts]
                where = "s.ts BETWEEN ? AND ? AND s.real_power_w IS NOT NULL"
                if device_name:
                    where += " AND d.nut_name=?"
                    params.append(device_name)
                rows = conn.execute(
                    f"""SELECT d.nut_name,s.ts,s.real_power_w,s.price_per_kwh,s.power_estimated
                        FROM samples s JOIN devices d ON d.id=s.device_id
                        WHERE {where} ORDER BY d.nut_name,s.ts""",
                    params,
                ).fetchall()
                previous: dict[str, sqlite3.Row] = {}
                for row in rows:
                    name = row["nut_name"]
                    prev = previous.get(name)
                    previous[name] = row
                    if prev is None or int(row["ts"]) < raw_from:
                        continue
                    elapsed = int(row["ts"]) - int(prev["ts"])
                    if 0 < elapsed <= MAX_INTEGRATION_GAP_SECONDS:
                        avg_w = (float(prev["real_power_w"]) + float(row["real_power_w"])) / 2
                        kwh = avg_w * (elapsed / 3600) / 1000
                        avg_price = (float(prev["price_per_kwh"]) + float(row["price_per_kwh"])) / 2
                        energy += kwh
                        cost += kwh * avg_price
                        if prev["power_estimated"] or row["power_estimated"]:
                            estimated = True
                    elif elapsed > MAX_INTEGRATION_GAP_SECONDS:
                        complete = False
        return {"energy_kwh": energy, "cost": cost, "complete": complete, "estimated": estimated}

    def energy_and_cost(self, start_ts: int, end_ts: int, device_name: str | None = None) -> tuple[float, float, bool]:
        summary = self.energy_summary(start_ts, end_ts, device_name)
        return summary["energy_kwh"], summary["cost"], summary["complete"]

    def history_hourly(self, start_ts: int, end_ts: int, device_name: str | None, max_points: int = 500) -> list[dict[str, Any]]:
        """Long-range chart series from the permanent hourly rollups."""
        bucket = max(HOUR_SECONDS, math.ceil((end_ts - start_ts) / max_points))
        params: list[Any] = [start_ts, end_ts]
        where = "h.hour_ts BETWEEN ? AND ?"
        if device_name:
            where += " AND d.nut_name=?"
            params.append(device_name)
        query = f"""
        SELECT d.nut_name,
               CAST(h.hour_ts / {bucket} AS INTEGER) * {bucket} AS ts,
               AVG(h.battery_charge) AS battery_charge,
               AVG(h.runtime_seconds) AS runtime_seconds,
               AVG(h.load_percent) AS load_percent,
               AVG(h.input_voltage) AS input_voltage,
               AVG(h.output_voltage) AS output_voltage,
               AVG(h.real_power_w) AS real_power_w
        FROM samples_hourly h JOIN devices d ON d.id=h.device_id
        WHERE {where}
        GROUP BY d.nut_name, CAST(h.hour_ts / {bucket} AS INTEGER)
        ORDER BY ts,d.nut_name
        """
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

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
        """Delete raw samples older than the retention period, but only once they are rolled up hourly.

        The last quarter hour before the rollup boundary is kept: the energy interval that crosses the
        boundary is still integrated from raw samples.
        """
        with self._write_lock, self.connect() as conn:
            watermark = self._rollup_watermark(conn)
            if watermark == 0:
                return 0
            cutoff = min(int(time.time()) - retention_days * 86400, watermark - MAX_INTEGRATION_GAP_SECONDS)
            cursor = conn.execute("DELETE FROM samples WHERE ts<?", (cutoff,))
            conn.commit()
        return int(cursor.rowcount)
