const state = {
  page: "overview",
  authenticated: false,
  username: null,
  historyRange: "7d",
  historyDevice: "",
  selectedDevice: "",
  refreshTimer: null,
};

const main = document.getElementById("main-content");
const appShell = document.getElementById("app-shell");
const loginView = document.getElementById("login-view");
const setupView = document.getElementById("setup-view");
const toastEl = document.getElementById("toast");

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

function uiIcon(name, extraClass = "") {
  return `<svg class="ui-icon ${extraClass}" aria-hidden="true" focusable="false"><use href="#icon-${name}"></use></svg>`;
}

function toast(message, error = false) {
  toastEl.textContent = message;
  toastEl.className = `toast${error ? " error" : ""}`;
  clearTimeout(toastEl._timer);
  toastEl._timer = setTimeout(() => toastEl.classList.add("hidden"), 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  let payload = null;
  try { payload = await response.json(); } catch { payload = {}; }
  if (response.status === 401) {
    showLogin();
    throw new Error("Authentication required");
  }
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function formatNumber(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function formatRuntime(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.max(0, Math.round(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  return hours ? `${hours}h ${String(minutes).padStart(2, "0")}m` : `${minutes}m`;
}

function relativeTime(timestamp) {
  if (!timestamp) return "Never";
  const diff = Math.max(0, Math.round(Date.now() / 1000 - timestamp));
  if (diff < 5) return "Just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return new Date(timestamp * 1000).toLocaleString();
}

function eventDuration(event) {
  const end = event.ended_at || Math.round(Date.now() / 1000);
  const seconds = Math.max(0, end - event.started_at);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
}

function deviceState(device) {
  const tokens = new Set(String(device.status || "").split(/\s+/));
  if (!device.online || device.stale) return { label: device.stale ? "Stale" : "Offline", className: "critical", color: "var(--red)" };
  if (tokens.has("LB") || tokens.has("RB") || tokens.has("OVER")) return { label: tokens.has("LB") ? "Low battery" : "Critical", className: "critical", color: "var(--red)" };
  if (tokens.has("OB") || tokens.has("BOOST") || tokens.has("TRIM")) return { label: tokens.has("OB") ? "On battery" : "Warning", className: "warning", color: "var(--amber)" };
  return { label: "Online", className: "", color: "var(--green)" };
}

function batteryState(value) {
  const numericValue = Number(value);
  if (value === null || value === undefined || !Number.isFinite(numericValue)) {
    return { level: 0, label: "N/A", color: "var(--muted-2)", state: "unknown" };
  }
  const level = Math.max(0, Math.min(100, numericValue));
  if (level <= 15) return { level, label: `${formatNumber(level)}%`, color: "var(--red)", state: "critical" };
  if (level < 30) return { level, label: `${formatNumber(level)}%`, color: "var(--amber)", state: "warning" };
  return { level, label: `${formatNumber(level)}%`, color: "var(--green)", state: "healthy" };
}

function pageHeader(title, status, statusClass, updated, subtitle = "") {
  return `<header class="page-header">
    <div><h1>${escapeHtml(title)}</h1>${subtitle ? `<div class="subtitle">${escapeHtml(subtitle)}</div>` : ""}</div>
    <span class="status-badge ${statusClass || ""}">${escapeHtml(status)}</span>
    <span class="updated">${escapeHtml(updated)}</span>
  </header>`;
}

function metric(iconName, label, value, iconClass = "") {
  return `<div class="metric"><span class="metric-icon">${uiIcon(iconName, iconClass)}</span><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function upsCard(device, withDeviceMeta = false) {
  const status = deviceState(device);
  const battery = batteryState(device.battery_charge);
  const title = device.model || device.description || device.nut_name;
  const meta = withDeviceMeta ? `<div class="device-meta">
      <div class="device-meta-item">Driver<strong>${escapeHtml(device.driver || "—")}</strong></div>
      <div class="device-meta-item">USB<strong>${escapeHtml(device.usb_id || "—")}</strong></div>
      <button class="button device-details" data-device="${escapeHtml(device.nut_name)}">View details ${uiIcon("arrow-right", "button-icon")}</button>
    </div>` : `<div class="cost-footer"><span class="coin">${uiIcon("coins")}</span><span class="cost-items">
      <span>Monthly ${formatNumber(device.monthly_cost, 2)} Lei</span><i aria-hidden="true">•</i><span>Annual ${formatNumber(device.annual_cost, 2)} Lei</span>
    </span></div>`;
  return `<article class="panel ${withDeviceMeta ? "device-card" : "ups-card"}">
    <div class="card-heading"><div><h2>${escapeHtml(title)}</h2><div class="device-id">${escapeHtml(device.nut_name)}</div></div>
      <span class="status-badge ${status.className}">${escapeHtml(status.label)}</span></div>
    <div class="ups-body">
      <div class="battery-visual ${battery.state}" style="--battery-level:${battery.level / 100};--battery-color:${battery.color}" role="img" aria-label="Battery ${escapeHtml(battery.label)}">
        <div class="battery-terminal"></div>
        <div class="battery-shell">
          <div class="battery-fill"></div><div class="battery-segments"></div>
          <div class="battery-content"><div class="battery-value">${escapeHtml(battery.label)}</div><div class="battery-label">Battery</div></div>
        </div>
      </div>
      <div class="metric-list">
        ${metric("clock", "Runtime", formatRuntime(device.runtime_seconds))}
        ${metric("gauge", "Load", `${formatNumber(device.load_percent)}%`)}
        ${metric("zap", "Real power", `${formatNumber(device.real_power_w)} W`, "accent-amber")}
        ${metric("plug-input", "Input voltage", `${formatNumber(device.input_voltage)} V`)}
        ${withDeviceMeta ? "" : metric("plug-output", "Output voltage", `${formatNumber(device.output_voltage)} V`)}
      </div>
    </div>${meta}
  </article>`;
}

function chartSvg(series, metricName, options = {}) {
  const width = 760, height = 210;
  const pad = { left: 47, right: 14, top: 12, bottom: 29 };
  const rows = series.filter((row) => row[metricName] !== null && row[metricName] !== undefined);
  const deviceNames = [...new Set(series.map((row) => row.nut_name))];
  const values = rows.map((row) => Number(row[metricName]));
  const timestamps = rows.map((row) => Number(row.ts));
  const minX = timestamps.length ? Math.min(...timestamps) : Date.now() / 1000 - 3600;
  const maxX = timestamps.length ? Math.max(...timestamps) : Date.now() / 1000;
  let minY = options.min ?? (values.length ? Math.min(...values) : 0);
  let maxY = options.max ?? (values.length ? Math.max(...values) : 100);
  if (minY === maxY) { minY = Math.max(0, minY - 1); maxY += 1; }
  if (options.padY !== false) {
    const delta = (maxY - minY) * 0.12;
    minY = Math.max(options.allowNegative ? -Infinity : 0, minY - delta);
    maxY += delta;
  }
  const plotW = width - pad.left - pad.right, plotH = height - pad.top - pad.bottom;
  const x = (ts) => pad.left + ((ts - minX) / Math.max(1, maxX - minX)) * plotW;
  const y = (value) => pad.top + (1 - (value - minY) / Math.max(.0001, maxY - minY)) * plotH;
  const grid = Array.from({ length: 5 }, (_, index) => {
    const yy = pad.top + index * plotH / 4;
    const val = maxY - index * (maxY - minY) / 4;
    return `<line class="grid-line" x1="${pad.left}" y1="${yy}" x2="${width-pad.right}" y2="${yy}"/><text class="axis-text" x="1" y="${yy+4}">${formatNumber(val, options.decimals ?? 0)}${options.unit || ""}</text>`;
  }).join("");
  const timeLabels = Array.from({ length: 5 }, (_, index) => {
    const ts = minX + index * (maxX - minX) / 4;
    const date = new Date(ts * 1000);
    const label = options.multiDay ? `${date.toLocaleDateString(undefined,{month:"short",day:"2-digit"})} ${date.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"})}` : date.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"});
    const xx = pad.left + index * plotW / 4;
    return `<text class="axis-text" text-anchor="${index === 0 ? "start" : index === 4 ? "end" : "middle"}" x="${xx}" y="${height-5}">${escapeHtml(label)}</text>`;
  }).join("");
  const paths = deviceNames.map((name, index) => {
    const points = rows.filter((row) => row.nut_name === name).map((row) => `${x(row.ts).toFixed(1)},${y(row[metricName]).toFixed(1)}`).join(" ");
    return `<polyline class="${index % 2 ? "series-amber" : "series-blue"}" points="${points}"/>`;
  }).join("");
  const empty = rows.length ? "" : `<text class="axis-text" x="${width/2}" y="${height/2}" text-anchor="middle">Waiting for historical samples…</text>`;
  return `<svg class="chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${grid}${timeLabels}${paths}${empty}</svg>
    <div class="legend">${deviceNames.map((name,index)=>`<span><i class="${index%2 ? "amber" : ""}"></i>${escapeHtml(name)}</span>`).join("")}</div>`;
}

function combinedBatteryLoadChart(series, multiDay = false) {
  return `<svg class="chart" viewBox="0 0 760 210" preserveAspectRatio="none">
    ${chartGrid(760, 210, "%")}
    ${polylineFor(series, "battery_charge", 0, 100, "series-blue", 0)}
    ${polylineFor(series, "battery_charge", 0, 100, "series-blue-dash", 1)}
    ${polylineFor(series, "load_percent", 0, 100, "series-amber", 0)}
    ${polylineFor(series, "load_percent", 0, 100, "series-amber-dash", 1)}
  </svg><div class="legend"><span><i></i>APC Battery</span><span><i></i>CyberPower Battery</span><span><i class="amber"></i>APC Load</span><span><i class="amber"></i>CyberPower Load</span></div>`;
}

function chartGrid(width, height, unit = "") {
  const left = 47, right = 14, top = 12, bottom = 29, plotH = height-top-bottom;
  return Array.from({length:5},(_,i)=>{
    const yy=top+i*plotH/4, val=100-i*25;
    return `<line class="grid-line" x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}"/><text class="axis-text" x="3" y="${yy+4}">${val}${unit}</text>`;
  }).join("");
}

function polylineFor(series, metricName, minY, maxY, className, deviceIndex) {
  const devices=[...new Set(series.map(row=>row.nut_name))];
  const selected=series.filter(row=>row.nut_name===devices[deviceIndex] && row[metricName]!==null && row[metricName]!==undefined);
  if (!selected.length) return "";
  const minX=Math.min(...selected.map(row=>row.ts)), maxX=Math.max(...selected.map(row=>row.ts));
  const points=selected.map(row=>{
    const xx=47+((row.ts-minX)/Math.max(1,maxX-minX))*(760-47-14);
    const yy=12+(1-(row[metricName]-minY)/(maxY-minY))*(210-12-29);
    return `${xx.toFixed(1)},${yy.toFixed(1)}`;
  }).join(" ");
  return `<polyline class="${className}" points="${points}"/>`;
}

function loading() {
  main.innerHTML = `<div class="loading"><div><div class="spinner"></div>Loading UPS data…</div></div>`;
}

async function renderOverview() {
  loading();
  const [data, history] = await Promise.all([api("/api/overview"), api("/api/history?range=24h")]);
  const overall = !data.nut_configured ? ["Ready for setup", "warning"] : data.all_online ? ["All systems online", ""] : ["Attention required", "warning"];
  const devices = data.devices || [];
  main.innerHTML = `${pageHeader("NUTPulse", overall[0], overall[1], `Updated ${relativeTime(data.updated_at)}`)}
    ${devices.length ? `<section class="grid two-cols">${devices.map((device)=>upsCard(device)).join("")}</section>
      <section class="grid two-cols" style="margin-top:16px">
        <article class="panel chart-card"><h2 class="panel-title"><span class="icon">${uiIcon("battery")}</span>Battery & Load</h2>${combinedBatteryLoadChart(history.series)}</article>
        <article class="panel chart-card"><h2 class="panel-title"><span class="icon">${uiIcon("plug-input")}</span>Input Voltage</h2>${chartSvg(history.series,"input_voltage",{min:180,max:260,unit:" V"})}</article>
      </section>
      ${technicalDetails(devices)}` : noDevices()}`;
  document.querySelector("[data-configure-ups]")?.addEventListener("click",()=>navigate("settings"));
  updateConnection(data.all_online, data.updated_at, data.nut_configured);
}

function noDevices() {
  return `<section class="panel empty-state"><span class="empty-state-icon">${uiIcon("plug")}</span><h2>Connect your first UPS</h2><p>NUTPulse is ready. Add the address of an existing NUT server to discover its UPS devices.</p><button class="button primary" type="button" data-configure-ups>Open UPS settings</button></section>`;
}

function technicalDetails(devices) {
  const rows = devices.flatMap((device) => Object.entries(device.variables || {}).slice(0, 18).map(([key,value])=>`<div><span>${escapeHtml(device.nut_name)} · ${escapeHtml(key)}</span><code>${escapeHtml(value)}</code></div>`)).join("");
  return `<details class="panel" style="margin-top:16px"><summary class="collapsible"><span>${uiIcon("list-tree")}</span>Technical details<span class="chevron">${uiIcon("chevron-down")}</span></summary><div class="technical-grid">${rows || "No variables available."}</div></details>`;
}

async function renderHistory() {
  loading();
  const deviceQuery = state.historyDevice ? `&device=${encodeURIComponent(state.historyDevice)}` : "";
  const [data, devicesData] = await Promise.all([api(`/api/history?range=${state.historyRange}${deviceQuery}`), api("/api/devices")]);
  const multiDay = state.historyRange !== "24h";
  main.innerHTML = `${pageHeader("History", `${devicesData.devices.length} devices online`, "", `Updated ${relativeTime(devicesData.server.last_success)}`)}
    <div class="filter-row">
      <div class="segmented">${["24h","7d","30d"].map(range=>`<button data-range="${range}" class="${state.historyRange===range?"active":""}">${range==="24h"?"24 hours":range==="7d"?"7 days":"30 days"}</button>`).join("")}<button disabled>Custom</button></div>
      <select id="history-device" class="field-control"><option value="">All devices</option>${devicesData.devices.map(device=>`<option value="${escapeHtml(device.nut_name)}" ${state.historyDevice===device.nut_name?"selected":""}>${escapeHtml(device.nut_name)}</option>`).join("")}</select>
      <div class="field-control">${new Date(data.start_ts*1000).toLocaleDateString()} – ${new Date(data.end_ts*1000).toLocaleDateString()}</div>
      <button id="export-csv" class="button">${uiIcon("download", "button-icon")} Export CSV</button>
    </div>
    <section class="grid four-cols">
      ${kpi("zap","Energy used",`${formatNumber(data.energy_kwh,2)} kWh`)}
      ${kpi("coins","Estimated cost",`${formatNumber(data.cost,2)} Lei`)}
      ${kpi("triangle-alert","Power events",formatNumber(data.power_events))}
      ${kpi("circle-check","Availability",`${formatNumber(data.availability,2)}%`)}
    </section>
    ${data.complete ? "" : `<p style="color:var(--amber);margin:10px 2px 0">Historical totals exclude data gaps longer than two minutes.</p>`}
    <section class="grid two-cols" style="margin-top:16px">
      <article class="panel chart-card large"><h2 class="panel-title"><span class="icon">${uiIcon("chart-line")}</span>Power consumption</h2>${chartSvg(data.series,"real_power_w",{unit:" W",multiDay})}</article>
      <article class="panel chart-card"><h2 class="panel-title"><span class="icon">${uiIcon("battery")}</span>Battery & Load</h2>${combinedBatteryLoadChart(data.series,multiDay)}</article>
      <article class="panel chart-card"><h2 class="panel-title"><span class="icon">${uiIcon("plug-input")}</span>Input voltage</h2>${chartSvg(data.series,"input_voltage",{min:180,max:260,unit:" V",multiDay})}</article>
    </section>
    ${eventTable(data.events)}`;
  bindHistoryControls(data);
}

function kpi(iconName, label, value) {
  return `<article class="panel kpi"><span class="kpi-icon">${uiIcon(iconName)}</span><div><div class="kpi-label">${escapeHtml(label)}</div><div class="kpi-value">${escapeHtml(value)}</div></div></article>`;
}

function eventTable(events) {
  const rows = events.length ? events.map(event=>`<tr><td>${new Date(event.started_at*1000).toLocaleString()}</td><td>${escapeHtml(event.nut_name)}</td><td><span class="severity ${escapeHtml(event.severity)}">${escapeHtml(event.severity)}</span></td><td>${escapeHtml(event.message)}</td><td>${eventDuration(event)}</td></tr>`).join("") : `<tr><td colspan="5" class="empty-state"><span class="empty-icon">${uiIcon("inbox")}</span>No warning or critical events in this period</td></tr>`;
  return `<section class="panel table-panel"><h2 class="panel-title"><span class="icon">${uiIcon("history-list")}</span>Event history</h2><table><thead><tr><th>Time</th><th>Device</th><th>Severity</th><th>Event</th><th>Duration</th></tr></thead><tbody>${rows}</tbody></table></section>`;
}

function bindHistoryControls(data) {
  document.querySelectorAll("[data-range]").forEach(button=>button.addEventListener("click",()=>{state.historyRange=button.dataset.range;renderHistory().catch(showError);}));
  document.getElementById("history-device").addEventListener("change",event=>{state.historyDevice=event.target.value;renderHistory().catch(showError);});
  document.getElementById("export-csv").addEventListener("click",()=>exportHistoryCsv(data));
}

function exportHistoryCsv(data) {
  const header="timestamp,device,battery_charge,runtime_seconds,load_percent,input_voltage,output_voltage,real_power_w\n";
  const body=data.series.map(row=>[new Date(row.ts*1000).toISOString(),row.nut_name,row.battery_charge,row.runtime_seconds,row.load_percent,row.input_voltage,row.output_voltage,row.real_power_w].join(",")).join("\n");
  const blob=new Blob([header+body],{type:"text/csv"});
  const url=URL.createObjectURL(blob); const anchor=document.createElement("a"); anchor.href=url; anchor.download=`ups-history-${state.historyRange}.csv`; anchor.click(); URL.revokeObjectURL(url);
}

function firstNutValue(device, keys) {
  for (const key of keys) {
    const value = device.variables?.[key];
    if (value !== null && value !== undefined && String(value).trim() !== "") return value;
  }
  return null;
}

function nutMeasurement(value, unit, digits = 1) {
  if (value === null || value === undefined || String(value).trim() === "") return "Not reported";
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${formatNumber(numeric, digits)} ${unit}` : `${value} ${unit}`;
}

function batteryHealth(device) {
  const tokens = new Set(String(device.status || "").split(/\s+/));
  if (!device.online || device.stale) return { label: "Unavailable", className: "muted" };
  if (tokens.has("RB")) return { label: "Replace battery", className: "critical" };
  if (tokens.has("LB")) return { label: "Low battery", className: "warning" };
  return { label: "Healthy", className: "healthy" };
}

function detailRow(label, value, className = "") {
  return `<div class="detail-row"><span>${escapeHtml(label)}</span><strong class="${escapeHtml(className)}">${escapeHtml(value)}</strong></div>`;
}

function deviceSparkline(series, metricName) {
  const values = series.filter(row=>row[metricName]!==null && row[metricName]!==undefined).map(row=>Number(row[metricName]));
  if (values.length < 2) return `<div class="sparkline-empty">Waiting for trend data…</div>`;
  const width=420, height=62, min=Math.min(...values), max=Math.max(...values), spread=Math.max(1,max-min);
  const points=values.map((value,index)=>`${(index/(values.length-1)*width).toFixed(1)},${(height-5-(value-min)/spread*(height-10)).toFixed(1)}`).join(" ");
  return `<svg class="device-sparkline" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="24 hour battery trend"><line x1="0" y1="${height-6}" x2="${width}" y2="${height-6}"/><polyline points="${points}"/></svg>`;
}

function deviceSelector(device, selected) {
  const status = deviceState(device);
  return `<button class="panel device-selector ${selected?"selected":""}" data-select-device="${escapeHtml(device.nut_name)}">
    <span class="device-selector-icon">${uiIcon("devices")}</span>
    <span><strong>${escapeHtml(device.model||device.description||device.nut_name)}</strong><small>${escapeHtml(device.nut_name)}</small></span>
    <span class="status-badge ${status.className}">${escapeHtml(status.label)}</span>
  </button>`;
}

function selectedDeviceDetails(device, server, history) {
  const status = deviceState(device);
  const battery = batteryState(device.battery_charge);
  const health = batteryHealth(device);
  const load = Math.max(0,Math.min(100,Number(device.load_percent)||0));
  const runtimeSeconds = Math.max(0,Number(device.runtime_seconds)||0);
  const runtimePercent = Math.max(2,Math.min(100,runtimeSeconds/7200*100));
  const variables = device.variables || {};
  const batteryType = firstNutValue(device,["battery.type","battery.chemistry"]) || "Not reported";
  const batteryVoltage = firstNutValue(device,["battery.voltage"]);
  const nominalVoltage = firstNutValue(device,["battery.voltage.nominal"]);
  const lowVoltage = firstNutValue(device,["battery.voltage.low"]);
  const manufactureDate = firstNutValue(device,["battery.mfr.date","battery.manufacture.date","battery.manufactured.date"]) || "Not reported";
  const installationDate = firstNutValue(device,["battery.install.date","battery.date","battery.replace.date"]) || "Not reported";
  const frequency = firstNutValue(device,["input.frequency","output.frequency"]);
  const replaceBattery = String(device.status||"").split(/\s+/).includes("RB") ? "Yes" : "No";
  const serverName = server.name || server.host;
  const rawRows = Object.entries(variables).sort(([a],[b])=>a.localeCompare(b)).map(([key,value])=>`<div><span>${escapeHtml(key)}</span><code>${escapeHtml(value)}</code></div>`).join("");
  return `<section class="panel device-detail-shell">
    <header class="device-detail-heading">
      <div><div class="device-title-line"><h2>${escapeHtml(device.model||device.description||device.nut_name)}</h2><span class="status-badge ${status.className}">${escapeHtml(status.label)}</span></div><div class="device-id">${escapeHtml(device.nut_name)}</div></div>
      <div class="device-cost">${uiIcon("coins")}<span>Monthly ${formatNumber(device.monthly_cost,2)} Lei</span><i>•</i><span>Annual ${formatNumber(device.annual_cost,2)} Lei</span></div>
    </header>

    <article class="device-section identity-section">
      <h3>${uiIcon("list-tree")} Device identity</h3>
      <div class="identity-grid">
        ${detailRow("Manufacturer",device.manufacturer||"Not reported")}
        ${detailRow("Server",serverName)}
        ${detailRow("Model",device.model||device.description||"Not reported")}
        ${detailRow("NUT device",device.nut_name)}
        ${detailRow("Serial number",device.serial||"Not reported")}
        ${detailRow("Driver",device.driver||"Not reported")}
      </div>
    </article>

    <div class="device-summary-grid">
      <article class="device-section device-summary battery-summary">
        <h3>${uiIcon("battery")} Battery charge</h3>
        <div class="horizontal-battery ${battery.state}" style="--battery-level:${battery.level}%;--battery-color:${battery.color}">
          <div class="horizontal-battery-body"><div class="horizontal-battery-fill"></div><div class="horizontal-battery-segments"></div></div><i></i><strong>${escapeHtml(battery.label)}</strong>
        </div>
        <div class="summary-subtitle"><span>24h trend</span><span>100%</span></div>
        ${deviceSparkline(history.series||[],"battery_charge")}
      </article>
      <article class="device-section device-summary load-summary">
        <h3>${uiIcon("gauge")} Current load</h3>
        <div class="load-gauge" style="--gauge-value:${load}">
          <svg viewBox="0 0 180 105" role="img" aria-label="Current load ${formatNumber(load)} percent"><path class="gauge-track" pathLength="100" d="M20 90 A70 70 0 0 1 160 90"/><path class="gauge-value" pathLength="100" stroke-dasharray="${load} 100" d="M20 90 A70 70 0 0 1 160 90"/></svg>
          <div><strong>${formatNumber(load)}%</strong><span>Load</span></div>
        </div>
        <div class="gauge-scale"><span>0%</span><span>100%</span></div>
      </article>
      <article class="device-section device-summary runtime-summary">
        <h3>${uiIcon("clock")} Estimated runtime</h3>
        <div class="runtime-value">${escapeHtml(formatRuntime(runtimeSeconds).replace("m"," min"))}</div>
        <div class="runtime-track"><div style="width:${runtimePercent}%"></div><i style="left:${runtimePercent}%"></i></div>
        <div class="runtime-scale"><span>0 min</span><span>2 h</span></div>
      </article>
    </div>

    <div class="device-info-grid">
      <article class="device-section detail-card">
        <h3>${uiIcon("battery")} Battery details</h3>
        ${detailRow("Status",health.label,health.className)}
        ${detailRow("Type",batteryType)}
        ${detailRow("Voltage",nutMeasurement(batteryVoltage,"V"))}
        ${detailRow("Nominal voltage",nutMeasurement(nominalVoltage,"V"))}
        ${detailRow("Low voltage threshold",nutMeasurement(lowVoltage,"V"))}
        ${detailRow("Manufacture date",manufactureDate)}
        ${detailRow("Installation date",installationDate)}
        ${detailRow("Replace battery",replaceBattery)}
      </article>
      <article class="device-section detail-card">
        <h3>${uiIcon("zap")} Electrical & connection</h3>
        ${detailRow("Input voltage",nutMeasurement(device.input_voltage,"V"))}
        ${detailRow("Output voltage",nutMeasurement(device.output_voltage,"V"))}
        ${detailRow("Real power",nutMeasurement(device.real_power_w,"W",0))}
        ${detailRow("Frequency",nutMeasurement(frequency,"Hz"))}
        ${detailRow("Server",serverName)}
        ${detailRow("Endpoint",`${server.host}:${server.port}`)}
        ${detailRow("Mode",server.read_only?"Read-only":"Unknown")}
        ${detailRow("Last response",relativeTime(device.last_seen),device.online?"healthy":"critical")}
      </article>
    </div>

    <details class="device-raw-details"><summary><span>${uiIcon("list-tree")} All NUT variables</span><span class="chevron">${uiIcon("chevron-down")}</span></summary><div class="technical-grid">${rawRows||"No variables available."}</div></details>
  </section>`;
}

async function renderDevices() {
  loading();
  const data = await api("/api/devices");
  const connected = data.server.connected;
  let selected = data.devices.find(device=>device.nut_name===state.selectedDevice) || data.devices[0];
  if (selected) state.selectedDevice=selected.nut_name;
  const history = selected ? await api(`/api/history?range=24h&device=${encodeURIComponent(selected.nut_name)}`) : {series:[]};
  main.innerHTML = `${pageHeader("Devices", `${data.devices.length} devices connected`, connected?"":"warning", `Updated ${relativeTime(data.server.last_success)}`, "Hardware, battery and connection details")}
    ${data.devices.length ? `<section class="device-selector-grid">${data.devices.map(device=>deviceSelector(device,device.nut_name===selected.nut_name)).join("")}</section>${selectedDeviceDetails(selected,data.server,history)}` : noDevices()}`;
  document.querySelector("[data-configure-ups]")?.addEventListener("click",()=>navigate("settings"));
  document.querySelectorAll("[data-select-device]").forEach(button=>button.addEventListener("click",()=>{state.selectedDevice=button.dataset.selectDevice;renderDevices().catch(showError);}));
  updateConnection(connected, data.server.last_success, Boolean(data.server.host));
}

async function renderSettings() {
  loading();
  const [settings, preview] = await Promise.all([api("/api/settings"),api("/api/reports/preview")]);
  main.innerHTML = `${pageHeader("Settings", "Read-only mode", "", settings.timezone, "Dashboard, UPS and report preferences")}
    <section class="grid settings-grid">
      <form id="settings-form" class="settings-stack">
        <article class="panel settings-card"><h2 class="panel-title"><span class="icon">${uiIcon("coins")}</span>Energy & cost</h2>
          <div class="form-row"><label for="energy-price">Energy price</label><div class="field-with-unit"><input id="energy-price" class="field-control" type="number" min="0" step="0.01" value="${settings.energy_price}"><span class="unit">Lei/kWh</span></div></div>
          <div class="helper">Used for monthly, annual and historical cost calculations.</div>
          <div class="form-row"><span class="form-label">Calculation basis</span><select class="field-control" disabled><option>24/7 current power</option></select></div>
        </article>
        <article class="panel settings-card"><h2 class="panel-title"><span class="icon">${uiIcon("plug")}</span>UPS setup</h2>
          <div class="form-row"><label for="nut-host">Host</label><input id="nut-host" class="field-control" value="${escapeHtml(settings.nut_host)}"></div>
          <div class="form-row"><label for="nut-port">Port</label><input id="nut-port" class="field-control" type="number" min="1" max="65535" value="${settings.nut_port}"></div>
          <div class="form-row"><label for="poll-interval">Polling interval</label><div class="field-with-unit"><input id="poll-interval" class="field-control" type="number" min="2" value="${settings.poll_interval}"><span class="unit">seconds</span></div></div>
          <div class="form-row"><span class="form-label">Read-only mode</span><div class="switch-row"><label class="switch"><input type="checkbox" checked disabled><span class="slider"></span></label><span>Enabled</span></div></div>
          <div class="form-row"><span class="form-label">Discovered devices</span><div class="chips">${settings.discovered_devices.map(name=>`<span class="chip">${escapeHtml(name)}</span>`).join("")||"None yet"}</div></div>
          <div class="form-actions"><button id="test-connection" class="button" type="button">${uiIcon("plug", "button-icon")} Test connection</button></div>
        </article>
        <article class="panel settings-card"><h2 class="panel-title"><span class="icon">${uiIcon("mail")}</span>Send report</h2>
          <div class="form-row"><span class="form-label">Weekly email report</span><div class="switch-row"><label class="switch"><input id="report-enabled" type="checkbox" ${settings.weekly_report_enabled?"checked":""}><span class="slider"></span></label><span>Enabled</span></div></div>
          <div class="form-row"><label for="report-recipient">Recipient</label><input id="report-recipient" class="field-control" type="email" placeholder="name@example.com" value="${escapeHtml(settings.report_recipient)}"></div>
          <div class="form-row"><label for="report-weekday">Send schedule</label><div class="field-with-unit"><select id="report-weekday" class="field-control">${["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"].map((day,index)=>`<option value="${index}" ${settings.report_weekday===index?"selected":""}>Every ${day}</option>`).join("")}</select><input id="report-time" class="field-control" type="time" value="${escapeHtml(settings.report_time)}"></div></div>
          <div class="helper">SMTP: ${settings.smtp_configured?"Configured":"Not configured"}</div>
          <div class="form-actions"><button id="send-test-report" class="button" type="button">${uiIcon("send", "button-icon")} Send test report</button><button class="button primary" type="submit">${uiIcon("save", "button-icon")} Save settings</button></div>
        </article>
      </form>
      ${reportPreview(preview)}
    </section>`;
  bindSettings(settings);
}

function reportPreview(preview) {
  const labels={number_of_devices:"Number of Devices",warning_event:"Warning Event",critical_event:"Critical Event",utility_power_failed:"Utility Power Failed",enabling_buck:"Enabling Buck",enabling_boost:"Enabling Boost",overload:"Overload"};
  return `<aside class="panel report-preview"><div class="report-header"><h2>${uiIcon("file-chart")} Last 7 Days Event Summary</h2><span class="report-date">${escapeHtml(preview.date_from)} - ${escapeHtml(preview.date_to)}</span></div><div class="report-grid">
    ${Object.entries(labels).map(([key,label])=>`<div class="report-metric"><span>${label}</span><strong>${formatNumber(preview.summary[key])}</strong></div>`).join("")}
    </div><button class="button" style="margin-top:20px" type="button" data-page-link="history">Check details ${uiIcon("arrow-right", "button-icon")}</button></aside>`;
}

function settingsPayload() {
  return {
    energy_price:Number(document.getElementById("energy-price").value),
    nut_host:document.getElementById("nut-host").value.trim(),
    nut_port:Number(document.getElementById("nut-port").value),
    poll_interval:Number(document.getElementById("poll-interval").value),
    weekly_report_enabled:document.getElementById("report-enabled").checked,
    report_recipient:document.getElementById("report-recipient").value.trim(),
    report_weekday:Number(document.getElementById("report-weekday").value),
    report_time:document.getElementById("report-time").value,
  };
}

function bindSettings(settings) {
  document.getElementById("settings-form").addEventListener("submit",async event=>{event.preventDefault();try{await api("/api/settings",{method:"PUT",body:JSON.stringify(settingsPayload())});toast("Settings saved");await renderSettings();}catch(error){showError(error);}});
  document.getElementById("test-connection").addEventListener("click",async()=>{try{const payload=settingsPayload();const result=await api("/api/settings/test-nut",{method:"POST",body:JSON.stringify({host:payload.nut_host,port:payload.nut_port})});toast(`Connected — ${result.devices.length} devices found`);}catch(error){showError(error);}});
  document.getElementById("send-test-report").addEventListener("click",async()=>{const recipient=document.getElementById("report-recipient").value.trim();if(!recipient){toast("Enter a report recipient first",true);return;}try{await api("/api/reports/test",{method:"POST",body:JSON.stringify({recipient})});toast("Test report sent");}catch(error){showError(error);}});
  document.querySelector("[data-page-link='history']").addEventListener("click",()=>navigate("history"));
}

function updateConnection(ok, updatedAt, configured = true) {
  const indicator=document.getElementById("connection-indicator");
  indicator.className=`connection-pill ${ok?"ok":configured?"error":""}`;
  indicator.querySelector("span").textContent=!configured?"UPS not configured":ok?`Live · ${relativeTime(updatedAt)}`:"Connection issue";
}

function showError(error) {
  console.error(error);
  toast(error.message || String(error), true);
}

async function renderPage() {
  clearInterval(state.refreshTimer);
  try {
    if (state.page === "overview") await renderOverview();
    else if (state.page === "history") await renderHistory();
    else if (state.page === "devices") await renderDevices();
    else await renderSettings();
  } catch (error) { if (error.message !== "Authentication required") { showError(error); main.innerHTML=`<div class="loading">Unable to load this page.</div>`; } }
  if (state.page !== "settings") state.refreshTimer=setInterval(()=>renderPage(),15000);
}

function navigate(page) {
  state.page=page;
  document.querySelectorAll("[data-page]").forEach(button=>button.classList.toggle("active",button.dataset.page===page));
  renderPage();
}

function showLogin() {
  state.authenticated=false;
  clearInterval(state.refreshTimer);
  appShell.classList.add("hidden");
  setupView.classList.add("hidden");
  loginView.classList.remove("hidden");
}

function showSetup() {
  state.authenticated=false;
  clearInterval(state.refreshTimer);
  appShell.classList.add("hidden");
  loginView.classList.add("hidden");
  setupView.classList.remove("hidden");
}

function showApp() {
  state.authenticated=true;
  setupView.classList.add("hidden");
  loginView.classList.add("hidden");
  appShell.classList.remove("hidden");
  navigate(state.page);
}

document.querySelectorAll("[data-page]").forEach(button=>button.addEventListener("click",()=>navigate(button.dataset.page)));
document.getElementById("setup-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const error=document.getElementById("setup-error"); error.textContent="";
  const username=document.getElementById("setup-username").value.trim();
  const password=document.getElementById("setup-password").value;
  const confirmation=document.getElementById("setup-password-confirm").value;
  if(password!==confirmation){error.textContent="Passwords do not match";return;}
  try {
    const result=await api("/api/setup",{method:"POST",body:JSON.stringify({username,password})});
    state.username=result.username;
    document.getElementById("setup-password").value="";
    document.getElementById("setup-password-confirm").value="";
    showApp();
  } catch (exc) { error.textContent=exc.message; }
});
document.getElementById("login-form").addEventListener("submit",async event=>{
  event.preventDefault();
  const error=document.getElementById("login-error"); error.textContent="";
  try {
    await api("/api/auth/login",{method:"POST",body:JSON.stringify({username:document.getElementById("login-username").value,password:document.getElementById("login-password").value})});
    document.getElementById("login-password").value=""; showApp();
  } catch (exc) { error.textContent=exc.message; }
});
document.getElementById("logout-button").addEventListener("click",async()=>{try{await api("/api/auth/logout",{method:"POST",body:"{}"});}catch{}showLogin();});

(async()=>{
  try {
    const auth=await api("/api/auth/status");
    if(auth.setup_required) showSetup();
    else if(auth.authenticated) showApp();
    else showLogin();
  } catch { showLogin(); }
})();
