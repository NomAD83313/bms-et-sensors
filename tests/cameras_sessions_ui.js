/* Run with node tests/cameras_sessions_ui.js. */
const fs = require('node:fs');
const assert = require('node:assert/strict');

async function run() {
  const elements = new Map(), requests = [];
  let allowConfirm = true, failRequest = false;
  const makeElement = () => ({value: '', hidden: false, style: {}, dataset: {}, options: [], children: [], listeners: {},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    addEventListener(type, listener) { this.listeners[type] = listener; },
    setAttribute(name, value) { this[name] = value; },
    getBoundingClientRect() { return {left: 0, top: 0, width: 960, height: 540}; }});
  const element = id => {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  element('live').naturalWidth = 960; element('live').naturalHeight = 540;
  element('anomaly-learning-seconds').value = '60';
  element('anomaly-multiplier').value = '1.25';
  const document = {hidden: false, activeElement: null, getElementById: element,
    createElement: makeElement, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    const id = `session-command-${requests.length + 1}`;
    requests.push({url, body: JSON.parse(options.body), id});
    return Promise.resolve({ok: !failRequest, json: async () => ({id})});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    fs.readFileSync('app/cameras/web/app.js', 'utf8') + `
      return {sessionCommand, anomalyControl, update(data) {
        latestAnomalyData = data; latestAnomalyState = data.state; latestAnomalyUnavailable = false;
        syncAnalysisControls(data, false);
      }};`)(document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {confirm: () => allowConfirm});
  const base = {state: 'paused', model_key: 'mobilenetv3', input_size: '768x432', roi: null,
    source_width: 1920, source_height: 1080, multiplier: 1.25, confirmation_frames: 1,
    learning_seconds: 60, learning_sessions: [], session_limit: 8, sessions_compatible: false};
  api.update(base);
  assert.equal(element('session-add').disabled, true, 'Create the first saved session before adding');
  element('session-name').value = 'A permitted object'; element('session-note').value = 'Not tied to a time of day';
  await api.anomalyControl('learn');
  assert.equal(requests[0].body.session_name, 'A permitted object');
  assert.equal(requests[0].body.session_note, 'Not tied to a time of day');
  assert.equal(requests[0].body.learning_seconds, 60);
  await api.sessionCommand('add-session');
  assert.equal(requests.length, 1, 'Do not overwrite an unacknowledged request');
  const first = {id: 'a'.repeat(32), name: '<normal state>', note: 'Any reason', created: 1,
    frames: 30, calibration_threshold: 1.2};
  const saved = {...base, baseline_ready: true, sessions_compatible: true, learning_sessions: [first],
    last_request: requests[0].id};
  api.update(saved);
  assert.equal(element('session-add').disabled, false);
  let details = element('learning-sessions').children[0].children[1];
  assert.equal(details.children[0].value, '<normal state>', 'Names remain plain input values');
  assert.equal(details.children[3].children[0].disabled, true, 'Save is disabled until metadata changes');
  details.children[0].value = 'Renamed'; details.children[1].value = 'New comment';
  details.children[0].listeners.input();
  assert.equal(details.children[3].children[0].disabled, false);
  api.update({...saved, state: 'training'});
  api.update(saved);
  details = element('learning-sessions').children[0].children[1];
  assert.equal(details.children[0].value, 'Renamed', 'A control-state update must preserve the edited name');
  assert.equal(details.children[1].value, 'New comment', 'A control-state update must preserve the edited comment');
  await details.children[3].children[0].onclick();
  assert.equal(requests[1].url, 'api/anomaly/rename-session');
  assert.equal(requests[1].body.session_id, first.id);
  assert.equal(requests[1].body.session_note, 'New comment');
  api.update({...saved, last_request: requests[1].id});
  await api.sessionCommand('add-session');
  assert.equal(requests[2].url, 'api/anomaly/add-session');
  assert.equal(requests[2].body.session_id, undefined, 'Adding keeps all previous sessions');
  api.update({...saved, last_request: requests[2].id});
  allowConfirm = false;
  await api.sessionCommand('remove-session', first.id);
  assert.equal(requests.length, 3, 'Confirm removal before sending the request');
  allowConfirm = true;
  await api.sessionCommand('replace-session', first.id, 'Replacement', 'Object moved');
  assert.equal(requests[3].url, 'api/anomaly/replace-session');
  assert.equal(requests[3].body.session_id, first.id);
  api.update({...saved, last_request: requests[3].id,
    learning_sessions: Array.from({length: 8}, (_, i) => ({...first, id: String(i).repeat(32)}))});
  assert.equal(element('session-add').disabled, true, 'Eight slots are the limit');
  assert.equal(element('learning-sessions').children[0].children[1].children[3].children[1].disabled, false,
    'Replacement remains possible when all slots are full');
  api.update({...saved, sessions_compatible: false});
  assert.equal(element('session-add').disabled, true);
  assert.equal(element('session-rebuild').disabled, true);
  api.update(saved);
  failRequest = true;
  details = element('learning-sessions').children[0].children[1];
  details.children[1].value = 'Keep this draft'; details.children[1].listeners.input();
  await details.children[3].children[0].onclick();
  assert.equal(element('learning-sessions').children[0].children[1].children[1].value, 'Keep this draft',
    'A failed save must preserve the draft for retry');
  await api.sessionCommand('rebuild-sessions');
  assert.ok(element('session-status').textContent.includes('Session request failed'));
  assert.equal(element('session-add').disabled, false, 'An HTTP failure releases the request lock');
  api.update({...saved, state: 'training'});
  assert.equal(element('session-add').disabled, true, 'Training locks session operations');
  console.log('Normal-session naming, comments, acknowledgment, replacement, slot limit and failure UI passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
