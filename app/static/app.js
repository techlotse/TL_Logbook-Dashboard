const state = {
  data: null,
  map: null,
  tileLayer: null,
  routeLayer: null,
  airportLayer: null,
  pollTimer: null
};

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 });
const nmf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

function byId(id) {
  return document.getElementById(id);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatMinutes(minutes) {
  const value = Number(minutes || 0);
  const h = Math.floor(value / 60);
  const m = value % 60;
  return `${nf.format(h)} h ${String(m).padStart(2, "0")} m`;
}

function formatNm(value) {
  return `${nmf.format(Number(value || 0))} NM`;
}

function formatMonthLabel(value) {
  const text = String(value || "");
  if (!/^\d{4}-\d{2}$/.test(text)) return text;
  return `${text.slice(5, 7)}/${text.slice(2, 4)}`;
}

function setText(id, value) {
  const el = byId(id);
  if (el) el.textContent = value;
}

async function getJson(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail || "";
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return response.json();
}

function updateStatus(payload) {
  setText("statusStep", payload.step || "Ready");
  setText("statusMessage", payload.message || "");
  const progress = Math.max(0, Math.min(100, Number(payload.progress || 0)));
  const bar = byId("progressBar");
  if (bar) bar.style.width = `${progress}%`;
}

function setDashboardVisibility(isEmpty) {
  byId("emptyState")?.classList.toggle("is-hidden", !isEmpty);
  byId("dashboardMain")?.classList.toggle("is-hidden", isEmpty);
}

async function refreshStatus() {
  const payload = await getJson("/api/status");
  updateStatus(payload);
  return payload;
}

async function loadDashboard() {
  const data = await getJson("/api/dashboard");
  state.data = data;
  renderDashboard(data);
}

function routeAllowed(route, mode) {
  if (mode === "xc") return Number(route.xc_minutes || 0) > 0;
  if (mode === "pic") return Number(route.pic_minutes || 0) > 0;
  return true;
}

function initMap() {
  const mapEl = byId("map");
  if (!mapEl || !window.L || state.map) return;

  state.map = L.map(mapEl, {
    worldCopyJump: true,
    zoomControl: true
  }).setView([20, 0], 2);

  state.tileLayer = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors &copy; CARTO"
  }).addTo(state.map);

  state.routeLayer = L.layerGroup().addTo(state.map);
  state.airportLayer = L.layerGroup().addTo(state.map);
}

function renderMap(data) {
  initMap();
  if (!state.map || !state.routeLayer || !state.airportLayer) {
    setText("mapSubtitle", "Map library unavailable.");
    return;
  }

  state.routeLayer.clearLayers();
  state.airportLayer.clearLayers();

  const mode = byId("routeMode")?.value || "all";
  const bounds = [];
  const airports = data.airports || [];
  const routes = (data.routes || []).filter(route => routeAllowed(route, mode) && route.path);
  const maxRouteFlights = Math.max(1, ...routes.map(route => Number(route.flights || 0)));
  const maxVisits = Math.max(1, ...airports.map(airport => Number(airport.visits || 0)));

  routes.forEach(route => {
    const weight = 1.5 + (Number(route.flights || 0) / maxRouteFlights) * 4;
    const hasXc = Number(route.xc_minutes || 0) > 0;
    const hasPic = Number(route.pic_minutes || 0) > 0;
    const line = L.polyline(route.path, {
      color: hasPic ? "#00d1c7" : "#57d68d",
      opacity: hasXc ? 0.86 : 0.42,
      weight,
      dashArray: hasXc ? "" : "5 8"
    });
    line.bindPopup(`
      <strong>${escapeHtml(route.from_label)} to ${escapeHtml(route.to_label)}</strong><br>
      Flights: ${nf.format(route.flights || 0)}<br>
      Total: ${formatMinutes(route.total_minutes)}<br>
      PIC: ${formatMinutes(route.pic_minutes)}<br>
      XC: ${formatMinutes(route.xc_minutes)}<br>
      Direct: ${formatNm(route.distance_nm)}
    `);
    line.addTo(state.routeLayer);
    route.path.forEach(point => bounds.push(point));
  });

  airports.forEach(airport => {
    if (!Number.isFinite(Number(airport.lat)) || !Number.isFinite(Number(airport.lon))) return;
    const radius = 5 + Math.sqrt(Number(airport.visits || 0) / maxVisits) * 12;
    const marker = L.circleMarker([airport.lat, airport.lon], {
      radius,
      color: "#00d1c7",
      fillColor: Number(airport.landings || 0) > 0 ? "#f7b955" : "#23a7e0",
      fillOpacity: 0.78,
      weight: 1.5
    });
    marker.bindPopup(`
      <strong>${escapeHtml(airport.label)}</strong><br>
      ${escapeHtml(airport.name || "")}<br>
      Visits: ${nf.format(airport.visits || 0)}<br>
      Landings: ${nf.format(airport.landings || 0)}<br>
      Country: ${escapeHtml(airport.country || "")}
    `);
    marker.addTo(state.airportLayer);
    bounds.push([airport.lat, airport.lon]);
  });

  if (bounds.length) {
    state.map.fitBounds(bounds, { padding: [28, 28], maxZoom: 9 });
  } else {
    state.map.setView([20, 0], 2);
  }

  const routeWord = mode === "all" ? "routes" : `${mode.toUpperCase()} routes`;
  setText("mapSubtitle", `${nf.format(airports.length)} airports, ${nf.format(routes.length)} ${routeWord}.`);
}

function renderBars(containerId, rows, labelKey, limit = 8) {
  const container = byId(containerId);
  if (!container) return;
  container.innerHTML = "";

  const visibleRows = (rows || []).slice(0, limit);
  if (!visibleRows.length) {
    container.innerHTML = '<div class="empty-state">No data</div>';
    return;
  }

  const max = Math.max(1, ...visibleRows.map(row => Number(row.total_minutes || 0)));
  visibleRows.forEach(row => {
    const total = Number(row.total_minutes || 0);
    const pic = Number(row.pic_minutes || 0);
    const dual = Number(row.dual_minutes || 0);
    const other = Math.max(0, total - pic - dual);

    const wrap = document.createElement("div");
    wrap.className = "bar-row";

    const label = document.createElement("div");
    label.className = "bar-label";
    label.textContent = row[labelKey] || "Unknown";
    label.title = row[labelKey] || "Unknown";

    const track = document.createElement("div");
    track.className = "bar-track";
    [
      ["pic", pic],
      ["dual", dual],
      ["other", other]
    ].forEach(([className, value]) => {
      const segment = document.createElement("span");
      segment.className = `segment ${className}`;
      const percentOfRow = total ? (value / total) * 100 : 0;
      const percentOfMax = (total / max) * 100;
      segment.style.width = `${Math.max(0, (percentOfRow * percentOfMax) / 100)}%`;
      segment.title = `${className}: ${formatMinutes(value)}`;
      track.appendChild(segment);
    });

    const value = document.createElement("div");
    value.className = "bar-value";
    value.textContent = formatMinutes(total);

    wrap.append(label, track, value);
    container.appendChild(wrap);
  });
}

function renderMonthBars(rows) {
  const container = byId("monthBars");
  if (!container) return;
  container.innerHTML = "";

  const visibleRows = rows || [];
  if (!visibleRows.length) {
    container.innerHTML = '<div class="empty-state">No data</div>';
    return;
  }

  const max = Math.max(1, ...visibleRows.map(row => Number(row.total_minutes || 0)));
  visibleRows.forEach(row => {
    const item = document.createElement("div");
    item.className = "month-bar";
    const bar = document.createElement("span");
    bar.style.height = `${Math.max(2, (Number(row.total_minutes || 0) / max) * 170)}px`;
    bar.title = `${row.month}: ${formatMinutes(row.total_minutes)}`;
    const label = document.createElement("em");
    label.textContent = formatMonthLabel(row.month);
    item.append(bar, label);
    container.appendChild(item);
  });
}

function tableRows(tbodyId, rows, renderer, empty = "No data") {
  const tbody = byId(tbodyId);
  if (!tbody) return;
  tbody.innerHTML = "";
  if (!rows || !rows.length) {
    const row = document.createElement("tr");
    row.innerHTML = `<td colspan="8" class="empty-state">${empty}</td>`;
    tbody.appendChild(row);
    return;
  }
  rows.forEach(item => {
    const row = document.createElement("tr");
    row.innerHTML = renderer(item);
    tbody.appendChild(row);
  });
}

function renderDashboard(data) {
  const totals = data.totals || {};
  const meta = data.meta || {};
  const isEmpty = Boolean(meta.is_empty) || Number(meta.flight_count || 0) === 0;

  setDashboardVisibility(isEmpty);
  if (isEmpty) {
    setText("logbookMeta", "No logbook loaded");
    state.routeLayer?.clearLayers();
    state.airportLayer?.clearLayers();
    return;
  }

  setText("metricTotal", formatMinutes(totals.total_minutes));
  setText("metricPic", formatMinutes(totals.pic_minutes));
  setText("metricDual", formatMinutes(totals.dual_minutes));
  setText("metricPicXc", formatMinutes(totals.pic_xc_minutes));
  setText("metricXcNm", formatNm(totals.xc_distance_nm));
  setText("metricAirports", nf.format(totals.unique_airports || 0));

  const owner = meta.owner ? `${meta.owner} - ` : "";
  const source = meta.source_filename || "Uploaded logbook";
  const range = meta.first_date && meta.last_date ? ` - ${meta.first_date} to ${meta.last_date}` : "";
  setText("logbookMeta", `${owner}${source}${range}`);

  renderMap(data);
  renderBars("typeBars", data.aircraft_types, "aircraft_type", 10);
  renderBars("registrationBars", data.registrations, "registration", 10);
  renderBars("picBars", data.pic_names, "name", 10);
  renderMonthBars(data.monthly);

  tableRows("typeTable", data.aircraft_types, row => `
    <td class="route-code">${escapeHtml(row.aircraft_type)}</td>
    <td>${formatMinutes(row.total_minutes)}</td>
    <td>${formatMinutes(row.pic_minutes)}</td>
    <td>${formatMinutes(row.dual_minutes)}</td>
    <td>${formatMinutes(row.pic_xc_minutes)}</td>
  `);

  tableRows("registrationTable", data.registrations, row => `
    <td class="route-code">${escapeHtml(row.registration)}</td>
    <td>${formatMinutes(row.total_minutes)}</td>
    <td>${formatMinutes(row.pic_minutes)}</td>
    <td>${formatMinutes(row.dual_minutes)}</td>
    <td>${formatMinutes(row.pic_xc_minutes)}</td>
  `);

  tableRows("airportTable", (data.airports || []).slice(0, 16), row => `
    <td><span class="route-code">${escapeHtml(row.label)}</span><br>${escapeHtml(row.name || "")}</td>
    <td>${nf.format(row.visits || 0)}</td>
    <td>${nf.format(row.landings || 0)}</td>
    <td>${escapeHtml(row.country || "")}</td>
  `);

  tableRows("routeTable", (data.routes || []).slice(0, 18), row => `
    <td class="route-code">${escapeHtml(row.from_label)} -> ${escapeHtml(row.to_label)}</td>
    <td>${nf.format(row.flights || 0)}</td>
    <td>${formatMinutes(row.total_minutes)}</td>
    <td>${formatMinutes(row.xc_minutes)}</td>
    <td>${formatNm(row.distance_nm)}</td>
  `);

  tableRows("recentTable", data.recent_flights, row => `
    <td>${escapeHtml(row.date)}</td>
    <td class="route-code">${escapeHtml(row.dep_key)} -> ${escapeHtml(row.arr_key)}</td>
    <td>${escapeHtml(row.aircraft_type)}</td>
    <td>${escapeHtml(row.registration)}</td>
    <td>${formatMinutes(row.total_minutes)}</td>
    <td>${formatMinutes(row.pic_minutes)}</td>
    <td>${formatMinutes(row.dual_minutes)}</td>
    <td>${escapeHtml(row.name_pic)}</td>
  `);

  const quality = byId("qualityStatus");
  const unresolved = data.unresolved_airports || [];
  if (quality) {
    if (unresolved.length) {
      quality.textContent = `Missing coordinates: ${unresolved.slice(0, 8).join(", ")}${unresolved.length > 8 ? "..." : ""}`;
      quality.classList.add("visible");
    } else {
      quality.textContent = "";
      quality.classList.remove("visible");
    }
  }
}

async function handleUpload(event) {
  event.preventDefault();
  const input = byId("pdfInput");
  const uploadButton = document.querySelector("#uploadForm .primary-button");
  const file = input?.files?.[0];
  if (!file) {
    updateStatus({ step: "Upload", message: "Choose a PDF first.", progress: 0 });
    return;
  }

  const form = new FormData();
  form.append("file", file, file.name);
  uploadButton.disabled = true;

  try {
    updateStatus({ step: "Uploading", message: "Sending PDF.", progress: 10 });
    await getJson("/api/upload", { method: "POST", body: form });
    startPolling();
  } catch (error) {
    updateStatus({ step: "Error", message: error.message, progress: 100 });
  } finally {
    uploadButton.disabled = false;
  }
}

function startPolling() {
  if (state.pollTimer) clearInterval(state.pollTimer);
  state.pollTimer = setInterval(async () => {
    try {
      const payload = await refreshStatus();
      if (payload.status === "ready") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
        await loadDashboard();
      }
      if (payload.status === "error") {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    } catch (error) {
      updateStatus({ step: "Error", message: error.message, progress: 100 });
    }
  }, 700);
}

async function handleReset() {
  const resetButton = byId("resetButton");
  resetButton.disabled = true;
  try {
    await getJson("/api/reset", { method: "POST" });
    await refreshStatus();
    await loadDashboard();
  } catch (error) {
    updateStatus({ step: "Error", message: error.message, progress: 100 });
  } finally {
    resetButton.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  byId("uploadForm")?.addEventListener("submit", handleUpload);
  byId("resetButton")?.addEventListener("click", handleReset);
  byId("routeMode")?.addEventListener("change", () => {
    if (state.data && !state.data.meta?.is_empty) renderMap(state.data);
  });

  try {
    await refreshStatus();
    await loadDashboard();
  } catch (error) {
    updateStatus({ step: "Error", message: error.message, progress: 100 });
  }
});
