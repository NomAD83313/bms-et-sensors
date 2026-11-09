(function initGrafZoomState(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.GrafZoomState = api;
})(typeof window !== "undefined" ? window : globalThis, function createGrafZoomState() {
  function cloneRangeState(state) {
    if (!state || typeof state !== "object") return null;
    if (state.mode === "relative") return { mode: "relative", range: String(state.range || "") };
    if (state.mode === "custom") return { mode: "custom", fromMs: Number(state.fromMs), toMs: Number(state.toMs) };
    return null;
  }

  function rangeStatesEqual(left, right, toleranceMs = 1000) {
    if (!left || !right || left.mode !== right.mode) return false;
    if (left.mode === "relative") return String(left.range || "") === String(right.range || "");
    if (left.mode === "custom") {
      return (
        Math.abs(Number(left.fromMs) - Number(right.fromMs)) <= toleranceMs &&
        Math.abs(Number(left.toMs) - Number(right.toMs)) <= toleranceMs
      );
    }
    return false;
  }

  function isZoomedState(baseRange, currentRange) {
    if (!baseRange || !currentRange) return false;
    return !rangeStatesEqual(baseRange, currentRange);
  }

  function startZoomSession(baseRange, currentRange) {
    return cloneRangeState(baseRange) || cloneRangeState(currentRange);
  }

  function applyRangePresetSelection(nextRange) {
    return {
      baseRange: null,
      currentRange: cloneRangeState(nextRange),
    };
  }

  function applyManualCustomEdit(baseRange, nextRange) {
    return {
      baseRange: cloneRangeState(baseRange),
      currentRange: cloneRangeState(nextRange),
    };
  }

  function applyShiftScale(baseRange, nextRange) {
    return {
      baseRange: cloneRangeState(baseRange),
      currentRange: cloneRangeState(nextRange),
    };
  }

  function resetToBaseRange(baseRange, currentRange) {
    return cloneRangeState(baseRange) || cloneRangeState(currentRange);
  }

  return {
    cloneRangeState,
    rangeStatesEqual,
    isZoomedState,
    startZoomSession,
    applyRangePresetSelection,
    applyManualCustomEdit,
    applyShiftScale,
    resetToBaseRange,
  };
});
