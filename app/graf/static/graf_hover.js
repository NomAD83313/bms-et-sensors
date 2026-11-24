// Shared mouse-over tooltip for the SVG/canvas charts of the vibration page.
// A chart opts in with data-hover-chart="<type>"; the handler registered for that
// type receives (element, event) and returns tooltip HTML or null (outside the plot).
(() => {
  const handlers = {};
  let tip = null;
  let active = null;

  function ensureTip() {
    if (!tip) {
      tip = document.createElement("div");
      tip.className = "chart-tooltip";
      tip.style.position = "fixed";
      tip.setAttribute("role", "status");
      document.body.appendChild(tip);
    }
    return tip;
  }

  function hideMarkers(chart) {
    chart?.querySelectorAll?.(".hover-line, .hover-dot, .hover-points").forEach((node) => { node.style.display = "none"; });
  }

  function hide() {
    if (tip) tip.style.display = "none";
    hideMarkers(active);
    active = null;
  }

  function show(event, html) {
    const element = ensureTip();
    element.innerHTML = html;
    element.style.display = "block";
    const width = element.offsetWidth || 240;
    const height = element.offsetHeight || 80;
    let left = event.clientX + 14;
    if (left + width > window.innerWidth - 4) left = event.clientX - width - 14;
    let top = event.clientY + 14;
    if (top + height > window.innerHeight - 4) top = event.clientY - height - 14;
    element.style.left = `${Math.max(4, left)}px`;
    element.style.top = `${Math.max(4, top)}px`;
  }

  // Mouse position in the SVG's viewBox units (correct for any aspect ratio).
  function svgPoint(svg, event) {
    const matrix = svg.getScreenCTM?.();
    if (!matrix) return null;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(matrix.inverse());
  }

  // Mouse position in a canvas's chart units (viewWidth x viewHeight, default: buffer size).
  function canvasPoint(canvas, event, viewWidth = canvas.width, viewHeight = canvas.height) {
    const rect = canvas.getBoundingClientRect();
    return { x: ((event.clientX - rect.left) / rect.width) * viewWidth, y: ((event.clientY - rect.top) / rect.height) * viewHeight };
  }

  function setLine(svg, x) {
    const line = svg.querySelector(".hover-line");
    if (!line) return;
    line.setAttribute("x1", x.toFixed(1));
    line.setAttribute("x2", x.toFixed(1));
    line.style.display = "block";
  }

  function setDot(svg, x, y) {
    const dot = svg.querySelector(".hover-dot");
    if (!dot) return;
    dot.setAttribute("cx", x.toFixed(1));
    dot.setAttribute("cy", y.toFixed(1));
    dot.style.display = "block";
  }

  const colors = { x: "#34d399", y: "#f87171", z: "#e5e7eb" };

  // Markers on the series under the cursor, like the live charts: [{ x, y, axis, ring }].
  // A ring outlines an existing dot (scatter charts) instead of adding a filled point.
  function setPoints(svg, points) {
    let group = svg.querySelector(".hover-points");
    if (!group) {
      group = document.createElementNS("http://www.w3.org/2000/svg", "g");
      group.setAttribute("class", "hover-points");
      svg.appendChild(group);
    }
    group.innerHTML = points.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y)).map((point) => {
      const color = colors[point.axis] || "#7dd3fc";
      return point.ring
        ? `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="3.5" fill="none" stroke="${color}" stroke-width="1" />`
        : `<circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="1.25" fill="${color}" />`;
    }).join("");
    group.style.display = "block";
  }

  function number(value, unit = "") {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) return "–";
    const v = Number(value);
    const text = v !== 0 && Math.abs(v) < 0.001 ? v.toExponential(3) : Math.abs(v) >= 1000 ? v.toFixed(0) : String(Number(v.toPrecision(4)));
    return unit ? `${text} ${unit}` : text;
  }

  // Local clock time of an ISO timestamp plus an offset in seconds:
  // "24.11.2026 10:02:41.9" (withDate) or "10:02:41.93"; null when the time is unknown.
  function clock(iso, offsetSeconds = 0, withDate = false) {
    const base = new Date(iso || "");
    if (!iso || !Number.isFinite(base.getTime())) return null;
    const time = new Date(base.getTime() + offsetSeconds * 1000);
    const two = (value) => String(value).padStart(2, "0");
    const clockText = `${two(time.getHours())}:${two(time.getMinutes())}:${two(time.getSeconds())}`;
    if (withDate) {
      return `${two(time.getDate())}.${two(time.getMonth() + 1)}.${time.getFullYear()} ${clockText}.${Math.floor(time.getMilliseconds() / 100)}`;
    }
    return `${clockText}.${String(Math.floor(time.getMilliseconds() / 10)).padStart(2, "0")}`;
  }

  function row(axis, text) {
    return `<div class="tt-row"><span class="tt-dot" style="background:${colors[axis] || "#7dd3fc"}"></span><span>${text}</span></div>`;
  }

  function title(text) {
    return `<div class="tt-time">${text}</div>`;
  }

  document.addEventListener("mousemove", (event) => {
    const chart = event.target.closest?.("[data-hover-chart]");
    if (!chart || !handlers[chart.dataset.hoverChart]) {
      if (active) hide();
      return;
    }
    if (active && active !== chart) hideMarkers(active);
    active = chart;
    let html = null;
    try {
      html = handlers[chart.dataset.hoverChart](chart, event);
    } catch (_) {
      html = null;
    }
    if (html) show(event, html); else hide();
  });
  document.addEventListener("scroll", hide, { passive: true });

  window.grafHover = {
    register(type, handler) { handlers[type] = handler; },
    hide, svgPoint, canvasPoint, setLine, setDot, setPoints, number, row, title, colors, clock,
  };
})();
