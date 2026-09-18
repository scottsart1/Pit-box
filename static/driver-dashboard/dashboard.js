import {DISPLAY_PROFILES,LAYOUTS,cleanPreferences,resolveDisplay} from './display.mjs';
import {demoState,normalizeState,escapeHTML} from './model.mjs';
import {renderDashboard} from './render.mjs';

const $=id=>document.getElementById(id), storeKey='ypb-driver-dashboard-v2';
const params=new URLSearchParams(location.search), installed=location.pathname.includes('/static/driver-dashboard/');
const embedded=params.get('mode')==='embedded'&&parent!==window;
let preferences;try{preferences=cleanPreferences(JSON.parse(localStorage.getItem(storeKey)))}catch{preferences=cleanPreferences(null)}
if(LAYOUTS.includes(location.hash.slice(1)))preferences.layout=location.hash.slice(1);
let source=embedded?'embedded':installed&&params.get('mode')==='live'?'live':'demo';
let currentState=null,receivedAt=0,tick=0,motion=null,ws=null,retry=null,wakeLock=null,installPrompt=null,dragged=null;
let display,layout='cockpit',raceMode=false,framePending=false,lastFresh=null;
const names={cockpit:'Cockpit',focus:'Race Focus',battle:'Battle',endurance:'Endurance',modular:'My Layout',portrait:'Phone'};
const descriptions={cockpit:'Instruments, rivals and lap timing',focus:'Large delta and the essentials',battle:'Race order and rival lap comparisons',endurance:'Stint, fuel and lap consistency',modular:'Arrange your own cards',portrait:'A clear view for narrow screens'};
function save(){try{localStorage.setItem(storeKey,JSON.stringify(preferences))}catch{toast('Preferences work for this visit; browser storage is unavailable.')}}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3000)}
function viewportMetrics(){const v=window.visualViewport;return {width:document.documentElement.clientWidth,height:Math.round(v&&v.scale===1?v.height:innerHeight),touch:navigator.maxTouchPoints>0||matchMedia('(pointer:coarse)').matches}}
function applyDisplay(){display=resolveDisplay(preferences.profile,viewportMetrics());layout=preferences.layout==='auto'?display.recommendedLayout:preferences.layout;document.body.dataset.display=display.family;document.body.dataset.orientation=display.orientation;document.body.classList.toggle('compact-screen',display.compact);document.body.classList.toggle('short-screen',display.short);document.documentElement.style.setProperty('--usable-height',display.height+'px');$('displayStatus').textContent=`${display.label} · ${display.width} × ${display.height} CSS px`;$('profileSelect').value=preferences.profile;$('autoLayout').setAttribute('aria-pressed',String(preferences.layout==='auto'));$('layoutStatus').textContent=`${names[layout]} · ${descriptions[layout]}`;for(const b of $('conceptNav').children)b.setAttribute('aria-pressed',String(b.dataset.layout===layout));scheduleRender()}
// Patch text and attributes in place so 4 Hz telemetry does not steal keyboard focus.
function patchNode(old,next){if(old.nodeType!==next.nodeType||old.nodeName!==next.nodeName){old.replaceWith(next.cloneNode(true));return}if(old.nodeType===Node.TEXT_NODE){if(old.nodeValue!==next.nodeValue)old.nodeValue=next.nodeValue;return}if(old.nodeType!==Node.ELEMENT_NODE)return;for(const a of [...old.attributes])if(!next.hasAttribute(a.name))old.removeAttribute(a.name);for(const a of [...next.attributes])if(old.getAttribute(a.name)!==a.value)old.setAttribute(a.name,a.value);for(let i=0;i<next.childNodes.length;i++){if(!old.childNodes[i])old.appendChild(next.childNodes[i].cloneNode(true));else patchNode(old.childNodes[i],next.childNodes[i])}while(old.childNodes.length>next.childNodes.length)old.lastChild.remove()}
function scheduleRender(){if(framePending)return;framePending=true;requestAnimationFrame(()=>{framePending=false;render()})}
function render(){if(dragged)return;const m=normalizeState(currentState,{ageMs:source==='demo'?0:Date.now()-receivedAt,transport:source});lastFresh=m.fresh;const d=$('dashboard');d.dataset.layout=layout;d.dataset.scenario=m.flag.id;d.style.setProperty('--flag-color',m.flag.color);d.style.setProperty('--reading-scale',preferences.scale);for(const k of ['tires','resources','sectors','radio'])d.classList.toggle('hide-'+k,!preferences[k]);document.body.classList.toggle('high-contrast',preferences.contrast);document.documentElement.style.setProperty('--accent',({blue:'#3f86ff',cyan:'#30d6d0',violet:'#ba9aff'})[preferences.accent]);const next=document.createElement('section');next.innerHTML=renderDashboard(layout,m,preferences);for(let i=0;i<next.childNodes.length;i++){if(!d.childNodes[i])d.appendChild(next.childNodes[i].cloneNode(true));else patchNode(d.childNodes[i],next.childNodes[i])}while(d.childNodes.length>next.childNodes.length)d.lastChild.remove();$('sessionStatus').textContent=source==='demo'?'DEMO · SIMULATED':m.fresh?'LIVE TELEMETRY':'WAITING FOR TELEMETRY';$('trackStatus').textContent=m.fresh?m.track+' · '+m.session:'Start a simulator session';$('raceModeSource').textContent=source==='demo'?'DEMO':m.fresh?'LIVE':'NO DATA'}
function acceptState(s){if(!s||typeof s!=='object')return;currentState=s;receivedAt=Date.now();scheduleRender()}
function updateDemo(){acceptState(demoState($('scenarioSelect').value,tick))}
function stopMotion(){clearInterval(motion);motion=null;$('motionButton').textContent='Play demo';$('motionButton').setAttribute('aria-pressed','false')}
function disconnect(){clearTimeout(retry);retry=null;if(ws){ws.onclose=null;ws.close();ws=null}}
function connect(){disconnect();if(source!=='live'||!installed)return;const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);ws=socket;socket.onmessage=e=>{try{acceptState(JSON.parse(e.data))}catch{/* malformed frame stays stale */}if(socket.readyState===WebSocket.OPEN)socket.send('.')};socket.onclose=()=>{if(source==='live')retry=setTimeout(connect,1500)};socket.onerror=()=>socket.close()}
function setSource(){stopMotion();disconnect();source=embedded?'embedded':$('sourceSelect').value;currentState=null;receivedAt=0;$('demoControls').hidden=source!=='demo';if(source==='demo')updateDemo();else if(source==='live')connect();scheduleRender()}
function chooseLayout(id){if(!['auto',...LAYOUTS].includes(id))return;preferences.layout=id;save();history.replaceState(null,'',location.pathname+location.search+(id==='auto'?'':'#'+id));applyDisplay()}
async function acquireWake(){if(!raceMode||document.visibilityState!=='visible'||!('wakeLock'in navigator))return;try{wakeLock=await navigator.wakeLock.request('screen')}catch{/* Browser settings may deny a wake lock. */}}
async function releaseWake(){const lock=wakeLock;wakeLock=null;try{await lock?.release()}catch{/* Already released. */}}
function setRaceMode(on){raceMode=on;document.body.classList.toggle('race-mode',on);if(on){$('exitRaceMode').focus();window.scrollTo(0,0);acquireWake()}else{releaseWake();$('raceModeButton').focus()}applyDisplay()}
function showSettings(){const p=preferences;$('settingsBody').innerHTML=`<label class="setting-row">Speed units<select id="unitsSetting"><option value="kmh">km/h</option><option value="mph">mph</option></select></label><label class="setting-row">Number size<select id="scaleSetting"><option value="1">Standard</option><option value="1.1">Large · 110%</option><option value="1.2">Extra large · 120%</option></select></label><label class="setting-row">Accent<select id="accentSetting"><option value="blue">Blue</option><option value="cyan">Cyan</option><option value="violet">Violet</option></select></label>${[['contrast','High contrast'],['tires','Tyre temperatures'],['resources','Fuel and energy'],['sectors','Sector progress'],['radio','Engineer message']].map(([k,l])=>`<label class="setting-row">${l}<input type="checkbox" data-setting="${k}" ${p[k]?'checked':''}></label>`).join('')}<p class="settings-note">Saved on this browser. Race control flags always remain visible.</p>`;for(const k of ['units','scale','accent'])$(k+'Setting').value=p[k];$('settingsDialog').showModal()}
$('settingsBody').addEventListener('change',e=>{const k=e.target.dataset.setting||e.target.id.replace('Setting','');preferences=cleanPreferences({...preferences,[k]:e.target.type==='checkbox'?e.target.checked:e.target.value});save();scheduleRender()});
$('profileSelect').innerHTML=DISPLAY_PROFILES.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');
$('conceptNav').innerHTML=LAYOUTS.map((id,i)=>`<button type="button" class="concept-button" data-layout="${id}" aria-pressed="false" title="${descriptions[id]}"><small>0${i+1}</small>${names[id]}</button>`).join('');
$('conceptNav').addEventListener('click',e=>{const b=e.target.closest('[data-layout]');if(b)chooseLayout(b.dataset.layout)});
$('profileSelect').addEventListener('change',()=>{preferences.profile=$('profileSelect').value;save();applyDisplay()});
$('autoLayout').addEventListener('click',()=>chooseLayout('auto'));
$('customizeButton').addEventListener('click',showSettings);
$('doneSettings').addEventListener('click',()=>$('settingsDialog').close());
$('resetPreferences').addEventListener('click',()=>{preferences=cleanPreferences(null);save();$('settingsDialog').close();applyDisplay();toast('Display and layout preferences reset.')});
$('helpButton').addEventListener('click',()=>$('helpDialog').showModal());
$('doneHelp').addEventListener('click',()=>$('helpDialog').close());
$('raceModeButton').addEventListener('click',()=>setRaceMode(true));
$('exitRaceMode').addEventListener('click',()=>setRaceMode(false));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&raceMode)setRaceMode(false)});
$('scenarioSelect').addEventListener('change',updateDemo);
$('motionButton').addEventListener('click',()=>{if(motion)stopMotion();else{motion=setInterval(()=>{tick++;updateDemo()},500);$('motionButton').textContent='Pause demo';$('motionButton').setAttribute('aria-pressed','true')}});
$('sourceSelect').value=embedded?'live':source;$('sourceSelect').disabled=embedded;$('sourceSelect').querySelector('[value=live]').disabled=!installed&&!embedded;
$('sourceSelect').addEventListener('change',setSource);
function moveModule(id,direction,target){const order=preferences.order,from=order.indexOf(id);let to=target?order.indexOf(target):from+direction;if(from<0||to<0||to>=order.length)return;order.splice(to,0,order.splice(from,1)[0]);save();scheduleRender()}
$('dashboard').addEventListener('click',e=>{const b=e.target.closest('[data-move]');if(b)moveModule(b.dataset.move,Number(b.dataset.direction))});
$('dashboard').addEventListener('dragstart',e=>{const h=e.target.closest('[data-drag]');if(!h)return;dragged=h.dataset.drag;e.dataTransfer.setData('text/plain',dragged);e.dataTransfer.effectAllowed='move'});
$('dashboard').addEventListener('dragover',e=>{if(dragged&&e.target.closest('[data-module]'))e.preventDefault()});
$('dashboard').addEventListener('drop',e=>{const target=e.target.closest('[data-module]');if(dragged&&target){e.preventDefault();const id=dragged;dragged=null;moveModule(id,0,target.dataset.module)}});
$('dashboard').addEventListener('dragend',()=>{dragged=null;scheduleRender()});
window.addEventListener('resize',applyDisplay);window.visualViewport?.addEventListener('resize',applyDisplay);
window.addEventListener('hashchange',()=>{if(LAYOUTS.includes(location.hash.slice(1)))chooseLayout(location.hash.slice(1))});
window.addEventListener('message',e=>{if(embedded&&e.origin===location.origin&&e.source===parent&&e.data?.type==='pitbox:driver-state')acceptState(e.data.state)});
window.addEventListener('pagehide',()=>{disconnect();releaseWake()});
window.addEventListener('pageshow',e=>{if(e.persisted&&source==='live')connect()});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')acquireWake();else releaseWake()});
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;$('installButton').hidden=false});
$('installButton').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;$('installButton').hidden=true}});
if('serviceWorker'in navigator&&location.protocol==='https:'&&!installed)navigator.serviceWorker.register('./sw.js').catch(()=>{});
setInterval(()=>{if(source!=='demo'&&lastFresh&&Date.now()-receivedAt>=3500)scheduleRender()},500);
document.body.classList.toggle('embedded',embedded);applyDisplay();setSource();
if(embedded)parent.postMessage({type:'pitbox:driver-ready'},location.origin);
