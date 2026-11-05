/* Run with node tests/cameras_health_ui.js, or any JavaScript host. */
async function runCameraHealthTests(html) {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map();
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', className: '', appendChild() {}, addEventListener() {}, querySelectorAll() { return []; }
    });
    return elements.get(id);
  };
  const document = {getElementById: element, createElement: () => element(Symbol())};
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const api = new Function('document', 'window', 'fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'AbortController',
    script + '\nreturn {services, controllableTargets, statusLinks, probe, summarizeTargetState};')(
      document, {location: {protocol: 'http:', hostname: 'localhost'}}, () => new Promise(() => {}),
      () => {}, () => {}, () => {}, class { constructor() { this.signal = {}; } abort() {} });
  const camera = api.services.find(service => service.id === 'cameras');
  assert(camera && camera.target === 'cameras' && camera.group === 'device', 'Camera probe must be a device application');
  assert(api.controllableTargets.includes('cameras'), 'Cameras needs service controls');
  assert(api.statusLinks.cameras === '/cameras/', 'Camera status should open its application');
  const base = {recording_mode: 'anomaly', baseline_ready: true, pre_seconds: 7, post_seconds: 10, clips: []};
  for (const [recorder, detector, badge, level] of [
    ['buffering', 'normal', 'up', 'ok'],
    ['paused', 'paused', 'paused', 'warn'],
    ['paused', 'learning', 'learning', 'warn'],
    ['saving_event', 'anomaly', 'anomaly', 'warn'],
    ['buffering', 'offline', 'degraded', 'warn'],
    ['offline', 'normal', 'degraded', 'warn'],
    ['paused', 'untrained', 'degraded', 'warn'],
  ]) {
    const data = {...base, service: recorder, analysis_state: detector};
    const shared = new Map([[camera.url, Promise.resolve({ok: true, status: 200, text: JSON.stringify(data)})]]);
    await api.probe(camera, shared);
    assert(element('st-cameras').textContent === badge, 'Incorrect camera badge for ' + recorder + '/' + detector);
    assert(element('st-cameras').className === 'status ' + level, 'Incorrect severity for ' + detector);
    assert(element('dt-cameras').textContent.includes('7s before / 10s after'), 'Event windows must appear in the card');
  }
  await api.probe(camera, new Map([['/probe/svcctl-data', {manual_pauses: {cameras: {}}}]]));
  assert(element('st-cameras').textContent === 'paused', 'Manual service stop should not be shown as a probe failure');
  await api.probe(camera, new Map([[camera.url, Promise.resolve({ok: false, status: 502, text: ''})]]));
  assert(element('st-cameras').textContent === 'down', 'HTTP failure must remain visible');
  const damaged = {...base, service: 'buffering', analysis_state: 'normal', preview_quality: {state: 'recovering', decoder_errors: 3, discarded_frames: 12}};
  await api.probe(camera, new Map([[camera.url, Promise.resolve({ok: true, status: 200, text: JSON.stringify(damaged)})]]));
  assert(element('st-cameras').textContent === 'degraded', 'Decoder recovery must not be shown as healthy');
  assert(element('dt-cameras').textContent.includes('Decoder errors: 3'), 'Decoder diagnostics should be visible');
  assert(api.summarizeTargetState({recorder: 'running', dashboard: 'running', anomaly: 'exited'}, false).startsWith('partial'), 'Partial camera stacks must be visible');
  return 'System Health camera states, navigation and group controls passed.';
}

if (typeof require !== 'undefined' && require.main === module) {
  const fs = require('node:fs');
  const path = require('node:path');
  runCameraHealthTests(fs.readFileSync(path.join(__dirname, '../dashboard/simple-dash/health.html'), 'utf8'))
    .then(console.log).catch(error => { console.error(error); process.exitCode = 1; });
}
