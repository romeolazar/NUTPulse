from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import hash_password, verify_password
from .config import AppConfig
from .database import Database
from .poller import NutPoller
from .reporting import ReportService


config = AppConfig.from_env()
db = Database(config.database_path)
poller = NutPoller(db, config)
reports = ReportService(db, config)


def seed_defaults() -> None:
    defaults = {
        "nut_host": config.nut_host,
        "nut_port": config.nut_port,
        "poll_interval": config.nut_poll_interval,
        "weekly_report_enabled": False,
        "report_recipient": "",
        "report_weekday": 4,
        "report_time": "08:00",
    }
    for key, value in defaults.items():
        if db.get_setting(key, None) is None:
            db.set_setting(key, value)
    db.ensure_energy_price(config.energy_price)


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.initialize()
    seed_defaults()
    poll_task = asyncio.create_task(poller.run())
    report_task = asyncio.create_task(reports.run())
    try:
        yield
    finally:
        poller.stop()
        reports.stop()
        await asyncio.gather(poll_task, report_task, return_exceptions=True)


app = FastAPI(title="NUTPulse", version="0.2.0", lifespan=lifespan)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


def env_auth_configured() -> bool:
    return bool(config.web_password and config.auth_secret)


def database_auth() -> tuple[str, str, str]:
    return (
        str(db.get_setting("auth_username", "")),
        str(db.get_setting("auth_password_hash", "")),
        str(db.get_setting("auth_secret", "")),
    )


def auth_configured() -> bool:
    if config.auth_disabled or env_auth_configured():
        return True
    return all(database_auth())


def active_username() -> str:
    return config.web_username if env_auth_configured() else database_auth()[0]


def session_secret() -> str:
    return config.auth_secret if env_auth_configured() else database_auth()[2]


def sign_session(username: str, ttl: int = 86400) -> str:
    expires = int(time.time()) + ttl
    payload = f"{username}|{expires}"
    signature = hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}|{signature}".encode()).decode()


def session_username(token: str) -> str | None:
    try:
        if not auth_configured():
            return None
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        username, expires_text, signature = decoded.rsplit("|", 2)
        payload = f"{username}|{expires_text}"
        expected = hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
        if (
            not hmac.compare_digest(signature, expected)
            or int(expires_text) < int(time.time())
            or not hmac.compare_digest(username, active_username())
        ):
            return None
        return username
    except (ValueError, UnicodeDecodeError):
        return None


def require_auth(request: Request) -> str:
    if config.auth_disabled:
        return "disabled"
    if not auth_configured():
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    username = session_username(request.cookies.get("nutpulse_session", ""))
    if not username:
        raise HTTPException(status_code=401, detail="Authentication required")
    return username


def protect_write(request: Request, _: str = Depends(require_auth)) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Origin check failed")


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


class SettingsUpdate(BaseModel):
    energy_price: float = Field(ge=0, le=1000)
    nut_host: str = Field(default="", max_length=255)
    nut_port: int = Field(ge=1, le=65535)
    poll_interval: int = Field(ge=2, le=3600)
    weekly_report_enabled: bool = False
    report_recipient: str = Field(default="", max_length=320)
    report_weekday: int = Field(default=4, ge=0, le=6)
    report_time: str = Field(default="08:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ConnectionTest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)


class TestReportRequest(BaseModel):
    recipient: str = Field(min_length=3, max_length=320)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    host, _, _ = poller.connection_settings()
    setup_required = not auth_configured()
    status = "ok" if poller.state.last_success else "setup" if setup_required or not host else "degraded"
    return {
        "status": status,
        "setup_required": setup_required,
        "nut_configured": bool(host),
        "database": "ok" if config.database_path.exists() else "initializing",
        "poller": {
            "running": poller.state.running,
            "last_success": poller.state.last_success,
            "last_error": poller.state.last_error,
            "consecutive_errors": poller.state.consecutive_errors,
            "devices_seen": poller.state.devices_seen,
        },
    }


@app.get("/api/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    if config.auth_disabled:
        return {"authenticated": True, "disabled": True, "setup_required": False, "username": "disabled"}
    if not auth_configured():
        return {"authenticated": False, "disabled": False, "setup_required": True, "username": None}
    username = session_username(request.cookies.get("nutpulse_session", ""))
    return {"authenticated": bool(username), "disabled": False, "setup_required": False, "username": username}


@app.post("/api/setup")
async def setup(payload: SetupRequest, request: Request, response: Response) -> dict[str, Any]:
    if config.auth_disabled or auth_configured():
        raise HTTPException(status_code=409, detail="Initial setup is already complete")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(status_code=403, detail="Origin check failed")
    created = db.create_initial_admin(
        payload.username,
        hash_password(payload.password),
        secrets.token_urlsafe(48),
    )
    if not created:
        raise HTTPException(status_code=409, detail="Initial setup is already complete")
    response.set_cookie(
        "nutpulse_session",
        sign_session(payload.username),
        httponly=True,
        samesite="strict",
        secure=config.cookie_secure,
        max_age=86400,
        path="/",
    )
    return {"authenticated": True, "username": payload.username}


@app.post("/api/auth/login")
async def login(payload: LoginRequest, response: Response) -> dict[str, Any]:
    if not auth_configured():
        raise HTTPException(status_code=409, detail="Initial setup is required")
    valid_user = hmac.compare_digest(payload.username, active_username())
    if env_auth_configured():
        valid_password = hmac.compare_digest(payload.password, config.web_password)
    else:
        valid_password = verify_password(payload.password, database_auth()[1])
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie(
        "nutpulse_session",
        sign_session(payload.username),
        httponly=True,
        samesite="strict",
        secure=config.cookie_secure,
        max_age=86400,
        path="/",
    )
    return {"authenticated": True, "username": payload.username}


@app.post("/api/auth/logout", dependencies=[Depends(protect_write)])
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("nutpulse_session", path="/")
    return {"ok": True}


def serialize_device(item: dict[str, Any], now: int, price: float) -> dict[str, Any]:
    power = item.get("real_power_w")
    monthly = (power / 1000) * 24 * 30 * price if power is not None else None
    annual = (power / 1000) * 24 * 365 * price if power is not None else None
    sample_ts = item.get("sample_ts")
    freshness = now - int(sample_ts) if sample_ts else None
    return {
        "id": item["id"],
        "nut_name": item["nut_name"],
        "description": item["description"],
        "model": item["model"],
        "manufacturer": item["manufacturer"],
        "serial": item["serial"],
        "driver": item["driver"],
        "usb_id": item["usb_id"],
        "online": bool(item["online"]),
        "last_seen": item["last_seen"],
        "last_error": item["last_error"],
        "sample_ts": sample_ts,
        "freshness_seconds": freshness,
        "stale": freshness is None or freshness > max(60, int(db.get_setting("poll_interval", 10)) * 3),
        "battery_charge": item.get("battery_charge"),
        "runtime_seconds": item.get("runtime_seconds"),
        "load_percent": item.get("load_percent"),
        "input_voltage": item.get("input_voltage"),
        "output_voltage": item.get("output_voltage"),
        "real_power_w": power,
        "status": item.get("status") or "",
        "monthly_cost": monthly,
        "annual_cost": annual,
        "variables": item.get("variables", {}),
    }


@app.get("/api/overview", dependencies=[Depends(require_auth)])
async def overview() -> dict[str, Any]:
    now = int(time.time())
    price = db.current_energy_price()
    devices = [serialize_device(item, now, price) for item in db.latest_devices()]
    host, _, _ = poller.connection_settings()
    return {
        "updated_at": poller.state.last_success,
        "energy_price": price,
        "nut_configured": bool(host),
        "all_online": bool(devices) and all(item["online"] and not item["stale"] for item in devices),
        "devices": devices,
    }


@app.get("/api/devices", dependencies=[Depends(require_auth)])
async def devices() -> dict[str, Any]:
    current = await overview()
    host, port, interval = poller.connection_settings()
    return {
        "server": {
            "name": config.nut_server_name,
            "host": host,
            "port": port,
            "connected": poller.state.last_success is not None and poller.state.consecutive_errors == 0,
            "read_only": True,
            "poll_interval": interval,
            "last_success": poller.state.last_success,
            "last_error": poller.state.last_error,
        },
        "devices": current["devices"],
    }


@app.get("/api/history", dependencies=[Depends(require_auth)])
async def history(range: str = "7d", device: str | None = None) -> dict[str, Any]:
    seconds = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}.get(range, 7 * 86400)
    end_ts = int(time.time())
    start_ts = end_ts - seconds
    series = db.history(start_ts, end_ts, device)
    energy, cost, complete = db.energy_and_cost(start_ts, end_ts, device)
    events = db.events(start_ts, end_ts, device)
    duration = max(1, seconds)
    unavailable = sum(
        max(0, (event.get("ended_at") or end_ts) - event["started_at"])
        for event in events
        if event["event_type"] == "communication_lost"
    )
    availability = max(0.0, 100 * (1 - unavailable / duration))
    return {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "range": range,
        "device": device,
        "energy_kwh": energy,
        "cost": cost,
        "complete": complete,
        "power_events": sum(1 for event in events if event["event_type"] == "utility_power_failed"),
        "availability": availability,
        "series": series,
        "events": events,
    }


@app.get("/api/events", dependencies=[Depends(require_auth)])
async def events(days: int = 7, device: str | None = None) -> dict[str, Any]:
    end_ts = int(time.time())
    start_ts = end_ts - max(1, min(days, 365)) * 86400
    return {"events": db.events(start_ts, end_ts, device)}


@app.get("/api/settings", dependencies=[Depends(require_auth)])
async def settings() -> dict[str, Any]:
    saved = db.settings()
    return {
        "energy_price": db.current_energy_price(),
        "nut_host": saved.get("nut_host", config.nut_host),
        "nut_port": saved.get("nut_port", config.nut_port),
        "poll_interval": saved.get("poll_interval", config.nut_poll_interval),
        "weekly_report_enabled": saved.get("weekly_report_enabled", False),
        "report_recipient": saved.get("report_recipient", ""),
        "report_weekday": saved.get("report_weekday", 4),
        "report_time": saved.get("report_time", "08:00"),
        "timezone": config.timezone,
        "smtp_configured": reports.smtp_configured(),
        "read_only": True,
        "discovered_devices": [item["nut_name"] for item in db.latest_devices()],
    }


@app.put("/api/settings", dependencies=[Depends(protect_write)])
async def update_settings(payload: SettingsUpdate) -> dict[str, Any]:
    current_price = db.current_energy_price()
    if abs(current_price - payload.energy_price) > 1e-9:
        db.set_energy_price(payload.energy_price)
    values = payload.model_dump(exclude={"energy_price"})
    for key, value in values.items():
        db.set_setting(key, value)
    return await settings()


@app.post("/api/settings/test-nut", dependencies=[Depends(protect_write)])
async def test_nut(payload: ConnectionTest) -> dict[str, Any]:
    try:
        return await poller.test_connection(payload.host, payload.port)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/reports/preview", dependencies=[Depends(require_auth)])
async def report_preview() -> dict[str, Any]:
    preview = reports.preview()
    preview.pop("html", None)
    preview.pop("text", None)
    return preview


@app.post("/api/reports/test", dependencies=[Depends(protect_write)])
async def send_test_report(payload: TestReportRequest) -> dict[str, bool]:
    if "@" not in payload.recipient:
        raise HTTPException(status_code=422, detail="A valid recipient is required")
    try:
        await asyncio.to_thread(reports.send, payload.recipient, True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
