import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../app/static/app.js", import.meta.url), "utf8");
const index = readFileSync(new URL("../app/static/index.html", import.meta.url), "utf8");

assert.ok(index.includes("NUTPulse"), "Missing NUTPulse brand");
assert.ok(!index.includes("UPS Power"), "Legacy UPS Power brand remains");

const between = (start, end) => {
  const startAt = source.indexOf(start);
  const endAt = source.indexOf(end, startAt);
  assert.notEqual(startAt, -1, `Missing ${start}`);
  assert.notEqual(endAt, -1, `Missing ${end}`);
  return source.slice(startAt, endAt);
};

const formatSource = between("function formatNumber", "function formatRuntime");
const batterySource = between("function batteryState", "function pageHeader");
const batteryState = Function(`"use strict"; ${formatSource} ${batterySource}; return batteryState;`)();

assert.deepEqual(batteryState(100), { level: 100, label: "100%", color: "var(--green)", state: "healthy" });
assert.deepEqual(batteryState(50), { level: 50, label: "50%", color: "var(--green)", state: "healthy" });
assert.deepEqual(batteryState(20), { level: 20, label: "20%", color: "var(--amber)", state: "warning" });
assert.deepEqual(batteryState(10), { level: 10, label: "10%", color: "var(--red)", state: "critical" });
assert.deepEqual(batteryState(null), { level: 0, label: "N/A", color: "var(--muted-2)", state: "unknown" });

for (const icon of [
  "icon-overview", "icon-history", "icon-devices", "icon-settings", "icon-power",
  "icon-clock", "icon-gauge", "icon-zap", "icon-plug-input", "icon-plug-output",
  "icon-coins", "icon-arrow-right", "icon-battery", "icon-list-tree",
  "icon-chevron-down", "icon-download", "icon-triangle-alert", "icon-circle-check",
  "icon-chart-line", "icon-history-list", "icon-activity", "icon-server", "icon-plug",
  "icon-mail", "icon-file-chart", "icon-inbox", "icon-send", "icon-save",
]) {
  assert.ok(index.includes(`id="${icon}"`), `Missing ${icon}`);
}

for (const legacyGlyph of ["◷", "▥", "⚡", "↯", "◎", "△", "✓", "☷", "⌁", "✉", "▤", "⏻"]) {
  assert.ok(!source.includes(legacyGlyph), `Legacy glyph remains in app.js: ${legacyGlyph}`);
}

for (const devicesFeature of [
  "Device identity", "Battery charge", "Current load", "Estimated runtime",
  "Battery details", "Electrical & connection", "All NUT variables",
]) {
  assert.ok(source.includes(devicesFeature), `Missing Devices detail feature: ${devicesFeature}`);
}

console.log("Frontend checks passed: dynamic battery thresholds and Lucide-style icons across all pages.");
