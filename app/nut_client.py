from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass


class NutClientError(RuntimeError):
    pass


@dataclass
class NutUps:
    name: str
    description: str


class NutClient:
    def __init__(self, host: str, port: int = 3493, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    async def _command(self, command: str) -> list[str]:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise NutClientError(f"Unable to connect to {self.host}:{self.port}: {exc}") from exc

        lines: list[str] = []
        try:
            writer.write((command + "\n").encode())
            await asyncio.wait_for(writer.drain(), self.timeout)
            while True:
                raw = await asyncio.wait_for(reader.readline(), self.timeout)
                if not raw:
                    break
                line = raw.decode(errors="replace").strip()
                if line.startswith("ERR "):
                    raise NutClientError(line)
                lines.append(line)
                if line.startswith("END LIST ") or line.startswith("OK "):
                    break
                if not line.startswith(("BEGIN LIST ", "UPS ", "VAR ")):
                    break
        finally:
            writer.write(b"LOGOUT\n")
            try:
                await writer.drain()
            except (OSError, ConnectionError):
                pass
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        return lines

    async def list_ups(self) -> list[NutUps]:
        lines = await self._command("LIST UPS")
        devices: list[NutUps] = []
        for line in lines:
            if not line.startswith("UPS "):
                continue
            parts = shlex.split(line)
            if len(parts) >= 3:
                devices.append(NutUps(parts[1], parts[2]))
        return devices

    async def list_vars(self, ups_name: str) -> dict[str, str]:
        lines = await self._command(f"LIST VAR {ups_name}")
        result: dict[str, str] = {}
        for line in lines:
            if not line.startswith("VAR "):
                continue
            parts = shlex.split(line)
            if len(parts) >= 4:
                result[parts[2]] = parts[3]
        return result
