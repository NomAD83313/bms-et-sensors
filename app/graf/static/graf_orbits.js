(() => {
  const root = document.getElementById("orbitSensors");
  const status = document.getElementById("orbitStatus");
  const demoToggle = document.getElementById("orbitDemoToggle");
  const scaleSelect = document.getElementById("orbitScale");
  const customScaleWrap = document.getElementById("orbitCustomScaleWrap");
  const customScaleInput = document.getElementById("orbitCustomScale");
  const waveformRoot = document.getElementById("waveformSensors");
  if (!root || !status) return;

  const pairs = [["x", "y"], ["x", "z"], ["y", "z"]];
  let demoEnabled = false;
  let lastRender = null;
  const waveformScaleModes = { acc_g: "auto", velocity_mm_s: "auto", displacement_um: "auto" };
  const waveformCompareMinimums = { acc_g: 0.02, velocity_mm_s: 1, displacement_um: 50 };
  const waveformCompareLimits = {};

  function waveformScaleLimit(quantity, peak) {
    const required = Math.max(1e-9, Number.isFinite(peak) ? peak * 1.05 : 0);
    if (waveformScaleModes[quantity] === "auto") return required;
    let limit = waveformCompareLimits[quantity] || waveformCompareMinimums[quantity];
    while (limit < required) limit *= 2;
    waveformCompareLimits[quantity] = limit;
    return limit;
  }

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
    const axes = {
      x: { acc_g: [], velocity_mm_s: [], displacement_um: [] },
      y: { acc_g: [], velocity_mm_s: [], displacement_um: [] },
      z: { acc_g: [], velocity_mm_s: [], displacement_um: [] },
    };
    for (let index = 0; index < samples; index += 1) {
      const phase = (index / samples) * turns * Math.PI * 2;
      const deterministicNoise = Math.sin(index * 1.713) * 1.8 + Math.sin(index * 0.371) * 1.1;
      const x = Math.cos(phase) + 0.18 * Math.cos(2 * phase + 0.3);
      const y = 0.62 * Math.sin(phase + 0.22) + 0.14 * Math.sin(2 * phase);
      const z = 0.34 * Math.sin(phase + 1.1) + 0.1 * Math.sin(3 * phase);
      axes.x.acc_g.push(0.085 * x + deterministicNoise * 0.0005);
      axes.y.acc_g.push(0.085 * y + deterministicNoise * 0.0003);
      axes.z.acc_g.push(0.085 * z + deterministicNoise * 0.0002);
      axes.x.velocity_mm_s.push(3.8 * x);
      axes.y.velocity_mm_s.push(3.8 * y);
      axes.z.velocity_mm_s.push(3.8 * z);
      axes.x.displacement_um.push(74 * x + deterministicNoise);
      axes.y.displacement_um.push(74 * y + deterministicNoise * 0.6);
      axes.z.displacement_um.push(74 * z + deterministicNoise * 0.35);
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

  function waveformSvg(wave, quantity, label, unit) {
    const width = 1000;
    const height = 260;
    const pad = { left: 58, right: 18, top: 16, bottom: 30 };
    const series = ["x", "y", "z"].map((axis) => finiteValues(wave.axes?.[axis]?.[quantity]));
    const count = Math.min(...series.map((values) => values.length));
    const peak = Math.max(0, ...series.flatMap((values) => values.slice(0, count).map(Math.abs)));
    const range = waveformScaleLimit(quantity, peak);
    const duration = count > 1 ? (count - 1) / Number(wave.sample_rate_hz || 1) : 0;
    const sx = (index) => pad.left + (index / Math.max(1, count - 1)) * (width - pad.left - pad.right);
    const sy = (value) => pad.top + (1 - (value + range) / (2 * range)) * (height - pad.top - pad.bottom);
    const lines = series.map((values, axisIndex) => {
      const points = values.slice(0, count).map((value, index) => `${sx(index).toFixed(1)},${sy(value).toFixed(1)}`).join(" ");
      return `<polyline class="axis-${"xyz"[axisIndex]}" points="${points}" />`;
    }).join("");
    const xTicks = Array.from({ length: 6 }, (_, index) => {
      const fraction = index / 5;
      const x = pad.left + fraction * (width - pad.left - pad.right);
      const seconds = duration * fraction;
      return `<line class="wave-grid" x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}" />
        <text x="${x}" y="${height - 9}" text-anchor="middle">${seconds.toFixed(2)} s</text>`;
    }).join("");
    const yTicks = [-1, -0.5, 0, 0.5, 1].map((fraction) => {
      const y = sy(range * fraction);
      const value = range * fraction;
      return `<line class="wave-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${value.toPrecision(3)}</text>`;
    }).join("");
    return `
      <figure class="waveform-figure">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title">${label}, ${unit}<span class="waveform-legend"><span class="x">X</span><span class="y">Y</span><span class="z">Z</span></span></span>
          <span class="waveform-scale-controls" role="group" aria-label="${label} scale">
            <button type="button" data-wave-scale="compare" data-quantity="${quantity}" class="${waveformScaleModes[quantity] === "compare" ? "on" : ""}">Compare</button>
            <button type="button" data-wave-scale="auto" data-quantity="${quantity}" class="${waveformScaleModes[quantity] === "auto" ? "on" : ""}">Auto</button>
            <button type="button" data-wave-scale-reset="${quantity}" ${waveformScaleModes[quantity] === "auto" ? "disabled" : ""}>Reset scale</button>
          </span>
        </div>
        <svg class="waveform-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${label}">
          ${xTicks}${yTicks}
          ${lines}
        </svg>
      </figure>`;
  }

  function waveformPlots(wave) {
    return [
      waveformSvg(wave, "acc_g", "Acceleration", "g"),
      waveformSvg(wave, "velocity_mm_s", "Velocity", "mm/s"),
      waveformSvg(wave, "displacement_um", "Displacement", "µm"),
    ].join("");
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
      if (waveformRoot) waveformRoot.innerHTML = `<article class="waveform-sensor"><div class="orbit-sensor-head"><strong>Demo drive · simulated unbalance</strong></div><div class="waveform-plots">${waveformPlots(wave)}</div></article>`;
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
        if (waveformRoot) waveformRoot.innerHTML = "";
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
      if (waveformRoot) waveformRoot.innerHTML = available.map(({ sensor, wave }) => `
        <article class="waveform-sensor">
          <div class="orbit-sensor-head"><strong>${escapeHtml(sensor.name || `Sensor ${sensor.address}`)}</strong><span class="orbit-status">${escapeHtml(capturedLabel(wave.captured_at))}</span></div>
          <div class="waveform-plots">${waveformPlots(wave)}</div>
        </article>`).join("");
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
  if (waveformRoot) waveformRoot.addEventListener("click", (event) => {
    const scaleButton = event.target.closest("button[data-wave-scale]");
    if (scaleButton) {
      const quantity = scaleButton.dataset.quantity;
      if (quantity in waveformScaleModes) waveformScaleModes[quantity] = scaleButton.dataset.waveScale;
      if (lastRender) lastRender();
      return;
    }
    const resetButton = event.target.closest("button[data-wave-scale-reset]");
    if (resetButton) {
      const quantity = resetButton.dataset.waveScaleReset;
      delete waveformCompareLimits[quantity];
      if (lastRender) lastRender();
    }
  });

  loadScaleSetting();
  updateScaleControls();
  loadOrbits();
  window.setInterval(loadOrbits, 15000);
})();
