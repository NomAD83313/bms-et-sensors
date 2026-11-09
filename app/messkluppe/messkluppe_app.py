from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request
from influxdb_client import InfluxDBClient, Point, WritePrecision  # type: ignore
from influxdb_client.client.write_api import SYNCHRONOUS  # type: ignore

try:
    from .messkluppe_mock_node import build_mock_node_sample
    from .messkluppe_protocol import (
        DEFAULT_CLIP_ID,
        TASK_DEEP_SLEEP,
        TASK_FILE_DELETE,
        TASK_FILE_DELETE_ALL,
        TASK_FILE_DOWNLOAD,
        TASK_FILE_LIST,
        TASK_FILE_LIST_DONE,
        TASK_IDLE,
        TASK_LIVE_DATA,
        build_ack_command,
        build_delete_all_command,
        build_delete_file_command,
        build_file_download_command,
        build_file_list_command,
        build_logging_command,
        build_ping_command,
        decode_file_data_packet,
        decode_live_data_packet,
        decode_ping_packet,
        make_id_task,
        radio_bytes_to_words_16,
        split_id_task,
        words_to_radio_bytes_32,
    )
    from .messkluppe_radio_diag import radio_diag_config_from_env, run_radio_diagnostics
    from .messkluppe_radio_rx import MesskluppeRadioRx
    from .messkluppe_records import (
        DEFAULT_MEASUREMENT,
        MesskluppeInfluxRecord,
        file_packet_to_influx_record,
        live_packet_to_influx_record,
        ping_packet_to_influx_record,
    )
except ImportError:
    from messkluppe_mock_node import build_mock_node_sample
    from messkluppe_protocol import (
        DEFAULT_CLIP_ID,
        TASK_DEEP_SLEEP,
        TASK_FILE_DELETE,
        TASK_FILE_DELETE_ALL,
        TASK_FILE_DOWNLOAD,
        TASK_FILE_LIST,
        TASK_FILE_LIST_DONE,
        TASK_IDLE,
        TASK_LIVE_DATA,
        build_ack_command,
        build_delete_all_command,
        build_delete_file_command,
        build_file_download_command,
        build_file_list_command,
        build_logging_command,
        build_ping_command,
        decode_file_data_packet,
        decode_live_data_packet,
        decode_ping_packet,
        make_id_task,
        radio_bytes_to_words_16,
        split_id_task,
        words_to_radio_bytes_32,
    )
    from messkluppe_radio_diag import radio_diag_config_from_env, run_radio_diagnostics
    from messkluppe_radio_rx import MesskluppeRadioRx
    from messkluppe_records import (
        DEFAULT_MEASUREMENT,
        MesskluppeInfluxRecord,
        file_packet_to_influx_record,
        live_packet_to_influx_record,
        ping_packet_to_influx_record,
    )


MESSKLUPPE_APP_PORT = int(os.getenv("MESSKLUPPE_APP_PORT", "3080"))
MESSKLUPPE_FAKE_MODE = os.getenv("MESSKLUPPE_FAKE_MODE", "1").strip().lower() in {"1", "true", "yes", "on"}
MESSKLUPPE_INPUT_MODE = os.getenv("MESSKLUPPE_INPUT_MODE", "mock" if MESSKLUPPE_FAKE_MODE else "radio").strip().lower() or "mock"
if MESSKLUPPE_INPUT_MODE not in {"mock", "radio", "disabled"}:
    MESSKLUPPE_INPUT_MODE = "mock" if MESSKLUPPE_FAKE_MODE else "disabled"
MESSKLUPPE_FAKE_INTERVAL_SEC = float(os.getenv("MESSKLUPPE_FAKE_INTERVAL_SEC", "5.0"))
MESSKLUPPE_RADIO_POLL_SEC = float(os.getenv("MESSKLUPPE_RADIO_POLL_SEC", "0.05"))
MESSKLUPPE_RADIO_RECENT_PAYLOADS = max(1, int(os.getenv("MESSKLUPPE_RADIO_RECENT_PAYLOADS", "10")))
MESSKLUPPE_RADIO_COMMAND_REPEAT_SEC = max(0.05, float(os.getenv("MESSKLUPPE_RADIO_COMMAND_REPEAT_SEC", "0.25")))
MESSKLUPPE_RADIO_ACK_TX_ENABLED = os.getenv("MESSKLUPPE_RADIO_ACK_TX_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
MESSKLUPPE_RADIO_LIVE_REPEAT_ENABLED = os.getenv("MESSKLUPPE_RADIO_LIVE_REPEAT_ENABLED", "1").strip().lower() in {"1", "true", "yes", "on"}
MESSKLUPPE_RADIO_LIVE_REPEAT_MODE = os.getenv("MESSKLUPPE_RADIO_LIVE_REPEAT_MODE", "after_rx").strip().lower()
if MESSKLUPPE_RADIO_LIVE_REPEAT_MODE not in {"after_rx", "timer", "off"}:
    MESSKLUPPE_RADIO_LIVE_REPEAT_MODE = "after_rx"
MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE = os.getenv("MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE", "0").strip().lower() in {"1", "true", "yes", "on"}
MESSKLUPPE_CLIP_ID = max(1, int(os.getenv("MESSKLUPPE_CLIP_ID", str(DEFAULT_CLIP_ID))))
MESSKLUPPE_MEASUREMENT = os.getenv("MESSKLUPPE_INFLUX_MEASUREMENT", DEFAULT_MEASUREMENT).strip() or DEFAULT_MEASUREMENT
MESSKLUPPE_SOURCE_TAG = os.getenv("MESSKLUPPE_SOURCE_TAG", "messkluppe").strip() or "messkluppe"
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086").strip()
INFLUX_ORG = os.getenv("INFLUX_ORG", "").strip()
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "").strip()
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "").strip()


def _load_common_css() -> str:
    base_dir = Path(__file__).resolve().parent
    candidates = (
        base_dir / "static" / "device-common.css",
        base_dir / "device-common.css",
        base_dir.parent / "shared" / "device-common.css",
        Path("/app/static/device-common.css"),
        Path("/app/device-common.css"),
    )
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")
    return ""


COMMON_CSS = _load_common_css()

app = Flask(__name__)
_state_lock = threading.Lock()
_influx_lock = threading.Lock()
_radio_io_lock = threading.RLock()
_stop_event = threading.Event()
_collector_thread: threading.Thread | None = None
_active_radio: MesskluppeRadioRx | None = None
_influx_client: InfluxDBClient | None = None
_influx_write_api: Any = None

_state: dict[str, Any] = {
    "started_at": time.time(),
    "collector_running": False,
    "fake_mode": MESSKLUPPE_FAKE_MODE,
    "input_mode": MESSKLUPPE_INPUT_MODE,
    "radio_rx_ready": False,
    "radio_listening": False,
    "radio_rx_packets": 0,
    "radio_rx_empty_reads": 0,
    "radio_rx_errors": 0,
    "radio_rx_last_at": None,
    "radio_rx_last_error": "",
    "radio_rx_last_payload_hex": "",
    "radio_rx_recent_payloads": [],
    "radio_rx_parse_errors": 0,
    "radio_runtime": None,
    "radio_tx_commands": 0,
    "radio_tx_errors": 0,
    "radio_tx_auto_repeats": 0,
    "radio_ack_tx_enabled": MESSKLUPPE_RADIO_ACK_TX_ENABLED,
    "radio_live_repeat_enabled": MESSKLUPPE_RADIO_LIVE_REPEAT_ENABLED,
    "radio_live_repeat_mode": MESSKLUPPE_RADIO_LIVE_REPEAT_MODE,
    "radio_ack_pad_to_payload_size": MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE,
    "radio_tx_last_at": None,
    "radio_tx_last_action": "",
    "radio_tx_last_payload_hex": "",
    "radio_tx_last_error": "",
    "radio_tx_last_result": None,
    "radio_tx_recent_commands": [],
    "radio_repeat_action": "",
    "radio_repeat_payload": None,
    "packets_received": 0,
    "records_written": 0,
    "write_errors": 0,
    "parse_errors": 0,
    "last_packet_at": None,
    "last_write_at": None,
    "last_error": "",
    "last_record": None,
    "clip_mode": "idle",
    "clip_task": 0,
    "logging": False,
    "deep_sleep": False,
    "live_mode": False,
    "live_display": "linearForce",
    "sample_rate": 2500,
    "logging_time": 100,
    "last_command": None,
    "command_errors": 0,
    "online_files": [],
    "radio_config": radio_diag_config_from_env(),
    "radio_last_diag": None,
}

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Messkluppe Collector</title>
  <style>
__COMMON_CSS__
    body { padding: 24px; }
    .page-shell { max-width: 1180px; margin: 0 auto; display: grid; gap: 18px; }
    .page-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
    .page-title { display: grid; gap: 12px; }
    .page-title h1 { margin: 0; font-size: 1.9rem; line-height: 1.1; letter-spacing: 0; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end; }
    .toolbar button:disabled { opacity: .56; cursor: not-allowed; }
    .overview-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .control-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
    .panel { padding: 22px; }
    .panel-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; margin-bottom: 16px; }
    .panel-head h2 { margin: 0; font-size: 1.05rem; letter-spacing: 0; }
    .control-surface { display: grid; gap: 12px; padding: 14px 16px; border-radius: 18px; background: linear-gradient(180deg,#fbfdff 0%,#f4f8fb 100%); border: 1px solid var(--line); }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    label { display: grid; gap: 6px; color: var(--muted); font-size: .78rem; font-weight: 800; letter-spacing: .05em; text-transform: uppercase; }
    input, select { width: 100%; min-height: 42px; border: 1px solid var(--line-strong); border-radius: 14px; padding: 0 12px; color: var(--ink); background: #fff; font: inherit; }
    .button-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
    .table-wrap { overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 10px 8px; border-bottom: 1px solid var(--line); text-align: left; font-size: .9rem; }
    th { color: var(--muted); font-size: .76rem; letter-spacing: .05em; text-transform: uppercase; }
    .empty-note { color: var(--muted); font-size: .92rem; line-height: 1.45; }
    .state-list { display: grid; gap: 0; }
    .state-row { display: grid; grid-template-columns: 190px minmax(0, 1fr); gap: 12px; padding: 9px 0; border-top: 1px solid var(--line); font-size: .94rem; }
    .state-row:first-child { border-top: 0; }
    .state-key { color: var(--muted); font-weight: 700; }
    pre { margin: 0; padding: 18px; min-height: 180px; white-space: pre-wrap; overflow-wrap: anywhere; border-radius: 18px; background: #0d1722; color: #d5e9ff; line-height: 1.45; font-size: .9rem; box-shadow: inset 0 1px 0 rgba(255,255,255,.03); }
    .footer { color: var(--muted); font-size: .9rem; line-height: 1.45; }
    @media (max-width: 900px) { .page-head { display: grid; } .toolbar { justify-content: flex-start; } .overview-grid, .control-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 720px) { .control-grid, .form-grid { grid-template-columns: 1fr; } }
    @media (max-width: 560px) { body { padding: 18px 14px 32px; } .overview-grid { grid-template-columns: 1fr; } .state-row { grid-template-columns: 1fr; gap: 3px; } }
  </style>
</head>
<body>
  <main class="page-shell">
    <header class="page-head">
      <div class="page-title">
        <div class="eyebrow">Legacy nRF24 Collector</div>
        <h1>Messkluppe Collector</h1>
        <p class="page-subtitle">Host-side collector scaffold for Messkluppe binary payloads. Mock mode validates the InfluxDB path before node hardware is connected.</p>
      </div>
      <div class="toolbar">
        <button type="button" id="refreshBtn" class="graf-link-button">Refresh</button>
        <button type="button" id="fakeOnceBtn" class="graf-link-button">Mock once</button>
        <button type="button" id="startBtn" class="graf-link-button">Start mock loop</button>
        <button type="button" id="stopBtn" class="graf-link-button">Stop mock loop</button>
        <a class="graf-link-button" href="/health.html">Health</a>
      </div>
    </header>
    <div class="overview-grid">
      <div class="stat-block"><div class="stat-label">Status</div><div class="stat-value"><span class="status-pill status-warn" id="statusPill">checking</span></div></div>
      <div class="stat-block"><div class="stat-label">Mode</div><div class="stat-value" id="modeValue">-</div></div>
      <div class="stat-block"><div class="stat-label">Packets</div><div class="stat-value" id="packetsValue">-</div></div>
      <div class="stat-block"><div class="stat-label">Influx writes</div><div class="stat-value" id="writesValue">-</div></div>
    </div>
    <section class="control-grid">
      <div class="card panel">
        <div class="panel-head"><h2>Start Logging</h2><span class="status-pill status-warn" id="loggingPill">idle</span></div>
        <div class="control-surface">
          <div class="form-grid">
            <label>Sample rate Hz<input id="sampleRateInput" type="number" min="1" step="1" value="2500"></label>
            <label>Logging time sec<input id="loggingTimeInput" type="number" min="1" step="1" value="100"></label>
          </div>
          <div class="button-row">
            <button type="button" id="startLoggingBtn" class="graf-link-button">Start logging</button>
            <button type="button" id="stopLoggingBtn" class="graf-link-button">Stop logging</button>
          </div>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h2>Clip Mode</h2><span id="clipTaskLabel">task -</span></div>
        <div class="control-surface">
          <div class="button-row">
            <button type="button" id="resetModeBtn" class="graf-link-button">Reset mode</button>
            <button type="button" id="deepSleepStartBtn" class="graf-link-button">Start deep sleep</button>
            <button type="button" id="deepSleepStopBtn" class="graf-link-button">Stop deep sleep</button>
          </div>
          <div class="empty-note">These controls mirror the legacy Host UI. In mock mode they update collector state only.</div>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h2>Live Data</h2><span class="status-pill status-warn" id="livePill">stopped</span></div>
        <div class="control-surface">
          <label>Display values
            <select id="liveDisplaySelect">
              <option value="linearForce">Force</option>
              <option value="raw">Bits</option>
            </select>
          </label>
          <div class="button-row">
            <button type="button" id="liveStartBtn" class="graf-link-button">Start live</button>
            <button type="button" id="liveStopBtn" class="graf-link-button">Stop live</button>
          </div>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h2>Online Files</h2><button type="button" id="refreshFilesBtn" class="graf-link-button">Refresh list</button></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Name</th><th>Lines</th><th>Size</th><th>Action</th></tr></thead>
            <tbody id="onlineFilesBody"><tr><td colspan="4" class="empty-note">No files loaded. Real radio transport is pending.</td></tr></tbody>
          </table>
        </div>
        <div class="button-row" style="margin-top:12px">
          <button type="button" id="deleteAllFilesBtn" class="graf-link-button">Delete all online files</button>
        </div>
      </div>
      <div class="card panel">
        <div class="panel-head"><h2>Radio Diagnostics</h2><span class="status-pill status-warn" id="radioPill">not checked</span></div>
        <div class="control-surface">
          <div class="form-grid">
            <label>SPI device<input id="radioSpiInput" type="text" value="/dev/spidev0.0" readonly></label>
            <label>CE GPIO<input id="radioCeInput" type="number" min="0" step="1" value="25" readonly></label>
          </div>
          <div class="button-row">
            <button type="button" id="radioDiagBtn" class="graf-link-button">Check radio</button>
          </div>
          <pre id="radioDiagOutput" style="min-height:120px">-</pre>
        </div>
      </div>
    </section>
    <section class="card panel">
      <div class="panel-head"><h2>Collector State</h2><span id="updated">-</span></div>
      <div id="stateRows" class="state-list"></div>
    </section>
    <section class="card panel">
      <div class="panel-head"><h2>Last Record</h2><span id="recordTime">-</span></div>
      <pre id="lastRecord">-</pre>
    </section>
    <div class="footer">Real radio RX support is pending; mock mode validates the Influx path before node hardware is available.</div>
  </main>
  <script>
    const statusPill = document.getElementById('statusPill');
    const stateRows = document.getElementById('stateRows');
    const lastRecord = document.getElementById('lastRecord');
    const recordTime = document.getElementById('recordTime');
    const updated = document.getElementById('updated');
    const onlineFilesBody = document.getElementById('onlineFilesBody');
    const radioPill = document.getElementById('radioPill');
    const radioDiagOutput = document.getElementById('radioDiagOutput');

    function row(label, value) {
      const div = document.createElement('div');
      div.className = 'state-row';
      div.innerHTML = `<div class="state-key">${label}</div><div>${value ?? '-'}</div>`;
      return div;
    }

    function fmtTime(value) {
      if (!value) return '-';
      return new Date(Number(value) * 1000).toLocaleString();
    }

    function render(data) {
      const ok = data.ok && !data.last_error;
      statusPill.className = `status-pill ${ok ? 'status-ok' : (data.last_error ? 'status-err' : 'status-warn')}`;
      statusPill.textContent = ok ? 'ok' : (data.last_error ? 'error' : 'degraded');
      document.getElementById('modeValue').textContent = data.input_mode || (data.fake_mode ? 'mock' : 'radio');
      document.getElementById('packetsValue').textContent = data.packets_received ?? 0;
      document.getElementById('writesValue').textContent = data.records_written ?? 0;
      document.getElementById('sampleRateInput').value = data.sample_rate ?? 2500;
      document.getElementById('loggingTimeInput').value = data.logging_time ?? 100;
      document.getElementById('liveDisplaySelect').value = data.live_display || 'linearForce';
      document.getElementById('clipTaskLabel').textContent = `task ${data.clip_task ?? '-'}`;
      document.getElementById('loggingPill').className = `status-pill ${data.logging ? 'status-ok' : 'status-warn'}`;
      document.getElementById('loggingPill').textContent = data.logging ? 'logging' : 'idle';
      document.getElementById('livePill').className = `status-pill ${data.live_mode ? 'status-ok' : 'status-warn'}`;
      document.getElementById('livePill').textContent = data.live_mode ? 'running' : 'stopped';
      const radioConfig = data.radio_config || {};
      document.getElementById('radioSpiInput').value = `/dev/spidev${radioConfig.spi_bus ?? 0}.${radioConfig.spi_device ?? 0}`;
      document.getElementById('radioCeInput').value = radioConfig.ce_gpio ?? 25;
      const radioDiag = data.radio_last_diag || null;
      if (radioDiag) {
        radioPill.className = `status-pill ${radioDiag.ok ? 'status-ok' : 'status-err'}`;
        radioPill.textContent = radioDiag.ok ? 'ok' : 'error';
        radioDiagOutput.textContent = JSON.stringify(radioDiag, null, 2);
      }
      stateRows.innerHTML = '';
      [
        ['Collector running', data.collector_running ? 'yes' : 'no'],
        ['Input mode', data.input_mode || '-'],
        ['Radio RX ready', data.radio_rx_ready ? 'yes' : 'no'],
        ['Radio listening', data.radio_listening ? 'yes' : 'no'],
        ['Radio RX packets', data.radio_rx_packets ?? 0],
        ['Radio empty reads', data.radio_rx_empty_reads ?? 0],
        ['Radio last RX', fmtTime(data.radio_rx_last_at)],
        ['Radio last payload', data.radio_rx_last_payload_hex || '-'],
        ['Radio parse errors', data.radio_rx_parse_errors ?? 0],
        ['Radio error', data.radio_rx_last_error || '-'],
        ['Radio TX commands', data.radio_tx_commands ?? 0],
        ['Radio ACK TX enabled', data.radio_ack_tx_enabled ? 'yes' : 'no'],
        ['Radio live repeat', data.radio_live_repeat_enabled ? 'yes' : 'no'],
        ['Radio last TX', data.radio_tx_last_action ? `${data.radio_tx_last_action}: ${data.radio_tx_last_payload_hex || '-'}` : '-'],
        ['Radio TX error', data.radio_tx_last_error || '-'],
        ['Radio TX result', data.radio_tx_last_result ? JSON.stringify(data.radio_tx_last_result) : '-'],
        ['Influx configured', data.influx_configured ? 'yes' : 'no'],
        ['Last packet', fmtTime(data.last_packet_at)],
        ['Last write', fmtTime(data.last_write_at)],
        ['Parse errors', data.parse_errors ?? 0],
        ['Write errors', data.write_errors ?? 0],
        ['Last error', data.last_error || '-'],
        ['Last command', data.last_command ? `${data.last_command.action}: ${data.last_command.detail}` : '-'],
        ['Radio CE GPIO', radioConfig.ce_gpio ?? '-'],
        ['Radio last check', radioDiag ? (radioDiag.ok ? 'ok' : (radioDiag.error || 'error')) : '-'],
        ['Command errors', data.command_errors ?? 0],
        ['Uptime', `${data.uptime_sec ?? 0}s`],
      ].forEach(([k, v]) => stateRows.appendChild(row(k, v)));
      lastRecord.textContent = JSON.stringify(data.last_record || {}, null, 2);
      recordTime.textContent = data.last_record && data.last_record.time_ns ? `${data.last_record.time_ns} ns` : '-';
      updated.textContent = `updated ${new Date().toLocaleTimeString()}`;
      document.getElementById('startBtn').disabled = data.input_mode !== 'mock' || data.collector_running;
      document.getElementById('stopBtn').disabled = data.input_mode !== 'mock' || !data.collector_running;
    }

    async function api(path, options = {}) {
      const res = await fetch(path, { cache: 'no-store', ...options });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data.status || data;
    }

    async function postJson(path, payload = {}) {
      return api(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    }

    async function refresh() {
      try { render(await api('api/status')); }
      catch (err) {
        statusPill.className = 'status-pill status-err';
        statusPill.textContent = 'down';
        stateRows.innerHTML = '';
        stateRows.appendChild(row('Error', err.message || err));
      }
    }

    document.getElementById('refreshBtn').addEventListener('click', refresh);
    document.getElementById('fakeOnceBtn').addEventListener('click', async () => { await api('api/mock-node/once', { method: 'POST' }); await refresh(); });
    document.getElementById('startBtn').addEventListener('click', async () => { await api('api/mock-node/start', { method: 'POST' }); await refresh(); });
    document.getElementById('stopBtn').addEventListener('click', async () => { await api('api/mock-node/stop', { method: 'POST' }); await refresh(); });
    document.getElementById('startLoggingBtn').addEventListener('click', async () => {
      await postJson('api/clip/start-logging', {
        sample_rate: Number(document.getElementById('sampleRateInput').value || 2500),
        logging_time: Number(document.getElementById('loggingTimeInput').value || 100),
      });
      await refresh();
    });
    document.getElementById('stopLoggingBtn').addEventListener('click', async () => { await postJson('api/clip/stop-logging'); await refresh(); });
    document.getElementById('resetModeBtn').addEventListener('click', async () => { await postJson('api/clip/reset-mode'); await refresh(); });
    document.getElementById('deepSleepStartBtn').addEventListener('click', async () => { await postJson('api/clip/deep-sleep/start'); await refresh(); });
    document.getElementById('deepSleepStopBtn').addEventListener('click', async () => { await postJson('api/clip/deep-sleep/stop'); await refresh(); });
    document.getElementById('liveStartBtn').addEventListener('click', async () => {
      await postJson('api/clip/live/start', { display: document.getElementById('liveDisplaySelect').value });
      await refresh();
    });
    document.getElementById('liveStopBtn').addEventListener('click', async () => { await postJson('api/clip/live/stop'); await refresh(); });
    document.getElementById('refreshFilesBtn').addEventListener('click', async () => {
      const res = await fetch('api/clip/files', { cache: 'no-store' });
      const data = await res.json();
      const files = Array.isArray(data.files) ? data.files : [];
      onlineFilesBody.innerHTML = files.length
        ? files.map((file) => `<tr><td>${file.name || '-'}</td><td>${file.lines ?? '-'}</td><td>${file.size ?? '-'}</td><td><button type="button" class="graf-link-button" data-file="${file.name || ''}">Download</button></td></tr>`).join('')
        : '<tr><td colspan="4" class="empty-note">No online files available in mock mode.</td></tr>';
      await refresh();
    });
    document.getElementById('deleteAllFilesBtn').addEventListener('click', async () => {
      await postJson('api/clip/files/delete-all');
      onlineFilesBody.innerHTML = '<tr><td colspan="4" class="empty-note">No online files available in mock mode.</td></tr>';
      await refresh();
    });
    document.getElementById('radioDiagBtn').addEventListener('click', async () => {
      radioPill.className = 'status-pill status-warn';
      radioPill.textContent = 'checking';
      radioDiagOutput.textContent = 'checking...';
      const data = await postJson('api/radio/diagnose');
      render(data);
    });
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def _log(message: str) -> None:
    print(f"[messkluppe-collector] {message}", flush=True)


def _set_state(**updates: Any) -> None:
    with _state_lock:
        _state.update(updates)


def _bump(name: str, inc: int = 1) -> None:
    with _state_lock:
        _state[name] = int(_state.get(name, 0)) + inc


def _remember_radio_payload(payload: bytes, *, ok: bool | None = None, error: str = "") -> None:
    now = time.time()
    item = {
        "ts": now,
        "payload_hex": payload.hex(),
        "size": len(payload),
        "ok": ok,
        "error": error,
    }
    with _state_lock:
        recent = list(_state.get("radio_rx_recent_payloads") or [])
        recent.append(item)
        _state["radio_rx_recent_payloads"] = recent[-MESSKLUPPE_RADIO_RECENT_PAYLOADS:]
        _state["radio_rx_last_payload_hex"] = item["payload_hex"]
        _state["radio_rx_last_at"] = now


def _mark_last_radio_payload_result(*, ok: bool, error: str = "") -> None:
    with _state_lock:
        recent = list(_state.get("radio_rx_recent_payloads") or [])
        if recent:
            recent[-1] = {**recent[-1], "ok": ok, "error": error}
            _state["radio_rx_recent_payloads"] = recent


def _timestamp_ms() -> int:
    return int(time.time() * 1000) & 0xFFFFFFFF


def _command_payload_for_action(action: str, payload: dict[str, Any] | None = None) -> bytes:
    data = payload or {}
    clip_id = _int_payload_value(data, "clip_id", MESSKLUPPE_CLIP_ID, minimum=1, maximum=65)
    timestamp_ms = _timestamp_ms()
    if action == "ping":
        return build_ping_command(clip_id, timestamp_ms)
    if action == "start_logging":
        sample_rate = _int_payload_value(data, "sample_rate", int(_state_snapshot().get("sample_rate", 2500)), minimum=1, maximum=100000)
        logging_time = _int_payload_value(data, "logging_time", int(_state_snapshot().get("logging_time", 100)), minimum=1, maximum=86400)
        return build_logging_command(clip_id, timestamp_ms, int(time.time()), sample_rate, logging_time)
    if action in {"stop_logging", "reset_mode", "stop_live", "stop_deep_sleep"}:
        return build_ack_command(clip_id, timestamp_ms)
    if action == "start_deep_sleep":
        return words_to_radio_bytes_32([make_id_task(clip_id, TASK_DEEP_SLEEP), timestamp_ms])
    if action == "start_live":
        return words_to_radio_bytes_32([make_id_task(clip_id, TASK_LIVE_DATA), timestamp_ms])
    if action == "list_files":
        return build_file_list_command(clip_id, timestamp_ms)
    if action == "download_file":
        filename_epoch = _int_payload_value(data, "filename_epoch", _int_payload_value(data, "filename", 0, minimum=0, maximum=0xFFFFFFFF), minimum=0, maximum=0xFFFFFFFF)
        first_line = _int_payload_value(data, "first_line", 0, minimum=0, maximum=0xFFFFFFFF)
        line_count = _int_payload_value(data, "lines", 0, minimum=0, maximum=0xFFFFFFFF)
        return build_file_download_command(clip_id, timestamp_ms, filename_epoch, first_line, line_count)
    if action == "delete_file":
        filename_epoch = _int_payload_value(data, "filename_epoch", _int_payload_value(data, "filename", 0, minimum=0, maximum=0xFFFFFFFF), minimum=0, maximum=0xFFFFFFFF)
        return build_delete_file_command(clip_id, timestamp_ms, filename_epoch)
    if action == "delete_all_files":
        return build_delete_all_command(clip_id, timestamp_ms)
    return words_to_radio_bytes_32([make_id_task(clip_id, TASK_IDLE), timestamp_ms])


def _queue_radio_ack_payload(payload: bytes) -> dict[str, Any]:
    if not MESSKLUPPE_RADIO_ACK_TX_ENABLED:
        return {
            "ok": False,
            "skipped": True,
            "reason": "radio_ack_tx_disabled",
            "payload_hex": payload.hex(),
            "size": len(payload),
        }
    with _radio_io_lock:
        if _active_radio is None or not _active_radio.ready:
            raise RuntimeError("radio_rx_loop_not_ready")
        return _active_radio.write_ack_payload(payload, pipe=1, flush_tx=True, pad_to=_ack_payload_pad_to(_active_radio))


def _ack_payload_pad_to(radio: MesskluppeRadioRx) -> int | None:
    if not MESSKLUPPE_RADIO_ACK_PAD_TO_PAYLOAD_SIZE:
        return None
    return int(radio.config.get("payload_size", 32))


def _record_tx_command(
    action: str,
    payload: bytes,
    *,
    accepted: bool,
    detail: str = "",
    error: str = "",
    tx_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "action": action,
        "accepted": accepted,
        "detail": detail,
        "payload_hex": payload.hex(),
        "size": len(payload),
        "error": error,
        "tx_result": tx_result,
        "ts": time.time(),
    }
    with _state_lock:
        recent = list(_state.get("radio_tx_recent_commands") or [])
        recent.append(item)
        _state["radio_tx_recent_commands"] = recent[-MESSKLUPPE_RADIO_RECENT_PAYLOADS:]
        _state["radio_tx_last_at"] = item["ts"]
        _state["radio_tx_last_action"] = action
        _state["radio_tx_last_payload_hex"] = item["payload_hex"]
        _state["radio_tx_last_error"] = error
        _state["radio_tx_last_result"] = tx_result
        _state["last_command"] = item
        if accepted:
            _state["radio_tx_commands"] = int(_state.get("radio_tx_commands", 0)) + 1
        else:
            _state["radio_tx_errors"] = int(_state.get("radio_tx_errors", 0)) + 1
            _state["command_errors"] = int(_state.get("command_errors", 0)) + 1
    return item


def _send_live_ack_payload(radio: MesskluppeRadioRx, *, detail: str, flush_tx: bool) -> None:
    payload = _command_payload_for_action("start_live", {"clip_id": MESSKLUPPE_CLIP_ID})
    tx_result = radio.write_ack_payload(payload, pipe=1, flush_tx=flush_tx, pad_to=_ack_payload_pad_to(radio))
    _record_tx_command("start_live", payload, accepted=True, detail=detail, tx_result=tx_result)


def _repeat_live_ack_payload(radio: MesskluppeRadioRx) -> None:
    _send_live_ack_payload(radio, detail="ack_payload_auto_repeat_pipe_1", flush_tx=True)
    _bump("radio_tx_auto_repeats")


def _repeat_pending_ack_payload(radio: MesskluppeRadioRx, action: str, payload: dict[str, Any] | None = None) -> None:
    command_payload = _command_payload_for_action(action, payload)
    tx_result = radio.write_ack_payload(command_payload, pipe=1, flush_tx=False, pad_to=_ack_payload_pad_to(radio))
    _record_tx_command(action, command_payload, accepted=True, detail="ack_payload_after_rx_pipe_1", tx_result=tx_result)
    _bump("radio_tx_auto_repeats")


def _set_radio_repeat(action: str, payload: dict[str, Any] | None = None) -> None:
    with _state_lock:
        _state["radio_repeat_action"] = action
        _state["radio_repeat_payload"] = dict(payload or {})


def _clear_radio_repeat(*actions: str) -> None:
    with _state_lock:
        current = str(_state.get("radio_repeat_action") or "")
        if not actions or current in actions:
            _state["radio_repeat_action"] = ""
            _state["radio_repeat_payload"] = None


def _file_list_entry_from_words(words: tuple[int, ...]) -> dict[str, int | str]:
    filename_epoch = (int(words[2]) << 16) + int(words[1])
    return {
        "name": f"{filename_epoch}.dat",
        "filename_epoch": filename_epoch,
        "counter": int(words[3]),
        "size": (int(words[4]) * 1000) + int(words[5]),
        "lines": int(words[6]),
        "file_type": int(words[7]),
        "dir_index": int(words[8]),
    }


def _handle_radio_packet_state(payload: bytes) -> None:
    words = radio_bytes_to_words_16(payload)
    id_task = split_id_task(words[0])
    if id_task.task == TASK_FILE_LIST:
        entry = _file_list_entry_from_words(words)
        with _state_lock:
            files = list(_state.get("online_files") or [])
            if entry["filename_epoch"] and all(item.get("filename_epoch") != entry["filename_epoch"] for item in files):
                files.append(entry)
            _state["online_files"] = files
            _state["clip_task"] = TASK_FILE_LIST
            _state["clip_mode"] = "file_list"
        _clear_radio_repeat("list_files")
    elif id_task.task == TASK_FILE_LIST_DONE:
        with _state_lock:
            _state["clip_task"] = TASK_IDLE
            _state["clip_mode"] = "idle"
        _clear_radio_repeat("list_files")


def _state_snapshot() -> dict[str, Any]:
    with _state_lock:
        snap = dict(_state)
    snap["uptime_sec"] = round(time.time() - float(snap["started_at"]), 1)
    snap["influx_configured"] = bool(INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET)
    snap["ok"] = bool(snap["influx_configured"] and not snap.get("last_error"))
    return snap


def _command_result(action: str, *, accepted: bool, detail: str = "") -> dict[str, Any]:
    payload = {
        "action": action,
        "accepted": accepted,
        "detail": detail,
        "real_radio_pending": MESSKLUPPE_INPUT_MODE != "mock",
        "ts": time.time(),
    }
    with _state_lock:
        _state["last_command"] = payload
        if not accepted:
            _state["command_errors"] = int(_state.get("command_errors", 0)) + 1
    return payload


def _accept_command(action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    command_payload = _command_payload_for_action(action, payload)
    if MESSKLUPPE_INPUT_MODE == "radio":
        tx_result = _queue_radio_ack_payload(command_payload)
        detail = "ack_payload_queued_pipe_1" if not tx_result.get("skipped") else "tx_payload_built_ack_tx_disabled"
        return _record_tx_command(action, command_payload, accepted=True, detail=detail, tx_result=tx_result)
    return _record_tx_command(action, command_payload, accepted=True, detail="tx_payload_built_mock_mode")


def _not_implemented(action: str) -> dict[str, Any]:
    return _command_result(action, accepted=False, detail="real_radio_mode_not_implemented")


def _apply_control_action(action: str, updates: dict[str, Any]) -> tuple[dict[str, Any], int]:
    with _state_lock:
        _state.update(updates)
    try:
        result = _accept_command(action, updates)
        status = 200
    except Exception as exc:
        result = _record_tx_command(action, b"", accepted=False, detail="tx_payload_build_failed", error=str(exc))
        status = 400
    return {"ok": result["accepted"], "command": result, "status": _state_snapshot()}, status


def _json_payload() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    return payload if isinstance(payload, dict) else {}


def _int_payload_value(payload: dict[str, Any], name: str, default: int, *, minimum: int = 0, maximum: int = 1_000_000) -> int:
    try:
        value = int(payload.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _ensure_influx_writer():
    global _influx_client, _influx_write_api
    if not (INFLUX_TOKEN and INFLUX_ORG and INFLUX_BUCKET):
        _set_state(last_error="influx_not_configured")
        return None
    with _influx_lock:
        if _influx_write_api is not None:
            return _influx_write_api
        try:
            _influx_client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
            _influx_write_api = _influx_client.write_api(write_options=SYNCHRONOUS)
            _log("Influx writer initialized")
            return _influx_write_api
        except Exception as exc:
            _set_state(last_error=f"influx_init_error: {exc}")
            _bump("write_errors")
            return None


def _write_record(record: MesskluppeInfluxRecord) -> bool:
    writer = _ensure_influx_writer()
    if writer is None:
        return False
    try:
        point = Point(record.measurement)
        for key, value in record.tags.items():
            point = point.tag(key, str(value))
        for key, value in record.fields.items():
            point = point.field(key, value)
        if record.time_ns is not None:
            point = point.time(record.time_ns, WritePrecision.NS)
        writer.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point, write_precision=WritePrecision.NS)
        _bump("records_written")
        _set_state(last_write_at=time.time(), last_error="")
        return True
    except Exception as exc:
        _set_state(last_error=f"influx_write_error: {exc}")
        _bump("write_errors")
        return False


def _record_preview_fields(fields: dict[str, Any]) -> dict[str, Any]:
    priority = (
        "node_millis",
        "sensor_ms",
        "line",
        "unix_time",
        "force_x_raw",
        "force_y_raw",
        "force_z_raw",
        "accel_x_raw",
        "accel_y_raw",
        "yaw_raw",
        "yaw_deg",
        "imu_temperature_raw",
        "imu_temperature_c",
        "clip_temperature_raw",
        "battery_raw",
    )
    preview = {key: fields[key] for key in priority if key in fields}
    for key in sorted(fields):
        if key not in preview:
            preview[key] = fields[key]
        if len(preview) >= 24:
            break
    return preview


def ingest_payload(payload: bytes, *, file_id: str | int | None = None) -> bool:
    try:
        id_task = split_id_task(radio_bytes_to_words_16(payload)[0])
        if id_task.task == TASK_IDLE:
            packet = decode_ping_packet(payload)
            record = ping_packet_to_influx_record(
                packet,
                measurement=MESSKLUPPE_MEASUREMENT,
                source=MESSKLUPPE_SOURCE_TAG,
                file_id=file_id,
            )
        elif id_task.task == TASK_LIVE_DATA:
            packet = decode_live_data_packet(payload)
            record = live_packet_to_influx_record(
                packet,
                measurement=MESSKLUPPE_MEASUREMENT,
                source=MESSKLUPPE_SOURCE_TAG,
                file_id=file_id,
            )
        else:
            packet = decode_file_data_packet(payload)
            record = file_packet_to_influx_record(
                packet,
                measurement=MESSKLUPPE_MEASUREMENT,
                source=MESSKLUPPE_SOURCE_TAG,
                file_id=file_id,
            )
        _bump("packets_received")
        _set_state(
            last_packet_at=time.time(),
            last_record={
                "measurement": record.measurement,
                "tags": record.tags,
                "fields": _record_preview_fields(record.fields),
                "time_ns": record.time_ns,
            },
        )
        written = _write_record(record)
        if file_id == "radio":
            _mark_last_radio_payload_result(ok=written, error="" if written else "write_failed")
        return written
    except Exception as exc:
        _set_state(last_error=f"packet_parse_error: {exc}")
        _bump("parse_errors")
        if file_id == "radio":
            _bump("radio_rx_parse_errors")
            _mark_last_radio_payload_result(ok=False, error=str(exc))
        return False


def _mock_collector_loop() -> None:
    _set_state(collector_running=True)
    seq = 1
    _log("mock node collector loop started")
    while not _stop_event.is_set():
        sample = build_mock_node_sample(seq=seq)
        ingest_payload(sample.payload, file_id=sample.file_id)
        seq += 1
        _stop_event.wait(MESSKLUPPE_FAKE_INTERVAL_SEC)
    _set_state(collector_running=False)
    _log("mock node collector loop stopped")


def _radio_collector_loop() -> None:
    global _active_radio
    poll_sec = max(0.005, MESSKLUPPE_RADIO_POLL_SEC)
    next_live_repeat_at = 0.0
    _set_state(collector_running=True, radio_listening=False, radio_rx_ready=False, radio_rx_last_error="")
    _log("radio collector loop starting")
    try:
        with MesskluppeRadioRx() as radio:
            with _radio_io_lock:
                _active_radio = radio
            _set_state(
                radio_config=radio.config,
                radio_runtime=radio.snapshot(),
                radio_listening=True,
                radio_rx_ready=True,
                last_error="",
            )
            _log("radio collector loop listening")
            while not _stop_event.is_set():
                try:
                    with _radio_io_lock:
                        snap = _state_snapshot()
                        if (
                            MESSKLUPPE_RADIO_ACK_TX_ENABLED
                            and MESSKLUPPE_RADIO_LIVE_REPEAT_ENABLED
                            and MESSKLUPPE_RADIO_LIVE_REPEAT_MODE == "timer"
                            and snap.get("live_mode")
                            and time.time() >= next_live_repeat_at
                        ):
                            _repeat_live_ack_payload(radio)
                            next_live_repeat_at = time.time() + MESSKLUPPE_RADIO_COMMAND_REPEAT_SEC
                        payload = radio.read_payload()
                    if payload is None:
                        _bump("radio_rx_empty_reads")
                        _stop_event.wait(poll_sec)
                        continue
                    _bump("radio_rx_packets")
                    _remember_radio_payload(payload)
                    _set_state(radio_rx_last_error="")
                    with _radio_io_lock:
                        snap = _state_snapshot()
                        if (
                            MESSKLUPPE_RADIO_ACK_TX_ENABLED
                            and MESSKLUPPE_RADIO_LIVE_REPEAT_ENABLED
                            and MESSKLUPPE_RADIO_LIVE_REPEAT_MODE in {"after_rx", "timer"}
                            and snap.get("live_mode")
                        ):
                            _send_live_ack_payload(radio, detail="ack_payload_after_rx_pipe_1", flush_tx=False)
                        elif (
                            MESSKLUPPE_RADIO_ACK_TX_ENABLED
                            and snap.get("radio_repeat_action") == "list_files"
                        ):
                            _repeat_pending_ack_payload(radio, "list_files", snap.get("radio_repeat_payload"))
                    _handle_radio_packet_state(payload)
                    ingest_payload(payload, file_id="radio")
                except Exception as exc:
                    _bump("radio_rx_errors")
                    _set_state(radio_rx_last_error=str(exc), last_error=f"radio_rx_error: {exc}")
                    _stop_event.wait(max(0.2, poll_sec))
    except Exception as exc:
        _bump("radio_rx_errors")
        _set_state(radio_rx_last_error=str(exc), last_error=f"radio_start_error: {exc}")
        _log(f"radio collector loop failed: {exc}")
    finally:
        with _radio_io_lock:
            if _active_radio is not None:
                _active_radio = None
        _set_state(collector_running=False, radio_listening=False, radio_rx_ready=False)
        _log("radio collector loop stopped")


def _start_collector() -> None:
    global _collector_thread
    if _collector_thread is not None and _collector_thread.is_alive():
        return
    if MESSKLUPPE_INPUT_MODE == "disabled":
        _set_state(last_error="input_mode_disabled")
        _log("collector input mode is disabled")
        return
    _stop_event.clear()
    target = _radio_collector_loop if MESSKLUPPE_INPUT_MODE == "radio" else _mock_collector_loop
    _collector_thread = threading.Thread(target=target, daemon=True)
    _collector_thread.start()


def _stop_collector() -> None:
    _stop_event.set()
    if _collector_thread is not None and _collector_thread.is_alive():
        _collector_thread.join(timeout=2.0)


@app.route("/")
def index():
    return Response(INDEX_HTML.replace("__COMMON_CSS__", COMMON_CSS), mimetype="text/html")


@app.route("/health")
def health():
    return jsonify({"ok": True, **_state_snapshot()})


@app.route("/api/status")
def api_status():
    return jsonify(_state_snapshot())


@app.route("/api/radio/diagnose", methods=["POST"])
def api_radio_diagnose():
    snap = _state_snapshot()
    if snap.get("radio_listening"):
        runtime = snap.get("radio_runtime") or {}
        diag = {
            "ok": bool(snap.get("radio_rx_ready")),
            "configured": snap.get("radio_config", radio_diag_config_from_env()),
            "checks": {
                "radio_rx_loop": {
                    "ok": bool(snap.get("radio_rx_ready")),
                    "message": "radio_rx_loop_active",
                }
            },
            "registers": {
                "status": runtime.get("status"),
                "fifo_status": runtime.get("fifo_status"),
            },
            "details": {
                "radio_listening": True,
                "rx_packets": snap.get("radio_rx_packets", 0),
                "rx_empty_reads": snap.get("radio_rx_empty_reads", 0),
                "last_rx_at": snap.get("radio_rx_last_at"),
                "last_payload_hex": snap.get("radio_rx_last_payload_hex"),
                "rx_parse_errors": snap.get("radio_rx_parse_errors", 0),
            },
            "duration_ms": 0.0,
            "error": snap.get("radio_rx_last_error") or "",
        }
        _set_state(radio_last_diag=diag)
        return jsonify({"ok": bool(diag["ok"]), "radio": diag, "status": _state_snapshot()}), 200 if diag["ok"] else 503

    diag = run_radio_diagnostics()
    _set_state(radio_config=radio_diag_config_from_env(), radio_last_diag=diag)
    return jsonify({"ok": bool(diag.get("ok")), "radio": diag, "status": _state_snapshot()}), 200 if diag.get("ok") else 503


@app.route("/api/ingest-hex", methods=["POST"])
def api_ingest_hex():
    payload = request.get_json(silent=True) or {}
    text = str(payload.get("payload_hex", "")).strip().replace(" ", "")
    file_id = payload.get("file_id")
    try:
        raw = bytes.fromhex(text)
    except ValueError:
        return jsonify({"ok": False, "error": "invalid_hex"}), 400
    ok = ingest_payload(raw, file_id=file_id)
    return jsonify({"ok": ok, "status": _state_snapshot()})


@app.route("/api/radio/recent-payloads", methods=["GET"])
def api_radio_recent_payloads():
    snap = _state_snapshot()
    return jsonify(
        {
            "ok": True,
            "payloads": snap.get("radio_rx_recent_payloads", []),
            "last_payload_hex": snap.get("radio_rx_last_payload_hex", ""),
            "count": len(snap.get("radio_rx_recent_payloads", [])),
            "max_count": MESSKLUPPE_RADIO_RECENT_PAYLOADS,
        }
    )


@app.route("/api/radio/recent-commands", methods=["GET"])
def api_radio_recent_commands():
    snap = _state_snapshot()
    commands = snap.get("radio_tx_recent_commands", [])
    return jsonify(
        {
            "ok": True,
            "commands": commands,
            "last_action": snap.get("radio_tx_last_action", ""),
            "last_payload_hex": snap.get("radio_tx_last_payload_hex", ""),
            "count": len(commands),
            "max_count": MESSKLUPPE_RADIO_RECENT_PAYLOADS,
        }
    )


@app.route("/api/fake-once", methods=["POST"])
def api_fake_once():
    return api_mock_node_once()


@app.route("/api/mock-node/once", methods=["POST"])
def api_mock_node_once():
    seq = int(time.time()) & 0xFFFF
    sample = build_mock_node_sample(seq=seq)
    ok = ingest_payload(sample.payload, file_id=sample.file_id)
    return jsonify({"ok": ok, "sample": {"seq": sample.seq, "file_id": sample.file_id, "unix_time": sample.unix_time}, "status": _state_snapshot()})


@app.route("/api/clip/start-logging", methods=["POST"])
def api_clip_start_logging():
    payload = _json_payload()
    sample_rate = _int_payload_value(payload, "sample_rate", int(_state_snapshot().get("sample_rate", 2500)), minimum=1, maximum=100000)
    logging_time = _int_payload_value(payload, "logging_time", int(_state_snapshot().get("logging_time", 100)), minimum=1, maximum=86400)
    body, status = _apply_control_action(
        "start_logging",
        {
            "clip_mode": "logging",
            "clip_task": 20,
            "logging": True,
            "sample_rate": sample_rate,
            "logging_time": logging_time,
        },
    )
    return jsonify(body), status


@app.route("/api/clip/stop-logging", methods=["POST"])
def api_clip_stop_logging():
    body, status = _apply_control_action("stop_logging", {"clip_mode": "idle", "clip_task": 0, "logging": False})
    return jsonify(body), status


@app.route("/api/clip/reset-mode", methods=["POST"])
def api_clip_reset_mode():
    body, status = _apply_control_action(
        "reset_mode",
        {
            "clip_mode": "idle",
            "clip_task": 0,
            "logging": False,
            "live_mode": False,
        },
    )
    return jsonify(body), status


@app.route("/api/clip/deep-sleep/start", methods=["POST"])
def api_clip_deep_sleep_start():
    body, status = _apply_control_action("start_deep_sleep", {"clip_mode": "deep_sleep", "clip_task": 10, "deep_sleep": True})
    return jsonify(body), status


@app.route("/api/clip/deep-sleep/stop", methods=["POST"])
def api_clip_deep_sleep_stop():
    body, status = _apply_control_action("stop_deep_sleep", {"clip_mode": "idle", "clip_task": 0, "deep_sleep": False})
    return jsonify(body), status


@app.route("/api/clip/live/start", methods=["POST"])
def api_clip_live_start():
    payload = _json_payload()
    display = str(payload.get("display", _state_snapshot().get("live_display", "linearForce"))).strip() or "linearForce"
    if display not in {"linearForce", "raw"}:
        display = "linearForce"
    body, status = _apply_control_action(
        "start_live",
        {
            "clip_mode": "live",
            "clip_task": 60,
            "live_mode": True,
            "live_display": display,
        },
    )
    return jsonify(body), status


@app.route("/api/clip/live/stop", methods=["POST"])
def api_clip_live_stop():
    body, status = _apply_control_action("stop_live", {"clip_mode": "idle", "clip_task": 0, "live_mode": False})
    return jsonify(body), status


@app.route("/api/clip/files", methods=["GET"])
def api_clip_files():
    result = _accept_command("list_files")
    if result.get("accepted"):
        _set_radio_repeat("list_files")
    status = 200
    return jsonify({"ok": result["accepted"], "command": result, "files": _state_snapshot().get("online_files", []), "status": _state_snapshot()}), status


@app.route("/api/clip/files/download", methods=["POST"])
def api_clip_file_download():
    payload = _json_payload()
    filename = str(payload.get("filename", "")).strip()
    lines = _int_payload_value(payload, "lines", 0, minimum=0, maximum=10_000_000)
    result = _accept_command("download_file", {"filename": filename, "lines": lines})
    status = 200
    return jsonify({"ok": result["accepted"], "command": result, "filename": filename, "lines": lines, "status": _state_snapshot()}), status


@app.route("/api/clip/files/delete", methods=["POST"])
def api_clip_file_delete():
    payload = _json_payload()
    filename = str(payload.get("filename", "")).strip()
    result = _accept_command("delete_file", {"filename": filename})
    status = 200
    return jsonify({"ok": result["accepted"], "command": result, "filename": filename, "status": _state_snapshot()}), status


@app.route("/api/clip/files/delete-all", methods=["POST"])
def api_clip_files_delete_all():
    with _state_lock:
        _state["online_files"] = []
    result = _accept_command("delete_all_files")
    status = 200
    return jsonify({"ok": result["accepted"], "command": result, "status": _state_snapshot()}), status


@app.route("/api/fake/start", methods=["POST"])
def api_fake_start():
    return api_mock_node_start()


@app.route("/api/mock-node/start", methods=["POST"])
def api_mock_node_start():
    if MESSKLUPPE_INPUT_MODE != "mock":
        return jsonify({"ok": False, "error": "mock_input_mode_disabled", "status": _state_snapshot()}), 409
    _start_collector()
    return jsonify({"ok": True, "status": _state_snapshot()})


@app.route("/api/fake/stop", methods=["POST"])
def api_fake_stop():
    return api_mock_node_stop()


@app.route("/api/mock-node/stop", methods=["POST"])
def api_mock_node_stop():
    _stop_collector()
    return jsonify({"ok": True, "status": _state_snapshot()})


if __name__ == "__main__":
    _start_collector()
    app.run(host="0.0.0.0", port=MESSKLUPPE_APP_PORT, debug=False)
