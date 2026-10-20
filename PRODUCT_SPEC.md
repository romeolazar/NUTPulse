# NUTPulse — Product Specification

**Status:** Draft
**Date:** 2026-10-20
**Target repository:** `nutpulse`

## 1. Objective

Build a self-hosted Docker application that replaces the PeaNUT web interface while keeping the existing Network UPS Tools server (`nut-server`) unchanged.

The application must:

- monitor multiple UPS devices exposed by NUT;
- provide a polished, responsive dashboard;
- calculate estimated and historical electricity costs;
- store telemetry and events;
- send scheduled email reports;
- run read-only by default;
- be deployable alongside PeaNUT for a zero-downtime migration.

This application replaces the PeaNUT dashboard only. It does **not** replace `nut-server`, the NUT USB drivers, or the UPS hardware configuration.

## 2. Example deployment

- NUT endpoint inside Docker: `nut-server:3493`
- Host endpoint: configured through `NUT_HOST` and `NUT_PORT`
- UPS devices:
  - `apc700` — APC Back-UPS XS 700U
  - `cyberpower1600` — CyberPower CP1600EPFCLCD
- Initial web port: `9001`
- Existing PeaNUT port during migration: `9000`
- Currency: Romanian leu (`Lei`)
- Initial energy price: `1.20 Lei/kWh`
- Time zone: `Europe/Bucharest`

## 3. High-level architecture

```text
UPS devices over USB
        │
        ▼
nut-server:3493
        │
        ▼
NUTPulse backend
  ├── NUT polling
  ├── cost calculation
  ├── event detection
  ├── reporting
  └── persistent storage
        │
        ▼
Responsive web frontend
```

Recommended components:

- a web frontend implementing the approved dark dashboard;
- a server-side NUT client so the browser never connects directly to port `3493`;
- SQLite for settings, samples, events, and report history;
- a persistent Docker volume mounted at `/data`;
- SMTP support for scheduled reports;
- an application health endpoint and Docker healthcheck.

InfluxDB may be supported later, but it is not required for the first release.

## 4. Safety and security

- Default mode is read-only.
- The first release must not expose UPS shutdown, load-off, battery-test, or writable NUT commands.
- NUT credentials, SMTP passwords, and application secrets must come from environment variables, Docker secrets, or protected configuration files.
- Secrets must never be returned by the API or rendered in the settings page.
- Authentication is enabled by default.
- Sessions must use secure, HTTP-only cookies when served over HTTPS.
- The application should bind to the configured interface only and be placed behind the existing reverse proxy when promoted to production.

## 5. Navigation

The application contains four primary sections:

1. Overview
2. History
3. Devices
4. Settings

The desktop layout uses a fixed left sidebar. On mobile, the navigation collapses into a compact menu or bottom navigation.

## 6. Overview

The Overview page prioritizes current operational state.

### Header

- title: `NUTPulse`;
- aggregate status, for example `All systems online`;
- data freshness indicator, for example `Updated just now`;
- stale or disconnected data must be visibly distinguished from a healthy state.

### UPS cards

Render one primary card per UPS. Each card contains:

- device model and NUT identifier;
- translated status badge;
- battery charge;
- estimated runtime;
- UPS load;
- input voltage;
- output voltage, when available;
- real power in watts, when available;
- estimated monthly cost for that UPS;
- estimated annual cost for that UPS.

The energy price is **not** edited or displayed as a global card on Overview. It is configured in Settings.

Initial examples at `1.20 Lei/kWh`, assuming constant 24/7 power:

| Device | Real power | Monthly estimate | Annual estimate |
| --- | ---: | ---: | ---: |
| `apc700` | 27 W | 23.33 Lei | 283.82 Lei |
| `cyberpower1600` | 91 W | 78.62 Lei | 956.59 Lei |

### Charts

- Battery & Load
- Input Voltage

Raw NUT fields belong in a collapsed `Technical details` section. Fields such as `driver.*`, USB identifiers, firmware, and serial numbers must not dominate the main dashboard.

## 7. History

The History page provides persisted measurements and detected events.

### Filters

- predefined ranges: 24 hours, 7 days, 30 days;
- custom date range;
- device selector: all devices or one UPS;
- CSV export for the selected range.

### Summary metrics

- energy used in kWh;
- estimated cost for the selected period;
- number of power events;
- availability percentage.

### Charts

- power consumption;
- battery charge and load;
- input voltage;
- optional runtime history.

Charts must show separate series for `apc700` and `cyberpower1600` and must remain readable when only one device is selected.

### Event history

Columns:

- Time
- Device
- Severity
- Event
- Duration

If no event exists, show a healthy empty state rather than an empty table.

## 8. Devices

The Devices page shows server connectivity and device-level details.

### NUT server card

- host and port;
- connection status;
- polling interval;
- read-only status;
- last successful poll.

### Device cards

For each device show:

- model;
- NUT identifier;
- online/offline/stale state;
- battery charge;
- runtime;
- load;
- real power;
- input voltage;
- driver;
- USB vendor/product ID;
- a `View details` action.

### Connection health

Show:

- last response;
- data freshness;
- consecutive polling errors;
- current error, if any.

No destructive UPS control is included in the initial version.

## 9. Settings

### 9.1 Energy & cost

- energy price input, default `1.20`;
- unit `Lei/kWh`;
- calculation basis selector;
- optional effective date for future tariff changes;
- validation preventing negative or invalid values.

The configured price is applied to each UPS individually and to historical cost calculations.

### 9.2 UPS setup

- NUT host, default `nut-server`;
- NUT port, default `3493`;
- polling interval, default `10 seconds`;
- connection test;
- read-only mode, enabled by default;
- list of discovered devices;
- optional friendly name per NUT device.

Changing NUT connection settings must not silently erase historical data. Device history is keyed by a stable internal device ID plus the NUT identifier.

### 9.3 Send report

- enable/disable weekly email report;
- recipient address;
- schedule day;
- schedule time;
- time zone;
- `Send test report` action;
- report delivery status and last successful send;
- `Save settings` action.

SMTP configuration should be loaded securely from deployment configuration. The UI may show server status but must not reveal the SMTP password.

## 10. Weekly email report

Subject example:

```text
NUTPulse — Last 7 Days Event Summary
```

The email contains:

```text
Last 7 Days Event Summary
2026-10-09 - 2026-10-16

Number of Devices: 2
Warning Event: 0
Critical Event: 0
Utility Power Failed: 0
Enabling Buck: 0
Enabling Boost: 0
Overload: 0
```

The final email should use responsive HTML and include a plain-text alternative.

`Check details` links back to the History page with the report date range preselected.

## 11. Status and event mapping

NUT statuses should be translated into human-readable states while preserving the original code in technical details.

Suggested mapping:

| NUT signal | Display/event | Severity |
| --- | --- | --- |
| `OL` | Online | Healthy |
| `OB` | Utility Power Failed / On Battery | Warning |
| `LB` | Low Battery | Critical |
| `RB` | Replace Battery | Critical |
| `BOOST` | Enabling Boost | Warning |
| `TRIM` | Enabling Buck | Warning |
| `OVER` | Overload | Critical |
| stale polling | Communication Lost | Critical |

An event is created on a state transition, not on every poll. The event is closed when the condition clears, allowing duration calculation.

## 12. Cost calculations

### Current-load estimate

```text
monthly_cost = (real_power_w / 1000) × 24 × 30 × price_per_kwh
annual_cost  = (real_power_w / 1000) × 24 × 365 × price_per_kwh
```

This estimate must be labeled clearly because it assumes the current real power remains constant.

### Historical cost

Historical energy must be calculated by integrating power samples over time:

```text
energy_kwh += average_power_w × elapsed_hours / 1000
cost = energy_kwh × applicable_price_per_kwh
```

Use timestamps and the trapezoidal method between consecutive valid samples. Long data gaps must not be treated as continuous consumption and should be marked incomplete.

## 13. Persistence model

Minimum logical entities:

- `settings`
- `devices`
- `samples`
- `events`
- `energy_prices`
- `report_runs`

Recommended retention:

- raw samples: configurable, default 90 days;
- hourly aggregates: retain long term;
- events: retain until explicitly deleted;
- report runs: retain delivery metadata without storing SMTP secrets.

## 14. Application API

Suggested internal endpoints:

- `GET /api/health`
- `GET /api/overview`
- `GET /api/devices`
- `GET /api/devices/:id`
- `GET /api/history`
- `GET /api/events`
- `GET /api/settings`
- `PUT /api/settings`
- `POST /api/settings/test-nut`
- `POST /api/reports/test`
- `GET /api/reports/preview`

Write endpoints require authentication and CSRF protection.

## 15. Docker delivery

The project should provide:

- a production `Dockerfile`;
- multi-stage build;
- non-root runtime user;
- `linux/amd64` and `linux/arm64` images;
- image publication to GHCR or Docker Hub;
- Compose example;
- persistent volume for `/data`;
- healthcheck;
- `.env.example` without secrets;
- documented backup and restore procedure.

Example service shape:

```yaml
services:
  nutpulse:
    image: ghcr.io/romeolazar/nutpulse:latest
    restart: unless-stopped
    environment:
      NUT_HOST: nut-server
      NUT_PORT: "3493"
      TZ: Europe/Bucharest
    volumes:
      - ./nutpulse-data:/data
    ports:
      - "127.0.0.1:9001:8080"
```

The final Compose file must also declare the dependency on `nut-server` health and the required SMTP/authentication secrets.

## 16. Visual design

- dark charcoal background;
- restrained green for healthy states;
- amber for warnings;
- red for critical states;
- blue and amber chart series;
- high contrast, compact but readable spacing;
- 12 px card radius;
- no oversized mascot or decorative illustration;
- no raw variables on the primary views;
- desktop and narrow mobile layouts must both be verified.

## 17. Zero-downtime rollout

1. Build and run NUTPulse on port `9001`.
2. Keep PeaNUT running on port `9000`.
3. Compare device values with direct `upsc` output.
4. Verify sample persistence across container recreation.
5. Verify monthly and annual calculations.
6. Send a test report to an approved recipient.
7. Verify desktop and mobile layouts.
8. Switch the Homepage/reverse-proxy link to NUTPulse.
9. Keep PeaNUT available during an observation period.
10. Stop PeaNUT only after explicit approval.

Rollback consists of switching the link back to port `9000`; `nut-server` remains untouched throughout.

## 18. Acceptance criteria

- both UPS devices are discovered automatically;
- current values match `upsc` within one polling interval;
- stale data is never displayed as healthy;
- monthly and annual estimates are shown per UPS;
- the energy price is editable only in Settings;
- historical energy and costs survive container recreation;
- state transitions create exactly one event with correct duration;
- the weekly email includes all requested counters;
- test reports clearly identify themselves as tests;
- secrets are absent from logs and API responses;
- the application passes its Docker healthcheck;
- deployment and rollback do not interrupt `nut-server`.

## 19. Suggested implementation order

1. Project skeleton, Docker build, authentication, and healthcheck.
2. NUT read-only client and device discovery.
3. Overview and Devices pages.
4. SQLite sampling and History charts.
5. Cost calculation and Energy Price settings.
6. Event detection and event history.
7. Email report preview, scheduling, and delivery.
8. Responsive/mobile QA.
9. Multi-architecture image publishing.
10. Side-by-side production validation and controlled cutover.
