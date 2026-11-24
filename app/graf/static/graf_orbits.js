(() => {
  const root = document.getElementById("orbitSensors");
  const status = document.getElementById("orbitStatus");
  const demoToggle = document.getElementById("orbitDemoToggle");
  const scaleSelect = document.getElementById("orbitScale");
  const customScaleWrap = document.getElementById("orbitCustomScaleWrap");
  const customScaleInput = document.getElementById("orbitCustomScale");
  const waveformRoot = document.getElementById("waveformSensors");
  const recordSensor = document.getElementById("recordSensor");
  const recordAverages = document.getElementById("recordAverages");
  const recordNote = document.getElementById("recordNote");
  const recordButton = document.getElementById("recordTimeSignal");
  const recordStatus = document.getElementById("recordStatus");
  const recordsBody = document.getElementById("recordsTableBody");
  const savedFftRoot = document.getElementById("savedRecordFft");
  const recordAutoInterval = document.getElementById("recordAutoInterval");
  const recordRetentionDays = document.getElementById("recordRetentionDays");
  const recordMaxCount = document.getElementById("recordMaxCount");
  const recordAutoStart = document.getElementById("recordAutoStart");
  const recordAutoStop = document.getElementById("recordAutoStop");
  const recordAutoStatus = document.getElementById("recordAutoStatus");
  const recordsSelectAll = document.getElementById("recordsSelectAll");
  const recordsDeleteSelected = document.getElementById("recordsDeleteSelected");
  const recordsSelectionStatus = document.getElementById("recordsSelectionStatus");
  const burstDuration = document.getElementById("burstDuration");
  const burstNote = document.getElementById("burstNote");
  const burstStart = document.getElementById("burstStart");
  const burstStop = document.getElementById("burstStop");
  const burstStatus = document.getElementById("burstStatus");
  const orbitLiveButton = document.getElementById("orbitLiveButton");
  if (!root || !status) return;

  const pairs = [["x", "y"], ["x", "z"], ["y", "z"]];
  let demoEnabled = false;
  let lastRender = null;
  // A saved record or capture is shown in the waveform/orbit panels: live refresh pauses.
  let viewingRecord = false;
  const captureOperations = ["fft", "record", "burst"];
  const waveformScaleModes = { acc_g: "auto", velocity_mm_s: "auto", displacement_um: "auto" };
  const waveformCompareMinimums = { acc_g: 0.02, velocity_mm_s: 1, displacement_um: 50 };
  const waveformFixedLimits = { ...waveformCompareMinimums };
  const waveformCompareLimits = {};
  let recorderSensors = [];
  // Simulated sensors from /vibration/api/status (address, name, label, active, line speed …).
  let simSensors = [];
  const simAt = (address) => simSensors.find((item) => Number(item.address) === Number(address)) || null;
  let simSwitching = false;
  const recordSensorInfo = document.getElementById("recordSensorInfo");
  let latestRecordId = null;
  let fftScaleMode = "auto";
  let fftFixedMaxG = 0.01;
  let fftCompareMaxG = null;
  let currentRecordIds = [];
  const selectedRecordIds = new Set();

  try {
    const savedFftMode = localStorage.getItem("graf.vibration.fftScaleMode");
    if (["auto", "compare", "fixed"].includes(savedFftMode)) fftScaleMode = savedFftMode;
    const savedFftMax = Number(localStorage.getItem("graf.vibration.fftFixedMaxG"));
    if (Number.isFinite(savedFftMax) && savedFftMax > 0) fftFixedMaxG = savedFftMax;
    Object.keys(waveformScaleModes).forEach((quantity) => {
      const mode = localStorage.getItem(`graf.vibration.waveScaleMode.${quantity}`);
      if (["auto", "compare", "fixed"].includes(mode)) waveformScaleModes[quantity] = mode;
      const fixed = Number(localStorage.getItem(`graf.vibration.waveFixedMax.${quantity}`));
      if (Number.isFinite(fixed) && fixed > 0) waveformFixedLimits[quantity] = fixed;
    });
  } catch (_) {}

  function waveformScaleLimit(quantity, peak) {
    const required = Math.max(1e-9, Number.isFinite(peak) ? peak * 1.05 : 0);
    if (waveformScaleModes[quantity] === "auto") return required;
    if (waveformScaleModes[quantity] === "fixed") return waveformFixedLimits[quantity];
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

  // Waves shown in the charts, by key, for the mouse-over tooltip (graf_hover.js).
  const waveKeys = new WeakMap();
  const wavesByKey = new Map();
  let waveKeySeq = 0;
  function waveKey(wave) {
    if (!waveKeys.has(wave)) {
      const key = String(++waveKeySeq);
      waveKeys.set(wave, key);
      wavesByKey.set(key, wave);
      if (wavesByKey.size > 40) wavesByKey.delete(wavesByKey.keys().next().value);
    }
    return waveKeys.get(wave);
  }
  const ORBIT_SIZE = 260;
  const ORBIT_PAD = 28;
  const WAVE_WIDTH = 1000;
  const WAVE_PAD = { left: 58, right: 18, top: 16, bottom: 30 };
  const quantityUnits = { acc_g: ["Acceleration", "g"], velocity_mm_s: ["Velocity", "mm/s"], displacement_um: ["Displacement", "µm"] };

  function finiteValues(values) {
    return (Array.isArray(values) ? values : []).map(Number).filter(Number.isFinite);
  }

  function orbitSvg(wave, axisA, axisB) {
    const size = ORBIT_SIZE;
    const pad = ORBIT_PAD;
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
        <svg class="orbit-chart" viewBox="0 0 ${size} ${size}" role="img" aria-label="Orbit ${pair.toUpperCase()}" data-hover-chart="orbit" data-wave-key="${waveKey(wave)}" data-pair="${pair}" data-range="${range}">
          <line class="orbit-grid" x1="${size / 2}" x2="${size / 2}" y1="${pad}" y2="${size - pad}" />
          <line class="orbit-grid" x1="${pad}" x2="${size - pad}" y1="${size / 2}" y2="${size / 2}" />
          <polyline class="orbit-path" points="${points}" />
          <text x="${size - pad}" y="${size / 2 - 5}" text-anchor="end">${axisA.toUpperCase()}</text>
          <text x="${size / 2 + 5}" y="${pad + 10}">${axisB.toUpperCase()}</text>
          <text x="${pad}" y="${size - 8}">±${range.toPrecision(3)} µm</text>
          <circle class="hover-dot" r="1.75" />
        </svg>
        <figcaption><strong>${pair.toUpperCase()}</strong> · ratio ${ratioLabel}</figcaption>
      </figure>`;
  }

  function waveformSvg(wave, quantity, label, unit) {
    const width = WAVE_WIDTH;
    const height = 260;
    const pad = WAVE_PAD;
    const series = ["x", "y", "z"].map((axis) => finiteValues(wave.axes?.[axis]?.[quantity]));
    const count = Math.min(...series.map((values) => values.length));
    const peak = Math.max(0, ...series.flatMap((values) => values.slice(0, count).map(Math.abs)));
    const range = waveformScaleLimit(quantity, peak);
    const duration = count > 1 ? (count - 1) / Number(wave.sample_rate_hz || 1) : 0;
    const sx = (index) => pad.left + (index / Math.max(1, count - 1)) * (width - pad.left - pad.right);
    const sy = (value) => {
      const clipped = Math.max(-range, Math.min(range, value));
      return pad.top + (1 - (clipped + range) / (2 * range)) * (height - pad.top - pad.bottom);
    };
    const lines = series.map((values, axisIndex) => {
      const points = values.slice(0, count).map((value, index) => `${sx(index).toFixed(1)},${sy(value).toFixed(1)}`).join(" ");
      return `<polyline class="axis-${"xyz"[axisIndex]}" points="${points}" />`;
    }).join("");
    const xTicks = Array.from({ length: 6 }, (_, index) => {
      const fraction = index / 5;
      const x = pad.left + fraction * (width - pad.left - pad.right);
      const seconds = duration * fraction;
      // The first label is the absolute time of the first sample when it is known.
      const absolute = index === 0 ? window.grafHover?.clock(wave.started_at, 0, true) : null;
      return `<line class="wave-grid" x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}" />
        ${absolute ? `<text x="${x}" y="${height - 9}" text-anchor="start">${absolute}</text>` : `<text x="${x}" y="${height - 9}" text-anchor="middle">${seconds.toFixed(2)} s</text>`}`;
    }).join("");
    const yTicks = [-1, -0.5, 0, 0.5, 1].map((fraction) => {
      const y = sy(range * fraction);
      const value = range * fraction;
      return `<line class="wave-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${value.toPrecision(3)}</text>`;
    }).join("");
    const descriptions = {
      acc_g: "Signed acceleration with gravity/DC mean removed",
      velocity_mm_s: `Signed velocity integrated from acceleration above ${Number(wave.integration_min_hz || 2).toFixed(0)} Hz`,
      displacement_um: `Signed displacement integrated above ${Number(wave.integration_min_hz || 2).toFixed(0)} Hz`,
    };
    return `
      <figure class="waveform-figure">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title"><span>${label}, ${unit}<span class="waveform-legend"><span class="x">X axis · green</span><span class="y">Y axis · red</span><span class="z">Z axis · white</span></span></span>
            <span class="orbit-status">${descriptions[quantity]} · X: elapsed time (s) · Y: signed ${unit}</span>
          </span>
          <span class="waveform-scale-controls" role="group" aria-label="${label} scale">
            <label>Y scale <select data-wave-scale-mode="${quantity}"><option value="auto" ${waveformScaleModes[quantity] === "auto" ? "selected" : ""}>Auto</option><option value="compare" ${waveformScaleModes[quantity] === "compare" ? "selected" : ""}>Compare</option><option value="fixed" ${waveformScaleModes[quantity] === "fixed" ? "selected" : ""}>Fixed</option></select></label>
            <label ${waveformScaleModes[quantity] === "fixed" ? "" : "hidden"}>Max, ${unit} <input data-wave-fixed-max="${quantity}" type="number" min="0.000001" step="any" value="${waveformFixedLimits[quantity]}" /></label>
            <button type="button" data-wave-scale-reset="${quantity}" ${waveformScaleModes[quantity] === "compare" ? "" : "disabled"}>Reset scale</button>
          </span>
        </div>
        <svg class="waveform-chart" viewBox="0 0 ${width} ${height}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="${label}" data-hover-chart="wave" data-wave-key="${waveKey(wave)}" data-quantity="${quantity}" data-range="${range}">
          ${xTicks}${yTicks}
          ${lines}
          <line class="hover-line" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}" />
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

  function updateRecordHardware() {
    if (!recordSensor) return;
    const selected = recorderSensors.find((sensor) => String(sensor.address) === recordSensor.value);
    const rate = Number(selected?.fft?.sample_rate_hz || 246.36);
    const samples = 1024;
    document.getElementById("recordSampleRate").textContent = `${rate.toFixed(2)} Hz · sensor controlled`;
    document.getElementById("recordDuration").textContent = `${(samples / rate).toFixed(3)} s`;
    document.getElementById("recordResolution").textContent = `${(rate / samples).toFixed(3)} Hz`;
    document.getElementById("recordNyquist").textContent = `${(rate / 2).toFixed(2)} Hz`;
  }

  function syncRecorderSensors(payload) {
    if (!recordSensor) return;
    const selected = recordSensor.value;
    recorderSensors = (payload.sensors || []).filter((sensor) => sensor.enabled);
    simSensors = Array.isArray(payload.sim_sensors) ? payload.sim_sensors : [];
    // Simulated sensors are always offered: selecting one starts it, selecting another sensor stops it.
    const options = recorderSensors.map((sensor) => {
      const simulated = simAt(sensor.address);
      return { value: Number(sensor.address), label: simulated ? `${sensor.name} · ${simulated.label} (running)` : (sensor.name || `Sensor ${sensor.address}`) };
    });
    simSensors.filter((item) => !item.active).forEach((item) => options.push({ value: Number(item.address), label: `${item.name} · ${item.label} (off)` }));
    if (!simSwitching) {
      recordSensor.innerHTML = options.map((item) => `<option value="${item.value}">${escapeHtml(item.label)}</option>`).join("");
      const running = simSensors.find((item) => item.active);
      if (options.some((item) => String(item.value) === selected)) recordSensor.value = selected;
      else if (running) recordSensor.value = String(running.address);
    }
    updateSimInfo();
    const operation = payload.operation || {};
    const capturing = Boolean(operation.running && captureOperations.includes(operation.name));
    setCaptureButtons(Boolean(operation.running));
    if (burstStop) burstStop.disabled = !capturing;
    if (burstStatus) {
      burstStatus.textContent = capturing && operation.name === "burst"
        ? `Recording: ${operation.progress || "starting…"} · polling paused`
        : "Back-to-back FIFO captures · polling paused · short gaps between captures";
    }
    const config = payload.config || {};
    const syncInput = (element, value) => {
      if (element && document.activeElement !== element && value !== undefined) element.value = String(value);
    };
    syncInput(recordAutoInterval, config.fft_interval_sec);
    syncInput(recordRetentionDays, config.record_retention_days);
    syncInput(recordMaxCount, config.record_max_count);
    if (recordAverages && document.activeElement !== recordAverages && config.fft_averages) {
      recordAverages.value = String(config.fft_averages);
    }
    const periodic = Boolean(config.fft_periodic);
    const automatic = Boolean(periodic && config.fft_save_records);
    if (recordAutoStart) recordAutoStart.disabled = automatic || recorderSensors.length === 0;
    if (recordAutoStop) recordAutoStop.disabled = !periodic;
    if (recordAutoStatus) {
      const nextValues = recorderSensors
        .filter((sensor) => sensor.fft_next_in_sec !== null && sensor.fft_next_in_sec !== undefined)
        .map((sensor) => Number(sensor.fft_next_in_sec)).filter(Number.isFinite);
      const next = nextValues.length ? ` · next in ${Math.min(...nextValues).toFixed(0)} s` : "";
      recordAutoStatus.textContent = automatic
        ? `Automatic recording every ${Number(config.fft_interval_sec).toFixed(0)} s${next}`
        : periodic
          ? `Periodic FFT running every ${Number(config.fft_interval_sec).toFixed(0)} s · record saving is off`
          : "Automatic recording stopped";
    }
    updateRecordHardware();
  }

  function fftSvg(spectrum, quantity = "acc_g") {
    const width = 1000;
    const height = 300;
    const pad = { left: 62, right: 18, top: 16, bottom: 34 };
    const series = ["x", "y", "z"].map((axis) => finiteValues(spectrum.axes?.[axis]?.[quantity]));
    const count = Math.min(...series.map((values) => values.length));
    const dataPeak = Math.max(1e-12, ...series.flatMap((values) => values.slice(0, count)));
    let range = dataPeak * 1.05;
    if (fftScaleMode === "compare") {
      fftCompareMaxG = Math.max(fftCompareMaxG || 0, range);
      range = fftCompareMaxG;
    } else if (fftScaleMode === "fixed") {
      range = fftFixedMaxG;
    }
    const nyquist = Number(spectrum.nyquist_hz || 0);
    const sx = (index) => pad.left + (index / Math.max(1, count - 1)) * (width - pad.left - pad.right);
    const sy = (value) => pad.top + (1 - Math.min(value, range) / range) * (height - pad.top - pad.bottom);
    const lines = series.map((values, axisIndex) => {
      const points = values.slice(0, count).map((value, index) => `${sx(index).toFixed(1)},${sy(value).toFixed(1)}`).join(" ");
      return `<polyline class="axis-${"xyz"[axisIndex]}" points="${points}" />`;
    }).join("");
    const xTicks = Array.from({ length: 6 }, (_, index) => {
      const fraction = index / 5;
      const x = pad.left + fraction * (width - pad.left - pad.right);
      return `<line class="fft-grid" x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}" />
        <text x="${x}" y="${height - 10}" text-anchor="middle">${(nyquist * fraction).toFixed(1)} Hz</text>`;
    }).join("");
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
      const y = sy(range * fraction);
      return `<line class="fft-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${(range * fraction).toPrecision(3)}</text>`;
    }).join("");
    return `<svg class="saved-fft-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="Acceleration amplitude spectrum" data-range="${range}">
      ${xTicks}${yTicks}${lines}<line class="fft-hover" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}" />
      <text x="${(pad.left + width - pad.right) / 2}" y="${height - 1}" text-anchor="middle">Frequency, Hz</text>
      <text x="14" y="${height / 2}" text-anchor="middle" transform="rotate(-90 14 ${height / 2})">Amplitude, g</text>
    </svg>`;
  }

  function bindFftScaleControls(record) {
    const mode = savedFftRoot?.querySelector("[data-fft-scale-mode]");
    const fixed = savedFftRoot?.querySelector("[data-fft-fixed-max]");
    const reset = savedFftRoot?.querySelector("[data-fft-scale-reset]");
    if (!mode || !fixed || !reset) return;
    mode.addEventListener("change", () => {
      fftScaleMode = mode.value;
      try { localStorage.setItem("graf.vibration.fftScaleMode", fftScaleMode); } catch (_) {}
      renderSavedFft(record);
    });
    fixed.addEventListener("change", () => {
      const value = Number(fixed.value);
      if (!Number.isFinite(value) || value <= 0) return;
      fftFixedMaxG = value;
      try { localStorage.setItem("graf.vibration.fftFixedMaxG", String(value)); } catch (_) {}
      renderSavedFft(record);
    });
    reset.addEventListener("click", () => {
      fftCompareMaxG = null;
      renderSavedFft(record);
    });
  }

  function bindFftHover(record) {
    const svg = savedFftRoot?.querySelector(".saved-fft-chart");
    const tooltip = savedFftRoot?.querySelector(".chart-tooltip");
    const hoverLine = svg?.querySelector(".fft-hover");
    if (!svg || !tooltip || !hoverLine) return;
    const spectrum = record.spectrum || {};
    const arrays = ["x", "y", "z"].map((axis) => finiteValues(spectrum.axes?.[axis]?.acc_g));
    const count = Math.min(...arrays.map((values) => values.length));
    const df = Number(spectrum.df_hz || 0);
    const padLeft = 62;
    const plotWidth = 1000 - padLeft - 18;
    const colors = ["#34d399", "#f87171", "#e5e7eb"];
    const formatValue = (value) => Math.abs(value) < 0.001 ? value.toExponential(3) : value.toFixed(6);
    svg.addEventListener("mousemove", (event) => {
      const rect = svg.getBoundingClientRect();
      const viewX = ((event.clientX - rect.left) / rect.width) * 1000;
      const fraction = Math.max(0, Math.min(1, (viewX - padLeft) / plotWidth));
      const index = Math.round(fraction * Math.max(0, count - 1));
      const markerX = padLeft + (index / Math.max(1, count - 1)) * plotWidth;
      hoverLine.setAttribute("x1", markerX.toFixed(1));
      hoverLine.setAttribute("x2", markerX.toFixed(1));
      hoverLine.style.display = "block";
      const range = Number(svg.dataset.range) || 1;
      const sy = (value) => 16 + (1 - Math.min(value, range) / range) * (300 - 16 - 34);
      window.grafHover?.setPoints(svg, arrays.map((values, axis) => ({ x: markerX, y: sy(values[index] || 0), axis: "xyz"[axis] })));
      tooltip.innerHTML = `<div class="tt-time">FFT bin ${index} · ${(index * df).toFixed(3)} Hz</div>` +
        arrays.map((values, axisIndex) => `<div class="tt-row"><span class="tt-dot" style="background:${colors[axisIndex]}"></span>` +
          `<span>${"XYZ"[axisIndex]} acceleration amplitude: ${formatValue(values[index] || 0)} g</span></div>`).join("");
      tooltip.style.display = "block";
      const localX = event.clientX - savedFftRoot.getBoundingClientRect().left;
      const tooltipWidth = tooltip.offsetWidth || 280;
      let left = localX + 12;
      if (left + tooltipWidth > savedFftRoot.clientWidth - 4) left = localX - tooltipWidth - 12;
      tooltip.style.left = `${Math.max(4, left)}px`;
      tooltip.style.top = `${Math.max(36, svg.offsetTop + 12)}px`;
    });
    svg.addEventListener("mouseleave", () => {
      hoverLine.style.display = "none";
      const points = svg.querySelector(".hover-points");
      if (points) points.style.display = "none";
      tooltip.style.display = "none";
    });
  }

  function renderSavedFft(record) {
    if (!savedFftRoot) return;
    const spectrum = record.spectrum || {};
    savedFftRoot.innerHTML = `
      <div class="waveform-chart-head">
        <span class="waveform-chart-title"><span>FFT acceleration amplitude spectrum · ${escapeHtml(record.sensor || "Sensor")}${record.fftLabel ? ` · ${escapeHtml(record.fftLabel)}` : ""}
          <span class="waveform-legend"><span class="x">X axis · green</span><span class="y">Y axis · red</span><span class="z">Z axis · white</span></span></span>
        </span>
        <span class="orbit-status">Captured ${escapeHtml(capturedLabel(record.captured_at))} · Y: single-sided acceleration amplitude (g) · X: frequency (Hz) · Δf ${Number(spectrum.df_hz || 0).toFixed(3)} Hz · Nyquist ${Number(spectrum.nyquist_hz || 0).toFixed(2)} Hz · ${escapeHtml(record.fftNote || `${Number(record.averages || 1)} average(s)`)}</span>
        <span class="fft-scale-controls">
          <label>Y scale <select data-fft-scale-mode><option value="auto" ${fftScaleMode === "auto" ? "selected" : ""}>Auto</option><option value="compare" ${fftScaleMode === "compare" ? "selected" : ""}>Compare</option><option value="fixed" ${fftScaleMode === "fixed" ? "selected" : ""}>Fixed</option></select></label>
          <label class="fft-fixed-scale" ${fftScaleMode === "fixed" ? "" : "hidden"}>Max, g <input data-fft-fixed-max type="number" min="0.000001" step="any" value="${fftFixedMaxG}" /></label>
          <button type="button" data-fft-scale-reset ${fftScaleMode === "compare" ? "" : "disabled"}>Reset scale</button>
        </span>
      </div>${fftSvg(spectrum)}<div class="chart-tooltip" role="status"></div>`;
    bindFftScaleControls(record);
    bindFftHover(record);
  }

  // pin: the user opened the record, so it also replaces the live waveform/orbit panels.
  async function showRecord(recordId, scrollIntoView = true, pin = true) {
    const response = await fetch(`/vibration/api/records/${encodeURIComponent(recordId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`record HTTP ${response.status}`);
    const record = await response.json();
    const continuous = record.kind === "burst";
    const label = continuous ? "continuous record · last capture" : "saved record";
    if (pin) showWave({ name: `${record.sensor || "Sensor"} · ${label}`, address: record.address }, record.waveform);
    renderSavedFft(continuous ? { ...record, fftLabel: "average of all captures", fftNote: `${Number(record.capture_count || 1)} captures averaged` } : record);
    if (window.grafBurst) window.grafBurst.show(record, pin);
    if (scrollIntoView) savedFftRoot.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function showWave(sensor, wave) {
    viewingRecord = true;
    if (orbitLiveButton) orbitLiveButton.hidden = false;
    renderAvailable([{ sensor, wave }]);
    lastRender = () => renderAvailable([{ sensor, wave }]);
    status.textContent = "Saved record shown · live refresh paused";
  }

  // Used by graf_vibration_burst.js for one capture of a continuous record.
  window.grafVibration = {
    showWave,
    showSpectrum(record, spectrum, label, note) {
      renderSavedFft({ ...record, spectrum, fftLabel: label, fftNote: note });
    },
  };

  function renderRecords(records) {
    if (!recordsBody) return;
    currentRecordIds = records.map((record) => String(record.id));
    for (const itemId of selectedRecordIds) {
      if (!currentRecordIds.includes(itemId)) selectedRecordIds.delete(itemId);
    }
    if (!records.length) {
      recordsBody.innerHTML = `<tr><td colspan="10">No saved time signals yet.</td></tr>`;
      updateRecordSelectionControls();
      return;
    }
    recordsBody.innerHTML = records.map((record) => {
      const id = encodeURIComponent(record.id);
      const continuous = record.kind === "burst";
      return `<tr>
        <td><input type="checkbox" data-record-select="${id}" aria-label="Select record ${escapeHtml(capturedLabel(record.captured_at))}" ${selectedRecordIds.has(String(record.id)) ? "checked" : ""} /></td>
        <td>${escapeHtml(capturedLabel(record.captured_at))}</td><td>${escapeHtml(record.sensor || String(record.address))}</td>
        <td>${continuous ? "Continuous" : record.periodic ? "Automatic" : "Manual"}</td>
        <td>${Number(record.samples || 0) * (continuous ? Number(record.capture_count || 1) : 1)}</td><td>${Number(record.sample_rate_hz || 0).toFixed(2)} Hz</td>
        <td>${continuous ? `${Number(record.span_s || 0).toFixed(1)} s · ${(100 * Number(record.coverage || 0)).toFixed(0)} %` : `${Number(record.duration_s || 0).toFixed(3)} s`}</td><td>${Number(record.averages || 1)}</td>
        <td class="note-cell">${escapeHtml(record.note || "–")}</td>
        <td><span class="record-actions"><button type="button" data-record-view="${id}">${continuous ? "View timeline + FFT" : "View + FFT"}</button>
          <button type="button" data-record-csv="${id}" data-rate="${Number(record.sample_rate_hz)}">CSV</button>
          <a href="/vibration/api/records/${id}/download.json">JSON</a>
          <button type="button" data-record-delete="${id}">Delete</button></span></td></tr>`;
    }).join("");
    updateRecordSelectionControls();
  }

  function updateRecordSelectionControls() {
    const selected = selectedRecordIds.size;
    if (recordsDeleteSelected) recordsDeleteSelected.disabled = selected === 0;
    if (recordsSelectionStatus) recordsSelectionStatus.textContent = `${selected} selected`;
    if (recordsSelectAll) {
      recordsSelectAll.checked = currentRecordIds.length > 0 && selected === currentRecordIds.length;
      recordsSelectAll.indeterminate = selected > 0 && selected < currentRecordIds.length;
    }
  }

  async function loadRecords() {
    if (!recordsBody) return;
    try {
      const response = await fetch("/vibration/api/records", { cache: "no-store" });
      if (!response.ok) throw new Error(`records HTTP ${response.status}`);
      const records = (await response.json()).records || [];
      renderRecords(records);
      if (records[0]?.id && records[0].id !== latestRecordId) {
        latestRecordId = records[0].id;
        // A new record is shown automatically unless the user is looking at another one.
        if (!viewingRecord) await showRecord(latestRecordId, false, false);
      }
    } catch (error) {
      recordsBody.innerHTML = `<tr><td colspan="10">Records unavailable: ${escapeHtml(error.message || String(error))}</td></tr>`;
    }
  }

  async function waitForRecord() {
    const response = await fetch("/vibration/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error(`status HTTP ${response.status}`);
    const payload = await response.json();
    syncRecorderSensors(payload);
    const operation = payload.operation || {};
    if (operation.running) {
      recordStatus.textContent = operation.progress || "Capturing…";
      window.setTimeout(() => waitForRecord().catch(finishRecordError), 500);
      return;
    }
    setCaptureButtons(false);
    if (operation.error) throw new Error(operation.error);
    const result = operation.result || {};
    recordStatus.textContent = !result.record ? "Capture finished"
      : result.capture_error ? `Record saved · ended early: ${result.capture_error}`
        : result.stopped ? "Record saved · stopped" : "Record saved";
    await loadRecords();
    if (operation.result?.record?.id) await showRecord(operation.result.record.id);
  }

  function finishRecordError(error) {
    setCaptureButtons(false);
    recordStatus.textContent = `Recording failed: ${error.message || error}`;
  }

  function setCaptureButtons(running) {
    if (recordButton) recordButton.disabled = running || recorderSensors.length === 0;
    if (burstStart) burstStart.disabled = running || recorderSensors.length === 0;
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
      syncRecorderSensors(payload);
      if (viewingRecord) return;
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

  if (window.grafHover) {
    const hover = window.grafHover;
    hover.register("wave", (svg, event) => {
      const wave = wavesByKey.get(svg.dataset.waveKey);
      const point = hover.svgPoint(svg, event);
      if (!wave || !point) return null;
      const quantity = svg.dataset.quantity;
      const series = ["x", "y", "z"].map((axis) => finiteValues(wave.axes?.[axis]?.[quantity]));
      const count = Math.min(...series.map((values) => values.length));
      const plot = WAVE_WIDTH - WAVE_PAD.left - WAVE_PAD.right;
      if (count < 1 || point.x < WAVE_PAD.left - 4 || point.x > WAVE_WIDTH - WAVE_PAD.right + 4) return null;
      const index = Math.max(0, Math.min(count - 1, Math.round(((point.x - WAVE_PAD.left) / plot) * (count - 1))));
      const x = WAVE_PAD.left + (index / Math.max(1, count - 1)) * plot;
      hover.setLine(svg, x);
      const range = Number(svg.dataset.range) || 1;
      const sy = (value) => WAVE_PAD.top + (1 - (Math.max(-range, Math.min(range, value)) + range) / (2 * range)) * (260 - WAVE_PAD.top - WAVE_PAD.bottom);
      hover.setPoints(svg, series.map((values, axis) => ({ x, y: sy(values[index]), axis: "xyz"[axis] })));
      const rate = Number(wave.sample_rate_hz || 1);
      const [label, unit] = quantityUnits[quantity];
      const at = hover.clock(wave.started_at, index / rate);
      return hover.title(`Sample ${index} · ${(index / rate).toFixed(3)} s${at ? ` · ${at}` : ""}`) +
        series.map((values, axis) => hover.row("xyz"[axis], `${"XYZ"[axis]} ${label.toLowerCase()}: ${hover.number(values[index], unit)}`)).join("");
    });
    hover.register("orbit", (svg, event) => {
      const wave = wavesByKey.get(svg.dataset.waveKey);
      const point = hover.svgPoint(svg, event);
      if (!wave || !point) return null;
      const [axisA, axisB] = svg.dataset.pair.split("");
      const xs = finiteValues(wave.axes?.[axisA]?.displacement_um);
      const ys = finiteValues(wave.axes?.[axisB]?.displacement_um);
      const count = Math.min(xs.length, ys.length);
      const range = Number(svg.dataset.range);
      if (!count || !(range > 0)) return null;
      const scale = (value) => ORBIT_PAD + ((value + range) / (2 * range)) * (ORBIT_SIZE - 2 * ORBIT_PAD);
      let best = 0;
      let bestDistance = Infinity;
      for (let index = 0; index < count; index += 1) {
        const dx = scale(xs[index]) - point.x;
        const dy = ORBIT_SIZE - scale(ys[index]) - point.y;
        const distance = dx * dx + dy * dy;
        if (distance < bestDistance) { bestDistance = distance; best = index; }
      }
      hover.setDot(svg, scale(xs[best]), ORBIT_SIZE - scale(ys[best]));
      const rate = Number(wave.sample_rate_hz || 1);
      const ratio = Number(wave.orbits?.[`${axisA}${axisB}`]?.overall?.ratio);
      return hover.title(`Nearest point · sample ${best} · ${(best / rate).toFixed(3)} s`) +
        hover.row(axisA, `${axisA.toUpperCase()} displacement: ${hover.number(xs[best], "µm")}`) +
        hover.row(axisB, `${axisB.toUpperCase()} displacement: ${hover.number(ys[best], "µm")}`) +
        `<div>Axis ratio ${Number.isFinite(ratio) ? ratio.toFixed(2) : "–"} (0 = line, 1 = circle)</div>`;
    });
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
  if (waveformRoot) waveformRoot.addEventListener("change", (event) => {
    const modeSelect = event.target.closest("select[data-wave-scale-mode]");
    if (modeSelect) {
      const quantity = modeSelect.dataset.waveScaleMode;
      if (quantity in waveformScaleModes) waveformScaleModes[quantity] = modeSelect.value;
      try { localStorage.setItem(`graf.vibration.waveScaleMode.${quantity}`, modeSelect.value); } catch (_) {}
      if (lastRender) lastRender();
      return;
    }
    const fixedInput = event.target.closest("input[data-wave-fixed-max]");
    if (fixedInput) {
      const quantity = fixedInput.dataset.waveFixedMax;
      const value = Number(fixedInput.value);
      if (quantity in waveformFixedLimits && Number.isFinite(value) && value > 0) {
        waveformFixedLimits[quantity] = value;
        try { localStorage.setItem(`graf.vibration.waveFixedMax.${quantity}`, String(value)); } catch (_) {}
        if (lastRender) lastRender();
      }
    }
  });
  if (waveformRoot) waveformRoot.addEventListener("click", (event) => {
    const resetButton = event.target.closest("button[data-wave-scale-reset]");
    if (resetButton) {
      const quantity = resetButton.dataset.waveScaleReset;
      delete waveformCompareLimits[quantity];
      if (lastRender) lastRender();
    }
  });

  function updateSimInfo() {
    if (!recordSensorInfo) return;
    const selectedSim = recordSensor ? simAt(recordSensor.value) : null;
    if (!selectedSim) {
      recordSensorInfo.textContent = "";
      recordSensorInfo.title = "";
      return;
    }
    const bearing = selectedSim.bearing_defect ? ` · bearing defect at BPFO ${Number(selectedSim.bpfo_hz).toFixed(1)} Hz` : " · no defects";
    recordSensorInfo.textContent = selectedSim.active
      ? `Simulated · line ${Number(selectedSim.line_speed_m_min).toFixed(0)} m/min · roll ${Number(selectedSim.roll_hz).toFixed(2)} Hz${bearing}`
      : "Simulated sensor is off";
    recordSensorInfo.title = selectedSim.description || "";
  }

  async function switchSimSensor(address, active) {
    simSwitching = true;
    recordStatus.textContent = active ? "Starting simulated sensor…" : "Stopping simulated sensor…";
    try {
      const response = await fetch("/vibration/api/sim-sensor", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ address: Number(address), active }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      simSensors = payload.sim_sensors || simSensors;
      recordStatus.textContent = active ? `${payload.sim_sensor.name} running` : "Simulated sensor stopped";
    } catch (error) {
      recordStatus.textContent = `Simulated sensor: ${error.message || error}`;
    } finally {
      simSwitching = false;
      await loadOrbits();
    }
  }

  if (recordSensor) recordSensor.addEventListener("change", () => {
    updateRecordHardware();
    updateSimInfo();
    const chosen = simAt(recordSensor.value);
    const running = simSensors.find((item) => item.active);
    // Starting one simulator stops the other on the server; a real sensor stops the running one.
    if (chosen && !chosen.active) switchSimSensor(chosen.address, true);
    else if (!chosen && running) switchSimSensor(running.address, false);
  });
  async function updateAutomaticRecording(enabled) {
    const body = { fft_periodic: enabled, fft_save_records: enabled };
    if (enabled) {
      body.fft_interval_sec = Number(recordAutoInterval.value);
      body.fft_averages = Number(recordAverages.value);
      body.record_retention_days = Number(recordRetentionDays.value);
      body.record_max_count = Number(recordMaxCount.value);
    }
    const response = await fetch("/vibration/api/config", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    await loadOrbits();
  }
  if (recordAutoStart) recordAutoStart.addEventListener("click", async () => {
    recordAutoStatus.textContent = "Starting automatic recording…";
    try {
      await updateAutomaticRecording(true);
    } catch (error) {
      recordAutoStatus.textContent = `Start failed: ${error.message || error}`;
    }
  });
  if (recordAutoStop) recordAutoStop.addEventListener("click", async () => {
    recordAutoStatus.textContent = "Stopping automatic recording…";
    try {
      await updateAutomaticRecording(false);
    } catch (error) {
      recordAutoStatus.textContent = `Stop failed: ${error.message || error}`;
    }
  });
  if (recordButton) recordButton.addEventListener("click", async () => {
    const address = Number(recordSensor.value);
    if (!Number.isInteger(address)) return;
    setCaptureButtons(true);
    recordStatus.textContent = "Starting capture…";
    try {
      const response = await fetch(`/vibration/api/sensors/${address}/records`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ averages: Number(recordAverages.value), note: recordNote.value.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      await waitForRecord();
    } catch (error) {
      finishRecordError(error);
    }
  });
  if (burstStart) burstStart.addEventListener("click", async () => {
    const address = Number(recordSensor.value);
    const duration = Math.round(Number(burstDuration.value));
    if (!Number.isInteger(address)) return;
    if (!Number.isFinite(duration) || duration < 10 || duration > 600) {
      recordStatus.textContent = "Continuous recording: duration must be 10…600 s";
      return;
    }
    setCaptureButtons(true);
    recordStatus.textContent = "Starting continuous recording…";
    try {
      const response = await fetch(`/vibration/api/sensors/${address}/burst`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ duration_s: duration, note: (burstNote || recordNote).value.trim() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      await waitForRecord();
    } catch (error) {
      finishRecordError(error);
    }
  });
  if (burstStop) burstStop.addEventListener("click", async () => {
    burstStop.disabled = true;
    try {
      const response = await fetch("/vibration/api/operation/stop", { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      recordStatus.textContent = "Stopping after the current capture…";
    } catch (error) {
      recordStatus.textContent = `Stop failed: ${error.message || error}`;
    }
  });
  if (orbitLiveButton) orbitLiveButton.addEventListener("click", () => {
    viewingRecord = false;
    orbitLiveButton.hidden = true;
    loadOrbits();
  });
  if (recordsBody) recordsBody.addEventListener("click", async (event) => {
    const selection = event.target.closest("input[data-record-select]");
    if (selection) {
      const itemId = decodeURIComponent(selection.dataset.recordSelect);
      if (selection.checked) selectedRecordIds.add(itemId); else selectedRecordIds.delete(itemId);
      updateRecordSelectionControls();
      return;
    }
    const csv = event.target.closest("button[data-record-csv]");
    if (csv) {
      openExportModal(getExportConfig("vibrationRecord"), { id: csv.dataset.recordCsv, sampleRate: csv.dataset.rate });
      return;
    }
    const view = event.target.closest("button[data-record-view]");
    const remove = event.target.closest("button[data-record-delete]");
    try {
      if (view) await showRecord(decodeURIComponent(view.dataset.recordView));
      if (remove && window.confirm("Delete this saved time signal?")) {
        const response = await fetch(`/vibration/api/records/${remove.dataset.recordDelete}`, { method: "DELETE" });
        if (!response.ok) throw new Error(`delete HTTP ${response.status}`);
        await loadRecords();
      }
    } catch (error) {
      recordStatus.textContent = `Record action failed: ${error.message || error}`;
    }
  });
  if (recordsSelectAll) recordsSelectAll.addEventListener("change", () => {
    selectedRecordIds.clear();
    if (recordsSelectAll.checked) currentRecordIds.forEach((itemId) => selectedRecordIds.add(itemId));
    recordsBody.querySelectorAll("input[data-record-select]").forEach((checkbox) => {
      checkbox.checked = recordsSelectAll.checked;
    });
    updateRecordSelectionControls();
  });
  if (recordsDeleteSelected) recordsDeleteSelected.addEventListener("click", async () => {
    const itemIds = [...selectedRecordIds];
    if (!itemIds.length || !window.confirm(`Delete ${itemIds.length} selected record(s)?`)) return;
    recordsDeleteSelected.disabled = true;
    try {
      const response = await fetch("/vibration/api/records", {
        method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: itemIds }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `delete HTTP ${response.status}`);
      selectedRecordIds.clear();
      recordStatus.textContent = `${payload.deleted_count} record(s) deleted`;
      await loadRecords();
    } catch (error) {
      recordStatus.textContent = `Bulk delete failed: ${error.message || error}`;
      updateRecordSelectionControls();
    }
  });

  loadScaleSetting();
  updateScaleControls();
  loadOrbits();
  loadRecords();
  window.setInterval(() => {
    loadOrbits();
    loadRecords();
  }, 15000);
})();
