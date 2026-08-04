function dataXFromPixel(m, xPixel) {
  const clamped = Math.max(m.p, Math.min(m.w - m.p, xPixel));
  const ratio = (clamped - m.p) / (m.w - m.p * 2);
  const xData = m.minX + ratio * (m.maxX - m.minX);
  return { xData, xClamped: clamped, ratio };
}

function toXY(series) {
  return series.map(s => ({
    name: s.name,
    points: (s.points || [])
      .map(p => ({ x: new Date(p.t).getTime(), y: Number(p.v) }))
      .filter(p => Number.isFinite(p.x) && Number.isFinite(p.y))
      .sort((a, b) => a.x - b.x)
  })).filter(s => s.points.length > 0);
}

function normalizeDegrees(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return n;
  return ((n % 360) + 360) % 360;
}

function angularDistance(a, b) {
  const diff = Math.abs(normalizeDegrees(a) - normalizeDegrees(b));
  return Math.min(diff, 360 - diff);
}

function circularMedian(values) {
  const finite = values.map(normalizeDegrees).filter(Number.isFinite);
  if (!finite.length) return null;
  let best = finite[0];
  let bestScore = Number.POSITIVE_INFINITY;
  finite.forEach((candidate) => {
    const score = finite.reduce((acc, value) => acc + angularDistance(candidate, value), 0);
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
  });
  return best;
}

function filterYawSpikeSeries(series) {
  const spikeThresholdDeg = 35;
  const windowRadius = 3;
  return (series || []).map((s) => {
    const normalized = (s.points || []).map((pt) => ({ ...pt, y: normalizeDegrees(pt.y), origY: pt.y }));
    const points = normalized.map((pt, idx) => {
      const start = Math.max(0, idx - windowRadius);
      const end = Math.min(normalized.length, idx + windowRadius + 1);
      const neighborValues = normalized
        .slice(start, end)
        .filter((_item, neighborIdx) => start + neighborIdx !== idx)
        .map(item => item.y);
      const median = circularMedian(neighborValues);
      if (median !== null && angularDistance(pt.y, median) > spikeThresholdDeg) {
        return { ...pt, y: median, rawY: pt.y };
      }
      return pt;
    });
    return { name: s.name, points };
  });
}

function applyChartDisplayFilters(canvasId, rawSeries) {
  if (canvasId === "c7" && yawFilterModes[canvasId]) {
    return filterYawSpikeSeries(rawSeries);
  }
  return rawSeries || [];
}

function nearestPoint(points, targetX) {
  if (!points.length) return null;
  let lo = 0, hi = points.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].x < targetX) lo = mid + 1;
    else hi = mid;
  }
  if (lo === 0) return points[0];
  const prev = points[lo - 1];
  const curr = points[lo];
  return (targetX - prev.x) <= (curr.x - targetX) ? prev : curr;
}

function gapThresholdMs(points, canvasId) {
  if (!points || points.length < 2) return Number.POSITIVE_INFINITY;
  const deltas = [];
  for (let i = 1; i < points.length; i += 1) {
    const dt = points[i].x - points[i - 1].x;
    if (Number.isFinite(dt) && dt > 0) deltas.push(dt);
  }
  if (!deltas.length) return Number.POSITIVE_INFINITY;
  deltas.sort((a, b) => a - b);
  const median = deltas[Math.floor(deltas.length / 2)];
  const p90 = deltas[Math.floor((deltas.length - 1) * 0.9)];

  // Fast streams: tolerate short jitter/dropouts and split only on real outages.
  return Math.max(8 * median, 4 * p90, 3000);
}

function buildPathWithGaps(points, sx, sy, canvasId) {
  if (!points || !points.length) return "";
  if (points.length === 1) {
    const p = points[0];
    return `M${sx(p.x).toFixed(2)},${sy(p.y).toFixed(2)}`;
  }
  const gapThreshold = gapThresholdMs(points, canvasId);

  let d = "";
  let firstInSegment = true;
  for (let i = 0; i < points.length; i += 1) {
    const pt = points[i];
    if (i > 0) {
      const dt = pt.x - points[i - 1].x;
      if (dt > gapThreshold) firstInSegment = true;
    }
    const cmd = firstInSegment ? "M" : "L";
    d += `${cmd}${sx(pt.x).toFixed(2)},${sy(pt.y).toFixed(2)} `;
    firstInSegment = false;
  }
  return d.trim();
}

function formatXAxisLabel(tsMs, spanMs) {
  const d = new Date(tsMs);
  if (spanMs <= 60 * 1000) {
    return d.toLocaleTimeString([], { minute: "2-digit", second: "2-digit" });
  }
  if (spanMs <= 24 * 60 * 60 * 1000) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }
  return d.toLocaleString([], { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function formatTooltipTime(tsMs) {
  const d = new Date(tsMs);
  const pad2 = (n) => String(n).padStart(2, "0");
  const pad3 = (n) => String(n).padStart(3, "0");
  return `${pad2(d.getDate())}.${pad2(d.getMonth() + 1)}.${d.getFullYear()}, ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}.` +
    `${pad3(d.getMilliseconds())}`;
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;")
    .replaceAll("'", "&#39;");
}

function parseSeriesTags(name) {
  const tags = {};
  String(name || "").trim().split("|").forEach((part) => {
    const eq = part.indexOf("=");
    if (eq > 0) tags[part.slice(0, eq).trim()] = part.slice(eq + 1).trim();
  });
  return tags;
}

const matterNodeAliases = {
  "21": "Pico SPS30",
  "25": "BMS-TES3-974744"
};

function prettyMatterName(rawName) {
  const {
    node_id: nodeId = "",
    endpoint_id: endpointId = "",
    cluster_id: clusterId = "",
    attribute_id: attributeId = ""
  } = parseSeriesTags(rawName);

  const nodeLabel = matterNodeAliases[nodeId] || (nodeId ? `Matter N${nodeId}` : "Matter");
  if (clusterId === "1026") return `${nodeLabel} Temp`;
  if (clusterId === "1029") return `${nodeLabel} Humidity`;
  if (clusterId === "1027" && attributeId === "16") return `${nodeLabel} Pressure hPa`;
  if (clusterId === "1027") return `${nodeLabel} Pressure coarse`;
  if (clusterId === "1068") return `${nodeLabel} PM1.0 ug/m3`;
  if (clusterId === "1066") return `${nodeLabel} PM2.5 ug/m3`;
  if (clusterId === "1069") return `${nodeLabel} PM10 ug/m3`;
  if (clusterId === "47" && (!attributeId || attributeId === "12")) return `${nodeLabel} Battery`;
  if (endpointId) return `${nodeLabel} EP${endpointId}`;
  return nodeLabel;
}

function redlabDeviceName(rawName) {
  const name = String(rawName || "").trim();
  if (!name.startsWith("redlab:")) return "";
  const { device = "" } = parseSeriesTags(name.slice("redlab:".length));
  return device;
}

function buildRedlabDeviceOrder(series) {
  const devices = [];
  (series || []).forEach((s) => {
    const device = redlabDeviceName(s.name);
    if (device && !devices.includes(device)) devices.push(device);
  });
  devices.sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  return devices;
}

function lineStyleForSeries(name, deviceOrder = []) {
  const device = redlabDeviceName(name);
  if (!device) return {};
  const idx = Math.max(0, deviceOrder.indexOf(device));
  const styles = [
    {},
    { "stroke-dasharray": "7 4" },
    { "stroke-dasharray": "1 4", "stroke-linecap": "round" },
    { "stroke-dasharray": "10 3 2 3" },
  ];
  return styles[idx % styles.length] || {};
}

function prettySeriesName(rawName) {
  const name = String(rawName || "").trim();
  if (!name) return "";
  if (name.includes("_field=object_temperature_c")
      || name.includes("_field=sensor_head_temperature_c")
      || name.includes("_field=controller_box_temperature_c")) {
    return prettyPyrometerName(name);
  }
  if (name.includes("_field=force_") && name.includes("clip_id=")) return prettyMesskluppeName(name);
  if (name.includes("source=matter-server") && name.includes("node_id=")) {
    return prettyMatterName(name);
  }
  if (name.startsWith("redlab:")) {
    const rest = name.slice("redlab:".length);
    if (rest.startsWith("channel=")) return `RedLab ${rest.split("=", 2)[1] || "channel"}`;
    const { device = "", channel = "", channel_name: channelName = "" } = parseSeriesTags(rest);
    const dbName = String(channelName || "").trim();
    if (dbName) return dbName;
    const displayDevice = shortRedlabDeviceName(device);
    const displayKey = displayDevice && channel ? `${device}|${channel}` : channel;
    const customName = (typeof redlabDisplayNameForKey === "function")
      ? String(redlabDisplayNameForKey(displayKey) || "")
      : "";
    if (customName) return customName;
    if (displayDevice && channel) return `${displayDevice} ${channel}`;
    if (displayDevice) return displayDevice;
    return `RedLab ${channel || rest}`;
  }
  if (name.includes("mode=") || name.includes("sensor=") || name.includes("unit=")) {
    const almemo = prettyAlmemoName(name);
    if (almemo) return almemo;
  }
  if (!name.includes("node_id=") || !name.includes("source=")) return name;

  const { node_id: nodeId = "", source = "" } = parseSeriesTags(name);
  if (!nodeId || !source) return name;

  if (source === "mscl_config_stream") return `N${nodeId} stream`;
  if (source === "mscl_node_export") return `N${nodeId} export`;
  return name;
}

function shortRedlabDeviceName(device) {
  return String(device || "").replace(/^redlab[_-]/i, "").trim();
}

function prettyPyrometerName(rawName) {
  const { source = "", serial = "", _field: field = "" } = parseSeriesTags(rawName);
  const fieldLabels = {
    object_temperature_c: "TObj",
    sensor_head_temperature_c: "THead",
    controller_box_temperature_c: "TBox"
  };
  const serialLabel = serial || source || "Pyrometer";
  const label = fieldLabels[field] || "";
  return [serialLabel, label].filter(Boolean).join(" ");
}

function prettyMesskluppeName(rawName) {
  const { clip_id: clipId = "", file_id: fileId = "", _field: field = "" } = parseSeriesTags(rawName);
  const axis = String(field || "")
    .replace(/^force_/i, "")
    .replace(/_raw$/i, "")
    .toUpperCase();
  const parts = ["Messkluppe"];
  if (clipId) parts.push(`clip ${clipId}`);
  if (axis) parts.push(`F${axis}`);
  if (fileId && fileId !== "fake") parts.push(`file ${fileId}`);
  return parts.join(" ");
}

function prettyAlmemoName(rawName) {
  const { channel = "", sensor = "", unit = "", mode = "" } = parseSeriesTags(rawName);

  const compactChannel = channel
    .replace(/^channel[_\s-]*/i, "")
    .replace(/^measurement[_\s-]*/i, "")
    .trim();
  const compactSensor = sensor
    .replace(/^sensor[_\s-]*/i, "")
    .trim();

  if (compactChannel && compactSensor) return `${compactChannel} ${compactSensor}`;
  if (compactChannel && unit) return `${compactChannel} ${unit}`;
  if (compactChannel) return compactChannel;
  if (compactSensor) return compactSensor;
  if (mode) {
    return mode
      .replace(/^continuous$/i, "cont")
      .replace(/^relative$/i, "rel")
      .replace(/^absolute$/i, "abs");
  }
  return "";
}

function mergeTemperatureSeries(msclSeries, redlabSeries) {
  const mscl = (msclSeries || []).map((s) => ({ name: s.name, points: s.points || [] }));
  const redlab = (redlabSeries || []).map((s) => ({
    name: `redlab:${s.name || ""}`,
    points: s.points || []
  }));
  return [...mscl, ...redlab];
}

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs = {}) {
  const el = document.createElementNS(SVG_NS, tag);
  Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, String(v)));
  return el;
}

function formatSummaryValue(value) {
  if (!Number.isFinite(value)) return "n/a";
  const abs = Math.abs(value);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 10) return value.toFixed(2);
  return value.toFixed(3);
}

function formatSummaryTime(tsMs) {
  if (!Number.isFinite(tsMs)) return "n/a";
  return new Date(tsMs).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function medianOfSorted(values) {
  if (!values.length) return NaN;
  const mid = Math.floor(values.length / 2);
  if (values.length % 2 === 1) return values[mid];
  return (values[mid - 1] + values[mid]) / 2;
}

function formatIntervalMs(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "n/a";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const sec = ms / 1000;
  if (sec < 60) return `${sec.toFixed(sec < 10 ? 2 : 1)} s`;
  const min = sec / 60;
  return `${min.toFixed(min < 10 ? 2 : 1)} min`;
}

function formatCadence(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "n/a";
  const hz = 1000.0 / ms;
  if (hz >= 1) return `${formatIntervalMs(ms)} (${hz.toFixed(hz >= 10 ? 1 : 2)} Hz)`;
  return `${formatIntervalMs(ms)} (${hz.toFixed(3)} Hz)`;
}

function estimateSeriesIntervalMs(points) {
  if (!points || points.length < 2) return NaN;
  const deltas = [];
  for (let i = 1; i < points.length; i += 1) {
    const dt = points[i].x - points[i - 1].x;
    if (Number.isFinite(dt) && dt > 0) deltas.push(dt);
  }
  if (!deltas.length) return NaN;
  deltas.sort((a, b) => a - b);
  return medianOfSorted(deltas);
}

function estimateVisibleCadenceMs(seriesList) {
  const medians = (seriesList || [])
    .map((series) => estimateSeriesIntervalMs(series.points || []))
    .filter((value) => Number.isFinite(value) && value > 0)
    .sort((a, b) => a - b);
  if (!medians.length) return NaN;
  return medianOfSorted(medians);
}

function setPanelSummary(canvasId, chips, emptyText = "") {
  const root = document.getElementById(`summary-${canvasId}`);
  if (!root) return;
  root.replaceChildren();

  if (!chips.length) {
    const chip = document.createElement("span");
    chip.className = "summary-chip empty";
    chip.textContent = emptyText || "No summary available";
    root.appendChild(chip);
    return;
  }

  chips.forEach(({ label, value }) => {
    const chip = document.createElement("span");
    chip.className = "summary-chip";
    chip.innerHTML = `${escapeHtml(label)} <strong>${escapeHtml(value)}</strong>`;
    root.appendChild(chip);
  });
}

function renderPanelSummary(canvasId, rawSeries, visibleSeries) {
  const visible = visibleSeries || [];
  const raw = rawSeries || [];
  const meta = panelMetaByChart[canvasId] || {};
  if (!visible.length) {
    if (raw.length) {
      setPanelSummary(canvasId, [], "No visible channels selected");
    } else {
      setPanelSummary(canvasId, [], "No data in selected range");
    }
    return;
  }

  const allPoints = visible.flatMap((series) => series.points || []);
  if (!allPoints.length) {
    setPanelSummary(canvasId, [], "No valid points in selected range");
    return;
  }

  const ys = allPoints.map((point) => point.y).filter((value) => Number.isFinite(value));
  const xs = allPoints.map((point) => point.x).filter((value) => Number.isFinite(value));
  const lastPoint = allPoints.reduce((best, point) => {
    if (!best || point.x > best.x) return point;
    return best;
  }, null);
  const avg = ys.length ? ys.reduce((acc, value) => acc + value, 0) / ys.length : NaN;
  const cadenceMs = estimateVisibleCadenceMs(visible);

  setPanelSummary(canvasId, [
    { label: "Series", value: String(visible.length) },
    { label: "Samples", value: String(allPoints.length) },
    { label: "Window cadence", value: formatCadence(cadenceMs) },
    { label: "Influx raw", value: formatCadence(Number(meta.raw_cadence_ms)) },
    { label: "Min", value: formatSummaryValue(Math.min(...ys)) },
    { label: "Max", value: formatSummaryValue(Math.max(...ys)) },
    { label: "Avg", value: formatSummaryValue(avg) },
    { label: "Updated", value: formatSummaryTime(lastPoint ? lastPoint.x : Math.max(...xs)) },
  ]);
}

function renderChart(canvasId, hoverX = null) {
  const m = chartModels[canvasId];
  if (!m) return;

  const { canvas, tooltip, w, h, p, series, minX, maxX, minY, maxY, sx, sy } = m;
  canvas.replaceChildren();
  canvas.appendChild(svgEl("rect", {
    x: p, y: p, width: (w - p * 2), height: (h - p * 2),
    fill: "none", stroke: "#2b3e64", "stroke-width": 1
  }));

  const gridColor = "rgba(43,62,100,0.55)";
  for (let i = 1; i < 6; i += 1) {
    const gx = (p + (i / 6) * (w - p * 2)).toFixed(2);
    canvas.appendChild(svgEl("line", { x1: gx, y1: p, x2: gx, y2: h - p, stroke: gridColor, "stroke-width": 1 }));
  }
  for (let i = 0; i <= 4; i += 1) {
    const gyVal = minY + (i / 4) * (maxY - minY);
    const gyPx = sy(gyVal);
    if (i > 0 && i < 4) {
      canvas.appendChild(svgEl("line", { x1: p, y1: gyPx.toFixed(2), x2: w - p, y2: gyPx.toFixed(2), stroke: gridColor, "stroke-width": 1 }));
    }
    const ly = i === 0 ? gyPx - 3 : gyPx + 9;
    const gyLabel = normalizeModes[canvasId] ? (i / 4).toFixed(2) : gyVal.toFixed(2);
    canvas.appendChild(svgEl("text", { x: 4, y: ly.toFixed(2), fill: "#8ea6d9", "font-size": 10 })).textContent = gyLabel;
  }

  if (!series.length) {
    const emptyMessage = (m.rawSeries || []).length
      ? "No visible channels selected"
      : "No data in selected range";
    canvas.appendChild(svgEl("text", { x: p + 8, y: p + 18, fill: "#8ea6d9", "font-size": 12 })).textContent = emptyMessage;
    if (tooltip) tooltip.style.display = "none";
    return;
  }

  const redlabDeviceOrder = buildRedlabDeviceOrder(series);
  series.forEach((s, idx) => {
    const color = seriesColor(s.name, idx, canvasId);
    const d = buildPathWithGaps(s.points, sx, sy, canvasId);
    canvas.appendChild(svgEl("path", {
      d,
      fill: "none",
      stroke: color,
      "stroke-width": 1.5,
      "stroke-linejoin": "round",
      "stroke-linecap": "round",
      ...lineStyleForSeries(s.name, redlabDeviceOrder)
    }));

  });

  const spanMs = Math.max(1, maxX - minX);
  const xTicks = 6;
  for (let i = 0; i <= xTicks; i += 1) {
    const ratio = i / xTicks;
    const x = p + ratio * (w - p * 2);
    const ts = minX + ratio * (maxX - minX);
    const label = formatXAxisLabel(ts, spanMs);
    canvas.appendChild(svgEl("text", {
      x: Math.max(2, Math.min(w - 40, x - 18)).toFixed(2),
      y: (h - 8).toFixed(2),
      fill: "#8ea6d9",
      "font-size": 11
    })).textContent = label;
  }

  const ds = dragState[canvasId];
  if (ds && ds.dragging) {
    const x1 = Math.max(p, Math.min(w - p, ds.startX));
    const x2 = Math.max(p, Math.min(w - p, ds.currentX));
    const left = Math.min(x1, x2);
    const width = Math.max(1, Math.abs(x2 - x1));
    canvas.appendChild(svgEl("rect", {
      x: left, y: p, width, height: (h - p * 2),
      fill: "rgba(96,165,250,0.16)", stroke: "rgba(147,197,253,0.7)", "stroke-width": 1
    }));
  }

  if (hoverX === null) {
    if (tooltip) tooltip.style.display = "none";
    return;
  }
  const clampedX = Math.max(p, Math.min(w - p, hoverX));
  const xData = minX + ((clampedX - p) / (w - p * 2)) * (maxX - minX);

  canvas.appendChild(svgEl("line", {
    x1: clampedX, y1: p, x2: clampedX, y2: (h - p),
    stroke: "rgba(220,230,255,0.55)", "stroke-width": 1
  }));

  const values = [];
  series.forEach((s, idx) => {
    const pt = nearestPoint(s.points, xData);
    if (!pt) return;
    const color = seriesColor(s.name, idx, canvasId);
    const px = sx(pt.x);
    const py = sy(pt.y);
    canvas.appendChild(svgEl("circle", { cx: px, cy: py, r: 2.5, fill: color }));
    values.push({ color, name: s.name, point: pt });
  });

  if (!values.length) {
    if (tooltip) tooltip.style.display = "none";
    return;
  }
  if (tooltip) {
    const tRef = values[0].point.x;
    let html = `<div class=\"tt-time\">${escapeHtml(formatTooltipTime(tRef))}</div>`;
    values.forEach((v) => {
      const displayY = v.point.origY !== undefined ? v.point.origY : v.point.y;
      const rawSuffix = v.point.rawY !== undefined ? ` (raw ${v.point.rawY.toFixed(3)})` : "";
      html += `<div class=\"tt-row\"><span class=\"tt-dot\" style=\"background:${v.color}\"></span><span>${escapeHtml(prettySeriesName(v.name))}: ${displayY.toFixed(3)}${rawSuffix}</span></div>`;
    });
    tooltip.innerHTML = html;
    tooltip.style.display = "block";
    const tw = tooltip.offsetWidth || 280;
    let left = clampedX + 10;
    if (left + tw > w - 4) left = clampedX - tw - 10;
    if (left < 4) left = 4;
    tooltip.style.left = `${Math.round(left)}px`;
    tooltip.style.top = `${p + 6}px`;
  }
}

function bindHover(canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || canvas.dataset.hoverBound === "1") return;
  canvas.dataset.hoverBound = "1";

  canvas.addEventListener("mousemove", (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ds = dragState[canvasId];
    if (ds && ds.dragging) {
      ds.currentX = x;
      renderChart(canvasId, null);
      return;
    }
    renderChart(canvasId, x);
  });

  canvas.addEventListener("mouseleave", () => {
    const ds = dragState[canvasId];
    if (!ds || !ds.dragging) renderChart(canvasId, null);
  });

  canvas.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    const m = chartModels[canvasId];
    if (!m) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const clamped = Math.max(m.p, Math.min(m.w - m.p, x));
    dragState[canvasId] = { dragging: true, startX: clamped, currentX: clamped };
    canvas.style.cursor = "crosshair";
    renderChart(canvasId, null);
    e.preventDefault();
  });

  window.addEventListener("mouseup", (e) => {
    const ds = dragState[canvasId];
    if (!ds || !ds.dragging) return;
    ds.dragging = false;
    canvas.style.cursor = "";
    const m = chartModels[canvasId];
    if (!m) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    ds.currentX = x;
    const dx = Math.abs(ds.currentX - ds.startX);
    if (dx < 6) {
      renderChart(canvasId, null);
      return;
    }
    storeZoomBaseRangeIfNeeded();
    if (!isCustomRange()) {
      document.getElementById("rangeSel").value = "custom";
      updateCustomRangeVisibility();
    }
    const left = Math.min(ds.startX, ds.currentX);
    const right = Math.max(ds.startX, ds.currentX);
    const x1 = dataXFromPixel(m, left).xData;
    const x2 = dataXFromPixel(m, right).xData;
    if (!setCustomRangeMs(x1, x2)) {
      renderChart(canvasId, null);
      return;
    }
    renderZoomState();
    refreshData(true);
  });

  canvas.addEventListener("dblclick", (e) => {
    e.preventDefault();
    if (!zoomBaseRange) return;
    resetToZoomBaseRange();
  });
}

function drawChart(canvasId, series, xBounds = null) {
  const canvas = document.getElementById(canvasId);
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(rect.width));
  const h = Math.max(1, Math.round(rect.height));
  canvas.setAttribute("viewBox", `0 0 ${w} ${h}`);
  canvas.setAttribute("width", String(w));
  canvas.setAttribute("height", String(h));
  const p = getChartConfig(canvasId).padding;
  const rawSeries = series || [];
  const displaySeries = applyChartDisplayFilters(canvasId, rawSeries);
  const visibleSeries = filterSeriesByChart(canvasId, displaySeries);
  const scaledVisibleSeries = visibleSeries;

  let minX = 0;
  let maxX = 1;
  let minY = 0;
  let maxY = 1;
  let autoMinY = 0;
  let autoMaxY = 1;
  if (scaledVisibleSeries.length) {
    const all = scaledVisibleSeries.flatMap(s => s.points);
    minX = Math.min(...all.map(a => a.x));
    maxX = Math.max(...all.map(a => a.x));
    autoMinY = Math.min(...all.map(a => a.y));
    autoMaxY = Math.max(...all.map(a => a.y));
    if (minX === maxX) maxX += 1000;
    if (autoMinY === autoMaxY) autoMaxY += 1;
  }
  if (
    xBounds &&
    Number.isFinite(xBounds.minX) &&
    Number.isFinite(xBounds.maxX) &&
    xBounds.maxX > xBounds.minX
  ) {
    minX = Number(xBounds.minX);
    maxX = Number(xBounds.maxX);
  }
  const normMode = normalizeModes[canvasId];
  let drawSeries = scaledVisibleSeries;
  if (normMode && scaledVisibleSeries.length) {
    drawSeries = scaledVisibleSeries.map(s => {
      const ys = s.points.map(p => p.y);
      const sMin = Math.min(...ys);
      const sMax = Math.max(...ys);
      const range = sMax !== sMin ? sMax - sMin : 1;
      return {
        name: s.name,
        points: s.points.map(pt => ({ x: pt.x, y: (pt.y - sMin) / range, origY: pt.y }))
      };
    });
    minY = 0;
    maxY = 1;
  } else {
    const ov = yScaleOverrides[canvasId];
    if (ov && Number.isFinite(ov.min) && Number.isFinite(ov.max) && ov.max > ov.min) {
      minY = ov.min;
      maxY = ov.max;
    } else {
      minY = autoMinY;
      maxY = autoMaxY;
    }
  }

  const sx = x => p + ((x - minX) / (maxX - minX)) * (w - p * 2);
  const sy = y => h - p - ((y - minY) / (maxY - minY)) * (h - p * 2);

  const tooltip = document.getElementById(`tt-${canvasId}`);
  chartModels[canvasId] = { canvas, tooltip, w, h, p, series: drawSeries, rawSeries, displaySeries, minX, maxX, minY, maxY, autoMinY, autoMaxY, sx, sy };
  renderPanelSummary(canvasId, rawSeries, visibleSeries);
  if (getChartConfig(canvasId).kind === "redlab") renderTempChannelList(rawSeries);
  else renderSeriesChannelList(canvasId, rawSeries);
  bindHover(canvasId);
  syncYAxisInputs(canvasId);
  const t0 = performance.now();
  renderChart(canvasId, null);
  const t1 = performance.now();
  return t1 - t0;
}

function syncYAxisInputs(canvasId) {
  const minInput = document.getElementById(`yMin-${canvasId}`);
  const maxInput = document.getElementById(`yMax-${canvasId}`);
  const m = chartModels[canvasId];
  if (!minInput || !maxInput || !m) return;
  const normMode = normalizeModes[canvasId];
  const applyBtn = document.getElementById(`applyY-${canvasId}`);
  const autoBtn = document.getElementById(`autoY-${canvasId}`);
  minInput.disabled = normMode;
  maxInput.disabled = normMode;
  if (applyBtn) applyBtn.disabled = normMode;
  if (autoBtn) autoBtn.disabled = normMode;
  if (normMode) {
    minInput.value = "0";
    maxInput.value = "1";
    return;
  }
  if (document.activeElement === minInput || document.activeElement === maxInput) return;

  const draft = yScaleDrafts[canvasId];
  if (draft && draft.dirty) {
    minInput.value = draft.min ?? "";
    maxInput.value = draft.max ?? "";
    return;
  }

  const ov = yScaleOverrides[canvasId];
  if (ov) {
    minInput.value = String(ov.min);
    maxInput.value = String(ov.max);
  } else {
    minInput.value = Number.isFinite(m.autoMinY) ? m.autoMinY.toFixed(3) : "";
    maxInput.value = Number.isFinite(m.autoMaxY) ? m.autoMaxY.toFixed(3) : "";
  }
}

function applyYAxis(canvasId) {
  const minInput = document.getElementById(`yMin-${canvasId}`);
  const maxInput = document.getElementById(`yMax-${canvasId}`);
  const m = chartModels[canvasId];
  if (!minInput || !maxInput || !m) return;
  const min = Number(minInput.value);
  const max = Number(maxInput.value);
  if (!Number.isFinite(min) || !Number.isFinite(max) || max <= min) {
    setStatus(`error: invalid Y range for ${canvasId}, expected max > min`);
    return;
  }
  yScaleOverrides[canvasId] = { min, max };
  yScaleDrafts[canvasId] = { min: String(min), max: String(max), dirty: false };
  drawChart(canvasId, m.rawSeries || m.series || [], { minX: m.minX, maxX: m.maxX });
  setStatus(`ok | ${canvasId} Y range fixed [${min}..${max}]`);
}

function resetYAxisAuto(canvasId) {
  const m = chartModels[canvasId];
  if (!m) return;
  delete yScaleOverrides[canvasId];
  yScaleDrafts[canvasId] = { min: "", max: "", dirty: false };
  drawChart(canvasId, m.rawSeries || m.series || [], { minX: m.minX, maxX: m.maxX });
  setStatus(`ok | ${canvasId} Y range auto`);
}

function loadNormalizeMode(canvasId) {
  try {
    normalizeModes[canvasId] = localStorage.getItem(`graf.normalizeMode.${canvasId}`) === "1";
  } catch (_) {
    normalizeModes[canvasId] = false;
  }
}

function saveNormalizeMode(canvasId) {
  try {
    localStorage.setItem(`graf.normalizeMode.${canvasId}`, normalizeModes[canvasId] ? "1" : "0");
  } catch (_) {}
}

function updateNormButton(canvasId) {
  const wrap = document.getElementById(`normY-${canvasId}`);
  if (!wrap) return;
  const cb = wrap.querySelector("input[type=checkbox]");
  if (cb) cb.checked = Boolean(normalizeModes[canvasId]);
}

function applyNormalize(canvasId, value) {
  normalizeModes[canvasId] = value;
  saveNormalizeMode(canvasId);
  if (value) delete yScaleOverrides[canvasId];
  const m = chartModels[canvasId];
  if (m) drawChart(canvasId, m.rawSeries || m.series || [], { minX: m.minX, maxX: m.maxX });
  setStatus(`ok | ${canvasId} normalize ${value ? "on" : "off"}`);
}

function bindNormalizeControls() {
  chartIds.forEach((id) => {
    loadNormalizeMode(id);
    updateNormButton(id);
    const wrap = document.getElementById(`normY-${id}`);
    if (!wrap) return;
    const cb = wrap.querySelector("input[type=checkbox]");
    if (cb) cb.addEventListener("change", () => applyNormalize(id, cb.checked));
  });
}

function loadYawFilterMode(canvasId) {
  try {
    yawFilterModes[canvasId] = localStorage.getItem(`graf.yawFilterMode.${canvasId}`) === "1";
  } catch (_) {
    yawFilterModes[canvasId] = false;
  }
}

function saveYawFilterMode(canvasId) {
  try {
    localStorage.setItem(`graf.yawFilterMode.${canvasId}`, yawFilterModes[canvasId] ? "1" : "0");
  } catch (_) {}
}

function updateYawFilterButton(canvasId) {
  const wrap = document.getElementById(`yawFilter-${canvasId}`);
  if (!wrap) return;
  const cb = wrap.querySelector("input[type=checkbox]");
  if (cb) cb.checked = Boolean(yawFilterModes[canvasId]);
}

function applyYawFilter(canvasId, value) {
  yawFilterModes[canvasId] = value;
  saveYawFilterMode(canvasId);
  const m = chartModels[canvasId];
  if (m) drawChart(canvasId, m.rawSeries || m.series || [], { minX: m.minX, maxX: m.maxX });
  setStatus(`ok | ${canvasId} yaw spike filter ${value ? "on" : "off"}`);
}

function bindYawFilterControls() {
  chartIds.forEach((id) => {
    loadYawFilterMode(id);
    updateYawFilterButton(id);
    const wrap = document.getElementById(`yawFilter-${id}`);
    if (!wrap) return;
    const cb = wrap.querySelector("input[type=checkbox]");
    if (cb) cb.addEventListener("change", () => applyYawFilter(id, cb.checked));
  });
}

function bindYAxisControls() {
  chartIds.forEach((id) => {
    const minInput = document.getElementById(`yMin-${id}`);
    const maxInput = document.getElementById(`yMax-${id}`);
    const applyBtn = document.getElementById(`applyY-${id}`);
    const autoBtn = document.getElementById(`autoY-${id}`);
    const captureDraft = () => {
      if (!minInput || !maxInput) return;
      yScaleDrafts[id] = { min: minInput.value, max: maxInput.value, dirty: true };
    };
    const applyByEnter = (e) => {
      if (e.key === "Enter") applyYAxis(id);
    };
    if (minInput) {
      minInput.addEventListener("input", captureDraft);
      minInput.addEventListener("keydown", applyByEnter);
    }
    if (maxInput) {
      maxInput.addEventListener("input", captureDraft);
      maxInput.addEventListener("keydown", applyByEnter);
    }
    if (applyBtn) applyBtn.addEventListener("click", () => applyYAxis(id));
    if (autoBtn) autoBtn.addEventListener("click", () => resetYAxisAuto(id));
  });
}
