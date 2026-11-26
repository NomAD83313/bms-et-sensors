// Continuous vibration record view: time envelope, per-capture trends and spectrogram.
// A continuous record is a series of 1024-sample FIFO captures with short gaps; every
// chart shares one time axis and shows the gaps. Clicking a chart selects a capture,
// whose waveform/orbits and FFT are shown in the panels below (graf_orbits.js).
(() => {
  const root = document.getElementById("burstView");
  if (!root) return;
  // Trend, frequency and spectrogram charts live in their own panel below the orbits.
  const morePanel = document.getElementById("burstPanel");
  const more = document.getElementById("burstViewMore");
  const containers = [root, more].filter(Boolean);

  const width = 1000;
  const pad = { left: 62, right: 18, top: 14, bottom: 30 };
  const plotWidth = width - pad.left - pad.right;
  const axes = ["x", "y", "z"];
  const quantities = {
    acc_g: ["Acceleration", "g"],
    velocity_mm_s: ["Velocity", "mm/s"],
    displacement_um: ["Displacement", "µm"],
  };
  const metrics = {
    velocity_rms_mm_s: ["Velocity RMS", "mm/s"],
    acc_rms_g: ["Acceleration RMS", "g"],
    acc_peak_g: ["Acceleration peak", "g"],
    crest: ["Crest factor", ""],
    peak_hz: ["Dominant frequency", "Hz"],
  };
  // Perceptually ordered dark-to-bright map (viridis stops).
  const colorStops = [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]];
  const stored = (key, fallback, allowed) => {
    try {
      const value = localStorage.getItem(`graf.vibration.burst.${key}`);
      return value !== null && (!allowed || allowed.includes(value)) ? value : fallback;
    } catch (_) {
      return fallback;
    }
  };
  const store = (key, value) => {
    try { localStorage.setItem(`graf.vibration.burst.${key}`, String(value)); } catch (_) {}
  };
  const state = {
    record: null,
    analysis: null,
    decoded: {},
    selected: null,
    loadToken: 0,
    window: Number(stored("window", "256", ["256", "512", "1024"])),
    quantity: stored("quantity", "acc_g", Object.keys(quantities)),
    metric: stored("metric", "velocity_rms_mm_s", Object.keys(metrics)),
    axis: stored("axis", "z", axes),
    rangeDb: Number(stored("rangeDb", "60", ["40", "60", "80"])),
    spectrum: "average",
  };
  // Envelope Y scale per quantity, like the waveform charts: auto, compare (grows by
  // doubling until reset) or fixed.
  const scaleModes = ["auto", "compare", "fixed"];
  const compareMinimums = { acc_g: 0.02, velocity_mm_s: 1, displacement_um: 50 };
  const envScale = {};
  const envFixed = {};
  const envCompare = {};
  Object.keys(quantities).forEach((quantity) => {
    envScale[quantity] = stored(`envScale.${quantity}`, "auto", scaleModes);
    const fixed = Number(stored(`envFixed.${quantity}`, String(compareMinimums[quantity])));
    envFixed[quantity] = Number.isFinite(fixed) && fixed > 0 ? fixed : compareMinimums[quantity];
  });

  function envelopeLimit(quantity, peak) {
    const required = Math.max(1e-9, peak * 1.05);
    if (envScale[quantity] === "auto") return required;
    if (envScale[quantity] === "fixed") return envFixed[quantity];
    let limit = envCompare[quantity] || compareMinimums[quantity];
    while (limit < required) limit *= 2;
    envCompare[quantity] = limit;
    return limit;
  }

  function envelopeScaleControls() {
    const quantity = state.quantity;
    const mode = envScale[quantity];
    const unit = quantities[quantity][1];
    return `<label>Y scale<select data-burst-control="envScale">${scaleModes.map((value) => option(value, value[0].toUpperCase() + value.slice(1), mode)).join("")}</select></label>
      <label ${mode === "fixed" ? "" : "hidden"}>Max, ${unit}<input data-burst-control="envFixed" type="number" min="0.000001" step="any" value="${envFixed[quantity]}" /></label>
      <button type="button" data-burst-env-reset ${mode === "compare" ? "" : "disabled"}>Reset scale</button>`;
  }

  const option = (value, label, current) => `<option value="${value}" ${String(current) === String(value) ? "selected" : ""}>${label}</option>`;
  const span = () => Number(state.analysis?.span_s || 1);
  const sx = (seconds) => pad.left + (seconds / span()) * plotWidth;
  const timeAt = (viewX) => Math.max(0, Math.min(span(), ((viewX - pad.left) / plotWidth) * span()));

  // Wall-clock time of the record's first sample (older records: start of the recording).
  const recordStart = () => state.record?.capture_started_at?.[0] || state.record?.started_at || null;
  const clockAt = (seconds, withDate = false) => window.grafHover?.clock(recordStart(), seconds, withDate) || null;

  function timeTicks(height) {
    return Array.from({ length: 7 }, (_, index) => {
      const seconds = (span() * index) / 6;
      const x = sx(seconds);
      // The first label is the absolute start time of the record.
      const absolute = index === 0 ? clockAt(0, true) : null;
      return `<line class="wave-grid" x1="${x}" x2="${x}" y1="${pad.top}" y2="${height - pad.bottom}" />
        ${absolute ? `<text x="${x}" y="${height - 10}" text-anchor="start">${absolute}</text>` : `<text x="${x}" y="${height - 10}" text-anchor="middle">${seconds.toFixed(1)} s</text>`}`;
    }).join("");
  }

  function gapRects(height) {
    const captures = state.analysis.captures;
    const gaps = captures.slice(0, -1).map((capture, index) => {
      const x1 = sx(capture.end_s);
      const x2 = sx(captures[index + 1].start_s);
      return x2 > x1 ? `<rect class="burst-gap" x="${x1}" y="${pad.top}" width="${x2 - x1}" height="${height - pad.top - pad.bottom}" />` : "";
    }).join("");
    const selected = captures[state.selected];
    const highlight = selected
      ? `<rect class="burst-selected" x="${sx(selected.start_s)}" y="${pad.top}" width="${sx(selected.end_s) - sx(selected.start_s)}" height="${height - pad.top - pad.bottom}" />`
      : "";
    return gaps + highlight;
  }

  function timelineSvg() {
    const height = 240;
    const envelope = state.analysis.envelope;
    const channels = envelope.quantities[state.quantity];
    const binS = Number(envelope.bin_s);
    let peak = 0;
    axes.forEach((axis) => {
      channels[axis].min.forEach((value) => { if (value !== null) peak = Math.max(peak, Math.abs(value)); });
      channels[axis].max.forEach((value) => { if (value !== null) peak = Math.max(peak, Math.abs(value)); });
    });
    const range = envelopeLimit(state.quantity, peak);
    state.timelineRange = range;
    const sy = (value) => pad.top + (1 - (Math.max(-range, Math.min(range, value)) + range) / (2 * range)) * (height - pad.top - pad.bottom);
    // One filled band per axis between the minimum and the maximum of every time bin;
    // bands break at gaps.
    const bands = axes.map((axis) => {
      const lows = channels[axis].min;
      const highs = channels[axis].max;
      const shapes = [];
      let upper = [];
      let lower = [];
      const flush = () => {
        if (upper.length > 1) shapes.push(`<polygon class="band-${axis}" points="${upper.concat(lower.reverse()).join(" ")}" />`);
        upper = [];
        lower = [];
      };
      for (let index = 0; index < highs.length; index += 1) {
        if (highs[index] === null || lows[index] === null) { flush(); continue; }
        const x = sx((index + 0.5) * binS).toFixed(1);
        upper.push(`${x},${sy(highs[index]).toFixed(1)}`);
        lower.push(`${x},${sy(lows[index]).toFixed(1)}`);
      }
      flush();
      return shapes.join("");
    }).join("");
    const yTicks = [-1, -0.5, 0, 0.5, 1].map((fraction) => {
      const y = sy(range * fraction);
      return `<line class="wave-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${(range * fraction).toPrecision(3)}</text>`;
    }).join("");
    const [label, unit] = quantities[state.quantity];
    return `<svg class="burst-chart" data-burst-chart="timeline" data-hover-chart="burst-timeline" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} envelope over the record">
      ${gapRects(height)}${timeTicks(height)}${yTicks}${bands}<line class="hover-line" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}" />
      <text x="14" y="${height / 2}" text-anchor="middle" transform="rotate(-90 14 ${height / 2})">${label}, ${unit}</text>
    </svg>`;
  }

  function trendSvg() {
    const height = 220;
    const captures = state.analysis.captures;
    const values = captures.flatMap((capture) => axes.map((axis) => Number(capture.axes[axis][state.metric])));
    const finite = values.filter(Number.isFinite);
    const top = finite.length ? Math.max(...finite) * 1.1 || 1 : 1;
    state.trendTop = top;
    const sy = (value) => pad.top + (1 - value / top) * (height - pad.top - pad.bottom);
    const series = axes.map((axis) => {
      const points = captures.map((capture) => {
        const value = Number(capture.axes[axis][state.metric]);
        return Number.isFinite(value) ? [sx((capture.start_s + capture.end_s) / 2), sy(value)] : null;
      }).filter(Boolean);
      const line = points.length > 1 ? `<polyline class="trend-${axis}" points="${points.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ")}" />` : "";
      return line + points.map(([x, y]) => `<circle class="trend-${axis}" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.5" />`).join("");
    }).join("");
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
      const y = sy(top * fraction);
      return `<line class="wave-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${(top * fraction).toPrecision(3)}</text>`;
    }).join("");
    const [label, unit] = metrics[state.metric];
    return `<svg class="burst-chart" data-burst-chart="trend" data-hover-chart="burst-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="${label} per capture">
      ${gapRects(height)}${timeTicks(height)}${yTicks}${series}<line class="hover-line" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}" />
      <text x="14" y="${height / 2}" text-anchor="middle" transform="rotate(-90 14 ${height / 2})">${label}${unit ? `, ${unit}` : ""}</text>
    </svg>`;
  }

  // FFT peak frequencies per capture: up to three peaks per axis, dot size and opacity by
  // amplitude relative to the strongest peak of the record (frequency over time).
  function frequencySvg() {
    const height = 220;
    const captures = state.analysis.captures;
    const nyquist = Number(state.analysis.sample_rate_hz) / 2;
    const sy = (hz) => pad.top + (1 - hz / nyquist) * (height - pad.top - pad.bottom);
    // Amplitude is shown by dot size and brightness, scaled from the weakest to the
    // strongest peak of this record so that differences stay visible.
    const peaks = captures.flatMap((capture) => axes.flatMap((axis) => (capture.axes[axis].peaks || []).map(([hz, g]) => (
      { axis, hz: Number(hz), g: Number(g), x: sx((capture.start_s + capture.end_s) / 2) }))))
      .filter((peak) => Number.isFinite(peak.hz) && Number.isFinite(peak.g));
    const amplitudes = peaks.map((peak) => peak.g);
    const weakest = amplitudes.length ? Math.min(...amplitudes) : 0;
    const strongest = amplitudes.length ? Math.max(...amplitudes) : 1;
    const weight = (g) => (strongest > weakest ? (g - weakest) / (strongest - weakest) : 1);
    const radius = (w) => 1 + 5 * w;
    const opacity = (w) => 0.12 + 0.88 * w;
    // Weak peaks first so the strong ones are drawn on top.
    const dots = peaks.sort((a, b) => a.g - b.g).map((peak) => {
      const w = weight(peak.g);
      return `<circle class="trend-${peak.axis} freq-dot" cx="${peak.x.toFixed(1)}" cy="${sy(peak.hz).toFixed(1)}" r="${radius(w).toFixed(2)}" fill-opacity="${opacity(w).toFixed(2)}" />`;
    }).join("");
    const legendX = width - pad.right - 250;
    const legend = amplitudes.length ? `<g class="freq-legend">
        <rect x="${legendX}" y="${pad.top + 4}" width="244" height="22" rx="4" />
        <text x="${legendX + 8}" y="${pad.top + 19}">Amplitude</text>
        ${[0, 0.5, 1].map((w, index) => {
          const cx = legendX + 76 + index * 58;
          const g = weakest + w * (strongest - weakest);
          return `<circle cx="${cx}" cy="${pad.top + 15}" r="${radius(w)}" fill-opacity="${opacity(w)}" />
            <text x="${cx + 8}" y="${pad.top + 19}">${window.grafHover ? window.grafHover.number(g, "g") : g.toPrecision(2)}</text>`;
        }).join("")}
      </g>` : "";
    const yTicks = [0, 0.25, 0.5, 0.75, 1].map((fraction) => {
      const y = sy(nyquist * fraction);
      return `<line class="wave-grid" x1="${pad.left}" x2="${width - pad.right}" y1="${y}" y2="${y}" />
        <text x="${pad.left - 7}" y="${y + 4}" text-anchor="end">${(nyquist * fraction).toFixed(0)}</text>`;
    }).join("");
    return `<svg class="burst-chart" data-burst-chart="frequency" data-hover-chart="burst-frequency" viewBox="0 0 ${width} ${height}" role="img" aria-label="FFT peak frequencies per capture">
      ${gapRects(height)}${timeTicks(height)}${yTicks}${dots}<line class="hover-line" x1="0" x2="0" y1="${pad.top}" y2="${height - pad.bottom}" />
      ${legend}
      <text x="14" y="${height / 2}" text-anchor="middle" transform="rotate(-90 14 ${height / 2})">Frequency, Hz</text>
    </svg>`;
  }

  function color(fraction) {
    const position = Math.max(0, Math.min(1, fraction)) * (colorStops.length - 1);
    const index = Math.min(colorStops.length - 2, Math.floor(position));
    const t = position - index;
    const [a, b] = [colorStops[index], colorStops[index + 1]];
    return `rgb(${a.map((value, k) => Math.round(value + (b[k] - value) * t)).join(",")})`;
  }

  // Time span of every spectrogram column. Columns overlap by 50 %, so a column owns the
  // time from halfway to its neighbours; the first and last column of a capture reach the
  // capture's start and end, so the columns cover each capture exactly.
  function columnBounds() {
    const analysis = state.analysis;
    const spectrogram = analysis.spectrogram;
    const perCapture = Math.floor((analysis.samples - spectrogram.window) / spectrogram.hop) + 1;
    const times = spectrogram.times_s;
    return times.map((time, index) => {
      const capture = analysis.captures[Math.floor(index / perCapture)];
      const local = index % perCapture;
      const start = local === 0 ? capture.start_s : (times[index - 1] + time) / 2;
      const end = local === perCapture - 1 ? capture.end_s : (time + times[index + 1]) / 2;
      return [start, end];
    });
  }

  function drawSpectrogram() {
    const canvas = find("canvas[data-burst-chart='spectrogram']");
    if (!canvas) return;
    const height = 260;
    // Draw at the displayed size and device pixel ratio so text and cell edges stay sharp.
    const shown = canvas.getBoundingClientRect().width || width;
    const scale = (shown / width) * (window.devicePixelRatio || 1);
    const pixelWidth = Math.max(1, Math.round(width * scale));
    const pixelHeight = Math.max(1, Math.round(height * scale));
    if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
      canvas.width = pixelWidth;
      canvas.height = pixelHeight;
    }
    const context = canvas.getContext("2d");
    context.setTransform(1, 0, 0, 1, 0, 0);
    const spectrogram = state.analysis.spectrogram;
    const axisData = spectrogram.axes[state.axis];
    const codes = state.decoded[state.axis];
    const bins = Number(spectrogram.bins);
    const nyquist = Number(spectrogram.df_hz) * (bins - 1);
    const plotHeight = height - pad.top - pad.bottom;
    const dbSpan = Number(axisData.db_max) - Number(axisData.db_min);
    const floorDb = Number(axisData.db_max) - state.rangeDb;
    const bounds = state.columnBounds || [];
    context.fillStyle = "#0e1628";
    context.fillRect(0, 0, pixelWidth, pixelHeight);
    // Cells on whole device pixels: no blurred or overlapping edges.
    const rowEdges = Array.from({ length: bins + 1 }, (_, bin) => Math.round((pad.top + plotHeight - (bin / bins) * plotHeight) * scale));
    bounds.forEach(([start, end], column) => {
      const x0 = Math.round(sx(start) * scale);
      const x1 = Math.max(x0 + 1, Math.round(sx(end) * scale));
      for (let bin = 0; bin < bins; bin += 1) {
        const y1 = rowEdges[bin];
        const y0 = Math.min(y1 - 1, rowEdges[bin + 1]);
        const db = Number(axisData.db_min) + (codes[column * bins + bin] / 255) * dbSpan;
        context.fillStyle = color((db - floorDb) / state.rangeDb);
        context.fillRect(x0, y0, x1 - x0, y1 - y0);
      }
    });
    // Labels and frames in chart units, scaled like the SVG charts.
    context.setTransform(scale, 0, 0, scale, 0, 0);
    context.strokeStyle = "#2a3c63";
    context.lineWidth = 1 / scale;
    context.fillStyle = "#8ea6d9";
    context.font = "10px sans-serif";
    for (let index = 0; index <= 6; index += 1) {
      const x = sx((span() * index) / 6);
      const absolute = index === 0 ? clockAt(0, true) : null;
      context.textAlign = absolute ? "left" : "center";
      context.fillText(absolute || `${((span() * index) / 6).toFixed(1)} s`, x, height - 10);
    }
    context.textAlign = "right";
    for (let index = 0; index <= 4; index += 1) {
      const hz = (nyquist * index) / 4;
      const y = pad.top + plotHeight - (hz / nyquist) * plotHeight;
      context.beginPath();
      context.moveTo(pad.left, y);
      context.lineTo(pad.left - 4, y);
      context.stroke();
      context.fillText(`${hz.toFixed(0)} Hz`, pad.left - 7, y + 4);
    }
    const selected = state.analysis.captures[state.selected];
    if (selected) {
      context.strokeStyle = "#7dd3fc";
      context.strokeRect(sx(selected.start_s), pad.top, sx(selected.end_s) - sx(selected.start_s), plotHeight);
    }
  }

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => { if (state.analysis) drawSpectrogram(); }, 150);
  });

  function summaryChips() {
    const record = state.record;
    const analysis = state.analysis;
    const gaps = analysis ? analysis.gaps_s : [];
    const meanGap = gaps.length ? gaps.reduce((sum, value) => sum + value, 0) / gaps.length : 0;
    const maxGap = gaps.length ? Math.max(...gaps) : 0;
    const started = new Date(record.started_at || record.captured_at || "");
    const chips = [
      ["Started", Number.isFinite(started.getTime()) ? started.toLocaleString() : "–"],
      ["Span", `${Number(record.span_s || 0).toFixed(1)} s of ${Number(record.requested_duration_s || 0)} s requested`],
      ["Captures", `${Number(record.capture_count || 0)} × ${Number(record.samples || 1024)} samples`],
      ["Coverage", `${(100 * Number(record.coverage || 0)).toFixed(1)} %`],
      ["Gaps", gaps.length ? `mean ${(meanGap * 1000).toFixed(0)} ms · max ${(maxGap * 1000).toFixed(0)} ms` : "–"],
      ["Sampling rate", `${Number(record.sample_rate_hz || 0).toFixed(2)} Hz`],
    ];
    if (record.stopped) chips.push(["Ended", "stopped by user"]);
    if (record.error) chips.push(["Ended early", record.error]);
    return chips.map(([label, value]) => `<span class="summary-chip">${escapeHtml(label)} <strong>${escapeHtml(value)}</strong></span>`).join("");
  }

  function skeleton() {
    const record = state.record;
    return `
      <div class="waveform-chart-head">
        <span class="waveform-chart-title"><span>Continuous record · ${escapeHtml(record.sensor || "Sensor")}${record.note ? ` · ${escapeHtml(record.note)}` : ""}
          <span class="waveform-legend"><span class="x">X axis · green</span><span class="y">Y axis · red</span><span class="z">Z axis · white</span></span></span>
          <span class="orbit-status">Each capture is processed on its own (mean removed, integrated above 2 Hz); shaded strips are gaps without data. Click a chart to select a capture.</span>
        </span>
      </div>
      <div class="burst-chips" data-burst-part="chips">${summaryChips()}</div>
      <div data-burst-part="status" class="orbit-status">Analysing…</div>
      <div class="burst-block">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title"><span>Signal envelope over the record</span>
            <span class="orbit-status">Band = minimum … maximum per time bin · shaded strips are gaps · click to select a capture</span></span>
          <span class="burst-controls">
            <label>Quantity<select data-burst-control="quantity">${Object.entries(quantities).map(([key, [label, unit]]) => option(key, `${label}, ${unit}`, state.quantity)).join("")}</select></label>
            <span class="burst-controls" data-burst-part="envelope-scale">${envelopeScaleControls()}</span>
          </span>
        </div>
        <div data-burst-part="timeline"></div>
      </div>
      <div class="waveform-chart-head burst-capture-bar">
        <span class="waveform-chart-title"><span>Selected capture</span>
          <span class="orbit-status" data-burst-part="selected">No capture selected · its waveform and orbits replace the live panels below</span></span>
        <span class="burst-controls">
          <button type="button" data-burst-step="-1">◀ Previous</button>
          <button type="button" data-burst-step="1">Next ▶</button>
          <label>FFT chart below<select data-burst-control="spectrum">${option("average", "Average of all captures", state.spectrum)}${option("peak", "Maximum of all captures", state.spectrum)}${option("capture", "Selected capture", state.spectrum)}</select></label>
        </span>
      </div>`;
  }

  function skeletonMore() {
    return `
      <div class="burst-block">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title"><span>Trend per capture</span>
            <span class="orbit-status">One point per capture (4.16 s) · click to select a capture</span></span>
          <span class="burst-controls">
            <label>Value<select data-burst-control="metric">${Object.entries(metrics).map(([key, [label, unit]]) => option(key, unit ? `${label}, ${unit}` : label, state.metric)).join("")}</select></label>
          </span>
        </div>
        <div data-burst-part="trend"></div>
      </div>
      <div class="burst-block">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title"><span>Vibration frequency</span>
            <span class="orbit-status">Three strongest FFT peaks per axis and capture (above 2 Hz) · dot size and brightness = amplitude, from the weakest to the strongest peak of this record (see legend)</span></span>
        </div>
        <div data-burst-part="frequency"></div>
      </div>
      <div class="burst-block">
        <div class="waveform-chart-head">
          <span class="waveform-chart-title"><span>Spectrogram</span>
            <span class="orbit-status" data-burst-part="spectrogram-head"></span></span>
          <span class="burst-controls">
            <label>Axis<select data-burst-control="axis">${axes.map((axis) => option(axis, axis.toUpperCase(), state.axis)).join("")}</select></label>
            <label>Window<select data-burst-control="window">${[256, 512, 1024].map((value) => option(value, `${value} samples`, state.window)).join("")}</select></label>
            <label>Colour range<select data-burst-control="rangeDb">${[40, 60, 80].map((value) => option(value, `${value} dB`, state.rangeDb)).join("")}</select></label>
          </span>
        </div>
        <canvas class="burst-chart" data-burst-chart="spectrogram" data-hover-chart="burst-spectrogram" width="${width}" height="260" aria-label="Spectrogram"></canvas>
      </div>
`;
  }

  const find = (selector) => containers.map((container) => container.querySelector(selector)).find(Boolean) || null;
  const part = (name) => find(`[data-burst-part="${name}"]`);

  function render() {
    if (!state.analysis) return;
    part("timeline").innerHTML = timelineSvg();
    part("envelope-scale").innerHTML = envelopeScaleControls();
    part("trend").innerHTML = trendSvg();
    part("frequency").innerHTML = frequencySvg();
    const spectrogram = state.analysis.spectrogram;
    part("spectrogram-head").textContent = `${state.axis.toUpperCase()} axis · acceleration amplitude, dB re 1 g · window ${spectrogram.window} samples (${(spectrogram.window / state.analysis.sample_rate_hz).toFixed(2)} s, Δf ${Number(spectrogram.df_hz).toFixed(2)} Hz) · 50 % overlap inside each capture`;
    drawSpectrogram();
    const selected = state.analysis.captures[state.selected];
    part("selected").textContent = selected
      ? `Capture ${state.selected + 1} of ${state.analysis.captures.length} · ${selected.start_s.toFixed(2)}–${selected.end_s.toFixed(2)} s · shown in the waveform and orbit panels below`
      : "No capture selected · click a chart or use Previous / Next";
  }

  async function loadAnalysis() {
    const token = ++state.loadToken;
    const record = state.record;
    part("status").textContent = `Analysing ${Number(record.capture_count || 0)} captures… (long records take a few seconds the first time)`;
    try {
      const response = await fetch(`/vibration/api/records/${encodeURIComponent(record.id)}/burst?window=${state.window}`, { cache: "no-store" });
      const payload = await response.json();
      if (token !== state.loadToken) return;
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      state.analysis = payload;
      state.columnBounds = columnBounds();
      state.decoded = Object.fromEntries(axes.map((axis) => {
        const binary = atob(payload.spectrogram.axes[axis].data);
        const codes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) codes[index] = binary.charCodeAt(index);
        return [axis, codes];
      }));
      part("status").textContent = "";
      part("chips").innerHTML = summaryChips();
      render();
      // Default view: the FFT of one capture, starting with the last one (the record's waveform).
      if (state.selected === null && state.spectrum === "capture") selectCapture(payload.captures.length - 1, state.pin);
    } catch (error) {
      if (token === state.loadToken) part("status").textContent = `Analysis unavailable: ${error.message || error}`;
    }
  }

  function showSpectrum() {
    const api = window.grafVibration;
    if (!api || !state.record) return;
    if (state.spectrum === "peak" && state.analysis) {
      const hold = state.analysis.peak_hold;
      api.showSpectrum(state.record, { axes: hold.axes, df_hz: hold.df_hz, nyquist_hz: hold.nyquist_hz },
        "maximum of all captures", `highest amplitude per frequency over ${state.analysis.captures.length} captures`);
    } else if (state.spectrum === "capture" && state.captureData) {
      api.showSpectrum(state.record, state.captureData.spectrum,
        `capture ${state.captureData.index + 1} of ${state.captureData.count}`, "1 capture");
    } else {
      api.showSpectrum(state.record, state.record.spectrum, "average of all captures",
        `${Number(state.record.capture_count || 1)} captures averaged`);
    }
  }

  // pin: also show the capture's waveform and orbits in the panels below (pauses live refresh).
  async function selectCapture(index, pin = true) {
    const captures = state.analysis?.captures || [];
    if (!captures.length) return;
    state.selected = Math.max(0, Math.min(captures.length - 1, index));
    render();
    const record = state.record;
    try {
      const response = await fetch(`/vibration/api/records/${encodeURIComponent(record.id)}/captures/${state.selected}`, { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
      if (record !== state.record) return;
      state.captureData = payload;
      if (pin) window.grafVibration?.showWave(
        { name: `${record.sensor || "Sensor"} · continuous record · capture ${payload.index + 1}/${payload.count} at ${Number(payload.start_s || 0).toFixed(2)} s`, address: record.address },
        payload.waveform,
      );
      state.spectrum = "capture";
      const select = find('[data-burst-control="spectrum"]');
      if (select) select.value = "capture";
      showSpectrum();
    } catch (error) {
      part("status").textContent = `Capture unavailable: ${error.message || error}`;
    }
  }

  function captureAtTime(seconds) {
    const captures = state.analysis?.captures || [];
    let best = 0;
    let distance = Infinity;
    captures.forEach((capture, index) => {
      const d = seconds < capture.start_s ? capture.start_s - seconds : seconds > capture.end_s ? seconds - capture.end_s : 0;
      if (d < distance) { distance = d; best = index; }
    });
    return best;
  }

  function viewX(element, event) {
    const rect = element.getBoundingClientRect();
    return ((event.clientX - rect.left) / rect.width) * width;
  }

  const onClick = (event) => {
    if (event.target.closest("[data-burst-env-reset]")) {
      delete envCompare[state.quantity];
      render();
      return;
    }
    const step = event.target.closest("[data-burst-step]");
    if (step) {
      selectCapture(state.selected === null ? 0 : state.selected + Number(step.dataset.burstStep));
      return;
    }
    const chart = event.target.closest("[data-burst-chart]");
    if (chart && state.analysis) selectCapture(captureAtTime(timeAt(viewX(chart, event))));
  };

  const onChange = (event) => {
    const control = event.target.closest("[data-burst-control]");
    if (!control) return;
    const key = control.dataset.burstControl;
    if (key === "envScale") {
      envScale[state.quantity] = control.value;
      store(`envScale.${state.quantity}`, control.value);
      render();
      return;
    }
    if (key === "envFixed") {
      const value = Number(control.value);
      if (!Number.isFinite(value) || value <= 0) return;
      envFixed[state.quantity] = value;
      store(`envFixed.${state.quantity}`, value);
      render();
      return;
    }
    if (key === "spectrum") {
      state.spectrum = control.value;
      if (state.spectrum === "capture" && !state.captureData) {
        selectCapture(state.selected ?? 0);
        return;
      }
      showSpectrum();
      return;
    }
    state[key] = ["window", "rangeDb"].includes(key) ? Number(control.value) : control.value;
    store(key, control.value);
    if (key === "window") loadAnalysis(); else render();
  };
  containers.forEach((container) => {
    container.addEventListener("click", onClick);
    container.addEventListener("change", onChange);
  });

  // Mouse-over tooltips (graf_hover.js).
  function nearestCapture(seconds) {
    const captures = state.analysis?.captures || [];
    let best = 0;
    let distance = Infinity;
    captures.forEach((capture, index) => {
      const d = Math.abs((capture.start_s + capture.end_s) / 2 - seconds);
      if (d < distance) { distance = d; best = index; }
    });
    return captures[best] ? best : null;
  }

  function containingCapture(seconds) {
    return (state.analysis?.captures || []).findIndex((capture) => seconds >= capture.start_s && seconds <= capture.end_s);
  }

  function captureTitle(hover, index) {
    const capture = state.analysis.captures[index];
    const at = clockAt(capture.start_s);
    return hover.title(`Capture ${index + 1} of ${state.analysis.captures.length} · ${capture.start_s.toFixed(2)}–${capture.end_s.toFixed(2)} s${at ? ` · from ${at}` : ""}`);
  }

  function plotTime(hover, svg, event) {
    const point = hover.svgPoint(svg, event);
    if (!state.analysis || !point || point.x < pad.left - 4 || point.x > width - pad.right + 4) return null;
    return timeAt(point.x);
  }

  if (window.grafHover) {
    const hover = window.grafHover;
    hover.register("burst-timeline", (svg, event) => {
      const seconds = plotTime(hover, svg, event);
      if (seconds === null) return null;
      hover.setLine(svg, sx(seconds));
      const envelope = state.analysis.envelope;
      const bin = Math.min(envelope.bins - 1, Math.floor(seconds / Number(envelope.bin_s)));
      const [label, unit] = quantities[state.quantity];
      const index = containingCapture(seconds);
      const at = clockAt(seconds);
      const head = `${seconds.toFixed(2)} s${at ? ` · ${at}` : ""} · ${index >= 0 ? `capture ${index + 1}` : "gap, no data"}`;
      const channels = envelope.quantities[state.quantity];
      const range = state.timelineRange || 1;
      const sy = (value) => pad.top + (1 - (Math.max(-range, Math.min(range, value)) + range) / (2 * range)) * (240 - pad.top - pad.bottom);
      const x = sx((bin + 0.5) * Number(envelope.bin_s));
      hover.setPoints(svg, axes.flatMap((axis) => ["min", "max"]
        .filter((key) => channels[axis][key][bin] !== null)
        .map((key) => ({ x, y: sy(channels[axis][key][bin]), axis }))));
      const rows = axes.map((axis) => {
        const low = channels[axis].min[bin];
        const high = channels[axis].max[bin];
        return low === null || high === null ? "" :
          hover.row(axis, `${axis.toUpperCase()} ${label.toLowerCase()}: ${hover.number(low)} … ${hover.number(high, unit)}`);
      }).join("");
      return hover.title(head) + (rows || "<div>No samples in this time bin</div>") +
        `<div>Min … max over ${(Number(envelope.bin_s) * 1000).toFixed(0)} ms · click to select the capture</div>`;
    });
    hover.register("burst-trend", (svg, event) => {
      const seconds = plotTime(hover, svg, event);
      if (seconds === null) return null;
      const index = nearestCapture(seconds);
      if (index === null) return null;
      const capture = state.analysis.captures[index];
      const x = sx((capture.start_s + capture.end_s) / 2);
      hover.setLine(svg, x);
      const top = state.trendTop || 1;
      hover.setPoints(svg, axes.filter((axis) => Number.isFinite(Number(capture.axes[axis][state.metric])))
        .map((axis) => ({ x, y: pad.top + (1 - Number(capture.axes[axis][state.metric]) / top) * (220 - pad.top - pad.bottom), axis, ring: true })));
      const [label, unit] = metrics[state.metric];
      return captureTitle(hover, index) + axes.map((axis) => (
        hover.row(axis, `${axis.toUpperCase()} ${label.toLowerCase()}: ${hover.number(capture.axes[axis][state.metric], unit)}`)
      )).join("");
    });
    hover.register("burst-frequency", (svg, event) => {
      const seconds = plotTime(hover, svg, event);
      if (seconds === null) return null;
      const index = nearestCapture(seconds);
      if (index === null) return null;
      const capture = state.analysis.captures[index];
      const x = sx((capture.start_s + capture.end_s) / 2);
      hover.setLine(svg, x);
      return captureTitle(hover, index) + axes.map((axis) => {
        const peaks = capture.axes[axis].peaks || [];
        const text = peaks.length ? peaks.map(([hz, g]) => `${Number(hz).toFixed(2)} Hz (${hover.number(g, "g")})`).join(" · ") : "no peak";
        return hover.row(axis, `${axis.toUpperCase()}: ${text}`);
      }).join("");
    });
    hover.register("burst-spectrogram", (canvas, event) => {
      if (!state.analysis) return null;
      const point = hover.canvasPoint(canvas, event, width, 260);
      const spectrogram = state.analysis.spectrogram;
      const bins = Number(spectrogram.bins);
      const plotHeight = 260 - pad.top - pad.bottom;
      const bin = Math.floor(((pad.top + plotHeight - point.y) / plotHeight) * bins);
      if (bin < 0 || bin >= bins || point.x < pad.left || point.x > width - pad.right) return null;
      const seconds = timeAt(point.x);
      const column = (state.columnBounds || []).findIndex(([start, end]) => seconds >= start && seconds <= end);
      const hz = bin * Number(spectrogram.df_hz);
      const atGap = clockAt(seconds);
      if (column < 0) return hover.title(`${seconds.toFixed(2)} s${atGap ? ` · ${atGap}` : ""} · ${hz.toFixed(2)} Hz`) + "<div>Gap, no data</div>";
      const rows = axes.map((axis) => {
        const data = spectrogram.axes[axis];
        const db = Number(data.db_min) + (state.decoded[axis][column * bins + bin] / 255) * (Number(data.db_max) - Number(data.db_min));
        const mark = axis === state.axis ? " (shown)" : "";
        return hover.row(axis, `${axis.toUpperCase()}${mark}: ${db.toFixed(1)} dB re 1 g · ${hover.number(10 ** (db / 20), "g")}`);
      }).join("");
      const at = clockAt(spectrogram.times_s[column]);
      return hover.title(`${spectrogram.times_s[column].toFixed(2)} s${at ? ` · ${at}` : ""} · ${hz.toFixed(2)} Hz`) + rows +
        `<div>Window ${spectrogram.window} samples · Δf ${Number(spectrogram.df_hz).toFixed(2)} Hz</div>`;
    });
  }

  function show(record, pin = true) {
    if (!record || record.kind !== "burst") {
      hide();
      return;
    }
    state.record = record;
    state.analysis = null;
    state.selected = null;
    state.captureData = null;
    state.spectrum = "capture";
    state.pin = pin;
    root.hidden = false;
    root.innerHTML = skeleton();
    if (more) more.innerHTML = skeletonMore();
    if (morePanel) {
      morePanel.hidden = false;
      const subtitle = document.getElementById("burstPanelRecord");
      if (subtitle) subtitle.textContent = `${record.sensor || "Sensor"}${record.note ? ` · ${record.note}` : ""} · click a chart to select a capture`;
    }
    loadAnalysis();
  }

  function hide() {
    state.loadToken += 1;
    state.record = null;
    state.analysis = null;
    root.hidden = true;
    root.innerHTML = "";
    if (more) more.innerHTML = "";
    if (morePanel) morePanel.hidden = true;
  }

  window.grafBurst = { show, hide };
})();
