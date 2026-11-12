/* Run with node tests/cameras_capture_ui.js. */
async function run(source) {
  const assert=(value,message)=>{if(!value)throw Error(message);};
  const html=fs.readFileSync('app/cameras/web/index.html','utf8');
  const elements=new Map(),requests=[];
  const element=id=>{
    if(!elements.has(id)) {
      const select=html.match(new RegExp('<select id="'+id+'">(.*?)</select>','s'));
      const options=select?[...select[1].matchAll(/<option value="([^"]+)"([^>]*)>/g)].map(match=>({
        value:match[1],dataset:{maxFps:match[2].match(/data-max-fps="(\d+)"/)?.[1]}})):[];
      elements.set(id,{value:id==='anomaly-multiplier'?'1.25':'',listeners:{},options,
        addEventListener(type,callback){this.listeners[type]=callback;}});
    }
    return elements.get(id);
  };
  const change=(id,value)=>{element(id).value=value;element(id).listeners.change();};
  const document={hidden:false,activeElement:null,getElementById:element,addEventListener(){}};
  let settings={capture_mode:'video',snapshot_interval_ms:200,shutter_us:0,width:1280,height:720,fps:15};
  const fetch=(url,options)=>{
    if(url!=='api/capture')return new Promise(()=>{});
    if(options){requests.push(JSON.parse(options.body));settings={...settings,...requests.at(-1)};
      return Promise.resolve({ok:true,json:async()=>({settings})});}
    return Promise.resolve({ok:true,json:async()=>({available:true,settings})});
  };
  new Function('document','fetch','Image','setInterval','setTimeout','performance','window',source)(
    document,fetch,class{},()=>{},()=>{},{now:()=>0},{});
  await new Promise(resolve=>setImmediate(resolve));
  assert(!element('capture-panel').hidden,'Native Pi capture controls should be visible');
  assert(element('capture-mode').value==='video','Initialize current mode');
  assert(!element('capture-resolution-label').hidden&&!element('capture-fps-label').hidden,'Video shows resolution and FPS');
  assert(element('capture-interval-label').hidden&&element('capture-exposure-label').hidden,'Video hides snapshot controls');
  assert(element('capture-resolution').value==='1280x720'&&element('capture-fps').value==='15','Initialize video settings');
  change('capture-mode','snapshots');
  assert(!element('capture-interval-label').hidden&&!element('capture-exposure-label').hidden,'Snapshots show interval and exposure');
  assert(!element('capture-resolution-label').hidden&&element('capture-fps-label').hidden,'Snapshots show resolution and hide video FPS');
  change('capture-resolution','1920x1080');
  change('capture-exposure','40000');
  await element('capture-apply').onclick();
  assert(requests[0].capture_mode==='snapshots'&&requests[0].snapshot_interval_ms===200,'Send snapshot mode and interval');
  assert(requests[0].shutter_us===40000&&requests[0].width===1920&&requests[0].height===1080,'Send snapshot exposure and resolution');
  assert(!('fps' in requests[0]),'Snapshot rate is determined by the interval');
  assert(element('capture-status').textContent.includes('Detection is paused'),'Explain paused detection');
  for(const shutter of ['500','200']) {
    change('capture-exposure',shutter);
    await element('capture-apply').onclick();
    assert(requests.at(-1).shutter_us===Number(shutter),'Send short exposure in microseconds');
  }
  change('capture-mode','video');change('capture-resolution','2560x1440');change('capture-fps','15');
  await element('capture-apply').onclick();
  let sent=requests.at(-1);
  assert(sent.width===2560&&sent.height===1440&&sent.fps===15,'Send selected video resolution and FPS');
  assert(sent.shutter_us===0,'Video uses Auto exposure');
  change('capture-resolution','3840x2160');
  assert(element('capture-fps').value==='15','Clamp full resolution to 15 FPS');
  assert(element('capture-fps').options.every(x=>Number(x.value)<=15),'Only offer rates supported by full sensor readout');
  await element('capture-apply').onclick();
  sent=requests.at(-1);
  assert(sent.width===3840&&sent.height===2160&&sent.fps===15,'Send clamped FPS with full resolution');
  change('capture-mode','snapshots');change('capture-resolution','1280x720');
  await element('capture-apply').onclick();
  sent=requests.at(-1);
  assert(sent.width===1280&&sent.height===720&&sent.capture_mode==='snapshots','Apply another snapshot resolution after switching modes');
  const shutters=element('capture-exposure').options.map(option=>Number(option.value));
  assert(shutters.includes(200)&&shutters.includes(500),'Offer 1/5000 and 1/2000 s');
  assert(shutters.includes(40000)&&shutters.every(value=>value<=40000),'Manual exposure is limited to 1/25 s');
  console.log('Video resolution/FPS, snapshot resolution/exposure, mode visibility and apply UI passed.');
}
const fs=require('node:fs');
run(fs.readFileSync('app/cameras/web/app.js','utf8')).catch(error=>{console.error(error);process.exitCode=1;});
