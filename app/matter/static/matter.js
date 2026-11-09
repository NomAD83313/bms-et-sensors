const UI_BUILD_ID = "2026-05-04-thread-otbr-master";
const REFRESH_INTERVAL_MS = 7000;
let refreshInFlight = false;
function yn(v) { return v ? "yes" : "no"; }
function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}
function nvl(value, fallback) {
  return value == null ? fallback : value;
}
function setStatus(status) {
  const badge = document.getElementById("statusBadge");
  if (!badge) return;
  badge.textContent = status || "unknown";
  badge.className = "status-pill " + (status === "ok" ? "status-ok" : (status === "degraded" ? "status-warn" : "status-err"));
}
function setOtStatus(status, info) {
  const el = document.getElementById("otStatus");
  if (!el) return;
  el.textContent = `${status || "unknown"}${info ? " | " + info : ""}`;
  el.className = "status-pill " + (status === "ok" ? "status-ok" : (status === "degraded" ? "status-warn" : "status-err"));
}
function setStackService(statusId, summaryId, level, label, summary) {
  const badge = document.getElementById(statusId);
  const summaryEl = document.getElementById(summaryId);
  const safeLevel = level === "ok" ? "ok" : (level === "warn" ? "warn" : "err");
  if (badge) {
    badge.textContent = label || "unknown";
    badge.className = "status-pill status-" + safeLevel;
  }
  if (summaryEl) summaryEl.textContent = summary || "-";
}
function summarizeContainerServices(state) {
  const services = (state && state.services) || {};
  const detail = Object.entries(services).map(([name, status]) => `${name}=${status}`).join(", ");
  return detail || "no containers";
}
function esc(value) {
  return String(nvl(value, "-"))
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
function reportUiError(prefix, err) {
  const text = `${prefix}: ${err && err.message ? err.message : String(err)}`;
  setStatus("js-error");
  setOtStatus("js-error", "render failure");
  setText("lastError", `${text} | build=${UI_BUILD_ID}`);
  setText("rawJson", JSON.stringify({ error: text, build: UI_BUILD_ID }, null, 2));
  console.error(prefix, err);
}
function safeRender(prefix, fn) {
  try {
    fn();
  } catch (err) {
    reportUiError(prefix, err);
  }
}
function displayMac(value) {
  return String(value || "").replaceAll("\\\\:", ":");
}
function formatDurationSeconds(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  const whole = Math.floor(seconds);
  const days = Math.floor(whole / 86400);
  const hours = Math.floor((whole % 86400) / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const secs = whole % 60;
  const parts = [];
  if (days) parts.push(`${days}d`);
  if (hours || days) parts.push(`${hours}h`);
  if (minutes || hours || days) parts.push(`${minutes}m`);
  parts.push(`${secs}s`);
  return parts.join(" ");
}
function formatTimestamp(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const date = new Date(text);
  if (Number.isNaN(date.getTime())) return text;
  return date.toLocaleString();
}
function runtimeMeta(node) {
  const meta = [];
  const uptime = formatDurationSeconds(node && node.uptime_sec);
  if (uptime) meta.push(`uptime ${esc(uptime)}`);
  const bootAt = formatTimestamp(node && node.estimated_last_boot_at);
  if (bootAt) meta.push(`last boot ~${esc(bootAt)}`);
  if (node && node.reboot_count != null) meta.push(`reboots ${esc(node.reboot_count)}`);
  if (node && node.boot_reason_label) meta.push(`boot ${esc(node.boot_reason_label)}`);
  return meta;
}
function badgeTokenClass(label) {
  const firstToken = String(label || "").trim().toLowerCase().split(/\\s+/)[0] || "";
  const fullToken = String(label || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const token = firstToken === "node" ? "" : (fullToken || firstToken);
  return token ? ` badge-${token}` : "";
}
function stateBadge(label, cls) {
  return `<span class="topology-badge ${cls}${badgeTokenClass(label)}">${esc(label)}</span>`;
}
function nodeBadge(nodeId) {
  return nodeId == null ? "" : stateBadge(`node ${nodeId}`, "node-id");
}
function renderMatterControlButtons(node) {
  if (!node || node.matter_node_id == null) return "";
  const controls = Array.isArray(node.standard_controls) ? node.standard_controls : [];
  const buttons = [];
  if (node.air_reboot_supported) {
    buttons.push(`
      <button type="button"
        class="btn matter-air-reboot-btn"
        data-node-id="${esc(node.matter_node_id)}">Reboot</button>
    `);
  }
  if (node.available === false) {
    return buttons.length ? `<div class="service-actions">${buttons.join("")}</div>` : "";
  }
  for (const control of controls) {
    const commands = Array.isArray(control.commands) ? control.commands : [];
    for (const commandName of commands) {
      buttons.push(`
        <button type="button"
          class="btn matter-command-btn"
          data-node-id="${esc(node.matter_node_id)}"
          data-endpoint-id="${esc(control.endpoint_id)}"
          data-cluster-id="${esc(control.cluster_id)}"
          data-command-name="${esc(commandName)}">${esc(commandName)}</button>
      `);
    }
  }
  return buttons.length ? `<div class="service-actions">${buttons.join("")}</div>` : "";
}
function roleBadge(role) {
  const text = String(role || "").trim().toLowerCase();
  if (!text) return "";
  const cls = text === "leader" ? "role-leader" : (text === "router" ? "role-router" : (text === "child" ? "role-child" : ""));
  return stateBadge(text, cls || "status-warn");
}
function signalBadge(level) {
  const text = String(level || "").trim().toLowerCase();
  if (!text || text === "unknown") return stateBadge("signal unknown", "status-muted");
  if (text === "weak") return stateBadge("weak link", "status-err");
  if (text === "warn" || text === "stale") return stateBadge(`signal ${text}`, "status-warn");
  return stateBadge(`signal ${text}`, "status-ok");
}
function wifiSignalClass(client) {
  const band = String((client || {}).signal_band || "").trim().toLowerCase();
  if (band === "excellent" || band === "good") return "status-ok";
  if (band === "fair") return "status-warn";
  if (band === "weak" || band === "poor") return "status-err";
  const quality = Number((client || {}).signal_quality);
  if (Number.isFinite(quality)) {
    if (quality >= 80) return "status-ok";
    if (quality >= 50) return "status-warn";
    return "status-err";
  }
  return "status-muted";
}
function rssiSignalClass(value) {
  const rssi = Number(value);
  if (!Number.isFinite(rssi)) return "status-muted";
  if (rssi >= -60) return "status-ok";
  if (rssi >= -75) return "status-warn";
  return "status-err";
}
function wifiRssiBadge(client) {
  if (!client || client.signal_dbm == null) return "";
  return stateBadge(`rssi ${client.signal_dbm} dBm`, rssiSignalClass(client.signal_dbm));
}
function wifiQualityBadge(client) {
  if (!client || client.signal_quality == null) return "";
  return stateBadge(`quality ${client.signal_quality}%`, wifiSignalClass(client));
}
function threadIdMeta(node) {
  const record = node || {};
  return [
    `ext ${esc(record.ext_address || "unknown")}`,
    `rloc ${esc(record.rloc16 || "unknown")}`,
  ];
}
function rssiMetrics(record) {
  const item = record || {};
  const metrics = [];
  if (item.last_rssi_dbm != null) metrics.push(`rssi last ${esc(item.last_rssi_dbm)} dBm`);
  else if (item.average_rssi_dbm != null) metrics.push(`rssi avg ${esc(item.average_rssi_dbm)} dBm`);
  if (item.last_rssi_dbm != null && item.average_rssi_dbm != null) metrics.push(`avg ${esc(item.average_rssi_dbm)} dBm`);
  if (item.rssi_observer_label && item.rssi_target_label) {
    metrics.push(`${esc(item.rssi_observer_label)} hears ${esc(item.rssi_target_label)}`);
  } else if (item.rssi_observer_label) {
    metrics.push(`heard by ${esc(item.rssi_observer_label)}`);
  }
  return metrics;
}
function rlocBadge(record) {
  const rloc = String((record || {}).rloc16 || "").trim();
  return rloc ? stateBadge(`rloc ${rloc}`, "status-muted") : "";
}
function threadRssiBadge(record) {
  const item = record || {};
  const value = item.last_rssi_dbm != null ? item.last_rssi_dbm : item.average_rssi_dbm;
  if (value == null) return "";
  return stateBadge(`rssi ${value} dBm`, rssiSignalClass(value));
}
function matterNodeCard(node, extraMeta) {
  const badges = [stateBadge("matter", "status-ok")];
  const networkType = String(node.network_type || "").trim();
  if (networkType) badges.push(stateBadge(networkType.toLowerCase(), "status-muted"));
  if (node.role) badges.push(roleBadge(node.role));
  if (node.available === false) badges.push(stateBadge("offline", "status-warn"));
  if (node.available === true) badges.push(stateBadge("available", "status-ok"));
  if (node.address_trusted === false) badges.push(stateBadge("address conflict", "status-err"));
  const title = node.serial_number || node.product_name || node.reported_ext_address || node.ext_address || node.rloc16 || "Matter node";
  const meta = [
    node.product_name ? `product ${esc(node.product_name)}` : "",
    node.vendor_name ? `vendor ${esc(node.vendor_name)}` : "",
    node.role ? `role ${esc(node.role)}` : "",
    node.matter_node_id != null ? `node ${esc(node.matter_node_id)}` : "",
    networkType ? `network ${esc(networkType)}` : "",
    ...runtimeMeta(node),
    ...(Array.isArray(extraMeta) ? extraMeta : []),
    node.reported_ext_address && node.reported_ext_address !== node.ext_address ? `reported ext ${esc(node.reported_ext_address)}` : "",
    node.ext_address ? `ext ${esc(node.ext_address)}` : "",
    node.reported_rloc16 && node.reported_rloc16 !== node.rloc16 ? `reported rloc ${esc(node.reported_rloc16)}` : "",
    node.rloc16 ? `rloc ${esc(node.rloc16)}` : "",
    node.channel != null ? `ch ${esc(node.channel)}` : "",
  ].filter(Boolean);
  return `
    <div class="topology-node">
      <div class="topology-title">
        <div class="topology-name">${esc(title)}</div>
        <div class="topology-badges">${badges.join("")}</div>
      </div>
      <div class="topology-meta">${meta.map((item) => `<span>${item}</span>`).join("")}</div>
      ${renderMatterControlButtons(node)}
    </div>
  `;
}
function renderWifiMaster(apStatus) {
  const el = document.getElementById("wifiApMaster");
  if (!el) return;
  const ap = apStatus || {};
  const profile = ap.profile || {};
  if (!Object.keys(ap).length || ap.status === "unavailable") {
    el.innerHTML = '<div class="topology-empty">Wi-Fi access point status unavailable.</div>';
    return;
  }
  const badges = [
    stateBadge("wlan master", "status-ok"),
    stateBadge(ap.active ? "online" : "offline", ap.active ? "status-ok" : "status-warn"),
  ];
  const meta = [
    `interface ${esc(ap.interface || profile.interface_name || "wlan0")}`,
    profile.ssid ? `ssid ${esc(profile.ssid)}` : "",
    profile.channel ? `ch ${esc(profile.channel)}` : "",
    ap.client_count != null ? `clients ${esc(ap.client_count)}` : "",
    ap.mac_address ? `mac ${esc(displayMac(ap.mac_address))}` : "",
    (ap.ipv4_addresses || []).length ? `ipv4 ${esc((ap.ipv4_addresses || []).join(", "))}` : "",
    profile.ipv6_method ? `ipv6 ${esc(profile.ipv6_method)}` : "",
    ap.state ? `state ${esc(ap.state)}` : "",
  ].filter(Boolean);
  el.innerHTML = `
    <div class="topology-node">
      <div class="topology-title">
        <div class="topology-name">${esc(profile.name || ap.active_connection || "Wi-Fi access point")}</div>
        <div class="topology-badges">${badges.join("")}</div>
      </div>
      <div class="topology-meta">${meta.map((item) => `<span>${item}</span>`).join("")}</div>
    </div>
  `;
}
function renderWifiMatterDevices(nodes, apStatus, snapshotPending) {
  const el = document.getElementById("wifiMatterNodes");
  if (!el) return;
  const clients = ((apStatus || {}).clients || []);
  const clientsByName = new Map();
  for (const client of clients) {
    for (const key of [client.display_name, client.hostname]) {
      const token = String(key || "").trim().toLowerCase();
      if (token) clientsByName.set(token, client);
    }
  }
  const wifiNodes = (Array.isArray(nodes) ? nodes : []).filter((node) => String(node.network_type || "").toLowerCase() === "wifi");
  if (!wifiNodes.length) {
    el.innerHTML = snapshotPending
      ? '<div class="topology-empty">Matter inventory refresh pending...</div>'
      : '<div class="topology-empty">No Wi-Fi Matter devices found.</div>';
    return;
  }
  el.innerHTML = wifiNodes.map((node) => {
    const apClient = clientsByName.get(String(node.serial_number || "").trim().toLowerCase());
    const badges = [
      stateBadge("matter", "status-ok"),
      stateBadge("wifi", "status-muted"),
      nodeBadge(node.matter_node_id),
      stateBadge(node.available === false ? "offline" : "available", node.available === false ? "status-warn" : "status-ok"),
    ];
    if (apClient) badges.push(stateBadge("matched", "status-ok"));
    else badges.push(stateBadge("unmatched", "status-warn"));
    badges.push(wifiRssiBadge(apClient));
    badges.push(wifiQualityBadge(apClient));
    const title = node.serial_number || node.product_name || "Wi-Fi Matter node";
    const meta = [
      node.product_name ? `product ${esc(node.product_name)}` : "",
      node.vendor_name ? `vendor ${esc(node.vendor_name)}` : "",
      node.matter_node_id != null ? `node ${esc(node.matter_node_id)}` : "",
      "network WiFi",
      ...runtimeMeta(node),
      ...(apClient ? [
      apClient.ip ? `ip ${esc(apClient.ip)}` : "",
      apClient.mac ? `wlan ${esc(displayMac(apClient.mac))}` : "",
      ] : ["ap client not matched"]),
    ].filter(Boolean);
    return `
      <div class="topology-node">
        <div class="topology-title">
        <div class="topology-name">${esc(title)}</div>
        <div class="topology-badges">${badges.filter(Boolean).join("")}</div>
      </div>
      <div class="topology-meta">${meta.map((item) => `<span>${item}</span>`).join("")}</div>
      ${renderMatterControlButtons(node)}
    </div>
  `;
  }).join("");
}
function renderThreadMaster(topology, otDiag) {
  const el = document.getElementById("threadOtbrMaster");
  if (!el) return;
  const observed = (topology && topology.observed_topology) || {};
  const otbr = observed.otbr || {};
  const diag = otDiag || {};
  const settings = diag.settings || {};
  const dongle = diag.dongle || {};
  if (!Object.keys(otbr).length && !Object.keys(settings).length && !Object.keys(dongle).length) {
    el.innerHTML = '<div class="topology-empty">OTBR master unavailable.</div>';
    return;
  }
  const serialMatches = Array.isArray(dongle.serial_matches) ? dongle.serial_matches : [];
  const primarySerial = serialMatches.length ? serialMatches[0] : {};
  const badges = [
    stateBadge("otbr master", "status-ok"),
    stateBadge("rcp", "status-muted"),
  ];
  const otbrRloc = otbr.rloc16 ? { rloc16: otbr.rloc16 } : (settings.rloc16 ? { rloc16: `0x${settings.rloc16}` } : {});
  badges.push(rlocBadge(otbrRloc));
  if (otbr.channel != null) badges.push(stateBadge(`ch ${otbr.channel}`, "status-muted"));
  else if (settings.channel) badges.push(stateBadge(`ch ${settings.channel}`, "status-muted"));
  if (settings.panid) badges.push(stateBadge(`pan ${settings.panid}`, "status-muted"));
  const role = String(otbr.role || settings.state || "").trim().toLowerCase();
  if (role) badges.push(roleBadge(role));
  if (diag.available === false) badges.push(stateBadge("offline", "status-warn"));
  else badges.push(stateBadge("online", "status-ok"));
  const meta = [
    dongle.rcp_device ? `device ${esc(dongle.rcp_device)}` : "",
    primarySerial.target ? `tty ${esc(primarySerial.target)}` : "",
    dongle.rcp_baud ? `baud ${esc(dongle.rcp_baud)}` : "",
    otbr.ext_address ? `ext ${esc(otbr.ext_address)}` : (settings.extaddr ? `ext ${esc(settings.extaddr)}` : ""),
    otbr.rloc16 ? `rloc ${esc(otbr.rloc16)}` : (settings.rloc16 ? `rloc 0x${esc(settings.rloc16)}` : ""),
    otbr.channel != null ? `ch ${esc(otbr.channel)}` : (settings.channel ? `ch ${esc(settings.channel)}` : ""),
    settings.panid ? `pan ${esc(settings.panid)}` : "",
    settings.version ? `version ${esc(settings.version)}` : "",
  ].filter(Boolean);
  el.innerHTML = `
    <div class="topology-node">
      <div class="topology-title">
        <div class="topology-name">OTBR / RCP</div>
        <div class="topology-badges">${badges.join("")}</div>
      </div>
      <div class="topology-meta">${meta.map((item) => `<span>${item}</span>`).join("")}</div>
    </div>
  `;
}
function renderKnownMatterDevices(nodes, topologyTree, snapshotPending) {
  const el = document.getElementById("topologyNodes");
  if (!el) return;
  const connectedKeys = new Set();
  function addNodeKeys(record) {
    if (!record) return;
    connectedKeys.add(String(nvl(record.matter_node_id, "")));
    connectedKeys.add(String(nvl(record.serial_number, "")));
    connectedKeys.add(String(nvl(record.ext_address, "")));
    connectedKeys.add(String(nvl(record.rloc16, "")));
    connectedKeys.add(String(nvl(record.candidate_matter_node_id, "")));
    connectedKeys.add(String(nvl(record.candidate_serial_number, "")));
  }
  for (const group of ((topologyTree || {}).groups || [])) {
    addNodeKeys(group.parent || {});
    for (const item of (group.children || [])) addNodeKeys(item.child || {});
  }
  const knownOnly = (Array.isArray(nodes) ? nodes : []).filter((node) => {
    const networkType = String(node.network_type || "").toLowerCase();
    if (networkType !== "thread") return false;
    const keys = [
      String(nvl(node.matter_node_id, "")),
      String(nvl(node.serial_number, "")),
      String(nvl(node.ext_address, "")),
      String(nvl(node.rloc16, "")),
    ];
    if (node.available === false) return true;
    return !keys.some((key) => key && connectedKeys.has(key));
  });
  if (!knownOnly.length) {
    if (snapshotPending) {
      el.innerHTML = '<div class="topology-empty">Matter inventory refresh pending...</div>';
      return;
    }
    el.innerHTML = '<div class="topology-empty">No extra Thread Matter devices found.</div>';
    return;
  }
  el.innerHTML = knownOnly.map((node) => {
    return matterNodeCard(node, []);
  }).join("");
}
function topologyNodeKey(node, fallback) {
  if (!node) return fallback || "";
  return [
    String(node.ext_address || ""),
    String(node.rloc16 || ""),
    String(node.matter_node_id || ""),
    String(node.label || node.serial_number || fallback || ""),
  ].join("|");
}
function nestTopologyGroups(groups) {
  const wrappers = (Array.isArray(groups) ? groups : []).map((group, index) => ({ group, index, nested: [] }));
  const byParentKey = new Map();
  for (const wrapper of wrappers) {
    byParentKey.set(topologyNodeKey((wrapper.group || {}).parent || {}, "group-" + wrapper.index), wrapper);
  }
  const roots = [];
  for (const wrapper of wrappers) {
    const upstreamNode = (((wrapper.group || {}).upstream || {}).node) || null;
    const parentWrapper = upstreamNode ? byParentKey.get(topologyNodeKey(upstreamNode, "")) : null;
    if (parentWrapper && parentWrapper !== wrapper) {
      parentWrapper.nested.push(wrapper);
      continue;
    }
    roots.push(wrapper);
  }
  const sortByParentLabel = (left, right) => {
    const leftLabel = String((((left || {}).group || {}).parent || {}).label || "");
    const rightLabel = String((((right || {}).group || {}).parent || {}).label || "");
    return leftLabel.localeCompare(rightLabel);
  };
  const sortNested = (items) => {
    items.sort(sortByParentLabel);
    for (const item of items) sortNested(item.nested || []);
  };
  sortNested(roots);
  return roots;
}
function topologyGroupRows(wrapper) {
  const group = (wrapper && wrapper.group) || {};
  const rows = (group.children || []).map((item) => ({ kind: "child", label: String((((item || {}).child) || {}).label || ""), item }));
  for (const nested of ((wrapper && wrapper.nested) || [])) {
    rows.push({ kind: "group", label: String((((nested.group || {}).parent) || {}).label || ""), nested });
  }
  rows.sort((left, right) => left.label.localeCompare(right.label));
  return rows;
}
function treeParentBadges(parent, upstream) {
  const badges = [];
  if (parent.matter_node_id != null) badges.push(nodeBadge(parent.matter_node_id));
  badges.push(rlocBadge(parent));
  badges.push(threadRssiBadge(upstream));
  if (parent.role) badges.push(roleBadge(parent.role));
  if (parent.matched) badges.push(stateBadge("matched", "status-ok"));
  return badges.filter(Boolean);
}
function upstreamMetrics(upstream) {
  const link = upstream || {};
  const metrics = [];
  const upstreamNode = link.node || {};
  if (upstreamNode.label || upstreamNode.ext_address || upstreamNode.rloc16) {
    metrics.push(`upstream ${esc(upstreamNode.label || upstreamNode.ext_address || upstreamNode.rloc16)}`);
  }
  if (link.link_quality_in != null) metrics.push(`up-lqi-in ${esc(link.link_quality_in)}`);
  if (link.link_quality_out != null) metrics.push(`up-lqi-out ${esc(link.link_quality_out)}`);
  metrics.push(...rssiMetrics(link));
  if (link.confidence) metrics.push(`upstream confidence ${esc(link.confidence)}`);
  return metrics;
}
function renderTreeChild(item) {
  const child = item.child || {};
  const childBadges = [];
  const childActions = renderMatterControlButtons(child);
  const displaySources = Array.isArray(child.source) && child.source.length ? child.source : (Array.isArray(item.source) ? item.source : []);
  const isResolvedInference = !!child.matched && !!child.inferred_match;
  const relationLabel = item.relation === "neighbor" ? "neighbor" : "parent-child";
  for (const source of displaySources) {
    childBadges.push(stateBadge(source, source === "otbr" ? "status-warn" : "status-ok"));
  }
  if (child.matter_node_id != null) childBadges.push(nodeBadge(child.matter_node_id));
  childBadges.push(rlocBadge(child));
  childBadges.push(threadRssiBadge(item));
  childBadges.push(stateBadge(relationLabel, "status-muted"));
  if (child.role) childBadges.push(roleBadge(child.role));
  if (child.matched) childBadges.push(stateBadge("matched", "status-ok"));
  if (child.candidate_match && !isResolvedInference) childBadges.push(stateBadge("candidate", "status-warn"));
  if (child.transient_match) childBadges.push(stateBadge("recent", "status-warn"));
  if (child.inferred_match) childBadges.push(stateBadge("inferred match", child.matched ? "status-ok" : "status-warn"));
  if (item.confidence) childBadges.push(stateBadge(item.confidence, item.confidence === "high" ? "status-ok" : "status-warn"));
  if (item.signal_level) childBadges.push(signalBadge(item.signal_level));
  const metrics = [
    item.link_quality_in != null ? `lqi-in ${esc(item.link_quality_in)}` : "",
    item.link_quality_out != null ? `lqi-out ${esc(item.link_quality_out)}` : "",
    child.matched_by ? `matched by ${esc(child.matched_by)}` : "",
    child.candidate_matter_node_id != null && !isResolvedInference ? `candidate node ${esc(child.candidate_matter_node_id)}` : "",
    child.candidate_serial_number && !isResolvedInference ? `candidate ${esc(child.candidate_serial_number)}` : "",
    ...runtimeMeta(child),
    ...rssiMetrics(item),
    item.confidence ? `confidence ${esc(item.confidence)}` : "",
  ].filter(Boolean);
  return `
    <div class="tree-child-row">
      <div class="tree-child-main">
        <div class="tree-child-name">${esc(child.label || child.serial_number || child.ext_address || "?")}</div>
        <div class="tree-child-meta">
          ${threadIdMeta(child).map((part) => `<span>${part}</span>`).join("")}
          ${metrics.map((metric) => `<span>${metric}</span>`).join("")}
        </div>
      </div>
      <div class="topology-badges">
        ${childBadges.join("")}
      </div>
      ${childActions}
    </div>
  `;
}
function renderNestedTopologyGroup(wrapper) {
  const group = (wrapper && wrapper.group) || {};
  const parent = group.parent || {};
  const upstream = group.upstream || null;
  const rows = topologyGroupRows(wrapper);
  const parentActions = renderMatterControlButtons(parent);
  const parentMetrics = [
    ...threadIdMeta(parent),
    `${esc(rows.length)} children`,
    ...upstreamMetrics(upstream),
  ];
  return `
    <div class="tree-child-branch">
      <div class="tree-child-row tree-child-group">
        <div class="tree-child-main">
          <div class="tree-child-name">${esc(parent.label || parent.serial_number || parent.ext_address || "?")}</div>
          <div class="tree-child-meta">
            ${parentMetrics.map((part) => `<span>${part}</span>`).join("")}
          </div>
        </div>
        <div class="topology-badges">
          ${treeParentBadges(parent, upstream).join("")}
        </div>
        ${parentActions}
      </div>
      <div class="tree-children">
        ${rows.map((row) => row.kind === "group" ? renderNestedTopologyGroup(row.nested) : renderTreeChild(row.item)).join("")}
      </div>
    </div>
  `;
}
function renderTopologyTree(tree, elementId = "topologyTree") {
  const el = document.getElementById(elementId);
  if (!el) return;
  const groups = (tree && tree.groups) || [];
  const unresolved = (tree && tree.unresolved_children) || [];
  if (!groups.length && !unresolved.length) {
    el.innerHTML = '<div class="topology-empty">No parent-child tree yet.</div>';
    return;
  }
  const blocks = [];
  for (const wrapper of nestTopologyGroups(groups)) {
    const group = wrapper.group || {};
    const parent = group.parent || {};
    const upstream = group.upstream || null;
    let upstreamHtml = "";
    if (upstream && upstream.node && upstream.node.node_class !== "otbr") {
      const upstreamNode = upstream.node || {};
      const upstreamBadges = [];
      upstreamBadges.push(stateBadge("border router", "status-ok"));
      for (const source of (Array.isArray(upstream.source) ? upstream.source : [])) {
        upstreamBadges.push(stateBadge(source, source === "otbr" ? "status-warn" : "status-ok"));
      }
      upstreamBadges.push(rlocBadge(upstreamNode));
      upstreamBadges.push(threadRssiBadge(upstream));
      upstreamBadges.push(stateBadge("neighbor", "status-muted"));
      if (upstreamNode.role) upstreamBadges.push(roleBadge(upstreamNode.role));
      if (upstream.confidence) upstreamBadges.push(stateBadge(upstream.confidence, upstream.confidence === "high" ? "status-ok" : "status-warn"));
      if (upstream.signal_level) upstreamBadges.push(signalBadge(upstream.signal_level));
      const upstreamMetrics = [
        upstream.link_quality_in != null ? `lqi-in ${esc(upstream.link_quality_in)}` : "",
        upstream.link_quality_out != null ? `lqi-out ${esc(upstream.link_quality_out)}` : "",
        ...rssiMetrics(upstream),
        upstream.confidence ? `confidence ${esc(upstream.confidence)}` : "",
      ].filter(Boolean);
      upstreamHtml = `
        <div class="tree-child-row tree-upstream-row">
          <div class="tree-child-main">
            <div class="tree-child-name">${esc(upstreamNode.label || upstreamNode.ext_address || upstreamNode.rloc16 || "OTBR")}</div>
            <div class="tree-child-meta">
              ${threadIdMeta(upstreamNode).map((part) => `<span>${part}</span>`).join("")}
              <span>neighbor link</span>
              <span class="tree-link-arrow">&lt;-&gt;</span>
              <span>${esc(parent.label || parent.serial_number || parent.ext_address || parent.rloc16 || "?")}</span>
              ${threadIdMeta(parent).map((part) => `<span>${part}</span>`).join("")}
              ${upstreamMetrics.map((metric) => `<span>${metric}</span>`).join("")}
            </div>
          </div>
          <div class="topology-badges">
            ${upstreamBadges.filter(Boolean).join("")}
          </div>
        </div>
      `;
    }
    const rows = topologyGroupRows(wrapper);
    const parentActions = renderMatterControlButtons(parent);
    const parentMetrics = [
      ...threadIdMeta(parent),
      `${esc(rows.length)} children`,
      ...upstreamMetrics(upstream),
    ];
    const childHtml = rows.map((row) => row.kind === "group" ? renderNestedTopologyGroup(row.nested) : renderTreeChild(row.item)).join("");
    blocks.push(`
      <div class="tree-group">
        ${upstreamHtml}
        <div class="tree-parent">
          <div class="tree-parent-main">
            <div class="tree-parent-name">${esc(parent.label || parent.serial_number || parent.ext_address || "?")}</div>
            <div class="tree-parent-meta">
              ${parentMetrics.map((part) => `<span>${part}</span>`).join("")}
            </div>
          </div>
          <div class="topology-badges">
            ${treeParentBadges(parent, upstream).join("")}
          </div>
          ${parentActions}
        </div>
        <div class="tree-children">
          ${childHtml}
        </div>
      </div>
    `);
  }
  if (unresolved.length) {
    unresolved.forEach((item) => {
      const child = item.child || {};
      const metrics = [
        item.link_quality_in != null ? `lqi-in ${esc(item.link_quality_in)}` : "",
        item.link_quality_out != null ? `lqi-out ${esc(item.link_quality_out)}` : "",
        ...rssiMetrics(item),
      ].filter(Boolean);
      blocks.push(`
      <div class="tree-group">
        <div class="tree-parent">
          <div class="tree-parent-main">
            <div class="tree-parent-name">${esc(child.label || child.serial_number || "?")}</div>
            <div class="tree-parent-meta">
              <span>parent unknown</span>
              ${metrics.map((metric) => `<span>${metric}</span>`).join("")}
            </div>
          </div>
          <div class="topology-badges">${stateBadge("unresolved", "status-warn")}</div>
        </div>
      </div>
    `);
    });
  }
  el.innerHTML = blocks.join("");
}
function renderTopologyWarnings(warnings, elementId = "topologyWarnings") {
  const el = document.getElementById(elementId);
  if (!el) return;
  const items = [];
  for (const warning of (warnings || [])) {
    if (!warning || typeof warning !== "object") continue;
    const title = warning.type || "warning";
    const meta = [
      warning.matter_node_id != null ? `node ${warning.matter_node_id}` : "",
      warning.serial_number ? `serial ${warning.serial_number}` : "",
      warning.relation ? `relation ${warning.relation}` : "",
      warning.ext_address ? `ext ${warning.ext_address}` : "",
      warning.rloc16 ? `rloc ${warning.rloc16}` : "",
      warning.src_label ? `src ${warning.src_label}` : "",
      warning.src_ext_address ? `src ${warning.src_ext_address}` : "",
      warning.src_rloc16 ? `src rloc ${warning.src_rloc16}` : "",
      warning.dst_label ? `dst ${warning.dst_label}` : "",
      warning.dst_ext_address ? `dst ${warning.dst_ext_address}` : "",
      warning.dst_rloc16 ? `dst rloc ${warning.dst_rloc16}` : "",
      warning.role ? `role ${warning.role}` : "",
      warning.rule ? `rule ${warning.rule}` : "",
      warning.signal_level ? `signal ${warning.signal_level}` : "",
      Array.isArray(warning.signal_reasons) && warning.signal_reasons.length ? `reason ${warning.signal_reasons.join(", ")}` : "",
    ].filter(Boolean);
    items.push({ title, meta });
  }
  if (!items.length) {
    el.innerHTML = '<div class="topology-empty">No topology warnings.</div>';
    return;
  }
  el.innerHTML = items.map((item) => `
    <div class="topology-node">
        <div class="topology-title">
          <div class="topology-name">${esc(item.title)}</div>
          <div class="topology-badges">${stateBadge("warning", "status-warn")}</div>
        </div>
      <div class="topology-meta">${item.meta.map((part) => `<span>${esc(part)}</span>`).join("")}</div>
    </div>
  `).join("");
}
function renderTopologySummary(topology) {
  const el = document.getElementById("topologySummary");
  if (!el) return;
  const counters = (topology && topology.counters) || {};
  const rules = Array.isArray((topology || {}).rules) ? topology.rules : [];
  const parts = [
    `matched ${esc(nvl(counters.matched_nodes, "-"))}`,
    `trusted matter ${esc(nvl(counters.trusted_matter_addresses, "-"))}`,
    `warnings ${esc(nvl(counters.warnings, "-"))}`,
    `tree ${esc(nvl(counters.tree_parents, "-"))}/${esc(nvl(counters.tree_relations, "-"))}`,
  ];
  el.innerHTML = `
    <div class="topology-meta">
      ${parts.map((part) => `<span>${part}</span>`).join("")}
      ${rules.map((rule) => `<span>rule: ${esc(rule)}</span>`).join("")}
    </div>
  `;
}
function setOtDiag(diag, status) {
  const settings = (diag && diag.settings) || {};
  const tables = (diag && diag.tables) || {};
  const signal = tables.neighbor_signal || {};
  const dongle = (diag && diag.dongle) || {};
  const transport = dongle.transport || {};
  const usbGuess = dongle.usb_guess || {};
  const dongleName = [usbGuess.manufacturer, usbGuess.product].filter(Boolean).join(" ").trim();
  const usbId = usbGuess.vendor_id && usbGuess.product_id ? `${usbGuess.vendor_id}:${usbGuess.product_id}` : "-";
  const isNetworkRcp = transport.kind === "network_rcp_bridge";
  setText("otNetworkName", nvl(settings.network_name, "-"));
  setText("otRole", nvl(settings.state, "-"));
  setText("otChannel", nvl(settings.channel, "-"));
  setText("otPanId", nvl(settings.panid, "-"));
  setText("otExtAddr", nvl(settings.extaddr, "-"));
  setText("otRloc16", settings.rloc16 ? `0x${settings.rloc16}` : "-");
  setText("otNeighbors", String(nvl(signal.node_count, 0)));
  setText("otBest", signal.best_dbm == null ? "-" : String(signal.best_dbm) + " dBm");
  setText("otWorst", signal.worst_dbm == null ? "-" : String(signal.worst_dbm) + " dBm");
  setText("otDongleLabel", isNetworkRcp ? "RCP Transport" : "Dongle");
  setText("otUsbLabel", isNetworkRcp ? "RCP Endpoint" : "USB VID:PID");
  setText("otDongleName", isNetworkRcp ? nvl(transport.label, "WLAN RCP bridge") : (dongleName || "-"));
  setText("otUsbId", isNetworkRcp ? nvl(transport.endpoint, "-") : usbId);
  const container = diag && diag.container_status ? `container=${diag.container_status}` : "";
  setOtStatus(status, container);
  const containerStatus = (diag && diag.container_status) || "";
  const level = status === "ok" ? "ok" : (containerStatus === "running" ? "warn" : "err");
  const label = status === "ok" ? "up" : (containerStatus === "running" ? "degraded" : "down");
  const role = settings.state ? `role=${settings.state}` : "role=-";
  setStackService("otbrServiceStatus", "otbrServiceSummary", level, label, `${container || "container=unknown"} | ${role}`);
}
async function refreshMatterServerService() {
  try {
    const res = await fetch("./control/matter-server/health", { cache: "no-store" });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    const state = (data && data.matter_server) || {};
    const running = !!state.all_running;
    setStackService(
      "matterServerStatus",
      "matterServerSummary",
      running ? "ok" : "warn",
      running ? "up" : "degraded",
      `Running: ${yn(running)} | ${summarizeContainerServices(state)}`
    );
  } catch (err) {
    setStackService("matterServerStatus", "matterServerSummary", "err", "down", String(err));
  }
}
async function invokeControlAction(path, button, busyText, idleText, onSuccess) {
  if (!button) return;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = busyText;
  try {
    const res = await fetch(path, { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || ("HTTP " + res.status));
    button.textContent = "Done";
    if (typeof onSuccess === "function") onSuccess(data);
    setTimeout(() => {
      button.textContent = idleText;
      button.disabled = false;
    }, 1200);
  } catch (err) {
    button.textContent = "Failed";
    setText("lastError", String(err) + ` | build=${UI_BUILD_ID}`);
    setTimeout(() => {
      button.textContent = idleText || previous || "Retry";
      button.disabled = false;
    }, 1800);
  }
}
async function submitWifiCredentials(form) {
  const button = document.getElementById("setWifiCredentials");
  const status = document.getElementById("wifiCredentialsStatus");
  const ssidInput = document.getElementById("matterWifiSsid");
  const passwordInput = document.getElementById("matterWifiPassword");
  if (!form || !button || !ssidInput || !passwordInput) return;
  const ssid = String(ssidInput.value || "").trim();
  const credentials = String(passwordInput.value || "");
  if (!ssid || !credentials) {
    if (status) status.textContent = "SSID and password are required";
    return;
  }
  button.disabled = true;
  button.textContent = "Setting...";
  if (status) status.textContent = "Sending credentials to matter-server";
  try {
    const res = await fetch("./control/matter-server/wifi-credentials", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ssid, credentials }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || ("HTTP " + res.status));
    passwordInput.value = "";
    if (status) status.textContent = `Wi-Fi credentials saved for ${data.ssid || ssid}`;
    button.textContent = "Saved";
    setTimeout(() => {
      button.textContent = "Set Wi-Fi";
      button.disabled = false;
    }, 1400);
  } catch (err) {
    if (status) status.textContent = String(err);
    setText("lastError", String(err) + ` | build=${UI_BUILD_ID}`);
    button.textContent = "Failed";
    setTimeout(() => {
      button.textContent = "Set Wi-Fi";
      button.disabled = false;
    }, 1800);
  }
}
async function invokeMatterCommand(button) {
  if (!button) return;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "...";
  try {
    const nodeId = button.dataset.nodeId;
    const res = await fetch(`./nodes/${encodeURIComponent(nodeId)}/commands`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint_id: Number(button.dataset.endpointId),
        cluster_id: Number(button.dataset.clusterId),
        command_name: button.dataset.commandName,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || ("HTTP " + res.status));
    button.textContent = "OK";
    setTimeout(() => {
      button.textContent = previous;
      button.disabled = false;
    }, 900);
    setTimeout(refresh, 350);
  } catch (err) {
    button.textContent = "Failed";
    setText("lastError", String(err) + ` | build=${UI_BUILD_ID}`);
    setTimeout(() => {
      button.textContent = previous || "Retry";
      button.disabled = false;
    }, 1600);
  }
}
async function invokeMatterAirReboot(button) {
  if (!button) return;
  const previous = button.textContent;
  button.disabled = true;
  button.textContent = "Rebooting...";
  try {
    const nodeId = button.dataset.nodeId;
    const res = await fetch(`./nodes/${encodeURIComponent(nodeId)}/air-reboot`, { method: "POST" });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || ("HTTP " + res.status));
    button.textContent = "Sent";
    setTimeout(() => {
      button.textContent = previous;
      button.disabled = false;
    }, 1800);
    setTimeout(refresh, 3000);
  } catch (err) {
    button.textContent = "Failed";
    setText("lastError", String(err) + ` | build=${UI_BUILD_ID}`);
    setTimeout(() => {
      button.textContent = previous || "Retry";
      button.disabled = false;
    }, 1800);
  }
}
async function refresh() {
  if (refreshInFlight) return;
  refreshInFlight = true;
  try {
    try {
      const res = await fetch("./health", { cache: "no-store" });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const d = await res.json();
      setStatus(d.status);
      setText("connected", yn(!!d.connected));
      setText("influxReady", yn(!!d.influx_ready));
      setText("eventsReceived", String(nvl(d.events_received, "-")));
      setText("eventsWritten", String(nvl(d.events_written, "-")));
      setText("writeErrors", String(nvl(d.write_errors, "-")));
      setText("parseErrors", String(nvl(d.parse_errors, "-")));
      setText("reconnects", String(nvl(d.reconnects, "-")));
      setText("lastEventType", String(nvl(d.last_event_type, "-")));
      setText("lastMessageAge", d.last_message_age_sec == null ? "-" : String(d.last_message_age_sec) + " s");
      setText("wsUrl", String(nvl(d.ws_url, "-")));
      setText("influx", String(nvl(d.influx_url, "-")) + " | org=" + String(nvl(d.influx_org, "-")) + " | bucket=" + String(nvl(d.influx_bucket, "-")));
      setText("lastError", String(d.last_error || "-") + ` | build=${UI_BUILD_ID}`);
      setText("rawJson", JSON.stringify(d, null, 2));
      setStackService(
        "matterConsoleStatus",
        "matterConsoleSummary",
        d.status === "ok" ? "ok" : (d.status === "degraded" ? "warn" : "err"),
        d.status === "ok" ? "up" : (d.status || "down"),
        `Connected: ${yn(!!d.connected)} | Influx ready: ${yn(!!d.influx_ready)} | Received: ${nvl(d.events_received, "-")} | Written: ${nvl(d.events_written, "-")}`
      );
    } catch (err) {
      setStatus("down");
      setText("lastError", String(err) + ` | build=${UI_BUILD_ID}`);
      setText("rawJson", JSON.stringify({ error: String(err), build: UI_BUILD_ID }, null, 2));
      setStackService("matterConsoleStatus", "matterConsoleSummary", "err", "down", String(err));
    }
    await refreshMatterServerService();
    let otData = null;
    try {
      try {
        const otRes = await fetch("./openthread/diag", { cache: "no-store" });
        if (!otRes.ok) throw new Error("HTTP " + otRes.status);
        otData = await otRes.json();
      } catch (_localErr) {}
      setOtDiag((otData && otData.diag) || {}, (otData && otData.status) || "degraded");
    } catch (err) {
      setOtDiag({}, "degraded");
      setOtStatus("degraded", "diag unavailable");
    }
    try {
      const topologyRes = await fetch("./thread-topology", { cache: "no-store" });
      if (!topologyRes.ok) throw new Error("HTTP " + topologyRes.status);
      const topologyData = await topologyRes.json();
      const topology = topologyData.topology || {};
      const matterSnapshotPending = !!topologyData.matter_snapshot_pending;
      let apStatus = {};
      try {
        const apRes = await fetch("/ap/api/status", { cache: "no-store" });
        if (apRes.ok) apStatus = await apRes.json();
      } catch (_apErr) {}
      safeRender("wifi-master", () => renderWifiMaster(apStatus));
      safeRender("wifi-nodes", () => renderWifiMatterDevices(topology.matter_inventory || [], apStatus, matterSnapshotPending));
      safeRender("topology-summary", () => renderTopologySummary(topology));
      safeRender("thread-master", () => renderThreadMaster(topology, (otData && otData.diag) || {}));
      safeRender("topology-tree", () => renderTopologyTree(topology.tree || {}));
      safeRender("topology-nodes", () => renderKnownMatterDevices(topology.matter_inventory || [], topology.tree || {}, matterSnapshotPending));
      safeRender("topology-warnings", () => renderTopologyWarnings(matterSnapshotPending ? [] : (topology.warnings || [])));
    } catch (_err) {
      safeRender("wifi-master-empty", () => renderWifiMaster({}));
      safeRender("wifi-nodes-empty", () => renderWifiMatterDevices([], {}, false));
      safeRender("topology-summary-empty", () => renderTopologySummary({}));
      safeRender("thread-master-empty", () => renderThreadMaster({}, {}));
      safeRender("topology-tree-empty", () => renderTopologyTree({}));
      safeRender("topology-nodes-empty", () => renderKnownMatterDevices([], {}, false));
      safeRender("topology-warnings-empty", () => renderTopologyWarnings([]));
    }
  } finally {
    refreshInFlight = false;
  }
}
window.addEventListener("error", (event) => {
  reportUiError("window-error", event.error || event.message || "unknown");
});
window.addEventListener("unhandledrejection", (event) => {
  reportUiError("promise-rejection", event.reason || "unknown");
});
const rawEl = document.getElementById("rawJson");
const toggleBtn = document.getElementById("toggleRaw");
const copyBtn = document.getElementById("copyRaw");
if (toggleBtn && rawEl) {
  toggleBtn.addEventListener("click", () => {
    rawEl.classList.toggle("hidden");
    toggleBtn.textContent = rawEl.classList.contains("hidden") ? "Show JSON" : "Hide JSON";
  });
}
if (copyBtn && rawEl) {
  copyBtn.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(rawEl.textContent || "");
      copyBtn.textContent = "Copied";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1200);
    } catch (_err) {
      copyBtn.textContent = "No clipboard";
      setTimeout(() => { copyBtn.textContent = "Copy"; }, 1500);
    }
  });
}
const msLink = document.getElementById("matterServerLink");
if (msLink) msLink.href = `http://${location.hostname}:5580/`;
const restartMatterServerBtn = document.getElementById("restartMatterServer");
if (restartMatterServerBtn) {
  restartMatterServerBtn.addEventListener("click", async () => {
    await invokeControlAction("./control/matter-server/restart", restartMatterServerBtn, "Restarting...", "Restart Matter Server");
  });
}
const restartOtbrBtn = document.getElementById("restartOtbr");
if (restartOtbrBtn) {
  restartOtbrBtn.addEventListener("click", async () => {
    await invokeControlAction("./control/openthread/restart", restartOtbrBtn, "Restarting...", "Restart OTBR", () => {
      setOtStatus("degraded", "restart requested");
    });
  });
}
const wifiForm = document.getElementById("wifiCredentialsForm");
if (wifiForm) {
  wifiForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await submitWifiCredentials(wifiForm);
  });
}
document.addEventListener("click", async (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const rebootButton = target.closest(".matter-air-reboot-btn");
  if (rebootButton instanceof HTMLButtonElement) {
    await invokeMatterAirReboot(rebootButton);
    return;
  }
  const button = target.closest(".matter-command-btn");
  if (!(button instanceof HTMLButtonElement)) return;
  await invokeMatterCommand(button);
});
refresh();
setInterval(refresh, REFRESH_INTERVAL_MS);
