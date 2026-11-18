/* Run with node tests/cameras_model_ui.js. */
const fs = require('node:fs');
async function run() {
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
    requests.push({url, body: JSON.parse(options.body)});
    return Promise.resolve({ok: true, json: async () => ({id: `model-${requests.length}`})});
  };
  const api = new Function('document', 'fetch', 'Image', 'setInterval', 'setTimeout', 'performance', 'window',
    fs.readFileSync('app/cameras/web/app.js', 'utf8') + '\nreturn {syncModel, syncDetectionControls, syncInputSize, applyModel};')(
      document, fetch, class {}, () => {}, () => {}, {now: () => 0}, {});
  api.syncModel({model_key: 'mobilenetv4-convsmall-x0.5'}, false);
  const select = element('anomaly-model');
  assert(select.value === 'mobilenetv4-convsmall-x0.5', 'Initialize from the actual worker model');
  select.value = 'mobilenetv3'; select.listeners.change();
  assert(!element('anomaly-model-apply').disabled, 'Offer Apply for a changed model');
  assert(element('anomaly-learn').disabled, 'Do not learn against an unapplied model selection');
  await api.applyModel();
  assert(requests[0].url === 'api/anomaly/model' && requests[0].body.model_key === 'mobilenetv3', 'Submit the selected model');
  api.syncModel({model_key: 'mobilenetv3', last_request: 'older'}, false);
  assert(select.disabled, 'Ignore stale worker acknowledgment');
  const applied = {model_key: 'mobilenetv3', last_request: 'model-1', state: 'untrained', baseline_ready: false, retraining_required: true};
  api.syncModel(applied, false); api.syncDetectionControls(applied, false);
  assert(!element('anomaly-learn').disabled && element('anomaly-monitor').disabled, 'Require new learning before monitoring');
  assert(element('model-status').textContent.includes('New learning'), 'Explain required learning');
  api.syncInputSize({...applied, input_size: '1920x1080', heatmap_width: 120, heatmap_height: 68}, false);
  assert(element('input-size-status').textContent.includes('New learning'), 'Retain the input-size retraining warning');
  api.syncInputSize({...applied, retraining_required: false, input_size: '1920x1080', heatmap_width: 120, heatmap_height: 68}, false);
  assert(element('input-size-status').textContent.includes('120 × 68'), 'Use the worker heatmap grid, including rounded height');
  for (const [model, fallback] of [['mobilenetv4-convsmall-x0.5', '120 × 68'],
      ['mobilenetv3', '120 × 68']]) {
    select.value = model; select.listeners.change();
    assert(!element('anomaly-model-apply').disabled, 'Offer Apply for the new model');
    assert(element('anomaly-learn').disabled, 'Wait for the new model to be applied before learning');
    await api.applyModel();
    const request = requests[requests.length - 1], id = `model-${requests.length}`;
    assert(request.url === 'api/anomaly/model' && request.body.model_key === model, 'Submit the new model identifier');
    assert(element('model-status').textContent.includes('Applying model'), 'Explain that the new model is pending');
    api.syncModel({model_key: model, last_request: 'older'}, false);
    assert(select.disabled, 'Wait for the new model acknowledgment');
    const status = {...applied, model_key: model, last_request: id};
    api.syncModel(status, false); api.syncDetectionControls(status, false);
    assert(!element('anomaly-learn').disabled && element('anomaly-monitor').disabled, 'Require new learning for the new model');
    assert(element('model-status').textContent.includes('New learning'), 'Explain learning for the new model');
    api.syncInputSize({...status, retraining_required: false, input_size: '1920x1080'}, false);
    assert(element('input-size-status').textContent.includes(fallback), 'Use the model-specific heatmap fallback');
    api.syncInputSize({...status, retraining_required: false, input_size: '1920x1080', heatmap_width: 119, heatmap_height: 67}, false);
    assert(element('input-size-status').textContent.includes('119 × 67'), 'Prefer the server heatmap over the fallback');
  }
  api.syncModel({...applied, state: 'learning'}, false);
  assert(select.disabled, 'Do not switch encoders during learning');
  const html = fs.readFileSync('app/cameras/web/index.html', 'utf8');
  const options = html.match(/<select id="anomaly-model"[^>]*>(.*?)<\/select>/s)[1];
  const visible = [...options.matchAll(/<option value="([^"]+)"([^>]*)>/g)]
    .filter(match => !/\bhidden\b|\bdisabled\b/.test(match[2])).map(match => match[1]);
  assert(JSON.stringify(visible) === JSON.stringify(['mobilenetv3', 'mobilenetv4-convsmall-x0.5']), 'Offer the two selected candidates');
  const shortlisted = [...options.matchAll(/<option value="([^"]+)"[^>]*class="model-shortlist"[^>]*>/g)].map(match => match[1]);
  assert(JSON.stringify(shortlisted) === JSON.stringify(['mobilenetv3']), 'Bold the strongest current Pi Camera model');
  assert(fs.readFileSync('app/cameras/web/style.css', 'utf8').includes('option.model-shortlist{font-weight:700}'), 'Render shortlisted model options in bold');
  for (const key of ['mobileone-s0', 'resnet-fp32', 'pp-lcnet-x0.5', 'resnet-int8', 'shufflenetv2-x0.5', 'shufflenetv2-x1.0', 'mobilenetv3-int8'])
    assert(!options.includes('value="' + key + '"'), 'Remove retired model identifiers from the UI');
  assert(html.includes('Experimental candidates: verify detection sensitivity'), 'Do not imply the shortlist passed quality acceptance');
  assert(html.includes('stride-sixteen heatmap'), 'Describe the shared stride-sixteen heatmap');
  console.log('Model selection, acknowledgment, retraining and stride-sixteen heatmap UI passed.');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
