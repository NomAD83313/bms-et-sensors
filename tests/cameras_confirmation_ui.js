/* Run with node tests/cameras_confirmation_ui.js. */
async function run(source) {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map(), requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {value: id === 'anomaly-multiplier' ? '1.25' : '',
      listeners: {}, addEventListener(type, callback) { this.listeners[type] = callback; }});
    return elements.get(id);
  };
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    requests.push({url, options});
    return Promise.resolve({ok: true, json: async () => ({id: 'confirmation-1'})});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    source + '\nreturn {syncConfirmation, syncDetectionControls};')(
    document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {});
  api.syncConfirmation({confirmation_frames: 2}, false);
  const select = element('anomaly-confirmation');
  assert(select.value === '2', 'Use current worker setting');
  select.value = '1'; await select.listeners.change();
  assert(requests[0].url === 'api/anomaly/confirmation', 'Use confirmation endpoint');
  assert(JSON.parse(requests[0].options.body).confirmation_frames === 1, 'Apply selected count');
  assert(select.disabled, 'Wait for worker acknowledgment');
  api.syncConfirmation({confirmation_frames: 1, last_request: 'old'}, false);
  assert(select.disabled, 'Stale status must not confirm command');
  const state = {confirmation_frames: 1, last_request: 'confirmation-1', state: 'normal', baseline_ready: true, monitoring_enabled: true};
  api.syncConfirmation(state, false); api.syncDetectionControls(state, false);
  assert(!select.disabled && select.value === '1', 'Confirmed count must remain selected');
  assert(element('confirmation-status').textContent.includes('No new learning'), 'Explain that baseline is retained');
  assert(!element('anomaly-pause').disabled, 'Monitoring remains active and can be paused');
  console.log('Confirmation dropdown, applied setting and baseline retention UI passed.');
}
const fs = require('node:fs');
run(fs.readFileSync('app/cameras/web/app.js', 'utf8')).catch(error => { console.error(error); process.exitCode = 1; });
