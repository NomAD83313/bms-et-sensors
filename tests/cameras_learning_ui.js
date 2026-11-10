/* Run with node tests/cameras_learning_ui.js. */
const fs = require('node:fs');

async function run() {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map(), requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'anomaly-multiplier' ? '1.25' : id === 'anomaly-learning-seconds' ? '120' : '',
      listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback; }
    });
    return elements.get(id);
  };
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    requests.push({url, body: JSON.parse(options.body)});
    return Promise.resolve({ok: true});
  };
  const source = fs.readFileSync('app/cameras/web/app.js', 'utf8');
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    source + '\nreturn {syncDetectionControls, anomalyControl};')(
      document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {confirm: () => false});

  const idle = {state: 'paused', baseline_ready: true, monitoring_enabled: false,
    recording_mode: 'continuous', learning_seconds: 180};
  api.syncDetectionControls(idle, false);
  const select = element('anomaly-learning-seconds');
  assert(select.value === '180', 'Restore the worker learning duration');
  assert(element('anomaly-learn').textContent.includes('3 min'), 'Show the selected duration on the action');
  select.value = '240'; select.listeners.change();
  assert(element('anomaly-learn').textContent.includes('4 min'), 'Update the action before learning starts');
  await api.anomalyControl('learn');
  assert(requests[0].url === 'api/anomaly/learn', 'Use the learning endpoint');
  assert(requests[0].body.learning_seconds === 240, 'Send the selected learning duration');
  api.syncDetectionControls({...idle, state: 'learning', learning_seconds: 240}, false);
  assert(select.disabled && element('anomaly-learn').disabled, 'Lock the duration during learning');

  const html = fs.readFileSync('app/cameras/web/index.html', 'utf8');
  for (const seconds of [60, 120, 180, 240])
    assert(html.includes(`option value="${seconds}"`), `Expose the ${seconds}-second option`);
  console.log('Learning duration selection and request UI passed.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
