'use strict';

const $ = (id) => document.getElementById(id);
let lastStatus = null;
let formLoaded = false;
let lastOperationFinished = null;
const openDetails = new Set();
// Full spectra fetched per sensor address, refreshed when a new capture appears.
const spectra = new Map();
let spectrumUnit = 'g';
// View per sensor: spectrum, waveform or orbit; waveform data is fetched only when shown.
const spectrumViews = new Map();
const waveformsCache = new Map();
let waveQuantity = 'displacement_um';
const WAVE_QUANTITIES = { acc_g: 'acceleration, g', velocity_mm_s: 'velocity, mm/s', displacement_um: 'displacement, µm' };
const scaleModes = new Map();
const compareLimits = new Map();
// Display ranges only. These values are not alarm thresholds.
const COMPARE_MINIMUMS = {
  spectrum: { acc_g: 0.01, velocity_mm_s: 1 },
  waveform: { acc_g: 0.02, velocity_mm_s: 1, displacement_um: 50 },
  orbit: { displacement_um: 50 },
};
const FFT_AVERAGE_CHOICES = [1, 2, 4, 8, 16];
// Interval typed but not yet applied, in seconds; survives the 1 s re-render.
let pendingIntervalSec = null;
// The table re-renders every second; a re-render between press and release would swallow the click.
let pointerDown = false;

function duration(seconds) {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')} min`;
  return `${Math.floor(s / 3600)} h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')} min`;
}

function hex(address) {
  return '0x' + Number(address).toString(16).toUpperCase().padStart(2, '0');
}

function fmt(value, digits) {
  return Number.isFinite(value) ? value.toFixed(digits) : '-';
}

function triple(values, prefix, suffix, digits) {
  return ['x', 'y', 'z'].map((axis) => fmt(values[`${prefix}_${axis}${suffix}`], digits)).join(' / ');
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function setMsg(id, text, kind) {
  const el = $(id);
  el.textContent = text || '';
  el.className = `msg${kind ? ' ' + kind : ''}`;
}

async function api(method, url, body) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* empty body */ }
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function parseAddress(text) {
  const value = String(text).trim().toLowerCase();
  const number = value.startsWith('0x') ? parseInt(value, 16) : parseInt(value, 10);
  if (!Number.isInteger(number) || number < 1 || number > 127) throw new Error('Address must be 1..127 (0x01..0x7F)');
  return number;
}

function fillSelect(select, items, current) {
  select.innerHTML = items.map((item) => {
    const value = typeof item === 'object' ? item.value : item;
    const label = typeof item === 'object' ? item.label : item;
    return `<option value="${escapeHtml(value)}"${String(value) === String(current) ? ' selected' : ''}>${escapeHtml(label)}</option>`;
  }).join('');
}

async function loadPorts(current) {
  const data = await api('GET', 'api/ports');
  const items = [{ value: '', label: '— not selected —' }].concat(
    data.ports.map((p) => ({ value: p.path, label: p.path === p.device ? p.path : `${p.path} → ${p.device}` })));
  if (current && !data.ports.some((p) => p.path === current)) items.push({ value: current, label: `${current} (not present)` });
  fillSelect($('port'), items, current);
}

function valuesDetails(address, values, fields) {
  const allFields = Object.values(fields).flat();
  const valueGroups = [
    { key: 'live', label: 'Live values' },
    { key: 'acceleration', label: 'Acceleration statistics' },
    { key: 'vibration', label: 'Vibration statistics' },
    { key: 'status', label: 'Sensor status' },
    { key: 'other', label: 'Other values' },
  ];
  const labels = {
    acc_g: 'Acceleration',
    velocity_mm_s: 'Vibration velocity',
    displacement_um: 'Vibration displacement',
    frequency_hz: 'Dominant frequency',
    energy_total: 'Total spectrum energy',
    crest_factor: 'Crest factor',
    kurtosis: 'Kurtosis',
    acc_abs_mean_g: 'Mean acceleration',
    acc_variance: 'Acceleration variance',
    acc_rms_g: 'RMS acceleration',
    waveform_index: 'Waveform factor',
    peak_index: 'Peak factor',
    pulse_index: 'Impulse factor',
    skewness: 'Skewness',
    velocity_rms_mm_s: 'RMS vibration velocity',
    velocity_kurtosis: 'Velocity kurtosis',
    displacement_rms_mm: 'RMS displacement',
    fault: 'Sensor fault code',
    temperature_c: 'Sensor temperature',
  };
  const fieldAxis = (name) => {
    const match = name.match(/(?:^|_)([xyz])(?:_|$)/);
    return match ? match[1] : null;
  };
  const baseName = (name, axis) => axis
    ? name.split('_').filter((part) => part !== axis).join('_')
    : name;
  const friendlyLabel = (name) => {
    const point = name.match(/^energy_point(\d+)$/);
    if (point) return `Spectrum point ${point[1]}`;
    const band = name.match(/^energy_band(\d+)$/);
    if (band) return `Frequency band ${band[1]}`;
    return labels[name] || name.replaceAll('_', ' ');
  };
  const valueGroup = (name) => {
    if (['acc_g', 'velocity_mm_s', 'displacement_um', 'frequency_hz'].includes(name)) return 'live';
    if (name.startsWith('energy_')) return 'spectrum';
    if (['crest_factor', 'kurtosis', 'acc_abs_mean_g', 'acc_variance', 'acc_rms_g', 'waveform_index', 'peak_index', 'pulse_index', 'skewness'].includes(name)) return 'acceleration';
    if (['velocity_rms_mm_s', 'velocity_kurtosis', 'displacement_rms_mm'].includes(name)) return 'vibration';
    if (name === 'fault') return 'status';
    return 'other';
  };
  const row = (field, axis) => {
    const label = friendlyLabel(baseName(field.name, axis));
    const unit = field.unit ? ` <span>${escapeHtml(field.unit)}</span>` : '';
    return `<div class="axis-value"><span class="metric">${escapeHtml(label)}</span><span class="reading">${escapeHtml(values[field.name])}${unit}</span></div>`;
  };
  const axes = ['x', 'y', 'z'].map((axis) => {
    const axisFields = allFields.filter((field) => field.name in values && fieldAxis(field.name) === axis);
    const rows = valueGroups.map((group) => {
      const groupRows = axisFields.filter((field) => valueGroup(baseName(field.name, axis)) === group.key)
        .map((field) => row(field, axis)).join('');
      return groupRows ? `<div class="value-group"><h5>${group.label}</h5>${groupRows}</div>` : '';
    }).join('');
    return `<section class="axis-values"><h4>Axis ${axis.toUpperCase()}</h4>${rows}</section>`;
  }).join('');
  const common = allFields.filter((field) => field.name in values && fieldAxis(field.name) === null)
    .map((field) => row(field, null)).join('');
  const groups = axes || common;
  const open = openDetails.has(address) ? ' open' : '';
  return groups ? `<details class="values" data-addr="${address}"${open}><summary>All values</summary>
    ${common ? `<div class="common-values">${common}</div>` : ''}
    <div class="axis-values-wrap"><div class="axis-values-grid">${axes}</div></div></details>` : '';
}

function sensorEnergyPanel(sensor, values, harmonics) {
  const address = sensor.address;
  if (!harmonics || !Number.isFinite(values.energy_x_point3)) return '';
  const axes = ['x', 'y', 'z'].map((axis) => {
    const frequency = values[`frequency_${axis}_hz`];
    const points = harmonics.points.map((_, i) => ({ label: `Point ${i + 1}`, value: values[`energy_${axis}_point${i + 1}`] }));
    const pointMax = Math.max(1, ...points.map((p) => p.value || 0));
    const pointBars = points.map((p) => `<div class="hbar"><span class="hlabel">${escapeHtml(p.label)}</span>
      <div class="track"><div class="fill" style="width:${Math.round(100 * (p.value || 0) / pointMax)}%"></div></div>
      <span>${p.value ?? '-'}</span></div>`).join('');
    const bands = harmonics.bands.map((_, i) => ({ label: `Band ${i + 1}`, value: values[`energy_${axis}_band${i + 1}`] }));
    const bandMax = Math.max(1, ...bands.map((band) => band.value || 0));
    const bandBars = bands.map((band) => `<div class="hbar band"><span class="hlabel">${escapeHtml(band.label)}</span>
      <div class="track"><div class="fill" style="width:${Math.round(100 * (band.value || 0) / bandMax)}%"></div></div>
      <span>${band.value ?? '-'}</span></div>`).join('');
    return `<div><h4>Axis ${axis.toUpperCase()} · total ${values[`energy_${axis}_total`] ?? '-'}</h4>
      <div class="detected-frequency">Detected dominant frequency: <strong>${Number.isFinite(frequency) && frequency > 0 ? fmt(frequency, 2) + ' Hz' : '—'}</strong></div>
      <section class="harmonic-group"><h5>Focus points</h5>${pointBars}
      </section>
      <section class="harmonic-group"><h5>Focus bands</h5>${bandBars}</section>
    </div>`;
  }).join('');
  const key = `h${address}`;
  const open = openDetails.has(key) ? ' open' : '';
  return `<details class="values" data-addr="${key}"${open}><summary>Sensor spectrum energy</summary><div class="harmonics">${axes}</div></details>`;
}

async function loadSpectrum(address, capturedAt) {
  const cached = spectra.get(address);
  if (cached && (cached.loading || cached.captured_at === capturedAt)) return;
  spectra.set(address, { ...(cached || {}), loading: true });
  try {
    const data = await api('GET', `api/sensors/${address}/spectrum`);
    spectra.set(address, data);
  } catch (_) {
    spectra.delete(address);
  }
  if (lastStatus) renderSensors(lastStatus);
}

function scaleLimit(address, view, quantity, peak) {
  const required = Math.max(1e-9, Number.isFinite(peak) ? peak * 1.05 : 0);
  if (scaleModes.get(address) === 'auto') return required;
  const key = `${address}:${view}:${quantity}`;
  let limit = compareLimits.get(key) ?? COMPARE_MINIMUMS[view][quantity];
  while (limit < required) limit *= 2;
  compareLimits.set(key, limit);
  return limit;
}

function scaleControls(address, view, quantity) {
  const compare = scaleModes.get(address) !== 'auto';
  return `<span class="unit scale-controls" role="group" aria-label="Chart scale">
    <button type="button" data-scale="compare" data-addr="${address}" class="${compare ? 'on' : ''}" aria-pressed="${compare}" title="Keep the scale between captures">Compare</button>
    <button type="button" data-scale="auto" data-addr="${address}" class="${compare ? '' : 'on'}" aria-pressed="${!compare}" title="Fit the current capture">Auto</button>
    <button type="button" data-scale-reset="${address}:${view}:${quantity}" title="Reset the scale for this chart and unit" ${compare ? '' : 'disabled'}>Reset scale</button>
  </span>`;
}

function spectrumChart(address, spectrum) {
  const width = 1000;
  const height = 260;
  const pad = { left: 52, right: 12, top: 12, bottom: 28 };
  const key = spectrumUnit === 'g' ? 'acc_g' : 'velocity_mm_s';
  const minHz = spectrumUnit === 'g' ? 0 : 2;
  const maxHz = spectrum.nyquist_hz;
  const series = ['x', 'y', 'z'].map((axis) => spectrum.axes[axis][key]
    .map((value, k) => [k * spectrum.df_hz, value]).filter(([f]) => f >= minHz && f <= maxHz));
  const top = scaleLimit(address, 'spectrum', key, Math.max(...series.flat().map(([, v]) => v)));
  const sx = (f) => pad.left + (f / maxHz) * (width - pad.left - pad.right);
  const sy = (v) => height - pad.bottom - (v / top) * (height - pad.top - pad.bottom);
  const gridX = [];
  for (let f = 0; f <= maxHz; f += maxHz > 100 ? 20 : 10) {
    gridX.push(`<line class="grid" x1="${sx(f)}" x2="${sx(f)}" y1="${pad.top}" y2="${height - pad.bottom}"/><text x="${sx(f)}" y="${height - 10}" text-anchor="middle">${f}</text>`);
  }
  const gridY = [0, 0.25, 0.5, 0.75, 1].map((p) => `<line class="grid" x1="${pad.left}" x2="${width - pad.right}" y1="${sy(top * p)}" y2="${sy(top * p)}"/>
    <text x="${pad.left - 6}" y="${sy(top * p) + 4}" text-anchor="end">${(top * p).toPrecision(2)}</text>`);
  const lines = series.map((points, i) => `<polyline class="axis-${'xyz'[i]}" points="${points.map(([f, v]) => `${sx(f).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}"/>`);
  return `<svg class="fft-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Amplitude spectrum">
    ${gridX.join('')}${gridY.join('')}${lines.join('')}
    <text x="${width - pad.right}" y="${height - 10}" text-anchor="end">Hz</text></svg>`;
}

async function loadWaveform(address, capturedAt) {
  const cached = waveformsCache.get(address);
  if (cached && (cached.loading || cached.captured_at === capturedAt)) return;
  waveformsCache.set(address, { ...(cached || {}), loading: true });
  try {
    waveformsCache.set(address, await api('GET', `api/sensors/${address}/waveform`));
  } catch (_) {
    waveformsCache.delete(address);
  }
  if (lastStatus) renderSensors(lastStatus);
}

function waveformChart(address, wave) {
  const width = 1000;
  const height = 260;
  const pad = { left: 56, right: 12, top: 12, bottom: 28 };
  const series = ['x', 'y', 'z'].map((axis) => wave.axes[axis][waveQuantity]);
  const top = scaleLimit(address, 'waveform', waveQuantity, Math.max(...series.flat().map(Math.abs)));
  const duration = wave.samples / wave.sample_rate_hz;
  const sx = (i) => pad.left + (i / (wave.samples - 1)) * (width - pad.left - pad.right);
  const sy = (v) => pad.top + (1 - (v + top) / (2 * top)) * (height - pad.top - pad.bottom);
  const gridX = [];
  for (let t = 0; t <= duration; t += 0.5) {
    const x = pad.left + (t / duration) * (width - pad.left - pad.right);
    gridX.push(`<line class="grid" x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}"/><text x="${x}" y="${height - 10}" text-anchor="middle">${t.toFixed(1)}</text>`);
  }
  const gridY = [-1, -0.5, 0, 0.5, 1].map((p) => `<line class="grid" x1="${pad.left}" x2="${width - pad.right}" y1="${sy(top * p)}" y2="${sy(top * p)}"/>
    <text x="${pad.left - 6}" y="${sy(top * p) + 4}" text-anchor="end">${(top * p).toPrecision(2)}</text>`);
  const lines = series.map((values, i) => `<polyline class="axis-${'xyz'[i]}" points="${values.map((v, k) => `${sx(k).toFixed(1)},${sy(v).toFixed(1)}`).join(' ')}"/>`);
  const buttons = Object.entries(WAVE_QUANTITIES).map(([key, label]) =>
    `<button data-wave="${key}" class="${waveQuantity === key ? 'on' : ''}">${label.split(',')[0]}</button>`).join('');
  return `<div class="fft-head"><span>Time signal of the last capture · velocity and displacement integrated above <strong>${fmt(wave.integration_min_hz, 1)} Hz</strong></span>
      ${scaleControls(address, 'waveform', waveQuantity)}<span class="unit">${buttons}</span></div>
    <svg class="fft-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img" aria-label="Time waveform">
      ${gridX.join('')}${gridY.join('')}${lines.join('')}<text x="${width - pad.right}" y="${height - 10}" text-anchor="end">s</text></svg>
    <div class="fft-legend"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z</span><span>${WAVE_QUANTITIES[waveQuantity]}</span></div>
    <p class="note">Look for short spikes (impacts: bearings), beating (two close frequencies) and clipped or asymmetric shapes (looseness, rub).</p>`;
}

function orbitCharts(address, wave) {
  const size = 240;
  const pad = 24;
  const pairs = [['x', 'y'], ['x', 'z'], ['y', 'z']];
  const peak = Math.max(...Object.values(wave.axes).flatMap((axis) => axis.displacement_um.map(Math.abs)));
  const range = scaleLimit(address, 'orbit', 'displacement_um', peak);
  const plots = pairs.map(([a, b]) => {
    const pair = a + b;
    const overallA = wave.axes[a].displacement_um;
    const overallB = wave.axes[b].displacement_um;
    const s = (v) => pad + ((v + range) / (2 * range)) * (size - 2 * pad);
    const sv = (v) => size - s(v);
    const path = (xs, ys) => xs.map((v, k) => `${s(v).toFixed(1)},${sv(ys[k]).toFixed(1)}`).join(' ');
    const ratio = wave.orbits[pair]?.overall?.ratio;
    const info = `ratio <strong>${fmt(ratio, 2)}</strong>`;
    return `<figure class="orbit"><svg viewBox="0 0 ${size} ${size}" role="img" aria-label="Orbit ${pair}">
        <line class="grid" x1="${size / 2}" x2="${size / 2}" y1="${pad}" y2="${size - pad}"/><line class="grid" x1="${pad}" x2="${size - pad}" y1="${size / 2}" y2="${size / 2}"/>
        <polyline class="orbit-all" points="${path(overallA, overallB)}"/>
        <text x="${size - pad}" y="${size / 2 - 4}" text-anchor="end">${a.toUpperCase()}</text><text x="${size / 2 + 4}" y="${pad + 8}">${b.toUpperCase()}</text>
        <text x="${pad}" y="${size - 6}">±${range.toPrecision(2)} µm</text></svg>
      <figcaption><strong>${a.toUpperCase()}${b.toUpperCase()}</strong> · ${info}</figcaption></figure>`;
  }).join('');
  return `<div class="fft-head"><span>Broadband displacement paths of the last capture</span>${scaleControls(address, 'orbit', 'displacement_um')}</div>
    <div class="orbits">${plots}</div>
    <p class="note">Each path shows displacement in the plane of two axes. A ratio of 1 is circular; a ratio near 0 is nearly a straight line.</p>`;
}

function spectrumPanel(sensor, busy) {
  const brief = sensor.fft;
  if (brief && brief.captured_at) loadSpectrum(sensor.address, brief.captured_at);
  const spectrum = spectra.get(sensor.address);
  const key = `f${sensor.address}`;
  const open = openDetails.has(key) ? ' open' : '';
  const config = lastStatus.config;
  const choices = FFT_AVERAGE_CHOICES.map((n) => ({ value: n, label: n === 1 ? '1 capture (4.2 s)' : `${n} captures (~${Math.round(n * 4.5)} s)` }));
  const select = `<label>Average<select data-averages ${busy ? 'disabled' : ''}>${choices.map((c) =>
    `<option value="${c.value}"${c.value === config.fft_averages ? ' selected' : ''}>${c.label}</option>`).join('')}</select></label>`;
  const button = `${select}<button data-act="spectrum" data-addr="${sensor.address}" ${busy || !sensor.enabled ? 'disabled' : ''}>Capture spectrum now</button>`;
  const error = brief && brief.error ? `<div class="msg err">${escapeHtml(brief.error)}</div>` : '';
  const seconds = pendingIntervalSec ?? config.fft_interval_sec;
  const state = !config.fft_periodic
    ? '<strong>stopped</strong>'
    : `<strong class="run">running</strong> · every ${duration(config.fft_interval_sec)}` +
      (sensor.fft_next_in_sec == null ? ' · waiting for the sensor to be online' : ` · next in <strong>${duration(sensor.fft_next_in_sec)}</strong>`);
  const periodic = `<div class="fft-periodic"><strong>Periodic capture</strong>
    <label>Every, s<input data-fft-interval type="number" min="5" max="86400" step="1" value="${escapeHtml(seconds)}"></label>
    <button data-act="fft-start">${config.fft_periodic ? 'Apply' : 'Start'}</button>
    <button class="danger" data-act="fft-stop" ${config.fft_periodic ? '' : 'disabled'}>Stop</button>
    <span class="state">${state}</span>
    <span class="note" style="flex-basis:100%;margin:0">Applies to all enabled sensors, one after another, with the Average setting above. The interval counts from the end of a capture series; each capture pauses polling for about 4.5 s. Stop also ends a running series after the current capture.</span></div>`;
  let body = '<p class="note">No capture yet. A capture reads 1024 raw samples per axis (about 4.2 s) and pauses polling meanwhile.</p>';
  const view = spectrumViews.get(sensor.address) || 'spectrum';
  const tabs = `<span class="unit view-tabs">${[['spectrum', 'Spectrum'], ['waveform', 'Waveform'], ['orbit', 'Orbit']].map(([v, label]) =>
    `<button data-view="${v}" data-addr="${sensor.address}" class="${view === v ? 'on' : ''}">${label}</button>`).join('')}</span>`;
  if (spectrum && spectrum.axes && view !== 'spectrum') {
    loadWaveform(sensor.address, spectrum.captured_at);
    const wave = waveformsCache.get(sensor.address);
    const content = wave && wave.axes
      ? (view === 'waveform' ? waveformChart(sensor.address, wave) : orbitCharts(sensor.address, wave))
      : '<p class="note">Loading…</p>';
    body = `<div class="fft-head"><span>Captured <strong>${escapeHtml(new Date(spectrum.captured_at).toLocaleString())}</strong></span>${tabs}</div>${content}`;
  } else if (spectrum && spectrum.axes) {
    const unitLabel = spectrumUnit === 'g' ? 'g' : 'mm/s';
    const rate = `${fmt(spectrum.sample_rate_hz, 1)} Hz${spectrum.sample_rate_measured ? '' : ' (default)'}`;
    const peakRows = ['x', 'y', 'z'].map((axis) => {
      const a = spectrum.axes[axis];
      const peaks = a.peaks.map((p) => `${fmt(p.hz, 2)} Hz · ${p.g.toPrecision(3)} g`).join('<br>') || '—';
      const bands = Object.entries(a.bands_g).sort((p, q) => parseFloat(p[0]) - parseFloat(q[0])).map(([k, v]) => `${escapeHtml(k.replace('_', '–').replace('nyq', 'Nyq'))}: ${v.toPrecision(2)}`).join('<br>');
      return `<tr><td class="l"><span class="axis-${axis}">${axis.toUpperCase()}</span></td><td class="l">${peaks}</td><td>${fmt(a.velocity_rms_mm_s, 2)}</td><td class="l">${bands}</td></tr>`;
    }).join('');
    body = `<div class="fft-head">
        <span>Captured <strong>${escapeHtml(new Date(spectrum.captured_at).toLocaleString())}</strong>${spectrum.periodic ? ' (periodic)' : ''}</span>
        <span>Sample rate <strong>${rate}</strong></span>
        <span>Length <strong>${fmt(spectrum.duration_s, 2)} s</strong> · resolution <strong>${fmt(spectrum.df_hz, 3)} Hz</strong></span>
        <span>Averaged <strong>${spectrum.averages || 1} capture${(spectrum.averages || 1) > 1 ? 's' : ''}</strong></span>
        ${tabs}${scaleControls(sensor.address, 'spectrum', spectrumUnit === 'g' ? 'acc_g' : 'velocity_mm_s')}
        <span class="unit"><button data-unit="g" class="${spectrumUnit === 'g' ? 'on' : ''}">g</button><button data-unit="mm_s" class="${spectrumUnit === 'g' ? '' : 'on'}">mm/s</button></span>
      </div>
      ${spectrumChart(sensor.address, spectrum)}
      <div class="fft-legend"><span class="axis-x">X</span><span class="axis-y">Y</span><span class="axis-z">Z</span><span>amplitude, ${unitLabel}</span></div>
      <table class="fft-peaks"><thead><tr><th class="l">Axis</th><th class="l">Top peaks</th><th>Velocity RMS ≥${fmt(spectrum.velocity_min_hz || 10, 0)} Hz, mm/s</th><th class="l">Band RMS, g (Hz)</th></tr></thead><tbody>${peakRows}</tbody></table>`;
  }
  return `<details class="values" data-addr="${key}"${open}><summary>Spectrum (FFT)</summary>
    <div class="fft-head">${button}</div>${periodic}${error}${body}</details>`;
}

function renderSensors(status) {
  const busy = status.operation.running;
  const rows = status.sensors.map((s) => {
    const v = s.values || {};
    const dot = !s.enabled ? 'off' : (s.online ? 'on' : '');
    const lastError = s.last_error ? `<div class="note">${escapeHtml(s.last_error)}</div>` : '';
    const details = spectrumPanel(s, busy) + sensorEnergyPanel(s, v, status.harmonics) + valuesDetails(s.address, v, status.fields);
    // Expandable panels get their own full-width row so opening them never moves the values.
    const detailRow = details ? `<tr class="detail-row"><td colspan="8" class="l">${details}</td></tr>` : '';
    return `<tr class="sensor-row">
      <td class="l"><span class="dot ${dot}"></span><strong>${escapeHtml(s.name)}</strong>${s.enabled ? '' : ' (disabled)'}${lastError}</td>
      <td>${hex(s.address)}</td>
      <td>${triple(v, 'velocity', '_mm_s', 2)}</td>
      <td>${triple(v, 'displacement', '_um', 0)}</td>
      <td>${triple(v, 'frequency', '_hz', 1)}</td>
      <td>${fmt(v.temperature_c, 1)}</td>
      <td>${s.reads} / ${s.errors}</td>
      <td><div class="actions">
        <button data-act="toggle" data-addr="${s.address}" ${busy ? 'disabled' : ''}>${s.enabled ? 'Disable' : 'Enable'}</button>
        <button data-act="rename" data-addr="${s.address}" ${busy ? 'disabled' : ''}>Rename</button>
        <button data-act="readdress" data-addr="${s.address}" ${busy ? 'disabled' : ''}>Set address</button>
        <button class="danger" data-act="delete" data-addr="${s.address}" ${busy ? 'disabled' : ''}>Remove</button>
      </div></td></tr>${detailRow}`;
  });
  $('sensorRows').innerHTML = rows.length ? rows.join('') : '<tr><td colspan="8" class="l">No sensors registered. Scan the bus or add one by address.</td></tr>';
}

function renderOperation(op) {
  const busy = op.running;
  ['scanBtn', 'applyBaud', 'saveBus', 'addBtn'].forEach((id) => { $(id).disabled = busy; });
  if (busy) {
    const target = op.name === 'scan' ? 'scanMsg' : (op.name === 'fft' ? 'sensorMsg' : 'busMsg');
    setMsg(target, `${op.name} running${op.progress ? ` (${op.progress})` : ''}…`);
    return;
  }
  if (!op.finished_at || op.finished_at === lastOperationFinished) return;
  const firstView = lastOperationFinished === null && lastStatus === null;
  lastOperationFinished = op.finished_at;
  if (firstView) return;
  if (op.name === 'scan') {
    if (op.error) { setMsg('scanMsg', op.error, 'err'); return; }
    const found = (op.result && op.result.found) || [];
    setMsg('scanMsg', found.length ? `Found ${found.length} sensor(s).` : 'No sensors answered.', found.length ? 'ok' : 'err');
    $('scanFound').innerHTML = found.map((f) =>
      `<button data-found="${f.address}" data-baud="${f.baud}">Add ${hex(f.address)} @ ${f.baud}</button>`).join('');
  } else if (op.error) {
    setMsg(['set_address', 'fft'].includes(op.name) ? 'sensorMsg' : 'busMsg', op.error, 'err');
  } else if (op.name === 'fft') {
    const r = op.result;
    setMsg('sensorMsg', r.stopped ? `Series stopped after ${r.averages} capture(s) for ${hex(r.address)}.` : `Spectrum captured for ${hex(r.address)}.`, 'ok');
  } else if (op.name === 'set_address') {
    setMsg('sensorMsg', `Address changed to ${hex(op.result.new_address)}.`, 'ok');
  } else if (op.name === 'set_baud') {
    setMsg('busMsg', `Bus moved to ${op.result.baud} baud: ${JSON.stringify(op.result.results)}`, 'ok');
    formLoaded = false;
  }
}

function renderSummary(status) {
  const h = status.health;
  const kind = h.status === 'ok' ? 'status-ok' : (h.status === 'idle' ? 'status-warn' : 'status-err');
  $('sumStatus').innerHTML = `<span class="status-pill ${kind}">${escapeHtml(h.status)}</span>${h.last_error ? `<div class="note">${escapeHtml(h.last_error)}</div>` : ''}`;
  const simulation = h.simulation;
  const simText = !simulation ? '' : simulation.scenario === 'steady' ? ' · SIMULATION'
    : ` · SIMULATION: ${simulation.stage}${simulation.elapsed_s != null ? ` (${duration(simulation.elapsed_s)} of ${duration(simulation.length_s)})` : ''}`;
  $('sumPort').textContent = (h.port ? `${h.port} @ ${h.baud}${h.port_present ? '' : ' (missing)'}` : 'not selected') + simText;
  $('sumOnline').textContent = `${h.sensors_online} / ${h.sensors_enabled}`;
  $('sumCycle').textContent = h.cycle_ms == null ? '-' : `${h.cycle_ms} ms`;
  const interval = status.config.fft_interval_sec;
  const waits = status.sensors.map((s) => s.fft_next_in_sec).filter((v) => v !== null && v !== undefined);
  $('sumFft').textContent = !status.config.fft_periodic ? 'stopped'
    : `every ${duration(interval)} · ×${status.config.fft_averages}` + (waits.length ? ` · next in ${duration(Math.min(...waits))}` : ' · no sensor online');
  $('sumInflux').textContent = !h.influx_configured ? 'not configured' : (h.influx_error ? `error: ${h.influx_error}` : 'writing');
}

async function loadForm(status) {
  const c = status.config;
  fillSelect($('baud'), status.bauds, c.baud);
  fillSelect($('newBaud'), status.bauds, c.baud);
  $('currentBaud').value = `${c.baud} baud`;
  fillSelect($('profile'), status.read_profiles, c.read_profile);
  $('poll').value = c.poll_interval_sec;
  if (!$('scanBauds').children.length) {
    $('scanBauds').innerHTML = status.bauds.map((b) =>
      `<label><input type="checkbox" value="${b}"${b === c.baud ? ' checked' : ''}> ${b}</label>`).join('');
  }
  await loadPorts(c.port);
  formLoaded = true;
}

async function refresh() {
  try {
    const status = await api('GET', 'api/status');
    renderOperation(status.operation);
    lastStatus = status;
    renderSummary(status);
    if (!pointerDown && !document.activeElement.matches('select[data-averages], input[data-fft-interval]')) renderSensors(status);
    if (!formLoaded) await loadForm(status);
  } catch (error) {
    $('sumStatus').textContent = `unreachable: ${error.message}`;
  }
}

async function run(msgId, action) {
  try {
    const text = await action();
    setMsg(msgId, text || '', 'ok');
  } catch (error) {
    setMsg(msgId, error.message, 'err');
  }
  refresh();
}

$('addBtn').addEventListener('click', () => run('sensorMsg', async () => {
  const address = parseAddress($('addAddress').value);
  const name = $('addName').value.trim() || `wtvb01-${address.toString(16).padStart(2, '0')}`;
  await api('POST', 'api/sensors', { address, name, enabled: true });
  $('addAddress').value = '';
  $('addName').value = '';
  return `Added ${name} (${hex(address)}).`;
}));

$('sensorRows').addEventListener('click', (event) => {
  const viewButton = event.target.closest('button[data-view]');
  if (viewButton) {
    spectrumViews.set(Number(viewButton.dataset.addr), viewButton.dataset.view);
    if (lastStatus) renderSensors(lastStatus);
    return;
  }
  const waveButton = event.target.closest('button[data-wave]');
  if (waveButton) {
    waveQuantity = waveButton.dataset.wave;
    if (lastStatus) renderSensors(lastStatus);
    return;
  }
  const unit = event.target.closest('button[data-unit]');
  if (unit) {
    spectrumUnit = unit.dataset.unit;
    if (lastStatus) renderSensors(lastStatus);
    return;
  }
  const scaleButton = event.target.closest('button[data-scale]');
  if (scaleButton) {
    scaleModes.set(Number(scaleButton.dataset.addr), scaleButton.dataset.scale);
    if (lastStatus) renderSensors(lastStatus);
    return;
  }
  const resetScale = event.target.closest('button[data-scale-reset]');
  if (resetScale) {
    compareLimits.delete(resetScale.dataset.scaleReset);
    if (lastStatus) renderSensors(lastStatus);
    return;
  }
  const button = event.target.closest('button[data-act]');
  if (!button || !lastStatus) return;
  const address = Number(button.dataset.addr);
  const sensor = lastStatus.sensors.find((s) => s.address === address);
  const act = button.dataset.act;
  if (act === 'spectrum') {
    openDetails.add(`f${address}`);
    const averages = lastStatus.config.fft_averages || 1;
    run('sensorMsg', async () => {
      await api('POST', `api/sensors/${address}/spectrum`, { averages });
      return `Capturing spectrum (${averages} × ~4.5 s)…`;
    });
  } else if (act === 'fft-start') {
    const seconds = pendingIntervalSec ?? lastStatus.config.fft_interval_sec;
    run('sensorMsg', async () => {
      if (!Number.isFinite(seconds) || seconds < 5 || seconds > 86400) throw new Error('Interval must be 5..86400 s');
      await api('POST', 'api/config', { fft_periodic: true, fft_interval_sec: seconds });
      pendingIntervalSec = null;
      return `Periodic capture every ${duration(seconds)} started.`;
    });
  } else if (act === 'fft-stop') {
    run('sensorMsg', async () => {
      await api('POST', 'api/config', { fft_periodic: false });
      return 'Periodic capture stopped.';
    });
  } else if (act === 'toggle') {
    run('sensorMsg', async () => { await api('PATCH', `api/sensors/${address}`, { enabled: !sensor.enabled }); });
  } else if (act === 'rename') {
    const name = prompt('New name', sensor.name);
    if (name) run('sensorMsg', async () => { await api('PATCH', `api/sensors/${address}`, { name }); });
  } else if (act === 'delete') {
    if (confirm(`Remove ${sensor.name} (${hex(address)}) from the registry? The sensor itself is not changed.`)) {
      run('sensorMsg', async () => { await api('DELETE', `api/sensors/${address}`); });
    }
  } else if (act === 'readdress') {
    const text = prompt(`New Modbus address for ${sensor.name} (currently ${hex(address)}), 1..127 or 0x01..0x7F`);
    if (text) {
      run('sensorMsg', async () => {
        await api('POST', `api/sensors/${address}/address`, { new_address: parseAddress(text) });
        return 'Changing address…';
      });
    }
  }
});

$('sensorRows').addEventListener('toggle', (event) => {
  const details = event.target;
  if (!details.matches || !details.matches('details[data-addr]')) return;
  const raw = details.dataset.addr;
  const key = /^[hf]/.test(raw) ? raw : Number(raw);
  if (details.open) openDetails.add(key); else openDetails.delete(key);
}, true);

$('sensorRows').addEventListener('pointerdown', () => { pointerDown = true; });
document.addEventListener('pointerup', () => { setTimeout(() => { pointerDown = false; }, 0); });
document.addEventListener('pointercancel', () => { pointerDown = false; });

$('sensorRows').addEventListener('input', (event) => {
  const interval = event.target.closest('input[data-fft-interval]');
  if (interval) pendingIntervalSec = Number(interval.value);
});

$('sensorRows').addEventListener('change', (event) => {
  const averages = event.target.closest('select[data-averages]');
  if (averages) {
    const value = Number(averages.value);
    averages.blur();
    run('sensorMsg', async () => { await api('POST', 'api/config', { fft_averages: value }); return `Average: ${value} capture(s).`; });
    return;
  }
});

$('refreshPorts').addEventListener('click', () => loadPorts($('port').value).catch((e) => setMsg('busMsg', e.message, 'err')));

$('saveBus').addEventListener('click', () => run('busMsg', async () => {
  await api('POST', 'api/config', {
    port: $('port').value,
    baud: Number($('baud').value),
    poll_interval_sec: Number($('poll').value),
    read_profile: $('profile').value,
  });
  formLoaded = false;
  return 'Saved.';
}));

$('applyBaud').addEventListener('click', () => {
  const baud = Number($('newBaud').value);
  if (!confirm(`Write ${baud} baud into every registered sensor and switch the port? Sensors that miss the change stay at the old rate.`)) return;
  run('busMsg', async () => { await api('POST', 'api/bus/baud', { baud }); return 'Changing baud…'; });
});

$('scanBtn').addEventListener('click', () => run('scanMsg', async () => {
  const bauds = [...$('scanBauds').querySelectorAll('input:checked')].map((i) => Number(i.value));
  $('scanFound').innerHTML = '';
  await api('POST', 'api/scan', { first: parseAddress($('scanFirst').value), last: parseAddress($('scanLast').value), bauds });
  return 'Scanning…';
}));

$('scanFound').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-found]');
  if (!button) return;
  const address = Number(button.dataset.found);
  const baud = Number(button.dataset.baud);
  run('scanMsg', async () => {
    if (lastStatus && baud !== lastStatus.config.baud) {
      throw new Error(`This sensor answered at ${baud} baud; set Port baud to ${baud} (or move it with Change sensor baud) first.`);
    }
    await api('POST', 'api/sensors', { address, name: `wtvb01-${address.toString(16).padStart(2, '0')}`, enabled: true });
    button.remove();
    return `Added ${hex(address)}.`;
  });
});

refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 1000);
