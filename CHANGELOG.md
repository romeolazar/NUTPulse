# Changelog

## 0.2.1

- **Long-term history:** completed hours are summarised into permanent hourly records, so energy and cost are available for 90 days, one year and beyond. Raw samples are still pruned after `RAW_SAMPLE_RETENTION_DAYS`, but only once summarised. The History page gains 90-day and 1-year ranges, a per-UPS table and a per-month table.
- **Power estimation:** UPS models without `ups.realpower` (such as APC Back-UPS) now get an estimated power from `ups.load` × `ups.realpower.nominal`, marked as estimated. Existing samples are backfilled on first start, so their cost history is no longer empty.
- **Much smaller database:** samples no longer store every NUT variable. On first start the database is migrated and compacted (a 943 MB database shrank to about 32 MB). Back up the `nutpulse_data` volume before upgrading.
- **Polling:** a UPS that returns an error (for example `DATA-STALE`) no longer stops the other devices from being recorded, and only that UPS is marked offline after three consecutive failures.
- **Fix:** logging in with a non-ASCII username or password, or sending a forged non-ASCII session cookie, no longer causes a server error.
- Default `TZ` is now `UTC` and the default server name `NUT Server` everywhere, matching the documentation.
