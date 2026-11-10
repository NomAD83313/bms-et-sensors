/* Run with node tests/cameras_roi_ui.js. */
const fs = require('node:fs');
const assert = require('node:assert/strict');

async function run() {
  const elements = new Map(), requests = [], timeouts = [];
  let nextResponse = null;
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'anomaly-multiplier' ? '1.25' : id === 'anomaly-input-size' ? '1280x720' : '',
      hidden: false, style: {}, dataset: {}, options: [], listeners: {},
      addEventListener(type, listener) { this.listeners[type] = listener; },
      getBoundingClientRect() { return this.rect || {left: 100, top: 50, width: 960, height: 540}; },
      setPointerCapture() {}, scrollIntoView() {}
    });
    return elements.get(id);
  };
  const image = element('live'); image.naturalWidth = 960; image.naturalHeight = 540;
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    const id = `roi-${requests.length + 1}`;
    const body = JSON.parse(options.body);
    requests.push({url, body, id});
    if (nextResponse) { const response = nextResponse; nextResponse = null; return Promise.resolve(response); }
    const full = body.roi && body.roi.x === 0 && body.roi.y === 0 && body.roi.width === 1 && body.roi.height === 1;
    return Promise.resolve({ok: true, json: async () => full ? {id, roi: null} : {id}});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    fs.readFileSync('app/cameras/web/app.js', 'utf8') + `
      return {beginROI, cancelROI, applyROI, roiPoint, fittedInput, applyModel, applyInputSize,
        anomalyControl, applyConfirmation,
        update(data) { latestAnomalyData = data; latestAnomalyState = data.state; latestAnomalyUnavailable = false; syncAnalysisControls(data, false); }};`)(
      document, fetch, class {}, () => {}, (callback, delay) => timeouts.push({callback, delay}), {now: () => 0}, {});
  const state = {state: 'paused', model_key: 'mobilenetv3', input_size: '1280x720',
    multiplier: 1.25, confirmation_frames: 2, baseline_ready: true, roi: null,
    source_width: 4000, source_height: 3000};
  api.update({...state, source_width: null, source_height: null});
  assert.equal(element('roi-select').disabled, true, 'Wait for actual source dimensions');
  image.hidden = true; api.update(state);
  assert.equal(element('roi-select').disabled, true, 'Wait for a loaded live image');
  image.hidden = false; api.update(state);
  assert.equal(element('roi-select').disabled, false);

  // A 4:3 source occupies x=220..940 inside the 960x540 preview at x=100.
  assert.equal(api.roiPoint({clientX: 150, clientY: 200}), null, 'Ignore preview letterbox bars');
  const center = api.roiPoint({clientX: 580, clientY: 320});
  assert.deepEqual(center, {x: .5, y: .5});
  image.rect = {left: 10, top: 20, width: 1000, height: 800};
  const outerBars = api.roiPoint({clientX: 510, clientY: 50});
  assert.equal(outerBars, null, 'Ignore outer object-fit bars as well as embedded bars');
  const fittedPoint = api.roiPoint({clientX: 285, clientY: 307.5});
  assert.ok(Math.abs(fittedPoint.x - .2) < 1e-9 && Math.abs(fittedPoint.y - .3) < 1e-9,
    'Map through both object-fit scaling and preview padding');
  image.rect = null;

  const pointer = (type, x, y, pointerId = 1) => element('roi-layer').listeners[type]({
    clientX: x, clientY: y, pointerId, button: 0, preventDefault() {}});
  api.beginROI();
  assert.equal(requests.length, 0, 'Selecting an area must not change active analysis');
  assert.equal(element('anomaly-learn').disabled, true, 'Do not learn with an unapplied area draft');
  pointer('pointerdown', 150, 200);
  pointer('pointerup', 580, 320);
  assert.equal(element('roi-apply').disabled, true, 'A drag starting in padding is ignored');
  pointer('pointerdown', 400, 185); pointer('pointerdown', 620, 220, 2);
  pointer('pointermove', 760, 455); pointer('pointerup', 760, 455);
  assert.equal(element('roi-apply').disabled, false);
  assert.equal(requests.length, 0, 'Dragging a draft sends no command');
  assert.ok(element('roi-status').textContent.includes('960 × 720'), 'A 4:3 area fits inside the selected 1280x720 maximum');
  assert.deepEqual(api.fittedInput({x: 0, y: 0, width: .75, height: 1}), {width: 720, height: 720, label: '720 × 720'}, 'Square ROI keeps square input');
  assert.deepEqual(api.fittedInput({x: 0, y: 0, width: .1, height: .1}),
    {width: 400, height: 300, label: '400 × 300'}, 'A small ROI keeps its native pixels');
  api.update({...state, source_width: 3280, source_height: 2464});
  assert.deepEqual(api.fittedInput({x: .242994, y: .120816, width: .524776, height: .425954}, '1920x1080'),
    {width: 1722, height: 1051, label: '1722 × 1051'}, 'Fractional pixel bounds match the backend without upscaling');
  api.update(state);
  await element('roi-apply').onclick();
  assert.deepEqual(requests[0], {url: 'api/anomaly/roi', body: {roi: {x: .25, y: .25, width: .5, height: .5}, multiplier: 1.25}, id: 'roi-1'});
  for (const id of ['roi-select', 'roi-apply', 'roi-full', 'roi-cancel', 'anomaly-model', 'anomaly-input-size', 'anomaly-confirmation', 'anomaly-learn', 'anomaly-monitor', 'anomaly-pause']) {
    assert.equal(element(id).disabled, true, `${id} waits for the ROI acknowledgment`);
  }
  await api.anomalyControl('learn'); await api.anomalyControl('configure'); await api.anomalyControl('pause');
  await api.applyConfirmation(); await api.applyInputSize(); await api.applyModel();
  assert.equal(requests.length, 1, 'Pending ROI cannot be overwritten by another command');
  api.update({...state, roi: requests[0].body.roi, last_request: 'older'});
  assert.equal(element('roi-cancel').disabled, true, 'Matching coordinates alone are insufficient');
  api.update({...state, roi: null, last_request: 'roi-1'});
  assert.equal(element('roi-cancel').disabled, true, 'Matching command id alone is insufficient');
  for (const timeout of timeouts.filter(timeout => timeout.delay === 15000)) timeout.callback();
  assert.equal(element('anomaly-learn').disabled, true, 'Timeout keeps commands locked until acknowledgment');
  const applied = {...state, roi: requests[0].body.roi, last_request: 'roi-1', baseline_ready: false,
    state: 'untrained', retraining_required: true, effective_input_width: 960, effective_input_height: 720};
  api.update(applied);
  assert.equal(element('roi-select').disabled, false);
  assert.equal(element('anomaly-learn').disabled, false);
  assert.equal(element('anomaly-monitor').disabled, true, 'New learning is required before detection');
  assert.ok(element('roi-status').textContent.includes('New learning required'));
  assert.ok(element('input-size-status').textContent.includes('960 × 720') && element('input-size-status').textContent.includes('maximum 1280 × 720'),
    'Display effective area dimensions separately from the maximum preset');

  api.beginROI(); pointer('pointerdown', 300, 150); pointer('pointerup', 500, 350);
  api.cancelROI();
  assert.equal(requests.length, 1, 'Cancel restores the applied area without a command');
  assert.equal(element('roi-label').textContent, 'ANALYSIS AREA');
  await element('roi-full').onclick();
  assert.equal(requests[1].body.roi, null, 'Full frame is an explicit application, not an editing reset');
  api.update({...applied, roi: null, last_request: 'roi-2', effective_input_width: 1280, effective_input_height: 720});
  assert.ok(element('input-size-status').textContent.includes('1280 × 720'));
  assert.equal(element('roi-full').disabled, true, 'Full frame is already applied');
  api.beginROI(); pointer('pointerdown', 400, 185); pointer('pointerup', 402, 187);
  assert.equal(element('roi-apply').disabled, true, 'Reject accidental tiny drags');
  api.cancelROI(); api.update({...applied, last_request: 'roi-2'});
  api.beginROI(); pointer('pointerdown', 220, 50); pointer('pointerup', 940, 590);
  assert.ok(element('roi-status').textContent.includes('1280 × 720'), 'Selecting the entire image uses the full-frame preset');
  await element('roi-apply').onclick();
  assert.deepEqual(requests[2].body.roi, {x: 0, y: 0, width: 1, height: 1});
  api.update({...state, roi: null, last_request: 'roi-3', baseline_ready: false, state: 'untrained', retraining_required: true});
  assert.equal(element('roi-select').disabled, false, 'Canonical full-frame acknowledgment releases the ROI command lock');
  assert.equal(element('anomaly-learn').disabled, false, 'Allow learning after the server canonicalizes a full rectangle to null');
  api.beginROI(); pointer('pointerdown', 220, 50); pointer('pointerup', 940, 590);
  assert.equal(element('roi-apply').disabled, true, 'A full rectangle equals an already applied full frame');
  api.cancelROI();
  let parsedError = false;
  nextResponse = {ok: false, json: async () => {parsedError = true; throw SyntaxError('Plain-text response');}};
  await api.applyROI({x: .1, y: .1, width: .4, height: .4});
  assert.equal(parsedError, false, 'Do not parse a plain-text control error as JSON');
  assert.ok(element('roi-status').textContent.includes('Check the selected area and live image'), 'Explain an application error without parser details');
  assert.equal(element('anomaly-learn').disabled, false, 'An unsuccessful control request releases the pending lock');

  const html = fs.readFileSync('app/cameras/web/index.html', 'utf8');
  assert.ok(html.includes('Maximum input') && !html.includes('INT8'));
  assert.ok(html.includes('snapshots, heatmaps and videos keep the full frame'));
  console.log('ROI draft/apply/reset, source geometry, letterboxing, aspect preservation and command acknowledgment UI passed.');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
