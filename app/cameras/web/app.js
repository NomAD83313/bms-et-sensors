const $ = id => document.getElementById(id);
let cameraName = 'Camera 01';
let clips = [], visible = 12, paused = false, timer, liveFailures = 0, recordingModeLoaded = false;
const size = bytes => bytes >= 1073741824 ? (bytes / 1073741824).toFixed(2) + ' GiB' : (bytes / 1048576).toFixed(1) + ' MiB';
const datetime = value => new Date(value * 1000).toLocaleString([], {month:'short', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit'});
function render() {
  const query = $('search').value.trim().toLowerCase();
  const filtered = clips.filter(c => (!$('events-only').checked || c.kind !== 'continuous') && (c.name + ' ' + datetime(c.timestamp)).toLowerCase().includes(query));
  const body = $('clips'); body.replaceChildren();
  filtered.slice(0, visible).forEach(c => {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    const title = document.createElement('div'); title.className = 'record-name';
    const icon = document.createElement('span'); icon.className = 'clip-icon'; icon.textContent = '▷';
    const label = document.createElement('div'); label.textContent = c.kind === 'event' ? 'Anomaly event' : c.kind === 'unclassified' ? 'Unchecked · analysis unavailable' : c.name === 'connection-check.mkv' ? 'Connection check' : cameraName + ' · Main stream';
    const filename = document.createElement('small'); filename.textContent = c.name; label.append(filename); title.append(icon,label); td.append(title); tr.append(td);
    for (const text of [datetime(c.timestamp), size(c.bytes)]) { const cell = document.createElement('td'); cell.textContent = text; tr.append(cell); }
    const saved = document.createElement('td'); saved.className = 'saved'; saved.textContent = c.kind === 'unclassified' ? '● Unchecked' : '● Saved'; tr.append(saved);
    const actions = document.createElement('td'); const box = document.createElement('div'); box.className = 'row-actions';
    const play = document.createElement('button'); play.textContent = '▷ Play'; play.onclick = () => openClip(c);
    const download = document.createElement('a'); download.textContent = '↓ Download'; download.href = 'api/download/' + encodeURIComponent(c.name);
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'delete-button'; remove.textContent = 'Delete';
    remove.onclick = () => deleteHistory('recording', c.name);
    box.append(play,download,remove); actions.append(box); tr.append(actions); body.append(tr);
  });
  if (!filtered.length) { const tr=document.createElement('tr'), td=document.createElement('td'); td.colSpan=5; td.className='empty'; td.textContent=query ? 'No recordings match your search.' : 'No completed clips yet. The first clip appears after recording finishes.'; tr.append(td); body.append(tr); }
  $('shown').textContent = `Showing ${Math.min(visible, filtered.length)} of ${filtered.length} recordings`;
  $('more').hidden = filtered.length <= visible;
}
async function refresh() {
  try {
    const response = await fetch('api/status'); if (!response.ok) throw Error();
    const data = await response.json(); clips = data.clips;
    cameraName = data.camera_name || 'Camera 01';
    $('camera-name').textContent = cameraName;
    $('camera-description').textContent = data.camera_description || 'Network camera';
    $('camera-resolution').textContent = data.camera_resolution || 'Main stream';
    $('live').alt = 'Live preview from ' + cameraName;
    $('recorder').textContent = !data.recording_enabled ? (data.service === 'paused' ? 'Stopped' : 'Stopping…') : data.service === 'recording' ? 'Recording' : data.service === 'connecting' ? 'Connecting…' : 'Offline';
    $('recorder').style.color = data.healthy ? '#557b35' : '#a37236';
    $('health').textContent = data.healthy ? 'Latest clip saved successfully' : 'No recent clip · check camera connection';
    $('recording-control-status').textContent = !data.recording_enabled ? (data.service === 'paused' ? 'Recording stopped. Live preview remains available.' : 'Stopping recording…') : data.service === 'recording' ? 'Recording is running.' : 'Waiting for the recorder…';
    if (!recordingModeLoaded) {
      $('events-only').checked = data.recording_mode === 'anomaly'; recordingModeLoaded = true;
    }
    $('recording-actions').hidden = data.recording_mode === 'anomaly';
    if (data.recording_mode === 'anomaly') {
      const active = data.service === 'saving_event', armed = data.service === 'buffering';
      if (data.recording_enabled) {
        $('recorder').textContent = active ? 'Saving event' : armed ? 'Armed' : data.service === 'connecting' ? 'Connecting…' : 'Offline';
        $('health').textContent = active ? 'Anomaly window is being captured' : !data.analysis_available ? 'Analysis unavailable · buffering only; no new events' : 'Normal video is buffered and discarded';
        $('recording-control-status').textContent = active ? 'Saving anomaly video and post-event context…' : armed ? 'Event recording armed. Normal video will not be kept.' : 'Waiting for video…';
      }
      $('recording-mode').textContent = 'Anomalies only';
      $('recording-note').textContent = `Keep ${data.pre_seconds}s before and ${data.post_seconds}s after an anomaly, rounded to keyframe segments. Repeated anomalies extend the event. Only confirmed anomaly windows are saved; other buffered video expires even when analysis is unavailable.`;
      $('recording-heading').textContent = 'Save unusual moments.';
      $('recording-description').textContent = 'Start detection to arm event recording. Pause detection to stop both. Live preview remains available.';
      if (!data.recording_enabled) $('recording-control-status').textContent = data.service === 'paused' ? 'Event recording is paused. Use Start detection to begin.' : 'Stopping event recording…';
    }
    $('start-recording').disabled = data.recording_enabled;
    $('stop-recording').disabled = !data.recording_enabled;
    $('health').textContent = !data.recording_enabled ? 'Recording stopped · live preview available' : $('health').textContent;
    if (data.snapshot_export_failed) {
      $('recorder').textContent = 'Export failed';
      $('health').textContent = 'Snapshot video could not be saved. Raw event frames are retained; check storage and restart the recorder.';
    }
    $('clip-count').textContent = clips.length; $('library-count').textContent = clips.length;
    $('storage').textContent = size(data.total_bytes); $('budget').textContent = `of ${size(data.max_storage_bytes)} budget · ${size(data.free_bytes)} free`;
    $('retention').textContent = `${data.max_age_hours} hours`; $('duration').textContent = data.recording_mode === 'anomaly' ? `${data.pre_seconds}s before · ${data.post_seconds}s after` : `${data.clip_seconds}-second recording clips`;
    $('setup-duration').textContent = data.recording_mode === 'anomaly' ? 'Variable event length' : `${data.clip_seconds} seconds`; $('api-error').hidden = true;
    render();
  } catch { $('api-error').hidden = false; $('api-error').textContent = 'Cannot reach the recorder. Displayed recordings may be out of date. Retrying automatically.'; $('recorder').textContent = 'Unavailable'; $('health').textContent = 'Dashboard connection lost'; }
}
function preview() {
  if (paused || document.hidden) return;
  const started = performance.now();
  const next = new Image();
  next.onload = () => { if (paused || document.hidden) return; $('live').src=next.src; $('live').hidden=false; $('live-message').hidden=true; $('live-badge').textContent='LIVE'; liveFailures=0; timer=setTimeout(preview,Math.max(0,100-(performance.now()-started))); };
  next.onerror = () => { liveFailures++; $('live-badge').textContent='RECONNECTING'; $('live-message').textContent='Live preview unavailable. Reconnecting…'; $('live-message').hidden=false; timer=setTimeout(preview,4000); };
  next.src='api/live.jpg?t='+Date.now();
}
function openClip(c) { $('player-title').textContent=datetime(c.timestamp)+' · Camera 01'; $('player').src='api/play/'+encodeURIComponent(c.name); $('player-download').href='api/download/'+encodeURIComponent(c.name); $('player-dialog').showModal(); $('player').play().catch(()=>{}); }
function closePlayer() { $('player').pause(); $('player').removeAttribute('src'); $('player').load(); }
$('close-player').onclick=()=>$('player-dialog').close(); $('player-dialog').addEventListener('close',closePlayer);
$('pause').onclick=()=>{ paused=!paused; clearTimeout(timer); $('pause').textContent=paused?'Resume preview':'Pause preview'; $('live-badge').textContent=paused?'PAUSED':'CONNECTING'; if(!paused) preview(); };
document.addEventListener('visibilitychange',()=>{clearTimeout(timer); if(!document.hidden) preview();});
$('events-only').addEventListener('change',()=>{visible=12; render();});
$('search').addEventListener('input',()=>{visible=12; render();}); $('more').onclick=()=>{visible+=24; render();}; $('refresh').onclick=refresh;
function clock(){ const now=new Date(); $('clock').textContent=now.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}); $('date').textContent=now.toLocaleDateString([], {weekday:'short',day:'numeric',month:'long',year:'numeric'}); }
clock(); setInterval(clock,1000); refresh(); setInterval(refresh,3000); preview();

async function recordingControl(action) {
  $('start-recording').disabled = true;
  $('stop-recording').disabled = true;
  try {
    const response = await fetch('api/recording/' + action, {method: 'POST', headers: {'X-Camera-Control': '1'}});
    if (!response.ok) throw Error();
    $('recording-control-status').textContent = action === 'stop' ? 'Stopping recording…' : 'Starting recording…';
    await refresh();
    setTimeout(refresh, 2000);
  } catch {
    $('recording-control-status').textContent = 'Could not change recording. Please retry.';
    await refresh();
  }
}
$('start-recording').onclick = () => recordingControl('start');
$('stop-recording').onclick = () => recordingControl('stop');

let anomalyRequest = false, anomalyEventsKey = '', latestAnomalyState = null;
let appliedConfirmation = null, pendingConfirmation = null, confirmationFailure = '';
let inputSizeDirty = false, appliedInputSize = null, pendingInputSize = null, inputSizeFailure = '';
let appliedModel = null, pendingModel = null, modelDirty = false, modelFailure = '';
let appliedHeatmap = null;
let appliedROI = null, draftROI = null, pendingROI = null, roiEditing = false, roiFailure = '';
let roiSource = null, roiPointer = null, roiDragStart = null, appliedEffectiveInput = null;
let latestAnomalyData = {}, latestAnomalyUnavailable = true;
const roiConfiguring = () => roiEditing || pendingROI !== null;
function canonicalROI(roi) {
  return roi && roi.x === 0 && roi.y === 0 && roi.width === 1 && roi.height === 1 ? null : roi;
}
function sameROI(left, right) {
  left = canonicalROI(left); right = canonicalROI(right);
  return left == null || right == null ? left == null && right == null
    : ['x', 'y', 'width', 'height'].every(key => Math.abs(left[key] - right[key]) < .000002);
}
function fittedInput(roi, preset = appliedInputSize) {
  roi = canonicalROI(roi);
  const bounds = modelDimensions(preset || $('anomaly-input-size').value || '768x432');
  if (!roi || !roiSource) return bounds;
  const width = roi.width * roiSource.width, height = roi.height * roiSource.height;
  const scale = Math.min(bounds.width / width, bounds.height / height);
  const fitted = {width: Math.round(width * scale), height: Math.round(height * scale)};
  fitted.label = `${fitted.width} × ${fitted.height}`;
  return fitted;
}
function roiSourceRect() {
  const image = $('live');
  if (!roiSource || !image?.getBoundingClientRect || !image.naturalWidth || !image.naturalHeight || image.hidden) return null;
  const rect = image.getBoundingClientRect();
  const previewWidth = image.naturalWidth, previewHeight = image.naturalHeight;
  const outerScale = Math.min(rect.width / previewWidth, rect.height / previewHeight);
  const sourceScale = Math.min(previewWidth / roiSource.width, previewHeight / roiSource.height);
  const width = roiSource.width * sourceScale * outerScale, height = roiSource.height * sourceScale * outerScale;
  return {left: rect.left + (rect.width - width) / 2, top: rect.top + (rect.height - height) / 2, width, height};
}
function roiPoint(event, clamp = false) {
  const rect = roiSourceRect();
  if (!rect || !rect.width || !rect.height) return null;
  const x = (event.clientX - rect.left) / rect.width, y = (event.clientY - rect.top) / rect.height;
  if (!clamp && (x < 0 || y < 0 || x > 1 || y > 1)) return null;
  return {x: Math.max(0, Math.min(1, x)), y: Math.max(0, Math.min(1, y))};
}
function drawROI() {
  const layer = $('roi-layer'), box = $('roi-box'), rect = roiSourceRect();
  if (!layer || !box) return;
  const roi = roiEditing ? draftROI : appliedROI;
  layer.hidden = !rect || (!roiEditing && roi === null);
  layer.className = 'roi-layer' + (roiEditing ? ' roi-editing' : '');
  box.hidden = !roi || !rect;
  if (!roi || !rect || !box.style) return;
  const container = layer.getBoundingClientRect?.() || $('live').getBoundingClientRect();
  box.style.left = `${rect.left - container.left + roi.x * rect.width}px`;
  box.style.top = `${rect.top - container.top + roi.y * rect.height}px`;
  box.style.width = `${roi.width * rect.width}px`; box.style.height = `${roi.height * rect.height}px`;
  $('roi-label').textContent = roiEditing ? 'DRAFT AREA' : 'ANALYSIS AREA';
}
function syncROI(data, unavailable) {
  if (Number(data.source_width) > 0 && Number(data.source_height) > 0) {
    roiSource = {width: Number(data.source_width), height: Number(data.source_height)};
  }
  if (Object.prototype.hasOwnProperty.call(data, 'roi')) {
    if (!sameROI(appliedROI, data.roi)) { appliedHeatmap = null; appliedEffectiveInput = null; }
    appliedROI = data.roi;
    if (pendingROI && pendingROI.id !== null && pendingROI.id === data.last_request && sameROI(pendingROI.value, appliedROI)) {
      pendingROI = null; roiEditing = false; draftROI = null; roiFailure = '';
    }
  }
  const busy = capturePending || unavailable || ['learning', 'training'].includes(data.state)
    || pendingROI !== null || pendingModel !== null || pendingInputSize !== null || pendingConfirmation !== null || pendingMultiplier !== null;
  const geometryReady = roiSourceRect() !== null;
  if ($('roi-select')) $('roi-select').disabled = busy || !geometryReady || roiEditing || modelDirty || inputSizeDirty;
  if ($('roi-apply')) $('roi-apply').disabled = busy || !roiEditing || draftROI === null || sameROI(draftROI, appliedROI) || roiPointer !== null;
  if ($('roi-full')) $('roi-full').disabled = busy || appliedROI === null || modelDirty || inputSizeDirty;
  if ($('roi-cancel')) $('roi-cancel').disabled = pendingROI !== null || !roiEditing;
  const roi = roiEditing ? draftROI : appliedROI;
  const dimensions = fittedInput(roi);
  if ($('roi-status')) $('roi-status').textContent = roiFailure || (pendingROI
    ? 'Applying analysis area. Detection and event recording will pause; new learning is required…'
    : roiEditing ? (draftROI ? `Draft area · input ${dimensions.label}. Apply area to use it; active analysis is unchanged.` : 'Drag on the live image to select an area. Active analysis is unchanged.')
    : !geometryReady ? 'Waiting for a live image and source dimensions before area selection.'
    : appliedROI ? `Analysis area applied · input ${dimensions.label}.${data.retraining_required ? ' New learning required: click Learn normal view.' : ''}`
    : 'Full frame is used for analysis. Select an area to focus detection.');
  drawROI();
}
function syncAnalysisControls(data = latestAnomalyData, unavailable = latestAnomalyUnavailable) {
  syncROI(data, unavailable); syncModel(data, unavailable); syncConfirmation(data, unavailable);
  syncInputSize(data, unavailable); syncThreshold(data, unavailable); syncDetectionControls(data, unavailable);
  if (captureApplied) syncCaptureOptions();
}
function beginROI() {
  if ($('roi-select')?.disabled || !roiSourceRect() || latestAnomalyUnavailable || capturePending
    || pendingROI !== null || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null
    || pendingConfirmation !== null || modelDirty || inputSizeDirty || ['learning', 'training'].includes(latestAnomalyState)) return;
  roiEditing = true; draftROI = appliedROI ? {...appliedROI} : null; roiFailure = '';
  syncAnalysisControls();
  $('live').scrollIntoView?.({behavior: 'smooth', block: 'center'});
}
function cancelROI() {
  if (pendingROI !== null) return;
  roiEditing = false; draftROI = null; roiPointer = null; roiDragStart = null; roiFailure = '';
  syncAnalysisControls();
}
async function applyROI(value = draftROI) {
  if (pendingROI !== null || capturePending || pendingModel !== null || pendingInputSize !== null
    || pendingConfirmation !== null || pendingMultiplier !== null || modelDirty || inputSizeDirty
    || ['learning', 'training'].includes(latestAnomalyState) || sameROI(value, appliedROI)) return;
  const dimensions = fittedInput(value);
  if (value !== null && (value.width < .01 || value.height < .01 || dimensions.width < 32 || dimensions.height < 32)) {
    roiFailure = 'Choose a larger area: each side must cover at least 1% of the image and produce at least 32 input pixels.';
    syncROI(latestAnomalyData, latestAnomalyUnavailable); return;
  }
  const multiplier = appliedMultiplier ?? 1.25;
  pendingROI = {value: value === null ? null : {...value}, id: null}; roiFailure = '';
  syncAnalysisControls();
  try {
    const response = await fetch('api/anomaly/roi', {method: 'POST', headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'},
      body: JSON.stringify({roi: value, multiplier})});
    if (!response.ok) throw Error();
    const command = await response.json();
    if (!command.id) throw Error();
    if (Object.prototype.hasOwnProperty.call(command, 'roi')) pendingROI.value = command.roi;
    pendingROI.id = command.id;
    setTimeout(refreshAnomaly, 500);
    setTimeout(() => {
      if (pendingROI?.id === command.id) {
        roiFailure = 'Waiting for the analysis service to confirm the area. Other changes remain locked until confirmation.';
        syncAnalysisControls();
      }
    }, 15000);
  } catch {
    pendingROI = null; roiFailure = 'Could not apply analysis area. Check the selected area and live image, then retry.';
    syncAnalysisControls();
  }
}
function roiPointerDown(event) {
  if (!roiEditing || roiPointer !== null || pendingROI !== null || (event.button != null && event.button !== 0)) return;
  const point = roiPoint(event);
  if (!point) return;
  event.preventDefault(); roiPointer = event.pointerId; roiDragStart = point; draftROI = null; roiFailure = '';
  $('roi-layer').setPointerCapture?.(event.pointerId); syncROI(latestAnomalyData, latestAnomalyUnavailable);
}
function roiPointerMove(event) {
  if (roiPointer === null || event.pointerId !== roiPointer || !roiDragStart) return;
  const point = roiPoint(event, true);
  if (!point) return;
  event.preventDefault();
  draftROI = {x: Math.min(point.x, roiDragStart.x), y: Math.min(point.y, roiDragStart.y),
    width: Math.abs(point.x - roiDragStart.x), height: Math.abs(point.y - roiDragStart.y)};
  drawROI();
}
function roiPointerUp(event) {
  if (roiPointer === null || event.pointerId !== roiPointer) return;
  roiPointerMove(event);
  const rect = roiSourceRect();
  if (draftROI && (draftROI.width < .01 || draftROI.height < .01 || !rect || draftROI.width * rect.width < 4 || draftROI.height * rect.height < 4)) {
    draftROI = null; roiFailure = 'Choose a larger area: drag at least 4 pixels and cover at least 1% of each image side.';
  }
  if (draftROI) for (const key of ['x', 'y', 'width', 'height']) draftROI[key] = Number(draftROI[key].toFixed(6));
  roiPointer = null; roiDragStart = null;
  syncROI(latestAnomalyData, latestAnomalyUnavailable);
}
if ($('roi-select')) $('roi-select').onclick = beginROI;
if ($('roi-apply')) $('roi-apply').onclick = () => applyROI();
if ($('roi-full')) $('roi-full').onclick = () => applyROI(null);
if ($('roi-cancel')) $('roi-cancel').onclick = cancelROI;
$('roi-layer')?.addEventListener('pointerdown', roiPointerDown);
$('roi-layer')?.addEventListener('pointermove', roiPointerMove);
$('roi-layer')?.addEventListener('pointerup', roiPointerUp);
$('roi-layer')?.addEventListener('pointercancel', event => {
  if (roiPointer === null || event.pointerId !== roiPointer) return;
  roiPointer = null; roiDragStart = null; draftROI = null; syncROI(latestAnomalyData, latestAnomalyUnavailable);
});
$('live').addEventListener('load', () => syncROI(latestAnomalyData, latestAnomalyUnavailable));
window.addEventListener?.('resize', drawROI);
function syncModel(data, unavailable) {
  if (data.model_key != null) {
    if (appliedModel !== data.model_key) appliedHeatmap = null;
    appliedModel = data.model_key;
    if (pendingModel && pendingModel.id === data.last_request && pendingModel.value === appliedModel) {
      pendingModel = null; modelDirty = false; modelFailure = '';
    }
    if (!modelDirty && pendingModel === null) $('anomaly-model').value = appliedModel;
  }
  const learning = ['learning', 'training'].includes(data.state);
  const busy = capturePending || unavailable || learning || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || pendingConfirmation !== null;
  $('anomaly-model').disabled = busy || inputSizeDirty;
  $('anomaly-model-apply').disabled = busy || !modelDirty;
  $('model-status').textContent = modelFailure || (pendingModel
    ? 'Applying model. Detection will pause; new learning is required…'
    : modelDirty ? 'Not applied. Apply model, then learn the normal view again.'
    : data.retraining_required ? 'New learning required for this model, input size, analysis area or source.'
    : appliedModel ? `Applied model: ${$('anomaly-model').options?.[ $('anomaly-model').selectedIndex ]?.text || data.model_label || appliedModel}.` : 'Checking analysis model…');
}
async function applyModel() {
  if (!modelDirty || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || pendingConfirmation !== null || ['learning', 'training'].includes(latestAnomalyState)) return;
  const value = $('anomaly-model').value;
  pendingModel = {value, id: null}; modelFailure = '';
  syncModel({}, false); syncDetectionControls({}, false);
  try {
    const response = await fetch('api/anomaly/model', {method: 'POST',
      headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'},
      body: JSON.stringify({model_key: value})});
    if (!response.ok) throw Error();
    const command = await response.json();
    pendingModel.id = command.id;
    setTimeout(refreshAnomaly, 500);
    setTimeout(() => {
      if (pendingModel && pendingModel.id === command.id) {
        pendingModel = null; modelFailure = 'Model change is not confirmed. Check the applied model and retry.';
        syncModel({}, false);
      }
    }, 15000);
  } catch {
    pendingModel = null; modelFailure = 'Could not change model. Please retry.';
    syncModel({}, false);
  }
}
$('anomaly-model').addEventListener('change', () => {
  modelDirty = $('anomaly-model').value !== appliedModel;
  modelFailure = ''; syncModel({}, false); syncDetectionControls({}, false);
});
$('anomaly-model-apply').onclick = applyModel;
let thresholdDirty = false, appliedMultiplier = null, pendingMultiplier = null, thresholdFailure = '', thresholdGeneration = 0;
function parseMultiplier(value) {
  const number = Number(value.trim().replace(',', '.'));
  return Number.isFinite(number) && number >= .1 && number <= 20 ? number : null;
}
function syncThreshold(data, unavailable) {
  const input = $('anomaly-multiplier');
  if (data.multiplier != null) {
    appliedMultiplier = data.multiplier;
    if (pendingMultiplier === appliedMultiplier) {
      pendingMultiplier = null;
      thresholdDirty = parseMultiplier(input.value) !== appliedMultiplier;
      thresholdFailure = '';
    }
    if (!thresholdDirty && document.activeElement !== input) input.value = String(appliedMultiplier);
  }
  $('anomaly-apply').disabled = unavailable || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || !thresholdDirty;
  input.disabled = roiConfiguring();
  $('threshold-status').textContent = thresholdFailure || (pendingMultiplier !== null ? 'Applying threshold…' : thresholdDirty ? `Applied multiplier: ${appliedMultiplier ?? '—'}. Edited value is not applied yet.` : `Applied multiplier: ${appliedMultiplier ?? '—'}.`);
}
function syncDetectionControls(data, unavailable) {
  const coupled = data.recording_mode === 'anomaly';
  const learning = ['learning', 'training'].includes(data.state);
  const monitoring = data.monitoring_enabled ?? ['normal', 'suspect', 'anomaly'].includes(data.state);
  $('anomaly-monitor').textContent = coupled ? 'Start detection & recording' : 'Start detection';
  $('anomaly-pause').textContent = coupled ? 'Pause detection & recording' : 'Pause detection';
  $('anomaly-learn').disabled = capturePending || unavailable || learning || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingConfirmation !== null || inputSizeDirty || modelDirty;
  $('anomaly-monitor').disabled = capturePending || unavailable || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingConfirmation !== null || inputSizeDirty || modelDirty || !data.baseline_ready || learning || monitoring;
  $('anomaly-pause').disabled = pendingROI !== null || pendingConfirmation !== null || (unavailable && !coupled) || !(learning || monitoring || (coupled && data.recording_enabled));
}
async function refreshAnomaly() {
  if (document.hidden || anomalyRequest) return;
  anomalyRequest = true;
  try {
    const response = await fetch('api/anomaly');
    if (!response.ok) throw Error();
    const data = await response.json();
    latestAnomalyState = data.state;
    const badge = $('anomaly-state');
    badge.textContent = data.state.toUpperCase(); badge.dataset.state = data.state;
    $('anomaly-message').textContent = data.message;
    const quality = data.preview_quality;
    $('stream-quality').textContent = quality
      ? `Stream quality: ${quality.state} · decoder errors: ${quality.decoder_errors} · excluded preview frames: ${quality.discarded_frames}` : '';
    $('anomaly-progress').hidden = data.state !== 'learning';
    $('anomaly-progress').max = data.target || 60; $('anomaly-progress').value = data.collected || 0;
    if (data.state === 'learning') $('anomaly-message').textContent += ` · ${data.collected}/${data.target} frames`;
    $('anomaly-score').textContent = data.score != null ? `Score ${data.score.toFixed(2)} / threshold ${data.threshold.toFixed(2)} (learned ${(data.threshold / data.multiplier).toFixed(3)} × ${data.multiplier}) · ${data.ratio.toFixed(2)}×` : 'Score: —';
    $('anomaly-speed').textContent = data.inference_ms != null ? `Analysis ${(1 / (data.analysis_interval_sec || 2)).toFixed(1)} FPS · confirm ${data.confirmation_frames || 3} frames · cycle ${data.inference_ms} ms` : 'Analysis: —';
    const unavailable = ['offline', 'loading'].includes(data.state);
    latestAnomalyData = data; latestAnomalyUnavailable = unavailable;
    syncAnalysisControls(data, unavailable);
    const key = (data.events || []).map(event => event.id + ':' + (event.videos || []).join(',')).join(',') + ':' + data.video_pending;
    if (key !== anomalyEventsKey) {
      anomalyEventsKey = key; $('anomaly-events').replaceChildren();
      for (const event of data.events || []) {
        const card = document.createElement('article'); card.className = 'anomaly-event';
        const image = document.createElement('img'); image.src = 'api/anomaly/events/' + encodeURIComponent(event.image); image.loading = 'lazy'; image.alt = 'Unusual view snapshot';
        const details = document.createElement('div'); details.textContent = datetime(event.timestamp);
        const score = document.createElement('p'); score.className = 'snapshot-score';
        score.textContent = `SCORE ${Number.isFinite(event.score) ? event.score.toFixed(2) : '—'} · THRESHOLD ${Number.isFinite(event.threshold) ? event.threshold.toFixed(2) : '—'}`;
        score.title = 'Score and threshold when this snapshot was captured'; details.append(score);
        const links = document.createElement('div');
        for (const [label, name] of [['Snapshot', event.image], ['Heatmap', event.heatmap]]) {
          const link = document.createElement('a'); link.textContent = label; link.href = 'api/anomaly/events/' + encodeURIComponent(name); link.target = '_blank'; link.rel = 'noopener'; links.append(link, document.createTextNode(' '));
        }
        for (const name of event.videos || []) {
          const play = document.createElement('button'); play.type = 'button'; play.className = 'secondary-button'; play.textContent = 'Play event video';
          play.onclick = () => openClip({name, timestamp: event.timestamp}); links.append(play);
        }
        if (!(event.videos || []).length && data.video_pending) {
          const pending = document.createElement('small'); pending.textContent = 'Video is being captured; available after the post-event window.'; details.append(pending);
        }
        const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'secondary-button delete-button'; remove.textContent = 'Delete snapshot';
        remove.onclick = () => deleteHistory('event', event.id); links.append(remove);
        details.append(links); card.append(image, details); $('anomaly-events').append(card);
      }
    }
  } catch {
    $('anomaly-state').textContent = 'UNAVAILABLE'; $('anomaly-state').dataset.state = 'offline';
    $('anomaly-message').textContent = 'Cannot reach visual analysis. Check the analysis service.';
    latestAnomalyUnavailable = true;
    syncAnalysisControls({}, true);
  } finally { anomalyRequest = false; }
}
async function anomalyControl(action) {
  if (pendingROI !== null || (roiEditing && action !== 'pause') || pendingModel !== null || pendingInputSize !== null || pendingConfirmation !== null) return;
  const configuring = action === 'configure';
  let generation = thresholdGeneration;
  const multiplier = configuring ? parseMultiplier($('anomaly-multiplier').value) : (appliedMultiplier ?? 1.25);
  if (multiplier === null) {
    thresholdFailure = 'Enter a threshold multiplier between 0.1 and 20.';
    syncThreshold({}, false); return;
  }
  if (configuring) {
    if (pendingMultiplier !== null) return;
    generation = ++thresholdGeneration;
    pendingMultiplier = multiplier; thresholdFailure = ''; syncThreshold({}, false);
  } else {
    for (const id of ['anomaly-learn', 'anomaly-monitor', 'anomaly-pause']) $(id).disabled = true;
  }
  try {
    const response = await fetch('api/anomaly/' + action, {method: 'POST', headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({multiplier})});
    if (!response.ok) throw Error();
    if (!configuring) $('anomaly-message').textContent = 'Request sent. Waiting for analysis…';
    setTimeout(refreshAnomaly, 1000);
    if (configuring) setTimeout(() => {
      if (thresholdGeneration === generation && pendingMultiplier === multiplier) {
        pendingMultiplier = null; thresholdFailure = 'Application is not confirmed. Check the applied value and retry.'; syncThreshold({}, false);
      }
    }, 15000);
  } catch {
    if (configuring) {
      pendingMultiplier = null; thresholdFailure = 'Could not apply threshold. Please retry.'; syncThreshold({}, false);
    } else $('anomaly-message').textContent = 'Could not change analysis. Please retry.';
    refreshAnomaly();
  }
}
function syncConfirmation(data, unavailable) {
  const select = $('anomaly-confirmation');
  if (data.confirmation_frames != null) {
    appliedConfirmation = data.confirmation_frames;
    if (pendingConfirmation && pendingConfirmation.id === data.last_request && pendingConfirmation.value === appliedConfirmation) {
      pendingConfirmation = null; confirmationFailure = '';
    }
    if (pendingConfirmation === null) select.value = String(appliedConfirmation);
  }
  select.disabled = unavailable || ['learning', 'training'].includes(data.state)
    || roiConfiguring() || pendingConfirmation !== null || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null;
  $('confirmation-status').textContent = confirmationFailure || (pendingConfirmation !== null
    ? 'Applying confirmation setting…'
    : appliedConfirmation !== null ? `Applied: ${appliedConfirmation} consecutive analyzed frame${appliedConfirmation === 1 ? '' : 's'}. No new learning required.` : 'Checking confirmation setting…');
}
async function applyConfirmation() {
  if (roiConfiguring() || pendingConfirmation !== null || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null) return;
  const value = Number($('anomaly-confirmation').value);
  if (value === appliedConfirmation) return;
  pendingConfirmation = {value, id: null}; confirmationFailure = '';
  syncConfirmation({}, false); syncInputSize({}, false); syncThreshold({}, false); syncDetectionControls({}, false);
  try {
    const response = await fetch('api/anomaly/confirmation', {method: 'POST',
      headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'},
      body: JSON.stringify({confirmation_frames: value})});
    if (!response.ok) throw Error();
    const command = await response.json();
    pendingConfirmation.id = command.id;
    setTimeout(refreshAnomaly, 500);
    setTimeout(() => {
      if (pendingConfirmation && pendingConfirmation.id === command.id) {
        pendingConfirmation = null;
        confirmationFailure = 'Change is not confirmed. Check the applied count and retry.';
        syncConfirmation({}, false);
      }
    }, 15000);
  } catch {
    pendingConfirmation = null;
    confirmationFailure = 'Could not change confirmation. Please retry.';
    if (appliedConfirmation !== null) $('anomaly-confirmation').value = String(appliedConfirmation);
    syncConfirmation({}, false);
  }
}
$('anomaly-confirmation').addEventListener('change', applyConfirmation);
function modelDimensions(value) {
  const [width, height = width] = String(value).split('x').map(Number);
  return {width, height, label: `${width} × ${height}`};
}
function syncInputSize(data, unavailable) {
  const select = $('anomaly-input-size');
  if (data.input_size != null) {
    if (appliedInputSize !== data.input_size) { appliedHeatmap = null; appliedEffectiveInput = null; }
    appliedInputSize = data.input_size;
    if (pendingInputSize && pendingInputSize.id === data.last_request && pendingInputSize.value === appliedInputSize) {
      pendingInputSize = null; inputSizeDirty = false; inputSizeFailure = '';
    }
    if (!inputSizeDirty && pendingInputSize === null) select.value = String(appliedInputSize);
  }
  const learning = ['learning', 'training'].includes(data.state);
  const bounds = modelDimensions(appliedInputSize);
  if (data.effective_input_width != null && data.effective_input_height != null) {
    appliedEffectiveInput = {width: data.effective_input_width, height: data.effective_input_height,
      label: `${data.effective_input_width} × ${data.effective_input_height}`};
  }
  const dimensions = appliedROI ? (appliedEffectiveInput || fittedInput(appliedROI)) : bounds;
  const inputLabel = dimensions.label + (appliedROI ? ` · maximum ${bounds.label} · area proportions preserved` : ' · full frame');
  if (data.heatmap_width != null) appliedHeatmap = [data.heatmap_width, data.heatmap_height];
  const stride = appliedModel === 'mobilenetv3'
    || ['pp-lcnet-x0.5', 'mobilenetv4-convsmall-x0.5'].includes(appliedModel) ? 16 : 8;
  const grid = appliedHeatmap || [Math.ceil(dimensions.width / stride), Math.ceil(dimensions.height / stride)];
  select.disabled = unavailable || learning || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || pendingConfirmation !== null;
  $('anomaly-size-apply').disabled = unavailable || learning || roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || pendingConfirmation !== null || !inputSizeDirty;
  $('input-size-status').textContent = inputSizeFailure || (pendingROI !== null
    ? 'Applying analysis area. New learning is required after confirmation…'
    : pendingModel !== null || pendingInputSize !== null
    ? 'Applying input size. Detection will pause; new learning is required…'
    : inputSizeDirty ? 'Not applied yet. Apply size, then learn the normal view again.'
    : learning ? `Learning baseline for ${dimensions.label}. Wait until learning completes.`
    : data.retraining_required ? `Input ${inputLabel} applied. New learning required: click Learn normal view.`
    : appliedInputSize !== null ? `Model input: ${inputLabel} · heatmap grid: ${grid[0]} × ${grid[1]}.` : 'Checking model input size…');
}
async function applyInputSize() {
  if (roiConfiguring() || pendingModel !== null || pendingInputSize !== null || pendingMultiplier !== null || pendingConfirmation !== null || !inputSizeDirty) return;
  const value = $('anomaly-input-size').value;
  pendingInputSize = {value, id: null}; inputSizeFailure = '';
  syncInputSize({}, false); syncDetectionControls({}, false);
  try {
    const response = await fetch('api/anomaly/resize', {method: 'POST',
      headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'},
      body: JSON.stringify({input_size: value})});
    if (!response.ok) throw Error();
    const command = await response.json();
    pendingInputSize.id = command.id;
    setTimeout(refreshAnomaly, 500);
    setTimeout(() => {
      if (pendingInputSize && pendingInputSize.id === command.id) {
        pendingInputSize = null;
        inputSizeFailure = 'Input size change is not confirmed. Check the applied size and retry.';
        syncInputSize({}, false);
      }
    }, 15000);
  } catch {
    pendingInputSize = null;
    inputSizeFailure = 'Could not apply input size. Please retry.';
    syncInputSize({}, false);
  }
}
$('anomaly-input-size').addEventListener('change', () => {
  inputSizeDirty = $('anomaly-input-size').value !== appliedInputSize;
  inputSizeFailure = ''; syncInputSize({}, false); syncDetectionControls({}, false);
});
$('anomaly-size-apply').onclick = applyInputSize;
$('anomaly-multiplier').addEventListener('input', () => {
  thresholdDirty = parseMultiplier($('anomaly-multiplier').value) !== appliedMultiplier;
  thresholdFailure = ''; syncThreshold({}, false);
});
$('anomaly-multiplier').addEventListener('keydown', event => {
  if (event.key === 'Enter') { event.preventDefault(); anomalyControl('configure'); }
});
$('anomaly-apply').onclick = () => anomalyControl('configure');
$('anomaly-learn').onclick = () => anomalyControl('learn');
$('anomaly-monitor').onclick = () => anomalyControl('monitor');
$('anomaly-pause').onclick = () => anomalyControl('pause');
refreshAnomaly(); setInterval(refreshAnomaly, 250);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshAnomaly(); });

let deletionPending = false;
async function deleteHistory(action, name) {
  if (deletionPending) return;
  const messages = {
    event: 'Delete this snapshot and its heatmap? Associated video recordings will be kept.',
    recording: 'Delete this saved video recording? Snapshots and heatmaps will be kept.',
    'all-events': 'Delete ALL saved snapshots and heatmaps? Video recordings will be kept.',
    'all-recordings': 'Delete ALL completed video recordings, including those hidden by the filter? Snapshots will be kept.',
    history: 'Delete ALL saved snapshots, heatmaps and completed video recordings?'
  };
  if (!window.confirm(messages[action] + '\n\nThis cannot be undone. The learned baseline, settings and live buffer are kept. New events and pending videos can appear afterwards.')) return;
  deletionPending = true;
  document.querySelectorAll('.history-menu').forEach(menu => menu.open = false);
  try {
    const response = await fetch('api/delete/' + action, {method: 'POST', headers: {'X-Camera-Control': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({name})});
    if (!response.ok) throw Error();
    const result = await response.json();
    $('history-status').textContent = `Deleted ${result.events} snapshot event(s) and ${result.recordings} recording(s).`;
    if ($('player-dialog').open) $('player-dialog').close();
    anomalyEventsKey = null;
    await Promise.all([refresh(), refreshAnomaly()]);
  } catch { $('history-status').textContent = 'Deletion failed. Refresh the list and retry.'; }
  finally { deletionPending = false; }
}
$('delete-all-events').onclick = () => deleteHistory('all-events');
$('delete-all-recordings').onclick = () => deleteHistory('all-recordings');
$('delete-history').onclick = () => deleteHistory('history');


let captureApplied = null, captureDirty = false, capturePending = false, capturePolling = false;
function captureDraft() {
  const snapshots = $('capture-mode').value === 'snapshots';
  const draft = {capture_mode: $('capture-mode').value,
    snapshot_interval_ms: Number($('capture-interval').value),
    shutter_us: snapshots ? Number($('capture-exposure').value) : 0};
  [draft.width, draft.height] = $('capture-resolution').value.split('x').map(Number);
  if (!snapshots) draft.fps = Number($('capture-fps').value);
  return draft;
}
function syncCaptureOptions() {
  const snapshots = $('capture-mode').value === 'snapshots';
  $('capture-interval-label').hidden = !snapshots;
  $('capture-exposure-label').hidden = !snapshots;
  $('capture-resolution-label').hidden = false;
  $('capture-fps-label').hidden = snapshots;
  const resolution = [...$('capture-resolution').options].find(option => option.value === $('capture-resolution').value);
  const maximumFps = Number(resolution?.dataset.maxFps || 30);
  for (const option of $('capture-fps').options) option.disabled = Number(option.value) > maximumFps;
  if (!snapshots && Number($('capture-fps').value) > maximumFps) $('capture-fps').value = String(maximumFps);
  const maximum = Number($('capture-interval').value)*1000;
  for (const option of $('capture-exposure').options) option.disabled = Number(option.value) > maximum;
  if (Number($('capture-exposure').value) > maximum) {
    $('capture-exposure').value = '0';
    $('capture-status').textContent = 'Previous exposure does not fit this interval. Auto exposure selected; apply to save.';
  }
  for (const id of ['capture-mode', 'capture-resolution', 'capture-fps', 'capture-interval', 'capture-exposure']) $(id).disabled = capturePending || roiConfiguring();
  $('capture-apply').disabled = capturePending || roiConfiguring() || !captureDirty || ['learning', 'training'].includes(latestAnomalyState) || pendingModel !== null || pendingInputSize !== null || pendingConfirmation !== null || pendingMultiplier !== null;
}
async function refreshCapture() {
  if (capturePolling || document.hidden) return;
  capturePolling = true;
  try {
    const response = await fetch('api/capture');
    if (!response.ok) throw Error();
    const data = await response.json();
    $('capture-panel').hidden = !data.available;
    if (!data.available) return;
    captureApplied = data.settings;
    $('preview-rate').textContent = captureApplied.capture_mode === 'snapshots'
      ? `SNAPSHOTS · ${1000/captureApplied.snapshot_interval_ms} FPS` : 'LIVE PREVIEW · 10 FPS';
    if (!captureDirty && !capturePending) {
      $('capture-mode').value = captureApplied.capture_mode;
      $('capture-interval').value = String(captureApplied.snapshot_interval_ms);
      $('capture-resolution').value = `${captureApplied.width}x${captureApplied.height}`;
      $('capture-fps').value = String(captureApplied.fps);
      $('capture-exposure').value = String(captureApplied.shutter_us);
      const exposure = captureApplied.shutter_us ? `${captureApplied.shutter_us/1000} ms requested exposure` : 'Auto exposure';
      $('capture-status').textContent = captureApplied.capture_mode === 'snapshots'
        ? `Snapshots: ${1000/captureApplied.snapshot_interval_ms} FPS · ${captureApplied.width} × ${captureApplied.height} · ${exposure}`
        : `Video: ${captureApplied.width} × ${captureApplied.height} · ${captureApplied.fps} FPS · ${exposure}`;
    }
    syncCaptureOptions();
  } catch {
    if (captureApplied) $('capture-status').textContent = 'Capture controls unavailable. Please retry.';
  } finally { capturePolling = false; }
}
for (const id of ['capture-mode', 'capture-resolution', 'capture-fps', 'capture-interval', 'capture-exposure']) $(id).addEventListener('change', () => {
  captureDirty = true; syncCaptureOptions();
  $('capture-status').textContent = 'Not applied. Applying pauses detection; check the new image and learn the normal view again.';
});
$('capture-apply').onclick = async () => {
  if (capturePending || roiConfiguring() || !captureDirty || ['learning', 'training'].includes(latestAnomalyState)) return;
  capturePending = true; syncCaptureOptions();
  $('capture-status').textContent = 'Applying capture settings; the live image will reconnect…';
  try {
    const response = await fetch('api/capture', {method: 'POST', headers: {
      'X-Camera-Control': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(captureDraft())});
    if (!response.ok) throw Error();
    const data = await response.json();
    captureApplied = data.settings; captureDirty = false;
    $('capture-status').textContent = 'Capture settings applied. Detection is paused. Check the image and learn the normal view again.';
    setTimeout(refreshAnomaly, 1000);
  } catch { $('capture-status').textContent = 'Could not apply settings. Check resolution, FPS and snapshot exposure, then retry.'; }
  finally { capturePending = false; syncCaptureOptions(); }
};
refreshCapture(); setInterval(refreshCapture, 5000);
