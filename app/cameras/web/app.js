const $ = id => document.getElementById(id);
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
    const label = document.createElement('div'); label.textContent = c.kind === 'event' ? 'Anomaly event' : c.kind === 'unclassified' ? 'Unchecked · analysis unavailable' : c.name === 'connection-check.mkv' ? 'Connection check' : 'Camera 01 · Main stream';
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

let anomalyRequest = false, anomalyEventsKey = '';
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
  $('anomaly-apply').disabled = unavailable || pendingMultiplier !== null || !thresholdDirty;
  $('threshold-status').textContent = thresholdFailure || (pendingMultiplier !== null ? 'Applying threshold…' : thresholdDirty ? `Applied multiplier: ${appliedMultiplier ?? '—'}. Edited value is not applied yet.` : `Applied multiplier: ${appliedMultiplier ?? '—'}.`);
}
function syncDetectionControls(data, unavailable) {
  const coupled = data.recording_mode === 'anomaly';
  const learning = ['learning', 'training'].includes(data.state);
  const monitoring = data.monitoring_enabled ?? ['normal', 'suspect', 'anomaly'].includes(data.state);
  $('anomaly-monitor').textContent = coupled ? 'Start detection & recording' : 'Start detection';
  $('anomaly-pause').textContent = coupled ? 'Pause detection & recording' : 'Pause detection';
  $('anomaly-learn').disabled = unavailable || learning;
  $('anomaly-monitor').disabled = unavailable || !data.baseline_ready || learning || monitoring;
  $('anomaly-pause').disabled = (unavailable && !coupled) || !(learning || monitoring || (coupled && data.recording_enabled));
}
async function refreshAnomaly() {
  if (document.hidden || anomalyRequest) return;
  anomalyRequest = true;
  try {
    const response = await fetch('api/anomaly');
    if (!response.ok) throw Error();
    const data = await response.json();
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
    syncThreshold(data, unavailable);
    syncDetectionControls(data, unavailable);
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
    syncDetectionControls({}, true);
    syncThreshold({}, true);
  } finally { anomalyRequest = false; }
}
async function anomalyControl(action) {
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
