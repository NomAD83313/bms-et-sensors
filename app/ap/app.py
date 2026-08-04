import os
from pathlib import Path

from flask import Flask, jsonify, render_template_string

try:
    from .host import AP_INTERFACE, build_ap_snapshot, set_ap_state
except ImportError:
    from host import AP_INTERFACE, build_ap_snapshot, set_ap_state


AP_UI_PORT = int(os.getenv("AP_UI_PORT", "3070"))


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


@app.after_request
def add_no_store_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


PAGE_TEMPLATE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AP Control</title>
  <style>{{ common_css|safe }}</style>
  <style>
    :root {
      --hero: linear-gradient(135deg, #1f5f9a 0%, #2b7db9 52%, #6fc4d7 100%);
    }
    body { padding-top: 0; }
    .shell {
      max-width: 1220px;
      margin: 0 auto;
      display: grid;
      gap: 24px;
      padding: 28px 0 40px;
    }
    .hero {
      padding: 28px;
      color: #fff;
      background: var(--hero);
      overflow: hidden;
      position: relative;
    }
    .hero::after {
      content: "";
      position: absolute;
      inset: auto -8% -30% auto;
      width: 340px;
      height: 340px;
      background: radial-gradient(circle, rgba(255,255,255,0.26), rgba(255,255,255,0.02) 64%, transparent 66%);
      pointer-events: none;
    }
    .hero .eyebrow {
      background: rgba(255,255,255,0.16);
      color: #fff;
      border-color: rgba(255,255,255,0.2);
    }
    .hero .page-subtitle {
      color: rgba(255,255,255,0.84);
    }
    .hero-row, .actions {
      display: grid;
      gap: 16px;
    }
    .hero-row {
      grid-template-columns: 1.4fr 1fr;
      align-items: end;
    }
    .hero-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      justify-content: flex-end;
    }
    .panel {
      padding: 22px;
    }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 16px;
    }
    .section-title {
      margin: 0;
      font-size: 1.05rem;
      letter-spacing: 0.01em;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 12px;
    }
    .client-table-wrap {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    th, td {
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      font-size: 0.94rem;
      vertical-align: middle;
    }
    th {
      color: var(--muted);
      font-size: 0.78rem;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }
    .band-pill {
      display: inline-flex;
      align-items: center;
      min-height: 30px;
      padding: 0 10px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: var(--panel-soft);
      font-size: 0.82rem;
      font-weight: 700;
    }
    .band-excellent { color: var(--ok); background: var(--ok-bg); border-color: rgba(27, 125, 77, 0.16); }
    .band-good { color: var(--accent); background: var(--accent-soft); border-color: rgba(31, 95, 154, 0.16); }
    .band-fair { color: var(--warn); background: var(--warn-bg); border-color: rgba(147, 93, 15, 0.16); }
    .band-weak, .band-unknown { color: var(--err); background: var(--err-bg); border-color: rgba(180, 30, 30, 0.16); }
    .muted { color: var(--muted); }
    .message {
      min-height: 24px;
      color: var(--muted);
      font-size: 0.92rem;
    }
    .message.error { color: var(--err); }
    .mono {
      font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
      font-size: 0.9em;
    }
    .device-cell {
      display: grid;
      gap: 4px;
    }
    .device-name {
      font-weight: 700;
      color: var(--ink);
      line-height: 1.2;
    }
    .device-meta {
      color: var(--muted);
      font-size: 0.82rem;
      line-height: 1.2;
    }
    @media (max-width: 900px) {
      .hero-row {
        grid-template-columns: 1fr;
      }
      .hero-actions {
        justify-content: flex-start;
      }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero card">
      <div class="hero-row">
        <div>
          <div class="eyebrow">Raspberry Pi Access Point</div>
          <h1>AP Control</h1>
          <p class="page-subtitle">
            Control the NetworkManager access point on <span class="mono" id="iface-label">{{ interface_name }}</span>,
            inspect connected devices, and watch live signal quality without leaving the stack dashboard.
          </p>
        </div>
        <div class="hero-actions">
          <button class="graf-link-button graf-link-button-light" data-action="start">Start AP</button>
          <button class="graf-link-button graf-link-button-light" data-action="restart">Restart AP</button>
          <button class="graf-link-button graf-link-button-light" data-action="stop">Stop AP</button>
        </div>
      </div>
    </section>

    <section class="card panel">
      <div class="section-head">
        <h2 class="section-title">Access Point</h2>
        <div>
          <span id="status-pill" class="status-pill status-warn">Loading</span>
          <span class="muted" id="refresh-note">Auto refresh: 5s</span>
        </div>
      </div>
      <div class="summary-grid">
        <div class="stat-block">
          <div class="stat-label">SSID</div>
          <div class="stat-value" id="ssid">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Interface</div>
          <div class="stat-value mono" id="interface-name">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Clients</div>
          <div class="stat-value" id="client-count">0</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Channel</div>
          <div class="stat-value" id="channel">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">IPv4</div>
          <div class="stat-value" id="ipv4">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">IPv6</div>
          <div class="stat-value" id="ipv6-method">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Profile</div>
          <div class="stat-value mono" id="profile-name">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Device State</div>
          <div class="stat-value" id="device-state">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Band</div>
          <div class="stat-value" id="band">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Mode</div>
          <div class="stat-value" id="mode">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Autoconnect</div>
          <div class="stat-value" id="autoconnect">-</div>
        </div>
        <div class="stat-block">
          <div class="stat-label">Adapter MAC</div>
          <div class="stat-value mono" id="adapter-mac">-</div>
        </div>
      </div>
    </section>

    <section class="card panel">
      <div class="section-head">
        <h2 class="section-title">Connected Devices</h2>
        <div id="last-updated" class="muted">Waiting for data</div>
      </div>
      <div class="client-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Device</th>
              <th>IP</th>
              <th>RSSI</th>
              <th>Quality</th>
              <th>Inactive</th>
              <th>RX</th>
              <th>TX</th>
            </tr>
          </thead>
          <tbody id="clients-body">
            <tr><td colspan="7" class="muted">Loading client list…</td></tr>
          </tbody>
        </table>
      </div>
      <div id="message" class="message"></div>
    </section>
  </div>

  <script>
    const statusPill = document.getElementById("status-pill");
    const messageEl = document.getElementById("message");
    const clientsBody = document.getElementById("clients-body");

    function setMessage(text, isError = false) {
      messageEl.textContent = text || "";
      messageEl.classList.toggle("error", Boolean(isError));
    }

    function setStatus(status, active) {
      statusPill.className = "status-pill";
      if (status === "ok") {
        statusPill.classList.add("status-ok");
        statusPill.textContent = active ? "AP Online" : "Ready";
      } else if (status === "warn") {
        statusPill.classList.add("status-warn");
        statusPill.textContent = "AP Stopped";
      } else {
        statusPill.classList.add("status-err");
        statusPill.textContent = "Profile Missing";
      }
    }

    function fmtBytes(value) {
      const num = Number(value || 0);
      if (!Number.isFinite(num) || num <= 0) return "0 B";
      const units = ["B", "KB", "MB", "GB"];
      let idx = 0;
      let current = num;
      while (current >= 1024 && idx < units.length - 1) {
        current /= 1024;
        idx += 1;
      }
      return `${current.toFixed(current >= 10 || idx === 0 ? 0 : 1)} ${units[idx]}`;
    }

    function fmtInactive(ms) {
      const num = Number(ms || 0);
      if (!Number.isFinite(num) || num < 1000) return `${Math.max(0, Math.round(num))} ms`;
      const sec = num / 1000;
      if (sec < 60) return `${sec.toFixed(1)} s`;
      return `${(sec / 60).toFixed(1)} min`;
    }

    function renderClients(clients) {
      if (!Array.isArray(clients) || clients.length === 0) {
        clientsBody.innerHTML = '<tr><td colspan="7" class="muted">No active stations reported by <span class="mono">iw</span>.</td></tr>';
        return;
      }
      clientsBody.innerHTML = clients.map((client) => {
        const band = client.signal_band || "unknown";
        const quality = client.signal_quality == null ? "-" : `${client.signal_quality}%`;
        const hasSignal = client.signal_dbm != null;
        const signal = hasSignal ? `${client.signal_dbm} dBm` : "N/A";
        const signalSource = client.signal_source || "unknown";
        const qualitySource = client.signal_quality_source || "unknown";
        const signalTitle = signalSource === "driver"
          ? "RSSI reported by the AP Wi-Fi driver"
          : "RSSI is not reported by this Wi-Fi driver";
        const qualityTitle = qualitySource === "rssi"
          ? "Quality calculated from RSSI"
          : "Quality unavailable without RSSI";
        const name = client.display_name || client.hostname || client.mac || "-";
        const meta = client.hostname ? (client.mac || "-") : "";
        return `
          <tr>
            <td>
              <div class="device-cell">
                <div class="${client.hostname ? "device-name" : "mono"}">${name}</div>
                ${meta ? `<div class="device-meta mono">${meta}</div>` : ""}
              </div>
            </td>
            <td class="mono">${client.ip || "-"}</td>
            <td title="${signalTitle}">${signal}</td>
            <td><span class="band-pill band-${band}" title="${qualityTitle}">${quality}</span></td>
            <td>${fmtInactive(client.inactive_ms)}</td>
            <td>${fmtBytes(client.rx_bytes)}</td>
            <td>${fmtBytes(client.tx_bytes)}</td>
          </tr>
        `;
      }).join("");
    }

    function applySnapshot(data) {
      const profile = data.profile || {};
      setStatus(data.status, data.active);
      document.getElementById("ssid").textContent = profile.ssid || "-";
      document.getElementById("interface-name").textContent = data.interface || profile.interface_name || "-";
      document.getElementById("client-count").textContent = String(data.client_count || 0);
      document.getElementById("channel").textContent = profile.channel || "-";
      document.getElementById("ipv4").textContent = (data.ipv4_addresses || []).join(", ") || "-";
      document.getElementById("profile-name").textContent = profile.name || data.profile_name || "-";
      document.getElementById("device-state").textContent = data.state || "-";
      document.getElementById("band").textContent = profile.band || "-";
      document.getElementById("mode").textContent = profile.mode || "-";
      document.getElementById("autoconnect").textContent = profile.autoconnect || "-";
      document.getElementById("ipv6-method").textContent = profile.ipv6_method || "-";
      document.getElementById("adapter-mac").textContent = String(data.mac_address || "-").replaceAll("\\\\:", ":");
      document.getElementById("last-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
      renderClients(data.clients || []);
    }

    async function loadSnapshot() {
      try {
        const response = await fetch("./api/status", { cache: "no-store" });
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        applySnapshot(data);
        setMessage("");
      } catch (error) {
        setMessage(`Failed to load AP status: ${error.message}`, true);
      }
    }

    async function triggerAction(action) {
      setMessage(`Running ${action}…`);
      try {
        const response = await fetch(`./api/control/${action}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action }),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
          throw new Error(data.message || `HTTP ${response.status}`);
        }
        setMessage(data.message || `${action} complete`);
        await loadSnapshot();
      } catch (error) {
        setMessage(`AP ${action} failed: ${error.message}`, true);
      }
    }

    document.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => triggerAction(button.dataset.action));
    });

    loadSnapshot();
    setInterval(loadSnapshot, 5000);
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(PAGE_TEMPLATE, common_css=COMMON_CSS, interface_name=AP_INTERFACE)


@app.get("/health")
def health():
    snapshot = build_ap_snapshot()
    return jsonify(
        {
            "status": "ok",
            "service": "ap-control",
            "ap_status": snapshot["status"],
            "active": snapshot["active"],
            "client_count": snapshot["client_count"],
            "interface": snapshot["interface"],
            "profile_name": snapshot["profile_name"],
        }
    )


@app.get("/api/status")
def api_status():
    return jsonify(build_ap_snapshot())


@app.post("/api/control/<action>")
def api_control(action: str):
    ok, message = set_ap_state(action)
    status_code = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "action": action}), status_code


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=AP_UI_PORT)
