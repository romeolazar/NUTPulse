from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    database_path: Path
    timezone: str
    nut_host: str
    nut_server_name: str
    nut_port: int
    nut_poll_interval: int
    energy_price: float
    retention_days: int
    web_username: str
    web_password: str
    auth_secret: str
    auth_disabled: bool
    cookie_secure: bool
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_starttls: bool

    @classmethod
    def from_env(cls) -> "AppConfig":
        data_dir = Path(os.getenv("DATA_DIR", "/data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            data_dir=data_dir,
            database_path=data_dir / "nutpulse.db",
            timezone=os.getenv("TZ", "Europe/Bucharest"),
            nut_host=os.getenv("NUT_HOST", ""),
            nut_server_name=os.getenv("NUT_SERVER_NAME", "NUC"),
            nut_port=int(os.getenv("NUT_PORT", "3493")),
            nut_poll_interval=max(2, int(os.getenv("NUT_POLL_INTERVAL", "10"))),
            energy_price=max(0.0, float(os.getenv("ENERGY_PRICE", "1.20"))),
            retention_days=max(7, int(os.getenv("RAW_SAMPLE_RETENTION_DAYS", "90"))),
            web_username=os.getenv("WEB_USERNAME", "admin"),
            web_password=os.getenv("WEB_PASSWORD", ""),
            auth_secret=os.getenv("AUTH_SECRET", ""),
            auth_disabled=env_bool("AUTH_DISABLED", False),
            cookie_secure=env_bool("COOKIE_SECURE", False),
            smtp_host=os.getenv("SMTP_HOST", ""),
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_starttls=env_bool("SMTP_STARTTLS", True),
        )
