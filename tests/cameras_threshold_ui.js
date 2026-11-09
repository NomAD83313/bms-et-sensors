/* Run with node tests/cameras_threshold_ui.js, or any JavaScript host. */
function runThresholdUITests(source) {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map();
  const requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'anomaly-multiplier' ? '1.25' : '',
      listeners: {},
      addEventListener(type, handler) { this.listeners[type] = handler; }
    });
    return elements.get(id);
  };
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (options) { requests.push({url, options}); return Promise.resolve({ok: true}); }
    return new Promise(() => {});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    source + '\nreturn {syncThreshold, parseMultiplier, syncDetectionControls};')(
      document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {confirm: () => false});
  const input = element('anomaly-multiplier');
  api.syncThreshold({multiplier: 4.5}, false);
  assert(input.value === '4.5', 'Applied value should initialize the field');
  document.activeElement = input;
  input.value = '6,5'; input.listeners.input();
  api.syncThreshold({multiplier: 4.5}, false);
  assert(input.value === '6,5', 'Polling must preserve text while editing');
  assert(!element('anomaly-apply').disabled, 'A draft above the old limit should be applicable');
  document.activeElement = null;
  api.syncThreshold({multiplier: 4.5}, false);
  assert(input.value === '6,5', 'Blurred unsaved drafts must be preserved too');
  let prevented = false;
  input.listeners.keydown({key: 'Enter', preventDefault() { prevented = true; }});
  assert(prevented, 'Enter must apply without navigating');
  assert(requests[0].url === 'api/anomaly/configure', 'Apply must use configure instead of starting detection');
  assert(JSON.parse(requests[0].options.body).multiplier === 6.5, 'Decimal commas must be parsed correctly');
  // Editing again while a command is pending must not lose the newer draft.
  input.value = '7.5'; input.listeners.input();
  api.syncThreshold({multiplier: 6.5}, false);
  assert(input.value === '7.5', 'An acknowledgement must not overwrite a newer draft');
  assert(!element('anomaly-apply').disabled, 'A newer draft must remain applicable');
  element('anomaly-apply').onclick();
  assert(JSON.parse(requests[1].options.body).multiplier === 7.5, 'Apply button must submit the edited value');
  api.syncThreshold({multiplier: 7.5}, false);
  assert(element('anomaly-apply').disabled, 'Confirmed unchanged values should not be resubmitted');
  assert(api.parseMultiplier('0.1') === .1 && api.parseMultiplier('20') === 20, 'New range must accept both boundaries');
  assert(api.parseMultiplier('21') === null && api.parseMultiplier('') === null, 'Invalid input must be rejected');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'paused', baseline_ready: true, monitoring_enabled: false}, false);
  assert(!element('anomaly-monitor').disabled && element('anomaly-pause').disabled, 'A paused session should offer a combined start');
  assert(element('anomaly-monitor').textContent === 'Start detection & recording', 'Anomaly mode must explain linked recording');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'normal', baseline_ready: true, monitoring_enabled: true}, false);
  assert(element('anomaly-monitor').disabled && !element('anomaly-pause').disabled, 'A running session should offer pause');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'learning', monitoring_enabled: false}, false);
  assert(element('anomaly-monitor').disabled && !element('anomaly-pause').disabled, 'Learning must be cancellable without starting recording');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'unavailable', monitoring_enabled: true}, false);
  assert(!element('anomaly-pause').disabled, 'A camera outage must not prevent pausing a running session');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'offline', recording_enabled: true}, true);
  assert(!element('anomaly-pause').disabled && element('anomaly-monitor').disabled, 'An offline worker must still allow stopping the buffer');
  api.syncDetectionControls({recording_mode: 'anomaly', state: 'untrained', monitoring_enabled: false}, false);
  assert(element('anomaly-monitor').disabled, 'Starting requires a learned baseline');
  api.syncDetectionControls({recording_mode: 'continuous', state: 'paused', baseline_ready: true}, false);
  assert(element('anomaly-monitor').textContent === 'Start detection', 'Continuous recording must retain independent controls');
  return 'Threshold editing and combined detection/recording UI tests passed.';
}

if (typeof require !== 'undefined' && require.main === module) {
  const fs = require('node:fs');
  const path = require('node:path');
  console.log(runThresholdUITests(fs.readFileSync(path.join(__dirname, '../app/cameras/web/app.js'), 'utf8')));
}
