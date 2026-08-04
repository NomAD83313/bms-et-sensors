let statusDetailsOpen = false;
let pendingRangeAction = null;
let rangeActionFeedbackTimer = null;
let resetZoomPending = false;

function statusPayloadFromMessage(msg, details = "") {
  const text = String(msg || "").trim() || "Idle";
  const explicitDetails = String(details || "").trim();
  let summary = text;
  let diagnostics = explicitDetails;
  let state = "idle";

  if (text.toLowerCase().startsWith("loading")) {
    state = "loading";
    summary = "Loading data...";
    if (!diagnostics) diagnostics = text;
  } else if (text.toLowerCase().startsWith("error:")) {
    state = "error";
    summary = text.slice("error:".length).trim() || "Error";
    if (!diagnostics) diagnostics = text;
  } else if (text.toLowerCase().startsWith("ok |")) {
    state = "ok";
    const parts = text.split("|").map((part) => part.trim()).filter(Boolean);
    const updatedPart = [...parts].reverse().find((part) => part.toLowerCase().startsWith("updated "));
    summary = updatedPart ? `OK · ${updatedPart}` : "OK";
    if (!diagnostics) diagnostics = parts.join("\n");
  } else if (text.toLowerCase().startsWith("ok")) {
    state = "ok";
    summary = text;
  }

  return { summary, diagnostics, state };
}

function renderStatus(payload) {
  const summaryEl = document.getElementById("statusSummary");
  const detailsEl = document.getElementById("statusDetails");
  const toggleEl = document.getElementById("btnStatusDetails");
  if (!summaryEl || !detailsEl || !toggleEl) return;

  summaryEl.textContent = payload.summary;
  summaryEl.dataset.state = payload.state;
  detailsEl.textContent = payload.diagnostics;
  const hasDetails = Boolean(payload.diagnostics);
  toggleEl.hidden = !hasDetails;
  if (!hasDetails) statusDetailsOpen = false;
  detailsEl.classList.toggle("show", hasDetails && statusDetailsOpen);
  toggleEl.textContent = statusDetailsOpen ? "Hide details" : "Details";
}

function setStatus(msg, details = "") {
  renderStatus(statusPayloadFromMessage(msg, details));
}

function getRangeActionButtons() {
  return [
    document.getElementById("btnShiftLeft"),
    document.getElementById("btnShiftRight"),
    document.getElementById("btnZoomOut25"),
    document.getElementById("btnZoomIn25"),
  ].filter(Boolean);
}

function setRangeActionButtonsBusy(isBusy) {
  if (!isCustomRange()) return;
  getRangeActionButtons().forEach((btn) => {
    btn.disabled = Boolean(isBusy);
  });
}

function clearRangeInputFlash() {
  const fromInput = document.getElementById("customFrom");
  const toInput = document.getElementById("customTo");
  if (fromInput) fromInput.classList.remove("range-input-flash");
  if (toInput) toInput.classList.remove("range-input-flash");
}

function flashRangeInputs() {
  clearRangeInputFlash();
  const fromInput = document.getElementById("customFrom");
  const toInput = document.getElementById("customTo");
  if (fromInput) fromInput.classList.add("range-input-flash");
  if (toInput) toInput.classList.add("range-input-flash");
  window.setTimeout(() => clearRangeInputFlash(), 1200);
}

function currentCustomRangeLabel() {
  const pair = getCustomRangeDates();
  if (!pair) return "";
  const fromLabel = pair.fromDt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const toLabel = pair.toDt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${fromLabel} - ${toLabel}`;
}

function renderRangeActionFeedback(text = "", state = "pending", sticky = false) {
  const el = document.getElementById("rangeActionFeedback");
  if (!el) return;
  if (rangeActionFeedbackTimer) {
    window.clearTimeout(rangeActionFeedbackTimer);
    rangeActionFeedbackTimer = null;
  }
  const show = Boolean(text) && isCustomRange();
  el.textContent = text;
  el.dataset.state = state;
  el.classList.toggle("show", show);
  if (show && !sticky) {
    rangeActionFeedbackTimer = window.setTimeout(() => {
      el.textContent = "";
      el.classList.remove("show");
      el.dataset.state = "pending";
      rangeActionFeedbackTimer = null;
    }, 1800);
  }
}

function beginRangeAction(actionLabel) {
  pendingRangeAction = actionLabel;
  renderRangeActionFeedback(`${actionLabel}...`, "pending", true);
  setRangeActionButtonsBusy(true);
  flashRangeInputs();
}

function finishRangeAction(success = true) {
  if (!pendingRangeAction) {
    setRangeActionButtonsBusy(false);
    return;
  }
  const label = pendingRangeAction;
  pendingRangeAction = null;
  setRangeActionButtonsBusy(false);
  const rangeLabel = success ? currentCustomRangeLabel() : "";
  renderRangeActionFeedback(
    success ? `${label} done${rangeLabel ? ` · ${rangeLabel}` : ""}` : `${label} failed`,
    success ? "done" : "error",
  );
}

function applyChartHeight(chartId, px) {
  const n = Math.max(180, Math.min(800, Number(px) || 240));
  chartHeights[chartId] = n;
  const surface = document.getElementById(chartId);
  if (surface) surface.style.height = `${n}px`;
  const inp = document.getElementById(`h-${chartId}`);
  if (inp) inp.value = String(n);
  try { localStorage.setItem(`graf.chartHeightPx.${chartId}`, String(n)); } catch (_) {}
  const m = chartModels[chartId];
  if (m) drawChart(chartId, m.rawSeries || m.series || []);
}

function bindHeightControls() {
  chartIds.forEach((chartId) => {
    const btn = document.getElementById(`applyH-${chartId}`);
    const inp = document.getElementById(`h-${chartId}`);
    if (!btn || !inp) return;
    btn.addEventListener("click", () => applyChartHeight(chartId, inp.value));
    inp.addEventListener("keydown", (e) => {
      if (e.key === "Enter") applyChartHeight(chartId, inp.value);
    });
  });
}

function loadSavedChartHeights() {
  chartIds.forEach((chartId) => {
    let saved = null;
    try {
      saved = localStorage.getItem(`graf.chartHeightPx.${chartId}`);
    } catch (_) {
      saved = null;
    }
    applyChartHeight(chartId, saved || 240);
  });
}

function cloneRangeState(state) {
  if (window.GrafZoomState && typeof window.GrafZoomState.cloneRangeState === "function") {
    return window.GrafZoomState.cloneRangeState(state);
  }
  if (!state || typeof state !== "object") return null;
  if (state.mode === "relative") return { mode: "relative", range: String(state.range || "") };
  if (state.mode === "custom") return { mode: "custom", fromMs: Number(state.fromMs), toMs: Number(state.toMs) };
  return null;
}

function getCurrentPageRangeState() {
  const range = String(document.getElementById("rangeSel").value || "");
  if (range !== "custom") return { mode: "relative", range };
  const pair = getCustomRangeDates();
  if (!pair) return null;
  return {
    mode: "custom",
    fromMs: pair.fromDt.getTime(),
    toMs: pair.toDt.getTime(),
  };
}

function rangeStatesEqual(left, right) {
  if (window.GrafZoomState && typeof window.GrafZoomState.rangeStatesEqual === "function") {
    return window.GrafZoomState.rangeStatesEqual(left, right);
  }
  if (!left || !right || left.mode !== right.mode) return false;
  if (left.mode === "relative") return String(left.range || "") === String(right.range || "");
  if (left.mode === "custom") {
    const toleranceMs = 1000;
    return (
      Math.abs(Number(left.fromMs) - Number(right.fromMs)) <= toleranceMs &&
      Math.abs(Number(left.toMs) - Number(right.toMs)) <= toleranceMs
    );
  }
  return false;
}

function applyRangeState(state) {
  const next = cloneRangeState(state);
  if (!next) return false;
  const rangeSel = document.getElementById("rangeSel");
  if (next.mode === "relative") {
    rangeSel.value = next.range;
    updateCustomRangeVisibility();
    return true;
  }
  rangeSel.value = "custom";
  updateCustomRangeVisibility();
  return setCustomRangeMs(next.fromMs, next.toMs);
}

function storeZoomBaseRangeIfNeeded() {
  const current = getCurrentPageRangeState();
  if (!current) return;
  if (window.GrafZoomState && typeof window.GrafZoomState.startZoomSession === "function") {
    zoomBaseRange = window.GrafZoomState.startZoomSession(zoomBaseRange, current);
    return;
  }
  if (!zoomBaseRange) zoomBaseRange = cloneRangeState(current);
}

function clearZoomBaseRange() {
  zoomBaseRange = null;
  renderZoomState();
}

function hasZoomBaseRange() {
  return Boolean(zoomBaseRange);
}

function isZoomedFromBase() {
  if (!zoomBaseRange) return false;
  const current = getCurrentPageRangeState();
  if (!current) return isCustomRange();
  if (window.GrafZoomState && typeof window.GrafZoomState.isZoomedState === "function") {
    return window.GrafZoomState.isZoomedState(zoomBaseRange, current);
  }
  return !rangeStatesEqual(current, zoomBaseRange);
}

function renderZoomState() {
  const chip = document.getElementById("zoomStateChip");
  const btn = document.getElementById("btnResetZoom");
  if (!chip || !btn) return;
  const hasBase = hasZoomBaseRange();
  const isZoomed = isZoomedFromBase();
  const showReset = isZoomed || resetZoomPending;
  if (!hasBase) {
    chip.dataset.state = "idle";
    chip.textContent = "Base range not set";
  } else if (isZoomed) {
    chip.dataset.state = "zoomed";
    chip.textContent = "Zoomed";
  } else {
    chip.dataset.state = "base";
    chip.textContent = "At base range";
  }
  btn.hidden = !showReset;
  btn.disabled = !hasBase || !isZoomed;
}

function resetToZoomBaseRange() {
  if (!zoomBaseRange) {
    setStatus("error: zoom base range is not set");
    return;
  }
  const targetRange = (
    window.GrafZoomState && typeof window.GrafZoomState.resetToBaseRange === "function"
  ) ? window.GrafZoomState.resetToBaseRange(zoomBaseRange, getCurrentPageRangeState()) : cloneRangeState(zoomBaseRange);
  if (!applyRangeState(targetRange)) {
    setStatus("error: failed to reset zoom");
    return;
  }
  resetZoomPending = true;
  renderZoomState();
  refreshData(true);
}

function isCustomRange() { return document.getElementById("rangeSel").value === "custom"; }

function normalizeTempChannelSelection(raw) {
  const out = {};
  for (let i = 0; i < 8; i += 1) {
    const key = `ch${i}`;
    out[key] = true;
  }
  if (raw && typeof raw === "object") {
    Object.entries(raw).forEach(([key, value]) => {
      if (String(key).includes("|")) {
        // device-specific selection keys
        out[key] = (typeof value === "object") ? (value.enabled !== false) : (value !== false);
        return;
      }
      // top-level channel key may be boolean or object {enabled,name}
      if (key.startsWith("ch")) {
        out[key] = (typeof value === "object") ? (value.enabled !== false) : (value !== false);
      }
    });
  }
  return out;
}

function extractTempChannelNames(raw) {
  const out = {};
  if (!raw || typeof raw !== "object") return out;
  Object.entries(raw).forEach(([key, value]) => {
    if (!value || typeof value !== "object") return;
    const name = String(value.name || "").trim();
    if (!name) return;
    out[String(key)] = name;
  });
  return out;
}

function redlabDisplayNameForKey(key) {
  const k = String(key || "");
  if (!k) return "";
  if (tempChannelNames && typeof tempChannelNames === "object") {
    if (tempChannelNames[k]) return String(tempChannelNames[k]);
    if (k.includes("|")) {
      const ch = k.split("|", 2)[1] || "";
      if (tempChannelNames[ch]) return String(tempChannelNames[ch]);
    }
    const ch = k.startsWith("ch") ? k : (k.split("|", 2)[1] || "");
    if (ch && tempChannelNames[ch]) return String(tempChannelNames[ch]);
  }
  return "";
}

async function loadTempChannelSelection() {
  try {
    const res = await fetch("api/redlab/channels");
    const data = await res.json();
    const channels = (data && data.channels) || {};
    tempChannelSelection = normalizeTempChannelSelection(channels);
    tempChannelNames = extractTempChannelNames(channels);
  } catch (_) {
    tempChannelSelection = normalizeTempChannelSelection({});
    tempChannelNames = {};
  }
  const params = new URLSearchParams(window.location.search);
  const nameOverride = params.get("channel_names");
  if (nameOverride) {
    try {
      const overrides = JSON.parse(decodeURIComponent(nameOverride));
      if (overrides && typeof overrides === "object") {
        Object.entries(overrides).forEach(([key, value]) => {
          if (value && String(value).trim()) {
            tempChannelNames[String(key)] = String(value).trim();
          }
        });
      }
    } catch (_) {}
  }
}

async function saveTempChannelSelection() {
  try {
    const payload = { ...tempChannelSelection };
    Object.entries(tempChannelNames || {}).forEach(([key, name]) => {
      const enabled = payload[key] !== false;
      payload[key] = { enabled, name: String(name || "") };
    });
    await fetch("api/redlab/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (_) {}
}

function seriesSelectionStorageKey(chartId) {
  return `graf.seriesSelection.${pageMode}.${chartId}`;
}

function loadSeriesSelection(chartId) {
  try {
    const raw = localStorage.getItem(seriesSelectionStorageKey(chartId));
    const parsed = raw ? JSON.parse(raw) : {};
    seriesSelection[chartId] = parsed && typeof parsed === "object" ? parsed : {};
  } catch (_) {
    seriesSelection[chartId] = {};
  }
}

function saveSeriesSelection(chartId) {
  try {
    localStorage.setItem(seriesSelectionStorageKey(chartId), JSON.stringify(seriesSelection[chartId] || {}));
  } catch (_) {}
}

function getChartConfig(chartId) {
  return chartConfigs[chartId] || { kind: "series", labelKind: "mscl", padding: 30, dataKey: "" };
}

function redlabSeriesParts(rawName) {
  const name = String(rawName || "").trim();
  if (!name.startsWith("redlab:")) return { device: "", channel: "" };
  const rest = name.slice("redlab:".length);
  if (rest.startsWith("channel=")) return { device: "", channel: rest.split("=", 2)[1] || "" };
  let device = "";
  let channel = "";
  for (const part of rest.split(" | ").map((x) => x.trim())) {
    if (part.startsWith("device=")) device = part.split("=", 2)[1] || "";
    if (part.startsWith("channel=")) channel = part.split("=", 2)[1] || "";
  }
  return { device, channel };
}

function redlabChannelKey(rawName) {
  return redlabSeriesParts(rawName).channel;
}

function redlabLineKey(rawName) {
  const { device, channel } = redlabSeriesParts(rawName);
  if (device && channel) return `${device}|${channel}`;
  return channel || "";
}

function redlabLineLabel(rawName) {
  const { device, channel } = redlabSeriesParts(rawName);
  const dbName = String(seriesTagValue(rawName, "channel_name") || "").trim();
  if (dbName) return dbName;
  const lineKey = redlabLineKey(rawName);
  const customName = redlabDisplayNameForKey(lineKey);
  if (customName) return customName;
  const displayDevice = String(device || "").replace(/^redlab[_-]/i, "").trim();
  if (displayDevice && channel) return `${displayDevice} ${channel}`;
  return channel || displayDevice || "RedLab";
}

function redlabDeviceStyleIndex(deviceLabel) {
  const rows = Array.from(document.querySelectorAll(".temp-channel-device-row"));
  const idx = rows.findIndex((row) => row.dataset.device === deviceLabel);
  return Math.max(0, idx);
}

function applyRedlabSwatchStyle(swatch, deviceLabel) {
  const styleIdx = redlabDeviceStyleIndex(deviceLabel) % 4;
  swatch.dataset.lineStyle = String(styleIdx);
}

function hashString(text) {
  let h = 0;
  const s = String(text || "");
  for (let i = 0; i < s.length; i += 1) h = ((h * 31) + s.charCodeAt(i)) >>> 0;
  return h >>> 0;
}

function shortHexId(text) {
  const value = String(text || "").trim();
  if (!value) return "?";
  if (value.startsWith("0x") && value.length > 6) return value.slice(-4);
  return value;
}

function seriesTagValue(rawName, key) {
  for (const part of String(rawName || "").split("|")) {
    const eq = part.indexOf("=");
    if (eq > 0 && part.slice(0, eq).trim() === key) return part.slice(eq + 1).trim();
  }
  return "";
}

function seriesColor(name, idx, chartId = "") {
  const redlabKey = redlabChannelKey(name);
  if (redlabKey) {
    const m = /^ch(\d+)$/.exec(redlabKey);
    const lineKey = redlabLineKey(name);
    if (m && !lineKey.includes("|")) return redlabColors[Number(m[1]) % redlabColors.length];
    if (m) return palette[(Number(m[1]) + hashString(lineKey)) % palette.length];
  }
  const labelKind = getChartConfig(chartId).labelKind;
  if (labelKind === "pyrometers") {
    const deviceKey = seriesTagValue(name, "serial") || seriesTagValue(name, "source") || name;
    return pyrometerPalette[hashString(deviceKey) % pyrometerPalette.length];
  }
  const chartPalette = labelKind === "mscl"
    ? msclPalette
    : labelKind === "matter"
      ? matterPalette
      : labelKind === "messkluppe"
        ? messkluppePalette
        : palette;
  const index = Number.isFinite(Number(idx)) ? Number(idx) : hashString(name || idx);
  return chartPalette[index % chartPalette.length];
}

function isTempSeriesVisible(seriesName) {
  const key = redlabLineKey(seriesName);
  if (!key) return true;
  return tempChannelSelection[key] !== false;
}

function filterTempSeries(series) {
  return (series || []).filter((s) => isTempSeriesVisible(s.name));
}

function isSeriesVisible(chartId, seriesName) {
  if (getChartConfig(chartId).kind === "redlab") return isTempSeriesVisible(seriesName);
  const selected = seriesSelection[chartId] || {};
  return selected[seriesName] !== false;
}

function filterSeriesByChart(chartId, series) {
  return (series || []).filter((s) => isSeriesVisible(chartId, s.name));
}

function seriesLabel(chartId, rawName) {
  return prettySeriesName(rawName);
}

function renderTempChannelList(series) {
  const root = document.getElementById("tempChannelList");
  if (!root) return;
  const master = document.getElementById("tempChannelToggleAll");
  const items = (series || [])
    .map((s) => ({
      key: redlabLineKey(s.name),
      label: redlabLineLabel(s.name),
      device: redlabSeriesParts(s.name).device,
      deviceLabel: String(redlabSeriesParts(s.name).device || "").replace(/^redlab[_-]/i, "").trim(),
      channel: redlabSeriesParts(s.name).channel,
      name: s.name,
    }))
    .filter((item) => item.key);
  const uniqueItems = [];
  const seen = new Set();
  items.forEach((item) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    uniqueItems.push(item);
  });
  uniqueItems.sort((a, b) => (
    a.deviceLabel.localeCompare(b.deviceLabel, undefined, { numeric: true }) ||
    a.channel.localeCompare(b.channel, undefined, { numeric: true })
  ));
  root.querySelectorAll(".temp-channel-device-row").forEach((node) => node.remove());
  if (master) master.style.display = "none";

  const deviceGroups = new Map();
  uniqueItems.forEach((item) => {
    const groupKey = item.deviceLabel || "RedLab";
    if (!deviceGroups.has(groupKey)) deviceGroups.set(groupKey, []);
    deviceGroups.get(groupKey).push(item);
  });

  async function setDeviceGroupVisible(groupItems, checked) {
    groupItems.forEach((item) => { tempChannelSelection[item.key] = checked; });
    await saveTempChannelSelection();
    const m = chartModels["c1"];
    if (m) drawChart("c1", m.rawSeries || [], { minX: m.minX, maxX: m.maxX });
  }

  function appendTempItem(parent, entry, idx, isDeviceMaster = false) {
    const item = document.createElement("label");
    item.className = isDeviceMaster ? "temp-channel-item temp-channel-device-master" : "temp-channel-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = entry.checked;
    checkbox.dataset.channel = entry.key;
    checkbox.addEventListener("change", entry.onchange);
    const swatch = document.createElement("span");
    swatch.className = "temp-channel-swatch";
    swatch.style.setProperty("--color", entry.color || seriesColor(entry.name, idx));
    applyRedlabSwatchStyle(swatch, entry.deviceLabel || entry.device || "");
    const text = document.createElement("span");
    text.className = "temp-channel-label";
    text.textContent = entry.label;
    item.appendChild(checkbox);
    item.appendChild(swatch);
    item.appendChild(text);
    parent.appendChild(item);
  }

  let colorIndex = 0;
  deviceGroups.forEach((groupItems, deviceLabel) => {
    const row = document.createElement("div");
    row.className = "temp-channel-device-row";
    row.dataset.device = deviceLabel;
    root.appendChild(row);

    const groupEnabled = groupItems.every((item) => tempChannelSelection[item.key] !== false);
    appendTempItem(row, {
      key: `${deviceLabel}|all`,
      label: `${deviceLabel} all`,
      checked: groupEnabled,
      color: "#cbd5e1",
      onchange: async (event) => setDeviceGroupVisible(groupItems, event.currentTarget.checked),
    }, colorIndex, true);

    groupItems.forEach((entry) => {
      const key = entry.key;
      appendTempItem(row, {
        ...entry,
        label: entry.label || (entry.channel ? `${entry.deviceLabel || deviceLabel} ${entry.channel}` : `${entry.deviceLabel || deviceLabel}`),
        checked: tempChannelSelection[key] !== false,
        onchange: async (event) => {
          tempChannelSelection[key] = event.currentTarget.checked;
          await saveTempChannelSelection();
          const m = chartModels["c1"];
          if (m) drawChart("c1", m.rawSeries || [], { minX: m.minX, maxX: m.maxX });
        },
      }, colorIndex);
      colorIndex += 1;
    });
  });
}

function renderSeriesChannelList(chartId, rawSeries) {
  const root = document.getElementById(`seriesChannelList-${chartId}`);
  if (!root) return;
  const master = document.getElementById(`seriesChannelToggleAll-${chartId}`);
  root.querySelectorAll(`.series-channel-item:not(.series-channel-master)`).forEach((node) => node.remove());
  const selected = seriesSelection[chartId] || {};
  const allEnabled = (rawSeries || []).every((s) => selected[s.name] !== false);
  if (master) {
    master.checked = allEnabled;
    master.onchange = () => {
      const checked = master.checked;
      (rawSeries || []).forEach((s) => { selected[s.name] = checked; });
      saveSeriesSelection(chartId);
      const m = chartModels[chartId];
      if (m) drawChart(chartId, m.rawSeries || [], { minX: m.minX, maxX: m.maxX });
    };
  }
  (rawSeries || []).forEach((s, idx) => {
    const item = document.createElement("label");
    item.className = "series-channel-item";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = selected[s.name] !== false;
    checkbox.addEventListener("change", () => {
      selected[s.name] = checkbox.checked;
      saveSeriesSelection(chartId);
      const m = chartModels[chartId];
      if (m) drawChart(chartId, m.rawSeries || [], { minX: m.minX, maxX: m.maxX });
    });
    const swatch = document.createElement("span");
    swatch.className = "series-channel-swatch";
    swatch.style.setProperty("--color", seriesColor(s.name, idx, chartId));
    const label = document.createElement("span");
    label.className = "series-channel-label";
    label.textContent = seriesLabel(chartId, s.name);
    item.appendChild(checkbox);
    item.appendChild(swatch);
    item.appendChild(label);
    root.appendChild(item);
  });
}

function toLocalInputValue(dateObj) {
  const d = new Date(dateObj.getTime() - dateObj.getTimezoneOffset() * 60000);
  return d.toISOString().slice(0, 19);
}

function initCustomRangeInputs() {
  const toInput = document.getElementById("customTo");
  const fromInput = document.getElementById("customFrom");
  if (!toInput.value || !fromInput.value) {
    const now = new Date();
    const from = new Date(now.getTime() - 60 * 60 * 1000);
    toInput.value = toLocalInputValue(now);
    fromInput.value = toLocalInputValue(from);
  }
}

function updateCustomRangeVisibility() {
  const show = isCustomRange();
  const feedbackEl = document.getElementById("rangeActionFeedback");
  document.getElementById("customFromWrap").style.display = show ? "" : "none";
  document.getElementById("customToWrap").style.display = show ? "" : "none";
  document.getElementById("btnShiftLeft").style.display = show ? "" : "none";
  document.getElementById("btnShiftRight").style.display = show ? "" : "none";
  document.getElementById("btnZoomOut25").style.display = show ? "" : "none";
  document.getElementById("btnZoomIn25").style.display = show ? "" : "none";
  document.getElementById("refreshSecWrap").style.display = show ? "none" : "";
  document.getElementById("btnRefresh").style.display = show ? "none" : "";
  document.getElementById("btnShiftLeft").disabled = !show;
  document.getElementById("btnShiftRight").disabled = !show;
  document.getElementById("btnZoomOut25").disabled = !show;
  document.getElementById("btnZoomIn25").disabled = !show;
  document.getElementById("refreshSec").disabled = show;
  document.getElementById("btnRefresh").disabled = show;
  if (!show) {
    pendingRangeAction = null;
    clearRangeInputFlash();
  }
  if (feedbackEl) {
    feedbackEl.classList.toggle("show", show && Boolean(feedbackEl.textContent));
  }
  if (show) initCustomRangeInputs();
  restartTimer();
  renderZoomState();
}

function getCustomRangeDates() {
  const fromRaw = document.getElementById("customFrom").value;
  const toRaw = document.getElementById("customTo").value;
  if (!fromRaw || !toRaw) return null;
  const fromDt = new Date(fromRaw);
  const toDt = new Date(toRaw);
  if (!Number.isFinite(fromDt.getTime()) || !Number.isFinite(toDt.getTime()) || toDt <= fromDt) return null;
  return { fromDt, toDt };
}

function setCustomRangeMs(fromMs, toMs) {
  const minSpanMs = 1000;
  const maxSpanMs = 31 * 24 * 3600 * 1000;
  let f = Number(fromMs);
  let t = Number(toMs);
  if (!Number.isFinite(f) || !Number.isFinite(t) || t <= f) return false;
  let span = t - f;
  if (span < minSpanMs) {
    const c = (f + t) / 2;
    f = c - minSpanMs / 2;
    t = c + minSpanMs / 2;
    span = minSpanMs;
  }
  if (span > maxSpanMs) {
    const c = (f + t) / 2;
    f = c - maxSpanMs / 2;
    t = c + maxSpanMs / 2;
  }
  document.getElementById("customFrom").value = toLocalInputValue(new Date(f));
  document.getElementById("customTo").value = toLocalInputValue(new Date(t));
  return true;
}

function shiftCustomRange(direction, actionLabel) {
  if (!isCustomRange()) return;
  const pair = getCustomRangeDates();
  if (!pair) {
    setStatus("error: invalid custom range");
    finishRangeAction(false);
    return;
  }
  const spanMs = pair.toDt.getTime() - pair.fromDt.getTime();
  const shiftMs = Math.round(spanMs / 2) * direction;
  const fromMs = pair.fromDt.getTime() + shiftMs;
  const toMs = pair.toDt.getTime() + shiftMs;
  if (!setCustomRangeMs(fromMs, toMs)) {
    setStatus("error: failed to shift range");
    finishRangeAction(false);
    return;
  }
  beginRangeAction(actionLabel || (direction < 0 ? "Shift left" : "Shift right"));
  renderZoomState();
  refreshData(true);
}

function scaleCustomRange(factor, actionLabel) {
  if (!isCustomRange()) return;
  const pair = getCustomRangeDates();
  if (!pair) {
    setStatus("error: invalid custom range");
    finishRangeAction(false);
    return;
  }
  const fromMs = pair.fromDt.getTime();
  const toMs = pair.toDt.getTime();
  const spanMs = toMs - fromMs;
  const center = fromMs + spanMs / 2;
  const newSpan = Math.round(spanMs * factor);
  const newFrom = center - newSpan / 2;
  const newTo = center + newSpan / 2;
  if (!setCustomRangeMs(newFrom, newTo)) {
    setStatus("error: failed to scale range");
    finishRangeAction(false);
    return;
  }
  beginRangeAction(actionLabel || (factor > 1 ? "Zoom out" : "Zoom in"));
  renderZoomState();
  refreshData(true);
}
