(() => {
  const root = document.getElementById("orbitSensors");
  const status = document.getElementById("orbitStatus");
  const demoToggle = document.getElementById("orbitDemoToggle");
  const scaleSelect = document.getElementById("orbitScale");
  const customScaleWrap = document.getElementById("orbitCustomScaleWrap");
  const customScaleInput = document.getElementById("orbitCustomScale");
  if (!root || !status) return;

  const pairs = [["x", "y"], ["x", "z"], ["y", "z"]];
  let demoEnabled = false;
  let lastRender = null;

  function loadScaleSetting() {
    try {
      const savedMode = localStorage.getItem("graf.vibration.orbitScale") || "auto";
      const validModes = [...scaleSelect.options].map((option) => option.value);
      scaleSelect.value = validModes.includes(savedMode) ? savedMode : "auto";
      const savedCustom = Number(localStorage.getItem("graf.vibration.orbitCustomScale"));
      if (Number.isFinite(savedCustom) && savedCustom > 0) customScaleInput.value = String(savedCustom);
    } catch (_) {}
  }

  function fixedOrbitRange() {
    const mode = scaleSelect.value;
    if (mode === "auto") return null;
    const value = Number(mode === "custom" ? customScaleInput.value : mode);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function updateScaleControls() {
    customScaleWrap.hidden = scaleSelect.value !== "custom";
    try {
      localStorage.setItem("graf.vibration.orbitScale", scaleSelect.value);
      const custom = Number(customScaleInput.value);
      if (Number.isFinite(custom) && custom > 0) localStorage.setItem("graf.vibration.orbitCustomScale", String(custom));
    } catch (_) {}
  }

  function demoWaveform() {
    const samples = 720;
    const turns = 8;
    const axes = { x: { displacement_um: [] }, y: { displacement_um: [] }, z: { displacement_um: [] } };
    for (let index = 0; index < samples; index += 1) {
      const phase = (index / samples) * turns * Math.PI * 2;
      const deterministicNoise = Math.sin(index * 1.713) * 1.8 + Math.sin(index * 0.371) * 1.1;
      axes.x.displacement_um.push(74 * Math.cos(phase) + 9 * Math.cos(2 * phase + 0.3) + deterministicNoise);
      axes.y.displacement_um.push(46 * Math.sin(phase + 0.22) + 7 * Math.sin(2 * phase) + deterministicNoise * 0.6);
      axes.z.displacement_um.push(25 * Math.sin(phase + 1.1) + 5 * Math.sin(3 * phase) + deterministicNoise * 0.35);
    }
    return {
      captured_at: new Date().toISOString(),
      sample_rate_hz: 360,
      samples,
      axes,
      orbits: {
        xy: { overall: { ratio: 0.61 } },
        xz: { overall: { ratio: 0.34 } },
        yz: { overall: { ratio: 0.52 } },
      },
    };
  }

  function finiteValues(values) {
    return (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite);
  }

  function orbitSvg(wave, axisA, axisB) {
    const size = 260;
    const pad = 28;
    const xs = finiteValues(wave.axes?.[axisA]?.displacement_um);
    const ys = finiteValues(wave.axes?.[axisB]?.displacement_um);
    const count = Math.min(xs.length, ys.length);
    const peak = Math.max(0, ...xs.slice(0, count).map(Math.abs), ...ys.slice(0, count).map(Math.abs));
    const range = fixedOrbitRange() || (peak > 0 ? peak * 1.05 : 1);
    const scale = (value) => pad + ((value + range) / (2 * range)) * (size - 2 * pad);
    const points = Array.from({ length: count }, (_, index) => (
      `${scale(xs[index]).toFixed(1)},${(size - scale(ys[index])).toFixed(1)}`
    )).join(" ");
    const pair = `${axisA}${axisB}`;
    const ratio = Number(wave.orbits?.[pair]?.overall?.ratio);
    const ratioLabel = Number.isFinite(ratio) ? ratio.toFixed(2) : "–";
    return `
      <figure class="orbit-figure">
        <svg class="orbit-chart" viewBox="0 0 ${size} ${size}" role="img" aria-label="Orbit ${pair.toUpperCase()}">
          <line class="orbit-grid" x1="${size / 2}" x2="${size / 2}" y1="${pad}" y2="${size - pad}" />
          <line class="orbit-grid" x1="${pad}" x2="${size - pad}" y1="${size / 2}" y2="${size / 2}" />
          <polyline class="orbit-path" points="${points}" />
          <text x="${size - pad}" y="${size / 2 - 5}" text-anchor="end">${axisA.toUpperCase()}</text>
          <text x="${size / 2 + 5}" y="${pad + 10}">${axisB.toUpperCase()}</text>
          <text x="${pad}" y="${size - 8}">±${range.toPrecision(3)} µm</text>
        </svg>
        <figcaption><strong>${pair.toUpperCase()}</strong> · ratio ${ratioLabel}</figcaption>
      </figure>`;
  }

  function capturedLabel(raw) {
    const date = new Date(raw || "");
    return Number.isFinite(date.getTime()) ? date.toLocaleString() : "unknown time";
  }

  async function loadOrbits() {
    if (demoEnabled) {
      const wave = demoWaveform();
      lastRender = () => loadOrbits();
      root.innerHTML = `
        <article class="orbit-sensor">
          <div class="orbit-sensor-head">
            <strong>Demo drive · simulated unbalance</strong>
            <span class="orbit-status">Simulation only · 8 revolutions · 360.0 Hz · ${wave.samples} samples</span>
          </div>
          <div class="orbit-plots">${pairs.map(([a, b]) => orbitSvg(wave, a, b)).join("")}</div>
        </article>`;
      status.textContent = "Simulated orbit data";
      return;
    }
    try {
      const statusResponse = await fetch("/vibration/api/status", { cache: "no-store" });
      if (!statusResponse.ok) throw new Error(`status HTTP ${statusResponse.status}`);
      const payload = await statusResponse.json();
      const sensors = (payload.sensors || []).filter((sensor) => sensor.enabled && sensor.fft?.captured_at);
      if (!sensors.length) {
        root.innerHTML = "";
        status.textContent = "No waveform capture is available yet";
        return;
      }

      const results = await Promise.all(sensors.map(async (sensor) => {
        const response = await fetch(`/vibration/api/sensors/${sensor.address}/waveform`, { cache: "no-store" });
        if (!response.ok) return null;
        return { sensor, wave: await response.json() };
      }));
      const available = results.filter(Boolean);
      lastRender = () => renderAvailable(available);
      renderAvailable(available);
    } catch (error) {
      status.textContent = `Orbit data unavailable: ${error.message || error}`;
    }
  }

  function renderAvailable(available) {
      root.innerHTML = available.map(({ sensor, wave }) => `
        <article class="orbit-sensor">
          <div class="orbit-sensor-head">
            <strong>${escapeHtml(sensor.name || `Sensor ${sensor.address}`)}</strong>
            <span class="orbit-status">Capture ${escapeHtml(capturedLabel(wave.captured_at))} · ${Number(wave.sample_rate_hz || 0).toFixed(1)} Hz · ${Number(wave.samples || 0)} samples</span>
          </div>
          <div class="orbit-plots">${pairs.map(([a, b]) => orbitSvg(wave, a, b)).join("")}</div>
        </article>`).join("");
      status.textContent = available.length ? `Updated ${new Date().toLocaleTimeString()}` : "No waveform data available";
  }

  if (demoToggle) {
    demoToggle.addEventListener("click", () => {
      demoEnabled = !demoEnabled;
      demoToggle.setAttribute("aria-pressed", String(demoEnabled));
      demoToggle.textContent = demoEnabled ? "Show live capture" : "Show demo drive";
      loadOrbits();
    });
  }

  scaleSelect.addEventListener("change", () => {
    updateScaleControls();
    if (lastRender) lastRender();
  });
  customScaleInput.addEventListener("change", () => {
    updateScaleControls();
    if (lastRender) lastRender();
  });

  loadScaleSetting();
  updateScaleControls();
  loadOrbits();
  window.setInterval(loadOrbits, 15000);
})();
