let latestHealth = null;
let selectedDeviceId = null;
const STATIC_PAGE_TITLE = 'Pyrometers';
const STATIC_PAGE_SUBTITLE = 'Unified monitor for compatible thermoMETER CT and Optris CT devices.';

function fmtValue(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '--.- °C';
  return `${value.toFixed(2)} °C`;
}

function fmtBool(value) {
  return value ? 'yes' : 'no';
}

function fmtHz(value) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-';
  return value.toFixed(2);
}

function fmtFactor(value) {
  if (value === null || value === undefined || value === '') return '';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '';
  return parsed.toFixed(3);
}

function fmtTemperatureInput(value) {
  if (value === null || value === undefined || value === '') return '';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return '';
  return parsed.toFixed(1);
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function jsonFetch(url, options) {
  const res = await fetch(url, options);
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

function setStatusPill(status) {
  const el = document.getElementById('statusPill');
  const ok = status === 'ok';
  el.textContent = status || 'unknown';
  el.className = `pill ${ok ? 'ok' : 'warn'}`;
}

function pickDevice(health) {
  const devices = health && health.devices ? Object.values(health.devices) : [];
  if (!devices.length) return null;
  if (selectedDeviceId) {
    const found = devices.find((item) => item.id === selectedDeviceId);
    if (found) return found;
  }
  return devices.find((item) => item.connected) || devices.find((item) => item.port_present) || devices[0];
}

function setDeviceOptions(health) {
  const select = document.getElementById('deviceSelect');
  if (!select) return;
  const devices = health && health.devices ? Object.values(health.devices) : [];
  select.innerHTML = '';
  devices.forEach((device) => {
    const option = document.createElement('option');
    option.value = device.id;
    option.textContent = `${device.display_name} (${device.connected ? 'connected' : (device.port_present ? 'present' : 'missing')})`;
    select.appendChild(option);
  });
  const current = pickDevice(health);
  if (current) {
    selectedDeviceId = current.id;
    select.value = current.id;
  }
}

function renderDeviceSummary(health) {
  const target = document.getElementById('deviceSummary');
  const devices = health && health.devices ? Object.values(health.devices) : [];
  if (!devices.length) {
    target.innerHTML = '<div class="summary-pill">No configured pyrometer profiles.</div>';
    return;
  }
  target.innerHTML = devices.map((item) => {
    const state = item.connected ? 'connected' : (item.port_present ? 'present' : 'missing');
    return `
      <div class="summary-pill">
        <strong>${escapeHtml(item.display_name || item.id)}</strong>
        <span class="summary-pill-state">${escapeHtml(state)}</span>
      </div>
    `;
  }).join('');
}

function renderFrame(device) {
  const payload = device && device.last_measurement_payload;
  document.getElementById('frameOut').textContent = payload ? JSON.stringify(payload, null, 2) : '(waiting)';
}

function renderHealth(health) {
  latestHealth = health;
  setStatusPill(health.status);
  setDeviceOptions(health);
  renderDeviceSummary(health);
  const device = pickDevice(health);
  document.getElementById('pageTitle').textContent = STATIC_PAGE_TITLE;
  document.getElementById('pageSubtitle').textContent = STATIC_PAGE_SUBTITLE;
  if (!device) {
    document.getElementById('readingValue').textContent = '--.- °C';
    document.getElementById('headValue').textContent = '--.- °C';
    document.getElementById('boxValue').textContent = '--.- °C';
    document.getElementById('objectValue').textContent = '--.- °C';
    document.getElementById('emissivityInput').value = '';
    document.getElementById('transmissivityInput').value = '';
    document.getElementById('ambientFixedInput').value = '';
    document.getElementById('metaPort').textContent = '-';
    document.getElementById('metaConnection').textContent = 'No configured device profiles.';
    document.getElementById('metaInput').textContent = '-';
    document.getElementById('metaLogging').textContent = '-';
    document.getElementById('metaLastRead').textContent = '-';
    document.getElementById('metaFrame').textContent = '-';
    document.getElementById('summarySelectedDevice').textContent = '-';
    document.getElementById('summaryLoggingTarget').textContent = '-';
    document.getElementById('summaryInputRate').textContent = '-';
    document.getElementById('summaryConnectionState').textContent = 'No configured device profiles';
    document.getElementById('signalProtocol').textContent = '-';
    document.getElementById('signalFrameState').textContent = 'No active device';
    document.getElementById('signalLastSample').textContent = '-';
    document.getElementById('frameStateText').textContent = 'No configured device profiles are available.';
    renderFrame(null);
    return;
  }
  document.getElementById('readingValue').textContent = fmtValue(device.object_temperature_c ?? device.last_measurement_c);
  document.getElementById('headValue').textContent = fmtValue(device.sensor_head_temperature_c);
  document.getElementById('boxValue').textContent = fmtValue(device.controller_box_temperature_c);
  document.getElementById('objectValue').textContent = fmtValue(device.object_temperature_c);
  const emissivityInput = document.getElementById('emissivityInput');
  const transmissivityInput = document.getElementById('transmissivityInput');
  const ambientSourceSelect = document.getElementById('ambientSourceSelect');
  const ambientFixedInput = document.getElementById('ambientFixedInput');
  if (emissivityInput && document.activeElement !== emissivityInput) {
    emissivityInput.value = fmtFactor(device.emissivity);
  }
  if (transmissivityInput && document.activeElement !== transmissivityInput) {
    transmissivityInput.value = fmtFactor(device.transmissivity);
  }
  if (ambientSourceSelect && document.activeElement !== ambientSourceSelect) {
    ambientSourceSelect.value = device.ambient_compensation_source === 'fixed' ? 'fixed' : 'internal';
  }
  if (ambientFixedInput && document.activeElement !== ambientFixedInput) {
    ambientFixedInput.value = fmtTemperatureInput(device.ambient_compensation_fixed_c);
  }
  const loggingTarget = Number(device.logging_target_hz ?? 10);
  const loggingLabel = loggingTarget > 0 ? `${fmtHz(loggingTarget)} Hz` : 'Full stream';
  const loggingSelect = document.getElementById('loggingHzSelect');
  if (loggingSelect && document.activeElement !== loggingSelect) {
    loggingSelect.value = String(loggingTarget);
  }
  document.getElementById('metaPort').textContent = device.port || '-';
  document.getElementById('metaConnection').textContent =
    `${fmtBool(device.port_present)} present, ${fmtBool(device.connected)} connected, ${device.protocol_mode || 'unknown'} protocol`;
  document.getElementById('metaInput').textContent =
    `${fmtHz(device.frames_per_sec)} fps input, ${fmtHz(device.effective_log_hz)} Hz avg over 10s`;
  document.getElementById('metaLogging').textContent =
    `${loggingLabel} target, ${fmtHz(device.effective_logged_hz)} Hz writing now, ${device.logged_samples_last_10s ?? '-'} samples in last 10s, ${device.logged_samples_total ?? '-'} total`;
  document.getElementById('metaLastRead').textContent = device.last_measurement_at || '-';
  document.getElementById('metaFrame').textContent =
    device.last_error && device.last_error !== '-'
      ? `Error: ${device.last_error}`
      : (device.last_binary_frame_hex || '-');
  document.getElementById('summarySelectedDevice').textContent = device.display_name || device.id || '-';
  document.getElementById('summaryLoggingTarget').textContent = loggingLabel;
  document.getElementById('summaryInputRate').textContent = `${fmtHz(device.frames_per_sec)} fps input`;
  document.getElementById('summaryConnectionState').textContent =
    device.connected ? 'Connected' : (device.port_present ? 'Port present' : 'Missing');
  document.getElementById('signalProtocol').textContent =
    `${device.protocol_mode || 'unknown'} via ${device.port || 'unset port'}`;
  document.getElementById('signalFrameState').textContent =
    device.last_error && device.last_error !== '-'
      ? 'Frame error present'
      : (device.connected ? 'Frames flowing' : (device.port_present ? 'Waiting for connection' : 'Device path missing'));
  document.getElementById('signalLastSample').textContent = device.last_measurement_at || 'No sample yet';
  document.getElementById('frameStateText').textContent =
    device.last_error && device.last_error !== '-'
      ? `Latest frame reported an error: ${device.last_error}`
      : (device.connected
          ? `Latest binary frame is being decoded from ${device.display_name || device.id}.`
          : (device.port_present
              ? 'Device path is present, but the pyrometer is not yet connected.'
              : 'Configured device path is missing on the host.'));
  renderFrame(device);
}

async function refreshAll() {
  const data = await jsonFetch('health');
  renderHealth(data);
}

async function applyLoggingRate() {
  const select = document.getElementById('loggingHzSelect');
  const button = document.getElementById('applyLoggingBtn');
  if (!select || !button) return;
  button.disabled = true;
  try {
    await jsonFetch('api/logging', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hz: select.value }),
    });
    await refreshAll();
  } finally {
    button.disabled = false;
  }
}

async function applyIrSettings() {
  const emissivityInput = document.getElementById('emissivityInput');
  const transmissivityInput = document.getElementById('transmissivityInput');
  const ambientSourceSelect = document.getElementById('ambientSourceSelect');
  const ambientFixedInput = document.getElementById('ambientFixedInput');
  const button = document.getElementById('applyIrSettingsBtn');
  const device = pickDevice(latestHealth);
  if (!emissivityInput || !transmissivityInput || !ambientSourceSelect || !ambientFixedInput || !button || !device) return;
  const payload = {};
  if (emissivityInput.value !== '') payload.emissivity = emissivityInput.value;
  if (transmissivityInput.value !== '') payload.transmissivity = transmissivityInput.value;
  payload.ambient_source = ambientSourceSelect.value || 'internal';
  if (ambientSourceSelect.value === 'fixed') {
    payload.ambient_fixed_c = ambientFixedInput.value;
  }
  button.disabled = true;
  try {
    await jsonFetch(`api/device/${encodeURIComponent(device.id)}/ir-settings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await refreshAll();
  } finally {
    button.disabled = false;
  }
}

async function readIrSettings() {
  const emissivityInput = document.getElementById('emissivityInput');
  const transmissivityInput = document.getElementById('transmissivityInput');
  const ambientSourceSelect = document.getElementById('ambientSourceSelect');
  const ambientFixedInput = document.getElementById('ambientFixedInput');
  const button = document.getElementById('readIrSettingsBtn');
  const device = pickDevice(latestHealth);
  if (!emissivityInput || !transmissivityInput || !ambientSourceSelect || !ambientFixedInput || !button || !device) return;
  button.disabled = true;
  try {
    const data = await jsonFetch(`api/device/${encodeURIComponent(device.id)}/ir-settings`);
    if (data.emissivity !== null && data.emissivity !== undefined) {
      emissivityInput.value = fmtFactor(data.emissivity);
    }
    if (data.transmissivity !== null && data.transmissivity !== undefined) {
      transmissivityInput.value = fmtFactor(data.transmissivity);
    }
    ambientSourceSelect.value = data.ambient_compensation_source === 'fixed' ? 'fixed' : 'internal';
    if (data.ambient_compensation_fixed_c !== null && data.ambient_compensation_fixed_c !== undefined) {
      ambientFixedInput.value = fmtTemperatureInput(data.ambient_compensation_fixed_c);
    }
    await refreshAll();
  } finally {
    button.disabled = false;
  }
}

refreshAll().catch((err) => {
  document.getElementById('frameOut').textContent = String(err);
});

document.getElementById('deviceSelect').addEventListener('change', (event) => {
  selectedDeviceId = event.target.value || null;
  if (latestHealth) renderHealth(latestHealth);
});

document.getElementById('applyLoggingBtn').addEventListener('click', () => {
  applyLoggingRate().catch((err) => {
    document.getElementById('frameOut').textContent = String(err);
  });
});

document.getElementById('applyIrSettingsBtn').addEventListener('click', () => {
  applyIrSettings().catch((err) => {
    document.getElementById('frameOut').textContent = String(err);
  });
});

document.getElementById('readIrSettingsBtn').addEventListener('click', () => {
  readIrSettings().catch((err) => {
    document.getElementById('frameOut').textContent = String(err);
  });
});

setInterval(() => {
  refreshAll().catch(() => {});
}, 3000);
