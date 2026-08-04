    function getAutoTargetPoints() {
      const chart = chartIds.map((id) => document.getElementById(id)).find(Boolean);
      const w = chart ? chart.getBoundingClientRect().width : 0;
      const width = Number.isFinite(w) && w > 0 ? w : (window.innerWidth || 1400);
      const density = pageMode === "all" ? 0.28 : 0.36;
      const maxPoints = pageMode === "all" ? 520 : 700;
      const estimate = Math.round(width * density);
      return Math.max(160, Math.min(maxPoints, estimate));
    }

    function buildDashboardParams() {
      const range = document.getElementById("rangeSel").value;
      const params = new URLSearchParams({ range: range });
      params.set("sample", "auto");
      params.set("target_points", String(getAutoTargetPoints()));
      if (range === "custom") {
        const fromRaw = document.getElementById("customFrom").value;
        const toRaw = document.getElementById("customTo").value;
        if (!fromRaw || !toRaw) {
          setStatus("error: set both custom from/to");
          return null;
        }
        const fromDt = new Date(fromRaw);
        const toDt = new Date(toRaw);
        if (!Number.isFinite(fromDt.getTime()) || !Number.isFinite(toDt.getTime())) {
          setStatus("error: invalid custom datetime");
          return null;
        }
        if (toDt <= fromDt) {
          setStatus("error: custom range requires to > from");
          return null;
        }
        params.set("from", fromDt.toISOString());
        params.set("to", toDt.toISOString());
      }
      return params;
    }

    async function refreshData(force = false) {
      if (inFlight && !force) return;
      if (inFlight && force && activeController) activeController.abort();

      const range = document.getElementById("rangeSel").value;
      const params = buildDashboardParams();
      if (!params) return;
      params.set("view", pageMode);
      const controller = new AbortController();
      activeController = controller;
      inFlight = true;
      setStatus(`loading (${range})...`);
      try {
        const res = await fetch(`api/dashboard?${params.toString()}`, { signal: controller.signal });
        const data = await res.json();
        if (!res.ok || !data.success) throw new Error(data.error || "API error");

        const chartSeries = {
          c1: mergeTemperatureSeries([], toXY(data.panels.redlab_temperature || [])),
          c2: toXY(data.panels.mscl_temperature || []),
          c3: toXY(data.panels.matter_temperature || []),
          c4: toXY(data.panels.almemo_live || []),
          c5: toXY(data.panels.pyrometers_temperature || []),
          c6: toXY(data.panels.messkluppe_force || []),
          c7: toXY(data.panels.messkluppe_orientation || []),
          c8: toXY(data.panels.messkluppe_battery || []),
          c9: toXY(data.panels.messkluppe_temperatures || []),
          c10: toXY(data.panels.matter_battery || []),
          c11: toXY(data.panels.matter_humidity || []),
          c12: toXY(data.panels.matter_pressure || []),
          c13: toXY(data.panels.matter_pm || []),
        };
        const panelMeta = data.panel_meta || {};
        panelMetaByChart.c1 = panelMeta.redlab_temperature || {};
        panelMetaByChart.c2 = panelMeta.mscl_temperature || {};
        panelMetaByChart.c3 = panelMeta.matter_temperature || {};
        panelMetaByChart.c4 = panelMeta.almemo_live || {};
        panelMetaByChart.c5 = panelMeta.pyrometers_temperature || {};
        panelMetaByChart.c6 = panelMeta.messkluppe_force || {};
        panelMetaByChart.c7 = panelMeta.messkluppe_orientation || {};
        panelMetaByChart.c8 = panelMeta.messkluppe_battery || {};
        panelMetaByChart.c9 = panelMeta.messkluppe_temperatures || {};
        panelMetaByChart.c10 = panelMeta.matter_battery || {};
        panelMetaByChart.c11 = panelMeta.matter_humidity || {};
        panelMetaByChart.c12 = panelMeta.matter_pressure || {};
        panelMetaByChart.c13 = panelMeta.matter_pm || {};
        const xMin = new Date(data.window_from_utc || "").getTime();
        const xMax = new Date(data.window_to_utc || "").getTime();
        const xBounds = Number.isFinite(xMin) && Number.isFinite(xMax) && xMax > xMin
          ? { minX: xMin, maxX: xMax }
          : null;
        const renderMs = chartIds.reduce((acc, chartId) => {
          if (!document.getElementById(chartId)) return acc;
          return acc + (drawChart(chartId, chartSeries[chartId] || [], xBounds) || 0);
        }, 0);
        const tempPoints = (chartSeries.c1 || []).reduce((acc, s) => acc + (s.points ? s.points.length : 0), 0);

        const sampleLabel = data.sample_label || "Auto";
        const targetPoints = data.target_points || getAutoTargetPoints();
        const updatedAt = new Date().toLocaleTimeString();
        setStatus(
          `ok | updated ${updatedAt}`,
          [
            `points(temp)=${tempPoints}`,
            `target=${targetPoints}`,
            `render=${renderMs.toFixed(1)}ms`,
            `window=${data.window}`,
            `sampling=${sampleLabel}`,
            `updated ${updatedAt}`,
          ].join("\n"),
        );
        resetZoomPending = false;
        finishRangeAction(true);
        syncUrlState();
      } catch (err) {
        if (err && err.name === "AbortError") return;
        resetZoomPending = false;
        finishRangeAction(false);
        setStatus(`error: ${err}`);
      } finally {
        if (activeController === controller) {
          inFlight = false;
          activeController = null;
        }
        if (!inFlight) resetZoomPending = false;
        if (!isCustomRange()) finishRangeAction(false);
        renderZoomState();
      }
    }

    function syncUrlState() {
      const range = document.getElementById("rangeSel").value;
      const params = new URLSearchParams({ range });
      if (range === "custom") {
        const fromRaw = document.getElementById("customFrom").value;
        const toRaw = document.getElementById("customTo").value;
        try { if (fromRaw) params.set("from", new Date(fromRaw).toISOString()); } catch (_) {}
        try { if (toRaw) params.set("to", new Date(toRaw).toISOString()); } catch (_) {}
      }
      history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
    }

    function refreshPresetValues() {
      const slider = document.getElementById("refreshSec");
      const raw = String(slider.dataset.values || "10,5,2,1,0.5,0.2");
      const values = raw.split(",").map((value) => Number(value.trim())).filter((value) => Number.isFinite(value) && value > 0);
      return values.length ? values : [10, 5, 2, 1, 0.5, 0.2];
    }

    function formatRefreshSec(sec) {
      if (sec >= 1) return String(sec).replace(/\.0$/, "");
      return String(sec);
    }

    function getRefreshSec() {
      const slider = document.getElementById("refreshSec");
      const values = refreshPresetValues();
      const idx = Math.max(0, Math.min(values.length - 1, Number(slider.value) || 0));
      return values[idx] || 5;
    }

    function updateRefreshSliderReadout() {
      const slider = document.getElementById("refreshSec");
      const readout = document.getElementById("refreshSecValue");
      const sec = getRefreshSec();
      const label = `${formatRefreshSec(sec)}s`;
      if (readout) readout.textContent = label;
      slider.setAttribute("aria-valuetext", `${formatRefreshSec(sec)} seconds`);
      slider.title = `Refresh every ${label}`;
    }

    function initRefreshSlider() {
      const slider = document.getElementById("refreshSec");
      const values = refreshPresetValues();
      const defaultSec = Number(slider.dataset.defaultSec || 5);
      const targetSec = Number.isFinite(defaultSec) && defaultSec > 0 ? defaultSec : 5;
      let bestIdx = 0;
      let bestDistance = Infinity;
      values.forEach((value, idx) => {
        const distance = Math.abs(value - targetSec);
        if (distance < bestDistance) {
          bestDistance = distance;
          bestIdx = idx;
        }
      });
      slider.max = String(values.length - 1);
      slider.value = String(bestIdx);
      updateRefreshSliderReadout();
    }

    function restartTimer() {
      if (timer) clearInterval(timer);
      if (isCustomRange()) {
        timer = null;
        return;
      }
      const sec = getRefreshSec();
      timer = setInterval(() => refreshData(false), sec * 1000);
    }

    function redrawAllFromModel() {
      chartIds.forEach((id) => {
        const m = chartModels[id];
        if (!m) return;
        drawChart(id, m.rawSeries || m.series || []);
      });
    }

    document.getElementById("btnRefresh").addEventListener("click", () => refreshData(true));
    document.getElementById("rangeSel").addEventListener("change", () => {
      const nextRange = getCurrentPageRangeState();
      if (nextRange && nextRange.mode === "relative") {
        if (window.GrafZoomState && typeof window.GrafZoomState.applyRangePresetSelection === "function") {
          zoomBaseRange = window.GrafZoomState.applyRangePresetSelection(nextRange).baseRange;
          renderZoomState();
        } else {
          clearZoomBaseRange();
        }
      } else {
        renderZoomState();
      }
      updateCustomRangeVisibility();
      refreshData(true);
    });
    document.getElementById("btnResetZoom").addEventListener("click", () => resetToZoomBaseRange());
    document.getElementById("refreshSec").addEventListener("input", () => {
      updateRefreshSliderReadout();
      restartTimer();
    });
    document.getElementById("customFrom").addEventListener("change", () => {
      const nextRange = getCurrentPageRangeState();
      if (nextRange && window.GrafZoomState && typeof window.GrafZoomState.applyManualCustomEdit === "function") {
        zoomBaseRange = window.GrafZoomState.applyManualCustomEdit(zoomBaseRange, nextRange).baseRange;
      }
      renderZoomState();
      refreshData(true);
    });
    document.getElementById("customTo").addEventListener("change", () => {
      const nextRange = getCurrentPageRangeState();
      if (nextRange && window.GrafZoomState && typeof window.GrafZoomState.applyManualCustomEdit === "function") {
        zoomBaseRange = window.GrafZoomState.applyManualCustomEdit(zoomBaseRange, nextRange).baseRange;
      }
      renderZoomState();
      refreshData(true);
    });
    document.getElementById("btnShiftLeft").addEventListener("click", () => shiftCustomRange(-1, "Shift left"));
    document.getElementById("btnShiftRight").addEventListener("click", () => shiftCustomRange(1, "Shift right"));
    document.getElementById("btnZoomOut25").addEventListener("click", () => scaleCustomRange(1.25, "Zoom out"));
    document.getElementById("btnZoomIn25").addEventListener("click", () => scaleCustomRange(0.75, "Zoom in"));
    initExportModals();
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        closeAllExportModals();
      }
    });
    const btnStatusDetails = document.getElementById("btnStatusDetails");
    if (btnStatusDetails) {
      btnStatusDetails.addEventListener("click", () => {
        statusDetailsOpen = !statusDetailsOpen;
        const detailsText = document.getElementById("statusDetails");
        const summaryText = document.getElementById("statusSummary");
        renderStatus({
          summary: summaryText ? summaryText.textContent : "Idle",
          diagnostics: detailsText ? detailsText.textContent : "",
          state: summaryText ? String(summaryText.dataset.state || "idle") : "idle",
        });
      });
    }
    document.getElementById("btnZoomHelp").addEventListener("click", () => {
      zoomHelpOpen = !zoomHelpOpen;
      document.getElementById("zoomHelpPop").classList.toggle("show", zoomHelpOpen);
    });
    document.addEventListener("click", (e) => {
      const wrap = document.querySelector(".help-wrap");
      if (!wrap) return;
      if (wrap.contains(e.target)) return;
      zoomHelpOpen = false;
      document.getElementById("zoomHelpPop").classList.remove("show");
    });
    bindYAxisControls();
    bindNormalizeControls();
    bindYawFilterControls();
    bindHeightControls();
    loadSavedChartHeights();
    initRefreshSlider();
    renderZoomState();
    refreshExportModalUiAll();
    window.addEventListener("resize", () => {
      if (resizeTimer) clearTimeout(resizeTimer);
      resizeTimer = setTimeout(redrawAllFromModel, 120);
    });
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (timer) { clearInterval(timer); timer = null; }
      } else {
        refreshData(true);
        restartTimer();
      }
    });

    (async () => {
      await loadTempChannelSelection();
      seriesChartIds.forEach((chartId) => loadSeriesSelection(chartId));
      const _urlp = new URLSearchParams(window.location.search);
      const _urlRange = _urlp.get("range");
      if (_urlRange) {
        const _sel = document.getElementById("rangeSel");
        if ([..._sel.options].some((o) => o.value === _urlRange)) _sel.value = _urlRange;
      }
      if (_urlRange === "custom") {
        const _f = _urlp.get("from");
        const _t = _urlp.get("to");
        try { if (_f) document.getElementById("customFrom").value = toLocalInputValue(new Date(_f)); } catch (_e) {}
        try { if (_t) document.getElementById("customTo").value = toLocalInputValue(new Date(_t)); } catch (_e) {}
      }
      updateCustomRangeVisibility();
      refreshData(true);
    })();
