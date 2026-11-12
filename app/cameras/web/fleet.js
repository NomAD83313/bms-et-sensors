/* Shared camera registration, editing and independent process controls. */
'use strict';
const fleetBase = new URL(document.querySelector('base').href);
const fleetElement = id => document.getElementById(id);
let fleetCameras = [], fleetSelected = '', fleetBusy = false, fleetLoaded = '', fleetRefreshSequence = 0;
function fleetURL(path) { return new URL(path, fleetBase).href; }
async function fleetRequest(path, body) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 45000);
  try {
    const options = {cache: 'no-store', signal: controller.signal};
    if (body !== undefined) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Camera-Control': '1'}, body: JSON.stringify(body)});
    const response = await fetch(fleetURL(path), options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
    return data;
  } finally { clearTimeout(timeout); }
}
function renderFleet() {
  const select = fleetElement('camera-select');
  const list = fleetElement('camera-list');
  if (select.options.length !== fleetCameras.length || [...select.options].some((option, i) => option.value !== fleetCameras[i].id || option.textContent !== fleetCameras[i].label)) {
    select.replaceChildren(); list.replaceChildren();
    fleetCameras.forEach(camera => {
      const option = document.createElement('option'); option.value = camera.id; option.textContent = camera.label; select.append(option);
      const button = document.createElement('button'); button.type = 'button'; button.className = 'camera-choice'; button.dataset.id = camera.id;
      const name = document.createElement('span'); name.textContent = camera.label;
      const status = document.createElement('small'); button.append(name, status); button.onclick = () => chooseFleetCamera(camera.id); list.append(button);
    });
  }
  if (!fleetCameras.some(camera => camera.id === fleetSelected)) fleetSelected = fleetCameras[0]?.id || '';
  select.value = fleetSelected;
  select.disabled = !fleetCameras.length;
  const camera = fleetCameras.find(item => item.id === fleetSelected);
  for (const button of list.children) {
    const item = fleetCameras.find(camera => camera.id === button.dataset.id);
    button.setAttribute('aria-current', String(item.id === fleetSelected));
    button.querySelector('small').textContent = item.state === 'running' ? `${item.analysis_state} · ${item.recording_state}` : item.state;
  }
  fleetElement('start-camera').disabled = fleetBusy || !camera || camera.enabled;
  fleetElement('stop-camera').disabled = fleetBusy || !camera || !camera.enabled;
  for (const id of ['edit-camera', 'delete-camera']) fleetElement(id).disabled = fleetBusy || !camera;
  for (const id of ['add-camera', 'add-sidebar']) fleetElement(id).disabled = fleetBusy;
  select.disabled = fleetBusy || !fleetCameras.length;
  fleetElement('camera-state').textContent = camera ? `${camera.state} · analysis ${camera.analysis_state}` : 'No cameras added';
  const frame = fleetElement('camera-workspace');
  const show = !!camera?.enabled;
  frame.hidden = !show;
  fleetElement('stopped-panel').hidden = show;
  fleetElement('stopped-title').textContent = camera ? `${camera.label} is stopped` : 'Add your first camera';
  const next = show ? camera.id : '';
  if (next !== fleetLoaded) {
    fleetLoaded = next;
    if (next) frame.src = fleetURL(`view/${encodeURIComponent(next)}/`);
    else frame.removeAttribute('src');
  }
}
function chooseFleetCamera(identifier) {
  fleetSelected = identifier;
  const url = fleetURL(`${identifier}/`);
  history.replaceState({}, '', url);
  renderFleet();
}
async function refreshFleet() {
  const sequence = ++fleetRefreshSequence;
  try {
    const data = await fleetRequest('api/cameras');
    if (sequence !== fleetRefreshSequence) return;
    fleetCameras = data.cameras;
    renderFleet();
  } catch (error) { fleetElement('fleet-message').textContent = `Camera manager unavailable: ${error.message}`; }
}
async function controlFleet(action) {
  if (!fleetSelected || fleetBusy) return;
  const identifier = fleetSelected;
  fleetBusy = true; renderFleet();
  try {
    await fleetRequest(`api/cameras/${encodeURIComponent(identifier)}/${action}`, {});
    fleetElement('fleet-message').textContent = action === 'stop' ? 'Camera stopped. Saved learning and recordings are kept.' : 'Camera started. Detection can be enabled in its settings.';
    await refreshFleet();
  } catch (error) { fleetElement('fleet-message').textContent = error.message; }
  finally { fleetBusy = false; renderFleet(); }
}
fleetElement('camera-select').addEventListener('change', event => chooseFleetCamera(event.target.value));
fleetElement('start-camera').onclick = () => controlFleet('start');
fleetElement('stop-camera').onclick = () => controlFleet('stop');
function syncFleetSource() {
  const local = fleetElement('new-source').value === 'local';
  fleetElement('new-url-label').hidden = local;
  fleetElement('new-url').disabled = local;
  fleetElement('new-id').readOnly = local;
  if (local) fleetElement('new-id').value = 'pi';
  else if (fleetElement('new-id').value === 'pi') fleetElement('new-id').value = '';
}
fleetElement('new-source').onchange = syncFleetSource;
function openFleetAdd() { syncFleetSource(); fleetElement('add-error').textContent = ''; fleetElement('add-dialog').showModal(); }
fleetElement('add-camera').onclick = fleetElement('add-sidebar').onclick = openFleetAdd;
fleetElement('cancel-add').onclick = () => fleetElement('add-dialog').close();
fleetElement('add-form').onsubmit = async event => {
  event.preventDefault();
  const button = fleetElement('save-camera'); button.disabled = true;
  try {
    const data = {id: fleetElement('new-id').value.trim(), label: fleetElement('new-name').value.trim(), url: fleetElement('new-url').value.trim(), enabled: fleetElement('new-start').checked};
    if (fleetElement('new-source').value === 'local') {
      data.url = 'http://127.0.0.1:8084/stream.ts';
      data.capture_url = 'http://127.0.0.1:8084';
    }
    const camera = await fleetRequest('api/cameras', data);
    fleetElement('new-url').value = '';
    fleetElement('add-form').reset(); fleetElement('add-dialog').close();
    fleetSelected = camera.id; await refreshFleet(); chooseFleetCamera(camera.id);
    fleetElement('fleet-message').textContent = 'Camera added. Start it, select the analysis area and learn its normal view.';
  } catch (error) { fleetElement('add-error').textContent = error.message; }
  finally { button.disabled = false; }
};
let fleetEditing = null, fleetDeleting = null;
fleetElement('edit-camera').onclick = () => {
  const camera = fleetCameras.find(item => item.id === fleetSelected);
  if (!camera || fleetBusy) return;
  fleetEditing = camera.id;
  fleetElement('edit-id').value = camera.id;
  fleetElement('edit-name').value = camera.label;
  fleetElement('edit-url').value = '';
  fleetElement('edit-url-label').hidden = camera.source_kind === 'local';
  fleetElement('edit-url').disabled = camera.source_kind === 'local';
  fleetElement('edit-local-note').hidden = camera.source_kind !== 'local';
  fleetElement('edit-error').textContent = '';
  fleetElement('edit-dialog').showModal();
};
fleetElement('delete-camera').onclick = () => {
  const camera = fleetCameras.find(item => item.id === fleetSelected);
  if (!camera || fleetBusy) return;
  fleetDeleting = camera.id;
  fleetElement('delete-description').textContent = `Remove ${camera.label} (${camera.id})?`;
  fleetElement('delete-error').textContent = '';
  fleetElement('delete-dialog').showModal();
};
for (const action of ['edit', 'delete']) {
  fleetElement(`cancel-${action}`).onclick = () => fleetElement(`${action}-dialog`).close();
  fleetElement(`${action}-dialog`).addEventListener('cancel', event => { if (fleetBusy) event.preventDefault(); });
  fleetElement(`${action}-dialog`).addEventListener('close', () => { if (action === 'edit') fleetElement('edit-url').value = ''; });
  fleetElement(`${action}-form`).onsubmit = async event => {
    event.preventDefault();
    if (fleetBusy) return;
    const identifier = action === 'edit' ? fleetEditing : fleetDeleting;
    const body = {};
    if (action === 'edit') {
      body.label = fleetElement('edit-name').value.trim();
      if (fleetElement('edit-url').value.trim()) body.url = fleetElement('edit-url').value.trim();
    }
    const submit = fleetElement(action === 'edit' ? 'save-edit' : 'confirm-delete');
    const cancel = fleetElement(`cancel-${action}`);
    fleetBusy = true; submit.disabled = cancel.disabled = true; renderFleet();
    try {
      await fleetRequest(`api/cameras/${encodeURIComponent(identifier)}/${action}`, body);
      fleetElement(`${action}-dialog`).close();
      // Discard polls that started before the registry change.
      ++fleetRefreshSequence;
      await refreshFleet();
      chooseFleetCamera(fleetSelected);
      fleetElement('fleet-message').textContent = action === 'edit'
        ? 'Changes saved. Camera stopped; start it when ready. If its view changed, learn a new baseline.'
        : 'Camera deleted. Saved learning and recordings remain on disk.';
    } catch (error) { fleetElement(`${action}-error`).textContent = error.message; }
    finally { fleetBusy = false; submit.disabled = cancel.disabled = false; renderFleet(); }
  };
}
window.addEventListener('message', event => {
  const frame = fleetElement('camera-workspace');
  if (event.origin === location.origin && event.source === frame.contentWindow && event.data?.type === 'camera-height' && Number.isFinite(event.data.height)) frame.style.height = `${Math.min(30000, Math.max(600, event.data.height + 12))}px`;
});
fleetSelected = decodeURIComponent(location.pathname.slice(fleetBase.pathname.length).split('/')[0] || '');
refreshFleet();
setInterval(refreshFleet, 2500);
