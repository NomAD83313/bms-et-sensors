/* Run with node tests/cameras_health_ui.js, or any JavaScript host. */
async function runCameraHealthTests(html) {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map();
  const makeElement = (tag = 'div') => {
    const el = {tagName: tag.toUpperCase(), className: '', href: '', children: [], attributes: {}, _text: '', _html: '',
      appendChild(child) { this.children.push(child); return child; },
      setAttribute(name, value) { this.attributes[name] = String(value); },
      addEventListener() {}, querySelectorAll() { return []; }};
    Object.defineProperty(el, 'textContent', {get() { return this._text; }, set(value) { this._text = String(value); this.children = []; }});
    Object.defineProperty(el, 'innerHTML', {get() { return this._html; }, set(value) { this._html = String(value); this.children = []; }});
    return el;
  };
  const element = id => {
    if (!elements.has(id)) elements.set(id, makeElement());
    return elements.get(id);
  };
  const document = {getElementById: element, createElement: tag => makeElement(tag)};
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  const build = fetchImplementation => new Function('document', 'window', 'fetch', 'setTimeout', 'clearTimeout', 'setInterval', 'AbortController',
    script + '\nreturn {services, controllableTargets, probe, summarizeTargetState, refreshControlStates, cameraTargets: () => cameraTargets};')(
      document, {location: {protocol: 'http:', hostname: 'localhost'}}, fetchImplementation,
      () => {}, () => {}, () => {}, class { constructor() { this.signal = {}; } abort() {} });
  const reply = (body, status = 200) => Promise.resolve({ok: status < 400, status, json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body))});
  // A fake network: the camera list plus each camera's dashboard status, by URL.
  const network = routes => url => (url in routes ? routes[url] : reply('', 404));
  const camera = (id, label, target, path) => ({id, label, target, path, status_url: path + 'api/status', services: {}});
  const wifi = camera('wifi', 'Camera WiFi', 'cameras', '/cameras/');
  const pi = camera('pi', 'Pi Camera', 'pi-camera', '/cameras/pi/');
  const garage = camera('garage', 'Garage', 'cam-garage', '/cameras/garage/');
  const listing = (cameras, extra = {}) => reply({success: true, cameras, manual_pauses: {}, ...extra});
  const base = {recording_mode: 'anomaly', baseline_ready: true, pre_seconds: 7, post_seconds: 10, clips: []};
  const status = (recorder, detector, extra = {}) => reply({...base, service: recorder, analysis_state: detector, ...extra});

  const api = build(() => new Promise(() => {}));
  const card = api.services.find(service => service.id === 'cameras');
  assert(card && card.type === 'cameras' && card.url === '/api/control/cameras' && card.group === 'device', 'One combined Cameras card is a device application');
  assert(!api.services.some(service => service.id === 'pi-camera'), 'There is no separate Pi camera card any more');
  assert(!api.controllableTargets.includes('cameras') && !api.controllableTargets.includes('pi-camera'), 'Camera controls come from the camera list');

  // Badge and severity of a single camera, as before.
  for (const [recorder, detector, badge, level] of [
    ['buffering', 'normal', 'up', 'ok'],
    ['paused', 'paused', 'paused', 'warn'],
    ['paused', 'learning', 'learning', 'warn'],
    ['saving_event', 'anomaly', 'anomaly', 'warn'],
    ['buffering', 'offline', 'degraded', 'warn'],
    ['offline', 'normal', 'degraded', 'warn'],
    ['paused', 'untrained', 'degraded', 'warn'],
  ]) {
    const fetcher = network({'/api/control/cameras': listing([wifi]), [wifi.status_url]: status(recorder, detector)});
    await build(fetcher).probe(card, new Map());
    assert(element('st-cameras').textContent === badge, 'Incorrect camera badge for ' + recorder + '/' + detector);
    assert(element('st-cameras').className === 'status ' + level, 'Incorrect severity for ' + detector);
    const line = element('dt-cameras').children[0];
    assert(line.children[0].textContent === 'Camera WiFi' && line.children[0].href === '/cameras/', 'Each camera links to its own application');
    assert(line.children[1].textContent === badge && line.children[1].className === 'status ' + level, 'The camera line repeats its own badge');
    assert(line.children[1].tagName === 'A' && line.children[1].href === wifi.path, 'Every WiFi status opens the WiFi application');
    assert(line.children[1].attributes['aria-label'] === `Open Camera WiFi: ${badge}`, 'Status links identify the camera and state');
    assert(line.children[2].textContent.includes('7s before / 10s after'), 'Event windows must appear in the camera line');
  }

  // Several cameras: one line each, one badge for the worst state.
  const up = status('buffering', 'normal');
  const routes = {'/api/control/cameras': listing([wifi, pi, garage]), [wifi.status_url]: up, [pi.status_url]: up, [garage.status_url]: up};
  await build(network(routes)).probe(card, new Map());
  assert(element('st-cameras').textContent === 'up' && element('st-cameras').className === 'status ok', 'All cameras up means up');
  const lines = element('dt-cameras').children;
  assert(lines.length === 4, 'One line per camera plus the analysis note');
  assert(lines.slice(0, 3).map(line => line.children[0].textContent).join() === 'Camera WiFi,Pi Camera,Garage', 'Cameras keep the controller order');
  assert(lines.slice(0, 3).every((line, index) => line.children[1].tagName === 'A' && line.children[1].href === [wifi, pi, garage][index].path), 'Each colored badge opens its own camera');
  assert(element('dt-cameras').children[3].textContent === 'Analysis: one shared worker for all cameras', 'The shared analysis worker is named');

  await build(network({...routes, [pi.status_url]: status('saving_event', 'anomaly')})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'anomaly' && element('st-cameras').className === 'status warn', 'An anomaly on any camera is the group state');
  assert(element('st-cameras').href === pi.path, 'The group badge opens the camera responsible for its state');
  await build(network({...routes, [garage.status_url]: reply('', 502)})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'down' && element('st-cameras').className === 'status err', 'A failed camera makes the group down');
  assert(element('st-cameras').href === garage.path, 'A failed camera is the group badge destination');
  assert(element('dt-cameras').children[2].children[1].textContent === 'down' && element('dt-cameras').children[0].children[1].textContent === 'up', 'Other cameras keep their own badge');
  await build(network({...routes, '/api/control/cameras': listing([wifi, pi, garage], {manual_pauses: {'cam-garage': {reason: 'manual_stop'}}})})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'paused' && element('st-cameras').className === 'status warn', 'A user-stopped camera is shown as paused, not as a failure');
  assert(element('dt-cameras').children[2].children[2].textContent.includes('User stopped'), 'A stopped camera is not probed');
  await build(network({'/api/control/cameras': reply('', 502)})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'down' && element('dt-cameras').textContent.includes('Camera list unavailable'), 'An unavailable controller must remain visible');
  await build(network({'/api/control/cameras': listing([])})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'no cameras' && element('st-cameras').className === 'status warn', 'An empty camera list is called out');
  const damaged = status('buffering', 'normal', {preview_quality: {state: 'recovering', decoder_errors: 3, discarded_frames: 12}});
  await build(network({'/api/control/cameras': listing([wifi]), [wifi.status_url]: damaged})).probe(card, new Map());
  assert(element('st-cameras').textContent === 'degraded', 'Decoder recovery must not be shown as healthy');
  assert(element('dt-cameras').children[0].children[2].textContent.includes('Decoder errors: 3'), 'Decoder diagnostics should be visible');

  // Service Control gets one row per camera, rebuilt when the camera list changes.
  const controlled = build(network({'/api/control/cameras': listing([wifi, pi, garage]), [wifi.status_url]: up, [pi.status_url]: up, [garage.status_url]: up}));
  await controlled.probe(card, new Map());
  assert(controlled.cameraTargets().join() === 'cameras,pi-camera,cam-garage', 'Camera targets follow the list');
  const rows = element('controlRows').children;
  assert(rows.length === controlled.controllableTargets.length + 3, 'A control row for every camera');
  assert(rows[rows.length - 1].innerHTML.includes('data-target="cam-garage"') && rows[rows.length - 1].innerHTML.includes('>Garage<'), 'Extra cameras are controllable by label');
  await build(network({'/api/control/cameras': listing([wifi]), [wifi.status_url]: up})).probe(card, new Map());
  assert(element('controlRows').children.length === 6 + 1, 'Rows of removed cameras disappear');
  assert(api.summarizeTargetState({recorder: 'running', dashboard: 'running', anomaly: 'exited'}, false).startsWith('partial'), 'Partial camera stacks must be visible');

  // Group status comes from /api/control/services; camera groups include the shared worker.
  const payload = {services: {cameras: {'camera-recorder': 'running', 'camera-dashboard': 'running', 'camera-analysis': 'running'},
    'pi-camera': {'pi-camera-recorder': 'exited', 'pi-camera-dashboard': 'exited', 'camera-analysis': 'running'},
    redlab: {'redlab-collector': 'running'}}, manual_pauses: {}};
  const state = build(network({'/api/control/cameras': listing([wifi, pi]), [wifi.status_url]: up, [pi.status_url]: up,
    '/api/control/services': reply(payload)}));
  await state.probe(card, new Map());
  await state.refreshControlStates();
  assert(element('ctrl-st-cameras').textContent.startsWith('state: running'), 'A fully running camera group is running');
  assert(element('ctrl-st-pi-camera').textContent.startsWith('state: partial'), 'A stopped camera next to the running shared worker is partial');
  assert(!element('ctrl-st-redlab').textContent.includes('analysis'), 'Other targets never mention analysis');

  // Shared mode controls the application containers while retaining individual camera probes.
  const shared = build(network({'/api/control/cameras': listing([pi], {
    control_targets: [{target: 'cameras', label: 'Cameras'}]}), [pi.status_url]: up}));
  await shared.probe(card, new Map());
  assert(shared.cameraTargets().join() === 'cameras', 'Shared mode has one application control');
  assert(element('controlRows').children.at(-1).innerHTML.includes('>Cameras<'), 'The application is named Cameras');
  const stopped = build(network({'/api/control/cameras': listing([], {success: false,
    control_targets: [{target: 'cameras', label: 'Cameras'}]})}));
  await stopped.probe(card, new Map());
  assert(stopped.cameraTargets().join() === 'cameras', 'Controls remain available when the application is stopped');
  assert(element('st-cameras').textContent === 'down', 'A stopped application is shown as down');
  return 'System Health camera states, navigation and group controls passed.';
}

if (typeof require !== 'undefined' && require.main === module) {
  const fs = require('node:fs');
  const path = require('node:path');
  runCameraHealthTests(fs.readFileSync(path.join(__dirname, '../dashboard/simple-dash/health.html'), 'utf8'))
    .then(console.log).catch(error => { console.error(error); process.exitCode = 1; });
}
