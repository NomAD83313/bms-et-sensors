const test = require("node:test");
const assert = require("node:assert/strict");

const {
  cloneRangeState,
  rangeStatesEqual,
  isZoomedState,
  startZoomSession,
  applyRangePresetSelection,
  applyManualCustomEdit,
  applyShiftScale,
  resetToBaseRange,
} = require("../app/graf/static/graf_zoom_state.js");

test("cloneRangeState clones relative range state", () => {
  const source = { mode: "relative", range: "1h" };
  const cloned = cloneRangeState(source);
  assert.deepEqual(cloned, source);
  assert.notEqual(cloned, source);
});

test("cloneRangeState clones custom range state as numbers", () => {
  const cloned = cloneRangeState({ mode: "custom", fromMs: "1000", toMs: "2000" });
  assert.deepEqual(cloned, { mode: "custom", fromMs: 1000, toMs: 2000 });
});

test("rangeStatesEqual matches equal relative ranges", () => {
  assert.equal(
    rangeStatesEqual({ mode: "relative", range: "1h" }, { mode: "relative", range: "1h" }),
    true,
  );
});

test("rangeStatesEqual matches custom ranges inside tolerance", () => {
  assert.equal(
    rangeStatesEqual(
      { mode: "custom", fromMs: 10000, toMs: 20000 },
      { mode: "custom", fromMs: 10050, toMs: 20050 },
    ),
    true,
  );
});

test("rangeStatesEqual rejects custom ranges outside tolerance", () => {
  assert.equal(
    rangeStatesEqual(
      { mode: "custom", fromMs: 10000, toMs: 20000 },
      { mode: "custom", fromMs: 12050, toMs: 22050 },
    ),
    false,
  );
});

test("isZoomedState reports false when current equals base", () => {
  assert.equal(
    isZoomedState(
      { mode: "relative", range: "5m" },
      { mode: "relative", range: "5m" },
    ),
    false,
  );
});

test("isZoomedState reports true when current differs from base", () => {
  assert.equal(
    isZoomedState(
      { mode: "relative", range: "1h" },
      { mode: "custom", fromMs: 1000, toMs: 2000 },
    ),
    true,
  );
});

test("startZoomSession stores current range when base is missing", () => {
  assert.deepEqual(
    startZoomSession(null, { mode: "relative", range: "1h" }),
    { mode: "relative", range: "1h" },
  );
});

test("startZoomSession preserves original base on repeated drag zoom", () => {
  assert.deepEqual(
    startZoomSession(
      { mode: "relative", range: "1h" },
      { mode: "custom", fromMs: 1000, toMs: 2000 },
    ),
    { mode: "relative", range: "1h" },
  );
});

test("applyManualCustomEdit preserves existing base range", () => {
  const result = applyManualCustomEdit(
    { mode: "relative", range: "1h" },
    { mode: "custom", fromMs: 1000, toMs: 2000 },
  );
  assert.deepEqual(result, {
    baseRange: { mode: "relative", range: "1h" },
    currentRange: { mode: "custom", fromMs: 1000, toMs: 2000 },
  });
});

test("applyShiftScale preserves existing base range", () => {
  const result = applyShiftScale(
    { mode: "relative", range: "1h" },
    { mode: "custom", fromMs: 2000, toMs: 3000 },
  );
  assert.deepEqual(result, {
    baseRange: { mode: "relative", range: "1h" },
    currentRange: { mode: "custom", fromMs: 2000, toMs: 3000 },
  });
});

test("applyRangePresetSelection clears zoom base", () => {
  const result = applyRangePresetSelection({ mode: "relative", range: "5m" });
  assert.deepEqual(result, {
    baseRange: null,
    currentRange: { mode: "relative", range: "5m" },
  });
});

test("resetToBaseRange restores original base range", () => {
  assert.deepEqual(
    resetToBaseRange(
      { mode: "relative", range: "1h" },
      { mode: "custom", fromMs: 1000, toMs: 2000 },
    ),
    { mode: "relative", range: "1h" },
  );
});
