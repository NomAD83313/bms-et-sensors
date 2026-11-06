/* Run with node tests/cameras_input_size_ui.js. */
async function run(source) {
  const assert = (condition, message) => { if (!condition) throw Error(message); };
  const elements = new Map(), requests = [];
  const element = id => {
    if (!elements.has(id)) elements.set(id, {
      value: id === 'anomaly-multiplier' ? '1.25' : '', listeners: {},
      addEventListener(type, callback) { this.listeners[type] = callback; }
    });
    return elements.get(id);
  };
  const document = {hidden: false, activeElement: null, getElementById: element, addEventListener() {}};
  const fetch = (url, options) => {
    if (!options) return new Promise(() => {});
    requests.push({url, options});
    return Promise.resolve({ok: true, json: async () => ({id: 'resize-1'})});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    source + '\nreturn {syncInputSize, syncDetectionControls, applyInputSize};')(
    document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {});
  api.syncInputSize({input_size: '768x432'}, false);
  const select = element('anomaly-input-size');
  assert(select.value === '768x432', 'Initialize from worker settings');
  select.value = '1024x576'; select.listeners.change();
  assert(!element('anomaly-size-apply').disabled, 'Changed selection should offer Apply');
  assert(element('input-size-status').textContent.includes('learn'), 'Warn about new learning before applying');
  await api.applyInputSize();
  assert(requests[0].url === 'api/anomaly/resize', 'Use explicit resize command');
  assert(JSON.parse(requests[0].options.body).input_size === '1024x576', 'Submit selected size');
  assert(element('anomaly-learn').disabled, 'Wait for worker acknowledgment');
  api.syncInputSize({input_size: '1024x576', last_request: 'old'}, false);
  assert(select.disabled, 'Stale worker response must not confirm command');
  const state = {input_size: '1024x576', last_request: 'resize-1', retraining_required: true, state: 'untrained', baseline_ready: false};
  api.syncInputSize(state, false); api.syncDetectionControls(state, false);
  assert(!select.disabled, 'Applied selection should unlock');
  assert(element('input-size-status').textContent.includes('New learning required'), 'Keep retraining warning visible');
  assert(!element('anomaly-learn').disabled && element('anomaly-monitor').disabled, 'Allow learning but block untrained detection');
  api.syncInputSize({...state, state: 'learning'}, false);
  assert(select.disabled, 'Do not change dimensions while training');
  const html = fs.readFileSync('app/cameras/web/index.html', 'utf8');
  const options = html.match(/<select id="anomaly-input-size"[^>]*>(.*?)<\/select>/s)[1];
  let currentState = {...state, state:'untrained'};
  for (const size of ['768x432','1024x576','1280x720','1536x864','1920x1080']) {
    assert(options.includes('value="'+size+'"'), 'Offer the larger input size');
    api.syncInputSize(currentState, false);
    select.value = String(size); select.listeners.change();
    await api.applyInputSize();
    assert(JSON.parse(requests.at(-1).options.body).input_size === size, 'Submit larger size');
    const applied = {...state, input_size: size};
    api.syncInputSize(applied, false); api.syncDetectionControls(applied, false);
    currentState = applied;
    assert(element('input-size-status').textContent.includes('New learning required'), 'Require learning for larger sizes');
  }
  const values = [...options.matchAll(/value="([^"]+)"/g)].map(match=>match[1]);
  assert(values.join(',')==='768x432,1024x576,1280x720,1536x864,1920x1080','Only requested 16:9 presets are offered');
  api.syncInputSize({...state,input_size:'1920x1080',retraining_required:false},false);
  assert(element('input-size-status').textContent.includes('1920 × 1080'),'Show both input dimensions');
  assert(element('input-size-status').textContent.includes('240 × 135'),'Show rectangular heatmap grid');
  console.log('16:9 model presets, acknowledgment, heatmap dimensions and retraining UI passed.');
}
const fs = require('node:fs');
run(fs.readFileSync('app/cameras/web/app.js', 'utf8')).catch(error => { console.error(error); process.exitCode = 1; });
