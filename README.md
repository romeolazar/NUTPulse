# NUTPulse

**A modern, read-only dashboard for [Network UPS Tools](https://networkupstools.org/).**

NUTPulse connects to an existing NUT server and turns UPS telemetry into a focused dashboard with live status, historical charts, per-device energy costs, battery details, event history, and weekly reports.

It does not configure UPS hardware and does not send control commands to NUT.

Docker images are published at [Docker Hub](https://hub.docker.com/r/romeolazar/nutpulse) for `linux/amd64` and `linux/arm64`. Use `romeolazar/nutpulse:latest` for the current release.

## Screenshots

All screenshots use fictional demonstration data.

| Overview | History |
| --- | --- |
| ![NUTPulse overview](docs/mockups/overview-lucide-metric-icons.png) | ![NUTPulse history](docs/mockups/history.png) |

| Device details | Settings |
| --- | --- |
| ![NUTPulse device details](docs/mockups/devices-technical-details.png) | ![NUTPulse settings](docs/mockups/settings.png) |

## Features

- Automatic discovery of all UPS devices exposed by one NUT server
- Live battery, runtime, load, power, voltage, and status information
- Detailed per-device identity, battery health, electrical data, and raw NUT variables
- Historical charts for power, battery, load, and input voltage
- Monthly and annual energy-cost estimates for each UPS
- Warning and critical event history
- Weekly HTML e-mail report preview and optional SMTP delivery
- Cookie-based web authentication
- Responsive desktop and mobile interface
- SQLite persistence with configurable retention
- Docker-first deployment

## Read-only by design

NUTPulse only uses the NUT protocol commands required to discover and read devices:

- `LIST UPS`
- `LIST VAR <ups>`

It does not issue `INSTCMD`, `SET`, forced shutdown, or UPS control commands.

## Requirements

- Docker with Docker Compose
- An existing NUT server reachable from the container
- One or more UPS devices exposed by that server

## Quick start

```bash
git clone https://github.com/romeolazar/NUTPulse.git
cd NUTPulse
cp .env.example .env
```

Start the application:

```bash
docker compose pull
docker compose up -d
```

Open <http://127.0.0.1:9001>. On first run, NUTPulse asks you to create the administrator account. No UPS server is configured automatically; add it later from **Settings → UPS setup**.

The browser-created password is stored only as a `scrypt` hash, and the session secret is generated locally in the persistent database. Environment-managed credentials remain available for automated deployments.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `NUT_HOST` | empty | Optional initial NUT server hostname or address |
| `NUT_SERVER_NAME` | `NUT Server` | Friendly server name shown in Devices |
| `NUT_PORT` | `3493` | NUT server TCP port |
| `NUT_POLL_INTERVAL` | `10` | Polling interval in seconds |
| `ENERGY_PRICE` | `1.20` | Initial energy price per kWh |
| `TZ` | `UTC` | Application timezone |
| `RAW_SAMPLE_RETENTION_DAYS` | `90` | Raw sample retention period |
| `WEB_USERNAME` | `admin` | Dashboard username |
| `WEB_PASSWORD` | empty | Optional environment-managed dashboard password |
| `AUTH_SECRET` | empty | Required only with an environment-managed password |
| `COOKIE_SECURE` | `false` | Set to `true` behind HTTPS |
| `SMTP_*` | empty | Optional weekly-report SMTP configuration |

Settings saved from the interface are stored in the application database and take precedence over the corresponding initial defaults.

## Persistent data

SQLite data is stored in the Docker volume `nutpulse_data`. Back up this volume before upgrades.

The database, `.env`, logs, and runtime configuration are intentionally excluded from version control.

## Production notes

- Complete first-run setup immediately after deployment, or provide a unique `WEB_PASSWORD` and `AUTH_SECRET` through the environment.
- Serve NUTPulse through HTTPS and enable `COOKIE_SECURE=true`.
- Restrict the NUT server so it is reachable only from trusted networks or containers.
- Do not publish `.env`, database files, SMTP credentials, UPS serial numbers, or internal addresses.
- Review `compose.production.example.yaml` before deployment.

## Development checks

```bash
python3 -m unittest discover -s tests -v
python3 -c 'from pathlib import Path; [compile(path.read_text(), str(path), "exec") for path in Path("app").glob("*.py")]'
docker compose config --quiet
```

## Roadmap

Planned improvements, including a clearer Settings page with separate save actions for energy, UPS, and e-mail configuration, are tracked in [ROADMAP.md](ROADMAP.md).

## License and third-party software

NUTPulse is released under the [MIT License](LICENSE). Third-party notices are documented in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
