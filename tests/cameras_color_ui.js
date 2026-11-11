/* Run with node tests/cameras_color_ui.js. */
const fs = require('node:fs');
const assert = require('node:assert/strict');

async function run() {
  const elements = new Map(), requests = [], timeouts = [];
  let fail = false;
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: '', style: {}, dataset: {}, options: [], listeners: {},
      addEventListener(type, callback) { this.listeners[type] = callback; }});
    return elements.get(id);
  };
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    requests.push({url, body: JSON.parse(options.body)});
    return Promise.resolve({ok: !fail, json: async () => ({id: 'color-' + requests.length})});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    fs.readFileSync('app/cameras/web/app.js', 'utf8') + `
      return {applyColorMode, anomalyControl, update(data) {
        latestAnomalyData = data; latestAnomalyState = data.state; latestAnomalyUnavailable = false;
        syncAnalysisControls(data, false);
      }};`)(document, fetch, class {}, () => {}, (callback, delay) => timeouts.push({callback, delay}), {now: () => 0}, {});
  const base = {state: 'normal', model_key: 'mobilenetv3', input_size: '768x432', roi: null,
    color_mode: 'rgb', baseline_ready: true, monitoring_enabled: true, multiplier: 1.3,
    confirmation_frames: 1, learning_seconds: 120, learning_sessions: []};
  api.update(base);
  assert.equal(element('anomaly-color-rgb').checked, true);
  assert.equal(element('anomaly-color-grayscale').disabled, false);
  await api.applyColorMode('rgb');
  assert.equal(requests.length, 0, 'Selecting the applied color mode sends no command');
  await element('anomaly-color-grayscale').listeners.change();
  assert.deepEqual(requests[0], {url: 'api/anomaly/color', body: {color_mode: 'grayscale', multiplier: 1.3}});
  for (const id of ['anomaly-color-rgb', 'anomaly-color-grayscale', 'anomaly-learn', 'anomaly-model', 'anomaly-input-size', 'session-add'])
    assert.equal(element(id).disabled, true, `${id} must wait for acknowledgment`);
  await api.anomalyControl('learn');
  assert.equal(requests.length, 1, 'A pending color command cannot be overwritten');
  api.update({...base, color_mode: 'grayscale', last_request: 'older'});
  assert.equal(element('anomaly-color-rgb').disabled, true, 'The mode alone is not acknowledgment');
  api.update({...base, last_request: 'color-1'});
  assert.equal(element('anomaly-color-rgb').disabled, true, 'The command ID alone is not acknowledgment');
  for (const timeout of timeouts.filter(item => item.delay === 15000)) timeout.callback();
  assert.equal(element('anomaly-color-rgb').disabled, true, 'A delayed acknowledgment keeps the setting locked');
  const gray = {...base, color_mode: 'grayscale', last_request: 'color-1',
    state: 'untrained', baseline_ready: false, monitoring_enabled: false, retraining_required: true};
  api.update(gray);
  assert.equal(element('anomaly-color-grayscale').checked, true);
  assert.equal(element('anomaly-color-grayscale').disabled, false);
  assert.equal(element('anomaly-learn').disabled, false);
  assert.equal(element('anomaly-monitor').disabled, true);
  assert.ok(element('color-status').textContent.includes('Learn a new baseline'));
  api.update({...gray, state: 'learning'});
  await api.applyColorMode('rgb');
  assert.equal(requests.length, 1, 'Learning locks color-mode changes');
  api.update(gray);
  fail = true;
  await api.applyColorMode('rgb');
  assert.equal(element('anomaly-color-grayscale').checked, true, 'A failure restores the applied mode');
  assert.equal(element('anomaly-color-rgb').disabled, false);
  assert.ok(element('color-status').textContent.includes('Could not change'));
  const html = fs.readFileSync('app/cameras/web/index.html', 'utf8');
  assert.ok(html.includes('name="analysis-color"') && html.includes('type="radio"'));
  console.log('Color/grayscale radios, command acknowledgment, retraining and failure UI passed.');
}
run().catch(error => {console.error(error); process.exitCode = 1;});
