from __future__ import annotations

import asyncio
import html
import smtplib
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from .config import AppConfig
from .database import Database


LABELS = [
    ("number_of_devices", "Number of Devices"),
    ("warning_event", "Warning Event"),
    ("critical_event", "Critical Event"),
    ("utility_power_failed", "Utility Power Failed"),
    ("enabling_buck", "Enabling Buck"),
    ("enabling_boost", "Enabling Boost"),
    ("overload", "Overload"),
]


def previous_seven_days(now: datetime) -> tuple[int, int, str, str]:
    end = now
    start = end - timedelta(days=7)
    return int(start.timestamp()), int(end.timestamp()), start.date().isoformat(), end.date().isoformat()


def render_report(summary: dict[str, int], date_from: str, date_to: str, details_url: str = "") -> tuple[str, str]:
    text_lines = ["Last 7 Days Event Summary", f"{date_from} - {date_to}", ""]
    cards = []
    for key, label in LABELS:
        value = int(summary.get(key, 0))
        text_lines.append(f"{label}: {value}")
        cards.append(
            f'<div style="border:1px solid #273142;border-radius:10px;padding:18px;background:#141922;">'
            f'<div style="color:#aab7c9;font-size:14px;">{html.escape(label)}</div>'
            f'<div style="color:#f5f7fa;font-size:30px;font-weight:700;margin-top:7px;">{value}</div></div>'
        )
    link = ""
    if details_url:
        safe_url = html.escape(details_url, quote=True)
        link = f'<a href="{safe_url}" style="display:inline-block;margin-top:22px;padding:12px 18px;border-radius:8px;background:#268cff;color:white;text-decoration:none;">Check details</a>'
        text_lines.extend(["", f"Check details: {details_url}"])
    body = f"""<!doctype html><html><body style="margin:0;background:#0b0e13;font-family:Arial,sans-serif;color:#f5f7fa;">
    <div style="max-width:760px;margin:0 auto;padding:32px 22px;">
      <div style="display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid #273142;padding-bottom:16px;">
        <h1 style="margin:0;font-size:28px;">Last 7 Days Event Summary</h1>
        <div style="color:#aab7c9;">{date_from} - {date_to}</div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px;margin-top:24px;">{''.join(cards)}</div>
      {link}
    </div></body></html>"""
    return "\n".join(text_lines), body


class ReportService:
    def __init__(self, db: Database, config: AppConfig):
        self.db = db
        self.config = config
        self._stop = asyncio.Event()

    def preview(self) -> dict:
        now = datetime.now(ZoneInfo(self.config.timezone))
        start_ts, end_ts, date_from, date_to = previous_seven_days(now)
        summary = self.db.report_summary(start_ts, end_ts)
        text, report_html = render_report(summary, date_from, date_to)
        return {
            "period_start": start_ts,
            "period_end": end_ts,
            "date_from": date_from,
            "date_to": date_to,
            "summary": summary,
            "text": text,
            "html": report_html,
        }

    def smtp_configured(self) -> bool:
        return bool(self.config.smtp_host and self.config.smtp_from)

    def send(self, recipient: str, test: bool = False) -> None:
        if not self.smtp_configured():
            raise RuntimeError("SMTP is not configured")
        preview = self.preview()
        subject = "NUTPulse — Last 7 Days Event Summary"
        if test:
            subject = f"[TEST] {subject}"
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.config.smtp_from
        message["To"] = recipient
        message.set_content(preview["text"])
        message.add_alternative(preview["html"], subtype="html")
        try:
            with smtplib.SMTP(self.config.smtp_host, self.config.smtp_port, timeout=15) as smtp:
                if self.config.smtp_starttls:
                    smtp.starttls()
                if self.config.smtp_username:
                    smtp.login(self.config.smtp_username, self.config.smtp_password)
                smtp.send_message(message)
            self.db.record_report(
                preview["period_start"], preview["period_end"], recipient, "sent"
            )
        except Exception as exc:
            self.db.record_report(
                preview["period_start"], preview["period_end"], recipient, "failed", str(exc)
            )
            raise

    async def run(self) -> None:
        last_key = str(self.db.get_setting("last_weekly_report_key", ""))
        while not self._stop.is_set():
            try:
                enabled = bool(self.db.get_setting("weekly_report_enabled", False))
                recipient = str(self.db.get_setting("report_recipient", ""))
                weekday = int(self.db.get_setting("report_weekday", 4))
                report_time = str(self.db.get_setting("report_time", "08:00"))
                now = datetime.now(ZoneInfo(self.config.timezone))
                current_key = f"{now.date().isoformat()}-{report_time}"
                if (
                    enabled
                    and recipient
                    and self.smtp_configured()
                    and now.weekday() == weekday
                    and now.strftime("%H:%M") == report_time
                    and current_key != last_key
                ):
                    await asyncio.to_thread(self.send, recipient, False)
                    self.db.set_setting("last_weekly_report_key", current_key)
                    last_key = current_key
            except Exception:
                # The next scheduler cycle retries; the report_runs table records send errors.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
