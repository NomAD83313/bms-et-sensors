let loadingCount = 0;
function setLoading(isLoading, statusText = 'Processing...') {
    if (isLoading) loadingCount++;
    else loadingCount--;

    if (loadingCount < 0) loadingCount = 0;
    const active = loadingCount > 0;

    const opStatus = document.getElementById('opStatus');
    const refreshBtn = document.getElementById('refreshBtn');
    if (opStatus) {
        if (isLoading || active) opStatus.textContent = statusText;
        opStatus.style.display = active ? 'inline' : 'none';
    }
    if (refreshBtn) refreshBtn.disabled = active;
    document.body.style.cursor = active ? 'wait' : 'default';

    document.querySelectorAll('.btn-primary, .btn-outline-secondary').forEach(btn => {
        if (btn.id !== 'heroToggleBtn') btn.disabled = active;
    });
}
function setLoadingText(statusText) {
    const opStatus = document.getElementById('opStatus');
    if (opStatus && loadingCount > 0) opStatus.textContent = statusText;
}

async function loadHealth(force = false){
    // Don't show loading overlay for background auto-refreshes unless forced
    if (force) setLoading(true, 'Refreshing cable & device...');
    
    try {
        const url = force ? 'health?refresh=1' : 'health';
        const res = await fetch(url);
        const data = await res.json();
        const status = data.status;
        const ok = status === 'ok';
        const _portFull = data.port || '';
        document.getElementById('devicePort').textContent = 'CP210x UART Bridge' + (_portFull ? ' (' + _portFull + ')' : '');
        const pill = document.getElementById('deviceStatus');
        const suggestions = document.getElementById('recoverySuggestions');

        if (status === 'ok') {
            pill.textContent = 'connected';
            pill.className = 'status-pill status-ok';
            if (suggestions) suggestions.style.display = 'none';
            // Stop auto-refresh once connected
            stopHealthAutoRefresh();
        } else if (status === 'cable_only') {
            pill.textContent = 'cable only — no device';
            pill.className = 'status-pill status-warn';
            if (suggestions) suggestions.style.display = 'block';
            startHealthAutoRefresh();
        } else {
            pill.textContent = 'no cable';
            pill.className = 'status-pill status-err';
            if (suggestions) suggestions.style.display = 'block';
            startHealthAutoRefresh();
        }

        if (!ok) {
            const ids = ['heroDeviceModel', 'heroSerial', 'heroChannels', 'heroPrintCycle'];
            for (const id of ids) {
                const el = document.getElementById(id);
                if (el) el.textContent = '-';
            }
            const _di = document.getElementById('deviceInfo');
            if (_di) _di.style.display = 'none';
            const _dit = document.getElementById('deviceInfoText');
            if (_dit) _dit.textContent = '';
            updateConnectedChannelsSummary([]);
        }
        if (ok) {
            const _di = document.getElementById('deviceInfo');
            const _dit = document.getElementById('deviceInfoText');
            if (_dit) _dit.textContent = 'loading...';
            if (_di) _di.style.display = '';
            await loadDeviceInfo();
        }
    } catch (err) {
        console.error('Health check failed', err);
    } finally {
        if (force) setLoading(false);
    }
}

let healthTimer = null;
const TAB_ID = Math.random().toString(36).substring(2);

function startHealthAutoRefresh() {
    if (healthTimer) return;
    healthTimer = setInterval(() => {
        // Simple leader election: only one tab polls health
        const now = Date.now();
        const leader = localStorage.getItem('almemo_health_leader');
        const lastActive = parseInt(localStorage.getItem('almemo_health_active') || '0', 10);
        
        if (!leader || leader === TAB_ID || (now - lastActive) > 7000) {
            localStorage.setItem('almemo_health_leader', TAB_ID);
            localStorage.setItem('almemo_health_active', now.toString());
            loadHealth(false);
        }
    }, 5000);
}
function stopHealthAutoRefresh() {
    if (healthTimer) {
        clearInterval(healthTimer);
        healthTimer = null;
    }
}
function toggleHeroGrid(){
const g=document.getElementById('heroGrid');
const b=document.getElementById('heroToggleBtn');
if(!g){return;}
const visible=g.style.display!=='none';
g.style.display=visible?'none':'';
if(b){b.textContent=visible?'Details ▾':'Details ▴';}
}
function setOverview(id, value){
const el=document.getElementById(id);
if(el){el.textContent=value;}
}
function firstLine(data){const txt=prettyFromLines(data);if(!txt){return '';}return String(txt).split('\n')[0].trim();}
const deviceMeta={activeChannels:null};
function updateConnectedChannelsSummary(list){
const el=document.getElementById('connectedChannelsSummary');
if(!el){return;}
const channels=Array.isArray(list)?list.filter(Boolean):[];
el.textContent='Connected channels: '+(channels.length?channels.join(', '):'-');
}
function parseF1P19(data){
const lines=(Array.isArray(data.lines)?data.lines:[]).map(l=>stripCtrl(l)).filter(Boolean);
const out={};
for(const line of lines){
if(line.startsWith('GB:')) out.device=line.replace('GB:','').trim();
else if(line.startsWith('VO.')) out.version=line.replace('VO.','').trim();
else if(line.startsWith('SN.')) out.serial=line.replace('SN.','').trim();
else if(line.startsWith('GE:')) {
out.channels=line.replace('GE:','').trim();
const m=out.channels.match(/\bA(\d{3})\b/);
if(m){out.activeChannels=parseInt(m[1],10);}
}
else if(line.startsWith('HY:')) out.hyst=line.replace('HY:','').trim();
else if(line.startsWith('KF:')) out.config=line.replace('KF:','').trim();
else if(line.startsWith('SW:')) out.setpoint=line.replace('SW:','').trim();
else if(line.startsWith('WR:')) out.conv=line.replace('WR:','').trim();
else if(line.startsWith('Z1:')) out.printCycle=line.replace('Z1:','').trim();
else if(line.startsWith('Z2:')) out.measCycle=line.replace('Z2:','').trim();
else if(line.startsWith('U1:')) out.startTime=line.replace('U1:','').trim();
else if(line.startsWith('D1:')) out.startDate=line.replace('D1:','').trim();
else if(line.startsWith('U2:')) out.endTime=line.replace('U2:','').trim();
else if(line.startsWith('D2:')) out.endDate=line.replace('D2:','').trim();
else if(line.startsWith('MD:')) out.duration=line.replace('MD:','').trim();
else if(line.startsWith('GV:')) out.lock=line.replace('GV:','').trim();
else if(line.startsWith('KG:')) out.calib=line.replace('KG:','').trim();
else if(line.startsWith('SP:')) out.lang=line.replace('SP:','').trim();
else if(line.startsWith('DI:')) out.display=line.replace('DI:','').trim();
}
return out;
}
function parseF2P19(data){
const lines=(Array.isArray(data.lines)?data.lines:[]).map(l=>stripCtrl(l)).filter(Boolean);
const out={};
for(const line of lines){
if(line.startsWith('TK:')) out.tempComp=line.replace('TK:','').trim();
else if(line.startsWith('LD:')) out.air=line.replace('LD:','').trim();
else if(line.startsWith('CJ.')) out.cj=line.replace('CJ.','').trim();
else if(line.startsWith('UZ:')) out.time=line.replace('UZ:','').trim();
else if(line.startsWith('DA:')) out.date=line.replace('DA:','').trim();
else if(line.startsWith('T1.')) out.t1=line.replace('T1.','').trim();
else if(line.startsWith('T2.')) out.t2=line.replace('T2.','').trim();
else if(line.startsWith('MZ.')) out.mz=line.replace('MZ.','').trim();
else if(line.startsWith('T3.')) out.t3=line.replace('T3.','').trim();
else if(line.startsWith('T4.')) out.t4=line.replace('T4.','').trim();
else if(line.startsWith('UB.')) out.ub=line.replace('UB.','').trim();
else if(line.startsWith('US:')) out.us=line.replace('US:','').trim();
else if(line.startsWith('UF.')) out.uf=line.replace('UF.','').trim();
else if(line.startsWith('AK:')) out.ak=line.replace('AK:','').trim();
else if(line.startsWith('LM.')) out.lm=line.replace('LM.','').trim();
else if(line.startsWith('LS.')) out.ls=line.replace('LS.','').trim();
}
return out;
}
async function loadDeviceInfo(){
try{
const data=await sendCmdSequence([
{command:'f1 P19',read_lines:0},
{command:'f2 P19',read_lines:0}
]);
const seq=Array.isArray(data.sequence)?data.sequence:[];
const f1=seq[0]||{};
const f2=seq[1]||{};
const a=parseF1P19(f1);
const b=parseF2P19(f2);
if(Number.isInteger(a.activeChannels)){deviceMeta.activeChannels=a.activeChannels;}
const _di=document.getElementById('deviceInfo');
if(_di){
const _parts=[];if(a.device)_parts.push(a.device);if(a.serial)_parts.push('S/N: '+a.serial);
const _dit=document.getElementById('deviceInfoText');
if(_parts.length){if(_dit)_dit.textContent=_parts.join(' · ');_di.style.display='';}
else{_di.style.display='none';}
}
document.querySelectorAll('#heroGrid .stat-block').forEach(b=>b.style.display='');
const _h=(id,val)=>{const el=document.getElementById(id);if(!el)return;const _v=val!=null?String(val).trim():'';const has=_v!==''&&!/^-+$/.test(_v);el.textContent=has?_v:'-';const blk=el.closest('.stat-block');if(blk)blk.style.display=has?'':'none';};
_h('heroDeviceModel',a.device);
_h('heroVersion',a.version);
_h('heroSerial',a.serial);
_h('heroChannels',Number.isInteger(a.activeChannels)?String(a.activeChannels):null);
_h('heroAddress',a.channels);
_h('heroPrintCycle',a.printCycle);
_h('heroConvRate',a.conv);
_h('heroSetpoint',a.setpoint);
_h('heroHysteresis',a.hyst);
_h('heroConfig',a.config);
_h('heroLock',a.lock);
_h('heroLang',a.lang);
_h('heroDisplay',a.display);
_h('heroStartTime',a.startTime);
_h('heroStartDate',a.startDate);
_h('heroEndTime',a.endTime);
_h('heroEndDate',a.endDate);
_h('heroDeviceTime',b.time);
_h('heroDeviceDate',b.date);
_h('heroCjTemp',b.cj);
_h('heroTempComp',b.tempComp);
_h('heroAirPressure',b.air);
_h('heroBattery',b.ub);
const _batEl=document.getElementById('heroBattery');
if(_batEl&&b.ub){const _batV=parseFloat(b.ub);const _batBlock=_batEl.closest('.stat-block');
if(_batBlock){const _c=Number.isFinite(_batV)?(_batV>=4.0?'bat-ok':_batV>=3.6?'bat-warn':'bat-low'):'';
_batBlock.classList.remove('bat-ok','bat-warn','bat-low');if(_c)_batBlock.classList.add(_c);}}
_h('heroSensorSupply',b.uf);
_h('heroSensorVSetpoint',b.us);
_h('heroRechargeCount',b.ak);
_h('heroChargingMode',b.lm);
_h('heroChargingCurrent',b.ls);
}catch(err){}
}
function stripCtrl(s){return String(s||'').replace(/�C/g,'°C').replace(/øC/g,'°C').replace(/[\x00-\x1f\x7f]/g,'').trim();}
function prettyFromLines(data){
if(!data || !Array.isArray(data.lines)) return '';
const cmd=String(data.command||'').trim();
const lines=data.lines.map(l=>stripCtrl(l)).filter(Boolean).filter(l=>l!==cmd);
if(!lines.length) return '';
const out=[];
for(const line of lines){
if(line.startsWith('TIME:')){out.push('Time: '+line.replace('TIME:','').trim());continue;}
if(line.startsWith('DATE:')){out.push('Date: '+line.replace('DATE:','').trim());continue;}
if(line.startsWith('A2490-')){out.push('Version: '+line.trim());continue;}
if(line.startsWith('TIME CONST:')){
const m=line.match(/TIME CONST:\s*(\d+)\s*:\s*(\d+)/);
if(m){const ch=m[1].padStart(2,'0');const n=m[2].padStart(2,'0');
out.push('Smoothing: Ch'+ch+' values#: '+n);continue;}
}
out.push(line);
}
return out.join('\n');
}
function prettyFromSequence(seq){
const blocks=[];
for(const item of seq){const head=item && item.command ? item.command : 'command';
const body=prettyFromLines(item);
const text=body?head+':\n'+body:head+': (no data)';
blocks.push(text);}
return blocks.join('\n\n');
}
function renderOutput(data){
document.getElementById('out').textContent=JSON.stringify(data,null,2);
if(data && Array.isArray(data.sequence)){
document.getElementById('outPretty').textContent=prettyFromSequence(data.sequence)||'(empty)';
setOverview('overviewCommandState','Sequence completed');
return;}
document.getElementById('outPretty').textContent=prettyFromLines(data)||'(empty)';
setOverview('overviewCommandState', firstLine(data) || 'Command completed');
}
function prettyWithTitle(title,data){
const body=prettyFromLines(data);
if(!body){return title+': (no data)';}
return title+':\n'+body;
}
async function sendCmdRaw(cmd,readLines,opts={}){
    const rl=Number.isFinite(readLines)?readLines:0;
    const body={command:cmd,read_lines:rl};
    if(opts.timeout_ms!=null)body.timeout_ms=opts.timeout_ms;
    const res=await fetch('api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    return await res.json();
}
async function sendCmdSequence(steps){
    const res=await fetch('api/command-sequence',{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({steps})
    });
    return await res.json();
}
async function sendCmd(e){
    e.preventDefault();
    const cmd=document.getElementById('cmd').value;
    const out=document.getElementById('out');
    const pretty=document.getElementById('outPretty');
    out.textContent='... waiting for ALMEMO (9600 baud) ...';
    pretty.textContent='... communicating ...';
    
    const parts=String(cmd||'').split(';').map(s=>s.trim()).filter(Boolean);
    setLoading(true, 'Sending command...');
    try{
        if(parts.length<=1){
            const data=await sendCmdRaw(cmd);
            renderOutput(data);
            return false;
        }
        setLoadingText(`Running ${parts.length} commands as one session...`);
        const combined=await sendCmdSequence(parts.map(part=>({command:part,read_lines:0})));
        renderOutput(combined);
    }catch(err){
        out.textContent=String(err);
        document.getElementById('recoverySuggestions').style.display = 'block';
    } finally {
        setLoading(false);
    }
    return false;
}
async function quick(cmd){document.getElementById('cmd').value=cmd;
await sendCmd(new Event('submit'));}
function runSelectedCmd(){const sel=document.getElementById('cmdSelect');
const val=sel.value; if(!val){return;}
if(val==='__SET_TIME_NOW__'){setTimeNow();return;}
if(val==='__SET_DATE_NOW__'){setDateNow();return;}
document.getElementById('cmd').value=val; sendCmd(new Event('submit'));}
function toggleCmdHelp(){const el=document.getElementById('cmdHelp');
if(!el){return;}el.classList.toggle('show');}
function normChannel(val){const d=String(val||'').replace(/\D/g,'');return d.padStart(2,'0').slice(-2);}
function normCount(val){const d=String(val||'').replace(/\D/g,'');const n=Math.max(0,Math.min(99,Number(d||0)));return String(n).padStart(2,'0');}
function updateChannelOptions(list){
const sel=document.getElementById('smoothChannel');
const current=sel.value;
sel.innerHTML='';
const items=list.length?list:[...Array(16).keys()].map(i=>String(i).padStart(2,'0'));
for(const ch of items){const opt=document.createElement('option');opt.value=ch;opt.textContent=ch;sel.appendChild(opt);}
if(items.includes(current)){sel.value=current;}
setOverview('overviewChannel','Channel '+(sel.value || '00'));
updateConnectedChannelsSummary(list);
}
async function refreshChannels(){
    setLoading(true, 'Refreshing channels...');
    try{
        const res=await sendCmdRaw('P18');
        const lines=(Array.isArray(res.lines)?res.lines:[]).map(l=>stripCtrl(l)).filter(Boolean);
        const chans=[];
        for(const line of lines){const m=line.match(/^(\d{2}):/);if(m && !chans.includes(m[1])){chans.push(m[1]);}}
        updateChannelOptions(chans);
    }catch(err){
        console.error('Refresh channels failed', err);
    } finally {
        setLoading(false);
    }
}
async function applySmoothing(){
    const ch=normChannel(document.getElementById('smoothChannel').value);
    setLoading(true, `Applying smoothing to Ch${ch}...`);
    try {
        await refreshChannels();
        const n=normCount(document.getElementById('smoothCount').value);
        document.getElementById('smoothChannel').value=ch;document.getElementById('smoothCount').value=n;
        const data=await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'M'+ch,read_lines:0,timeout_ms:300},
            {command:'f1 z'+n,read_lines:0}
        ]);
        renderOutput(data);
        const cur=document.getElementById('smoothCurrent'); if(cur){cur.textContent=n;}
    } finally {
        setLoading(false);
    }
}
async function resetSmoothing(){
    const ch=normChannel(document.getElementById('smoothChannel').value);
    setLoading(true, `Resetting smoothing for Ch${ch}...`);
    try {
        await refreshChannels();
        document.getElementById('smoothChannel').value=ch;
        const data=await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'M'+ch,read_lines:0,timeout_ms:300},
            {command:'f1 z00',read_lines:0}
        ]);
        renderOutput(data);
        const cur=document.getElementById('smoothCurrent'); if(cur){cur.textContent='00';}
    } finally {
        setLoading(false);
    }
}
async function readSmoothing(){
    const ch=normChannel(document.getElementById('smoothChannel').value);
    setLoading(true, `Reading smoothing for Ch${ch}...`);
    try {
        await refreshChannels();
        document.getElementById('smoothChannel').value=ch;
        const data=await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'M'+ch,read_lines:0,timeout_ms:300},
            {command:'P00',read_lines:3},
            {command:'P32',read_lines:2}
        ]);
        renderOutput(data);
        const seq=Array.isArray(data.sequence)?data.sequence:[];
        const p00=seq[2]||{};
        const p32=seq[3]||{};
        const n=smoothingCountFromP32(p32);const cur=document.getElementById('smoothCurrent'); if(cur){cur.textContent=n;}
        const info=sensorInfoFromP00(p00);
        const st=document.getElementById('sensorType'); if(st){st.textContent=info.type||'-';}
        const sc=document.getElementById('sensorChannel'); if(sc){sc.textContent=info.ch||'-';}
        const su=document.getElementById('sensorUnit'); if(su){su.textContent=info.unit||'-';}
    } finally {
        setLoading(false);
    }
}
function _extractSensorValue(data) {
    const lines = Array.isArray(data.lines) ? data.lines : [];
    for (const raw of lines) {
        const txt = stripCtrl(raw);
        const m = txt.match(/\d{1,3}(?:\.\d)?:\s*([<>]?[+-]?\d[\d.,eE+\-]*)\s*(\S+)?/);
        if (m) return m[2] ? `${m[1]} ${m[2]}` : m[1];
    }
    return '-';
}
function _extractTimestamp(data) {
    const lines = Array.isArray(data.lines) ? data.lines : [];
    for (const raw of lines) {
        const txt = stripCtrl(raw);
        const m = txt.match(/(\d{2}:\d{2}(?::\d{2})?)\s+(\d{2}\.\d{2}(?:\.\d{2,4})?)/);
        if (m) return `${m[1]} ${m[2]}`;
    }
    return '';
}
function smoothingCountFromP32(data){
const lines=Array.isArray(data.lines)?data.lines:[];
for(const line of lines){
const m=String(line||'').match(/TIME CONST:\s*\d+\s*:\s*(\d+)/);
if(m){return String(parseInt(m[1],10)).padStart(2,'0');}
}
const current=document.getElementById('smoothCurrent');
const currentText=current?String(current.textContent||'').trim():'';
if(/^\d{2}$/.test(currentText)){return currentText;}
return '--';
}
function sensorInfoFromP00(data){
const lines=Array.isArray(data.lines)?data.lines:[];
for(const line of lines){
const txt=stripCtrl(line);
const m=txt.match(/^(\d{2}):\s*([A-Za-z0-9\.\-]+)/);
if(m){return {ch:m[1],type:m[2],unit:extractUnit(txt)};}
}
return {ch:'-',type:'-',unit:'-'};
}
function extractUnit(text){
const t=String(text||'');
const candidates=[
{re:/°C/,val:'°C'},
{re:/�C/,val:'°C'},
{re:/°F/,val:'°F'},
{re:/�F/,val:'°F'},
{re:/\bms\b/,val:'ms'},
{re:/\bPa\b/,val:'Pa'},
{re:/\bmb\b/,val:'mb'},
{re:/\blx\b/i,val:'lx'},
{re:/\bRH\b/,val:'RH'},
{re:/\bm\/s\b/,val:'m/s'}
];
for(const c of candidates){if(c.re.test(t)){return c.val;}}
return '-';
}
async function readFullSensorInfo(){
    if(_sensorInfoRunning) return;
    _sensorInfoRunning=true;
    const ch=normChannel(document.getElementById('smoothChannel').value);
    setLoading(true, `Reading fast info for Ch${ch}...`);
    try {
        document.getElementById('smoothChannel').value=ch;
        const out=document.getElementById('out');const pretty=document.getElementById('outPretty');
        out.textContent='...';pretty.textContent='...';
        setLoadingText(`Ch${ch}: reading fast sensor info in one session...`);
        const data=await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'M'+ch,read_lines:0,timeout_ms:300},
            {command:'f2 P00',read_lines:4},
            {command:'P32',read_lines:2}
        ]);
        const seq=Array.isArray(data.sequence)?data.sequence:[];
        const p00=seq[2]||{};
        const p32=seq[3]||{};

        const n=smoothingCountFromP32(p32);
        const cur=document.getElementById('smoothCurrent'); if(cur){cur.textContent=n;}
        const info=sensorInfoFromP00(p00);
        const st=document.getElementById('sensorType'); if(st){st.textContent=info.type||'-';}
        const sc=document.getElementById('sensorChannel'); if(sc){sc.textContent=info.ch||'-';}
        const su=document.getElementById('sensorUnit'); if(su){su.textContent=info.unit||'-';}

        const prettyText=[
            prettyWithTitle('f2 P00 (fast channel programming)',p00),
            prettyWithTitle('P32 (smoothing)',p32),
        ].join('\n\n');
        out.textContent=JSON.stringify(data,null,2);
        pretty.textContent=prettyText;
    } catch(err) {
        console.error('Full sensor info failed', err);
    } finally {
        _sensorInfoRunning=false;
        setLoading(false);
    }
}
let _sensorInfoRunning=false;
let liveTimer=null;
let fastLiveTimer=null;
let liveLines=[];
let liveCurrentLine='';
let _lastLiveCycle='000001';
let _lastFastRate='10';
function liveCycleLabel(value){
const digits=String(value||'').replace(/\D/g,'').padStart(6,'0').slice(-6);
const hh=Number(digits.slice(0,2));
const mm=Number(digits.slice(2,4));
const ss=Number(digits.slice(4,6));
if(hh===0&&mm===0){return ss+' s';}
if(hh===0&&ss===0){return mm+' min';}
return hh.toString().padStart(2,'0')+':'+mm.toString().padStart(2,'0')+':'+ss.toString().padStart(2,'0');
}
function formatLiveLine(line){
    const txt=stripCtrl(line||'').replace(/\s+/g,' ').trim();
    // Support V6 (space after time) and V7 (semicolon after time)
    const m=txt.match(/^(\d{2}:\d{2}:\d{2}(?:\.\d{2})?)[ \s;]+(.*)$/);
    if(!m){return txt;}
    const time=m[1];
    let rest=m[2];
    
    if (rest.includes(';')) {
        // V7 format: 0.0;23.5;C -> 0.0 | 23.5 | C
        rest = rest.split(';').filter(Boolean).join(' | ');
    } else {
        // V6 format: 01: +0023.5 C -> 01: | +0023.5 | C
        rest = rest.replace(/\s+(?=\d{2}:\s)/g,' | ');
        rest = rest.replace(/(\d{2}:)\s+/g, '$1 | ');
    }
    return time+' | '+rest;
}
function clearLive(){
const el=document.getElementById('liveOut');
liveLines=[];liveCurrentLine='';
if(el){el.textContent='';}
}
function appendLiveLine(line){
const el=document.getElementById('liveOut');
if(!el){return;}
liveLines.push(formatLiveLine(line));
if(liveLines.length>10){liveLines=liveLines.slice(-10);}
el.textContent=liveLines.join('\n');
el.scrollTop=el.scrollHeight;
}
function _makeLivePoller(cycle){
    return async()=>{
        if (document.body.style.cursor === 'wait') return;
        try{
            const res=await fetch('api/live/poll');
            const data=await res.json();
            const lines=Array.isArray(data.lines)?data.lines:[];
            for(const raw of lines){
                const line=stripCtrl(raw||'').replace(/\s+$/,'');
                if(!line){continue;}
                if(/^\d{2}:\d{2}:\d{2}/.test(line)){
                    if(liveCurrentLine){appendLiveLine(liveCurrentLine.trim());}
                    liveCurrentLine=line;
                }else{liveCurrentLine=liveCurrentLine?(liveCurrentLine+' '+line.trim()):line;}
            }
            const activeCycle=data&&data.cycle?String(data.cycle):cycle;
            const _hasData = liveLines.length > 0;
            if (_hasData) _setLiveStarting(false);
            const _liveLabel = _hasData
                ? 'running ('+liveCycleLabel(activeCycle)+' print cycle)'
                : 'running — waiting for first data...';
            document.getElementById('liveStatus').textContent=_liveLabel;
            setOverview('overviewLiveState', _hasData ? 'running ('+liveCycleLabel(activeCycle)+')' : 'waiting for data');
        }catch(err){
            appendLiveLine('Poll: ' + String(err));
        }
    };
}
function _makeFastLivePoller(rate){
    return async()=>{
        if (document.body.style.cursor === 'wait') return;
        try{
            const res=await fetch('api/fast-live/poll');
            const data=await res.json();
            const lines=Array.isArray(data.lines)?data.lines:[];
            for(const raw of lines){
                const line=stripCtrl(raw||'').replace(/\s+$/,'');
                if(!line){continue;}
                if(/^\d{2}:\d{2}:\d{2}(?:\.\d{2})?/.test(line)){
                    if(liveCurrentLine){appendLiveLine(liveCurrentLine.trim());}
                    liveCurrentLine=line;
                }else{liveCurrentLine=liveCurrentLine?(liveCurrentLine+' '+line.trim()):line;}
            }
            const activeRate=data&&data.rate?String(data.rate):rate;
            const _hasData = liveLines.length > 0;
            if (_hasData) _setLiveStarting(false);
            const _fastLabel = _hasData
                ? 'running (Continuous Query '+activeRate+' M/s)'
                : 'running — waiting for first data...';
            document.getElementById('liveStatus').textContent=_fastLabel;
            setOverview('overviewLiveState', _hasData ? 'running (continuous query '+activeRate+' M/s)' : 'waiting for data');
        }catch(err){
            appendLiveLine('Fast poll: ' + String(err));
        }
    };
}
async function startLive(cycle){
    cycle=cycle||_lastLiveCycle;_lastLiveCycle=cycle;
    document.getElementById('liveStatus').textContent='starting ('+liveCycleLabel(cycle)+')...';
    setOverview('overviewLiveState','starting ('+liveCycleLabel(cycle)+')');
    if(liveTimer){clearInterval(liveTimer);liveTimer=null;}
    if(fastLiveTimer){
        clearInterval(fastLiveTimer);fastLiveTimer=null;
        try { await fetch('api/fast-live/stop',{method:'POST'}); } catch(_) {}
    }
    clearLive();
    try{
        await fetch('api/live/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cycle})});
        liveTimer=setInterval(_makeLivePoller(cycle),500);
    } catch(err) {
        console.error('Start live failed', err);
    }
}
async function stopLive(){
    if(liveTimer){clearInterval(liveTimer);liveTimer=null;}
    if(liveCurrentLine){appendLiveLine(liveCurrentLine.trim());liveCurrentLine='';}
    _setLiveStarting(false);
    try{
        await fetch('api/live/stop',{method:'POST'});
    } catch(err) {
        console.error('Stop live failed', err);
    } finally {
        document.getElementById('liveStatus').textContent='stopped';
        setOverview('overviewLiveState','stopped');
    }
}
async function startFastLive(rate){
    rate=rate||_lastFastRate;_lastFastRate=rate;
    document.getElementById('liveStatus').textContent='starting (Continuous Query '+rate+' M/s)...';
    setOverview('overviewLiveState','starting (continuous query '+rate+' M/s)');
    if(fastLiveTimer){clearInterval(fastLiveTimer);fastLiveTimer=null;}
    if(liveTimer){
        clearInterval(liveTimer);liveTimer=null;
        try { await fetch('api/live/stop',{method:'POST'}); } catch(_) {}
    }
    clearLive();
    try{
        await fetch('api/fast-live/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rate})});
        fastLiveTimer=setInterval(_makeFastLivePoller(rate),200);
    } catch(err) {
        console.error('Start fast live failed', err);
    }
}
async function stopFastLive(){
    if(fastLiveTimer){clearInterval(fastLiveTimer);fastLiveTimer=null;}
    if(liveCurrentLine){appendLiveLine(liveCurrentLine.trim());liveCurrentLine='';}
    _setLiveStarting(false);
    try{
        await fetch('api/fast-live/stop',{method:'POST'});
    } catch(err) {
        console.error('Stop fast live failed', err);
    } finally {
        document.getElementById('liveStatus').textContent='stopped';
        setOverview('overviewLiveState','stopped');
    }
}
function _setLiveStarting(starting) {
    const btn = document.getElementById('liveStartBtn');
    if (!btn) return;
    btn.disabled = starting;
    btn.textContent = starting ? 'Starting...' : 'Start';
}
async function startSelected(){
    const val=document.getElementById('liveMode').value;
    const idx=val.indexOf(':');
    const mode=val.slice(0,idx);
    const param=val.slice(idx+1);

    _setLiveStarting(true);
    setLoading(true, 'Preparing live stream...');
    try {
        if(mode==='print'){await startLive(param);}
        else if(mode==='fast'){await startFastLive(param);}
    } finally {
        setLoading(false);
        // Button stays disabled/Starting... until first data arrives (reset in pollers)
    }
}
async function stopSelected(){
    setLoading(true, 'Stopping all streams...');
    try {
        if(liveTimer){await stopLive();}
        if(fastLiveTimer){await stopFastLive();}
    } finally {
        setLoading(false);
    }
}
function clearSelected(){clearLive();}
async function syncLiveState(){
try{
const d=await(await fetch('api/live/poll')).json();
if(d.running){
const cycle=d.cycle||'000001';_lastLiveCycle=cycle;
const sel=document.getElementById('liveMode');
if(sel){const v='print:'+cycle;if([...sel.options].some(o=>o.value===v)){sel.value=v;}}
document.getElementById('liveStatus').textContent='running ('+liveCycleLabel(cycle)+' print cycle)';
setOverview('overviewLiveState','running ('+liveCycleLabel(cycle)+')');
if(!liveTimer){liveTimer=setInterval(_makeLivePoller(cycle),500);}
return;
}
}catch(_){}
try{
const d=await(await fetch('api/fast-live/poll')).json();
if(d.running){
const rate=d.rate||'10';_lastFastRate=rate;
const sel=document.getElementById('liveMode');
if(sel){const v='fast:'+rate;if([...sel.options].some(o=>o.value===v)){sel.value=v;}}
document.getElementById('liveStatus').textContent='running (Continuous Query '+rate+' M/s)';
setOverview('overviewLiveState','running (CQ '+rate+' M/s)');
if(!fastLiveTimer){fastLiveTimer=setInterval(_makeFastLivePoller(rate),200);}
}
}catch(_){}
}
async function readCh(ch, withTime){
    const out=document.getElementById('out');const pretty=document.getElementById('outPretty');
    out.textContent='...';pretty.textContent='...';
    const flag=withTime ? '&with_time=1' : '';
    setLoading(true, `Reading channel ${ch}...`);
    try{
        const res=await fetch('api/read?channel='+encodeURIComponent(ch)+flag);
        const data=await res.json();
        renderOutput(data);
    }catch(err){
        out.textContent=String(err);
    } finally {
        setLoading(false);
    }
}
async function scan(){
    const out=document.getElementById('out');const pretty=document.getElementById('outPretty');
    out.textContent='...';pretty.textContent='...';
    setLoading(true, 'Scanning basic channels (0-15)...');
    try{
        const res=await fetch('api/scan');
        const data=await res.json();
        renderOutput(data);
    }catch(err){
        out.textContent=String(err);
    } finally {
        setLoading(false);
    }
}
async function p15(){
    const out=document.getElementById('out');const pretty=document.getElementById('outPretty');
    out.textContent='...';pretty.textContent='...';
    setLoading(true, 'Requesting active channels (P15)...');
    try{
        const res=await fetch('api/p15');
        const data=await res.json();
        renderOutput(data);
    }catch(err){
        out.textContent=String(err);
    } finally {
        setLoading(false);
    }
}
function normTime(val){return String(val||'').replace(/\D/g,'').slice(0,6);}
function normDate(val){
const digits=String(val||'').replace(/\D/g,'');
if(digits.length>=8){return digits.slice(0,4)+digits.slice(-2);}return digits.slice(0,6);}
function pad2(v){return String(v).padStart(2,'0');}
function formatTimeInput(){
const el=document.getElementById('timeInput');
if(!el){return;}
const d=String(el.value||'').replace(/\D/g,'').slice(0,6);
const p=[d.slice(0,2),d.slice(2,4),d.slice(4,6)].filter(Boolean);
el.value=p.join(':');
}
function formatDateInput(){
const el=document.getElementById('dateInput');
if(!el){return;}
const d=String(el.value||'').replace(/\D/g,'').slice(0,6);
const p=[d.slice(0,2),d.slice(2,4),d.slice(4,6)].filter(Boolean);
el.value=p.join('-');
}
function getTimeNow(){const d=new Date();return pad2(d.getHours())+pad2(d.getMinutes())+pad2(d.getSeconds());}
function getDateNow(){const d=new Date();return pad2(d.getDate())+pad2(d.getMonth()+1)+pad2(d.getFullYear()%100);}
function fillTimeNow(){const el=document.getElementById('timeInput');if(!el){return;}el.value=getTimeNow(); formatTimeInput();}
function fillDateNow(){const el=document.getElementById('dateInput');if(!el){return;}el.value=getDateNow(); formatDateInput();}
async function sleep(ms){return new Promise(r=>setTimeout(r,ms));}
async function setTime(){
    const el=document.getElementById('timeInput');const v=normTime(el ? el.value : getTimeNow());
    if(el){el.value=v;} if(v.length!==6){return;}
    setLoading(true, 'Setting time...');
    try {
        renderOutput(await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'U'+v,read_lines:0}
        ]));
    } finally { setLoading(false); }
}
function hasError(data){
const lines=Array.isArray(data.lines)?data.lines:[];
return lines.some(l=>String(l).includes('ERROR'));}
async function setDate(){
    const el=document.getElementById('dateInput');const v=normDate(el ? el.value : getDateNow());
    if(el){el.value=v;} if(v.length!==6){return;}
    setLoading(true, 'Setting date...');
    try {
        renderOutput(await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'d'+v,read_lines:0}
        ]));
    } finally { setLoading(false); }
}
async function setTimeNow(){
    setLoading(true, 'Setting time...');
    try {
        const v=getTimeNow();
        renderOutput(await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'U'+v,read_lines:0}
        ]));
    } finally { setLoading(false); }
}
async function setDateNow(){
    setLoading(true, 'Setting date...');
    try {
        const v=getDateNow();
        renderOutput(await sendCmdSequence([
            {command:'G00',read_lines:0,timeout_ms:300},
            {command:'d'+v,read_lines:0}
        ]));
    } finally { setLoading(false); }
}
loadHealth();
syncLiveState();
refreshChannels();
setOverview('overviewChannel','Channel 00');
setOverview('overviewLiveState','stopped');
setOverview('overviewCommandState','Idle');
updateConnectedChannelsSummary([]);
document.getElementById('smoothChannel').addEventListener('change',()=>{setOverview('overviewChannel','Channel '+normChannel(document.getElementById('smoothChannel').value));});
