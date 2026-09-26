import {DISPLAY_PROFILES,LAYOUTS,WIDGET_CATALOG,WIDGET_PRESETS,cleanPreferences,cleanWidgets,presetWidgets,reorderWidgets,resizedWidget,resolveDisplay} from './display.mjs';
import {demoState,normalizeState,escapeHTML} from './model.mjs';
import {renderDashboard} from './render.mjs';
import {restorePreferences} from './preferences.mjs';

async function initializeDashboard(){
const $=id=>document.getElementById(id);
const params=new URLSearchParams(location.search), installed=location.pathname.includes('/static/driver-dashboard/');
const embedded=params.get('mode')==='embedded'&&parent!==window;
let browserStorage=null;try{browserStorage=window.localStorage}catch{/* Private browsing may disable DOM storage. */}
// Finish the native read (and first-use migration) before rendering or wiring
// controls. Default values must never race a restored Android layout.
const preferenceStore=await restorePreferences({storage:browserStorage,bridge:window.PitBoxDashboardPreferences});
let preferences=preferenceStore.preferences;
if(LAYOUTS.includes(location.hash.slice(1)))preferences.layout=location.hash.slice(1);
let source=installed&&params.get('mode')==='live'?'live':'demo';
let currentState=null,receivedAt=0,tick=0,motion=null,ws=null,retry=null,wakeLock=null,installPrompt=null,gesture=null;
let sourceChosen=params.get('mode')==='live',latestParent=null,parentReceivedAt=0;
const undoStack=[];
let display,layout='cockpit',raceMode=false,framePending=false,lastFresh=null,renderedProvenance=null;
const names={cockpit:'Cockpit',focus:'Race Focus',battle:'Battle',endurance:'Endurance',modular:'My Layout',portrait:'Phone'};
const descriptions={cockpit:'Instruments, rivals and lap timing',focus:'Large delta and the essentials',battle:'Race order and rival lap comparisons',endurance:'Stint, fuel and lap consistency',modular:'Arrange your own cards',portrait:'A clear view for narrow screens'};
function save(){preferenceStore.save(preferences).then(result=>{preferenceStore.mode=result.mode;if(result.issue)toast(result.issue);updateStorageLabel()})}
function updateStorageLabel(){const label=$('dashboard').querySelector('.layout-saved');if(label)label.textContent=preferenceStore.mode==='android'?'AUTOSAVED ON THIS DEVICE':preferenceStore.mode==='memory'?'THIS VISIT ONLY':preferenceStore.nativeAvailable?'SAVED IN THIS VIEW':'AUTOSAVED ON THIS BROWSER'}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,3000)}
function viewportMetrics(){const v=window.visualViewport;return {width:document.documentElement.clientWidth,height:Math.round(v&&v.scale===1?v.height:innerHeight),touch:navigator.maxTouchPoints>0||matchMedia('(pointer:coarse)').matches}}
function applyDisplay(){display=resolveDisplay(preferences.profile,viewportMetrics());layout=preferences.layout==='auto'?display.recommendedLayout:preferences.layout;document.body.dataset.display=display.family;document.body.dataset.orientation=display.orientation;document.body.classList.toggle('compact-screen',display.compact);document.body.classList.toggle('short-screen',display.short);document.documentElement.style.setProperty('--usable-height',display.height+'px');$('displayStatus').textContent=`${display.label} · ${display.width} × ${display.height} CSS px`;$('profileSelect').value=preferences.profile;$('autoLayout').setAttribute('aria-pressed',String(preferences.layout==='auto'));$('layoutStatus').textContent=`${names[layout]} · ${descriptions[layout]}`;$('workspaceToolbar').hidden=layout!=='modular';for(const b of $('conceptNav').children)b.setAttribute('aria-pressed',String(b.dataset.layout===layout));scheduleRender()}
// Patch text and attributes in place so 4 Hz telemetry does not steal keyboard focus.
function patchNode(old,next){if(old.nodeType!==next.nodeType||old.nodeName!==next.nodeName){old.replaceWith(next.cloneNode(true));return}if(old.nodeType===Node.TEXT_NODE){if(old.nodeValue!==next.nodeValue)old.nodeValue=next.nodeValue;return}if(old.nodeType!==Node.ELEMENT_NODE)return;for(const a of [...old.attributes])if(!next.hasAttribute(a.name))old.removeAttribute(a.name);for(const a of [...next.attributes])if(old.getAttribute(a.name)!==a.value)old.setAttribute(a.name,a.value);for(let i=0;i<next.childNodes.length;i++){if(!old.childNodes[i])old.appendChild(next.childNodes[i].cloneNode(true));else patchNode(old.childNodes[i],next.childNodes[i])}while(old.childNodes.length>next.childNodes.length)old.lastChild.remove()}
function scheduleRender(){if(framePending)return;framePending=true;requestAnimationFrame(()=>{framePending=false;render()})}
function render(){
  const m=normalizeState(currentState,{ageMs:source==='demo'?0:Date.now()-receivedAt,transport:source});lastFresh=m.fresh;
  if(gesture&&(!m.fresh||renderedProvenance!==m.provenance))finishGesture({pointerId:gesture.pointer},true);
  renderedProvenance=m.provenance;
  const d=$('dashboard');d.dataset.layout=layout;d.dataset.scenario=m.flag.id;d.style.setProperty('--flag-color',m.flag.color);d.style.setProperty('--reading-scale',preferences.scale);
  for(const k of ['tires','resources','sectors','radio'])d.classList.toggle('hide-'+k,layout!=='modular'&&!preferences[k]);
  document.body.classList.toggle('high-contrast',preferences.contrast);document.documentElement.style.setProperty('--accent',({blue:'#3f86ff',cyan:'#30d6d0',violet:'#ba9aff'})[preferences.accent]);
  const next=document.createElement('section');next.innerHTML=renderDashboard(layout,m,preferences);
  // Keep the dragged card's DOM stable, while flags and data-source labels remain live.
  const incoming=[...next.children],keys=new Set(incoming.map(node=>node.classList[0]));
  for(const old of [...d.children])if(!keys.has(old.classList[0]))old.remove();
  incoming.forEach((node,index)=>{
    const old=[...d.children].find(child=>child.classList[0]===node.classList[0]);
    if(!old)d.insertBefore(node.cloneNode(true),d.children[index]||null);
    else if(!(gesture&&node.classList.contains('data-surface')))patchNode(old,node);
  });
  const sourceLabel=m.provenance==='sample'?'SAMPLE · SIMULATED':m.provenance==='replay'?'RECORDED REPLAY':m.fresh?'LIVE TELEMETRY':'WAITING FOR TELEMETRY';
  $('sessionStatus').textContent=sourceLabel;$('trackStatus').textContent=m.fresh?m.track+' · '+m.session:'Start a simulator session';$('raceModeSource').textContent=sourceLabel;
  updateStorageLabel();
}
function acceptState(s){if(!s||typeof s!=='object')return;currentState=s;receivedAt=Date.now();scheduleRender()}
function updateDemo(){acceptState(demoState($('scenarioSelect').value,tick))}
function stopMotion(){clearInterval(motion);motion=null;$('motionButton').textContent='Play sample';$('motionButton').setAttribute('aria-pressed','false')}
function disconnect(){clearTimeout(retry);retry=null;if(ws){ws.onclose=null;ws.close();ws=null}}
function connect(){disconnect();if(source!=='live'||!installed)return;const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws`);ws=socket;socket.onmessage=e=>{try{acceptState(JSON.parse(e.data))}catch{/* malformed frame stays stale */}if(socket.readyState===WebSocket.OPEN)socket.send('.')};socket.onclose=()=>{if(source==='live')retry=setTimeout(connect,1500)};socket.onerror=()=>socket.close()}
function setSource(){stopMotion();disconnect();source=$('sourceSelect').value==='demo'?'demo':embedded?'embedded':'live';currentState=null;receivedAt=0;$('demoControls').hidden=source!=='demo';if(source==='demo')updateDemo();else if(source==='live')connect();else if(latestParent){currentState=latestParent;receivedAt=parentReceivedAt}scheduleRender()}
function chooseLayout(id){if(!['auto',...LAYOUTS].includes(id))return;preferences.layout=id;save();history.replaceState(null,'',location.pathname+location.search+(id==='auto'?'':'#'+id));applyDisplay()}
async function acquireWake(){if(!raceMode||document.visibilityState!=='visible'||!('wakeLock'in navigator))return;try{wakeLock=await navigator.wakeLock.request('screen')}catch{/* Browser settings may deny a wake lock. */}}
async function releaseWake(){const lock=wakeLock;wakeLock=null;try{await lock?.release()}catch{/* Already released. */}}
function setRaceMode(on){raceMode=on;document.body.classList.toggle('race-mode',on);if(on){$('exitRaceMode').focus();window.scrollTo(0,0);acquireWake()}else{releaseWake();$('raceModeButton').focus()}applyDisplay()}
function showSettings(){const p=preferences;$('settingsBody').innerHTML=`<label class="setting-row">Speed units<select id="unitsSetting"><option value="kmh">km/h</option><option value="mph">mph</option></select></label><label class="setting-row">Number size<select id="scaleSetting"><option value="1">Standard</option><option value="1.1">Large · 110%</option><option value="1.2">Extra large · 120%</option></select></label><label class="setting-row">Accent<select id="accentSetting"><option value="blue">Blue</option><option value="cyan">Cyan</option><option value="violet">Violet</option></select></label>${[['contrast','High contrast'],['tires','Tyre temperatures'],['resources','Fuel and energy'],['sectors','Sector progress'],['radio','Engineer message']].map(([k,l])=>`<label class="setting-row">${l}<input type="checkbox" data-setting="${k}" ${p[k]?'checked':''}></label>`).join('')}<p class="settings-note">Saved locally. Race control flags always remain visible.</p>`;for(const k of ['units','scale','accent'])$(k+'Setting').value=p[k];$('settingsDialog').showModal()}
$('settingsBody').addEventListener('change',e=>{const k=e.target.dataset.setting||e.target.id.replace('Setting','');preferences=cleanPreferences({...preferences,[k]:e.target.type==='checkbox'?e.target.checked:e.target.value});save();scheduleRender()});
$('profileSelect').innerHTML=DISPLAY_PROFILES.map(p=>`<option value="${p.id}">${escapeHTML(p.name)}</option>`).join('');
$('conceptNav').innerHTML=LAYOUTS.map((id,i)=>`<button type="button" class="concept-button" data-layout="${id}" aria-pressed="false" title="${descriptions[id]}"><small>0${i+1}</small>${names[id]}</button>`).join('');
$('conceptNav').addEventListener('click',e=>{const b=e.target.closest('[data-layout]');if(b)chooseLayout(b.dataset.layout)});
$('profileSelect').addEventListener('change',()=>{preferences.profile=$('profileSelect').value;save();applyDisplay()});
$('autoLayout').addEventListener('click',()=>chooseLayout('auto'));
$('customizeButton').addEventListener('click',showSettings);
$('doneSettings').addEventListener('click',()=>$('settingsDialog').close());
$('resetPreferences').addEventListener('click',()=>{preferences=cleanPreferences(null);undoStack.length=0;$('undoLayout').disabled=true;save();$('settingsDialog').close();applyDisplay();toast('Display and layout preferences reset.')});
$('helpButton').addEventListener('click',()=>$('helpDialog').showModal());
$('doneHelp').addEventListener('click',()=>$('helpDialog').close());
$('raceModeButton').addEventListener('click',()=>setRaceMode(true));
$('exitRaceMode').addEventListener('click',()=>setRaceMode(false));
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&raceMode)setRaceMode(false)});
$('scenarioSelect').addEventListener('change',updateDemo);
$('motionButton').addEventListener('click',()=>{if(motion)stopMotion();else{motion=setInterval(()=>{tick++;updateDemo()},500);$('motionButton').textContent='Pause sample';$('motionButton').setAttribute('aria-pressed','true')}});
$('sourceSelect').value=source;$('sourceSelect').querySelector('[value=live]').disabled=!installed&&!embedded;
$('sourceSelect').addEventListener('change',()=>{sourceChosen=true;setSource()});

function commitWidgets(widgets,message){
  const next=cleanWidgets(widgets);
  if(JSON.stringify(next)===JSON.stringify(preferences.widgets))return;
  undoStack.push(preferences.widgets.map(w=>({...w})));if(undoStack.length>20)undoStack.shift();
  preferences.widgets=next;save();$('undoLayout').disabled=false;scheduleRender();if(message)toast(message);
}
function focusWidget(id,kind='drag'){
  requestAnimationFrame(()=>$('dashboard').querySelector(`[data-${kind}="${id}"]`)?.focus({preventScroll:true}));
}
function moveModule(id,direction,target){
  const from=preferences.widgets.findIndex(w=>w.id===id),destination=target||preferences.widgets[from+direction]?.id;
  if(!destination||destination===id)return;
  commitWidgets(reorderWidgets(preferences.widgets,id,destination),'Widget moved. Layout saved.');focusWidget(id);
}
function sizeModule(id,w,h){
  const widget=preferences.widgets.find(v=>v.id===id);if(!widget)return;
  commitWidgets(preferences.widgets.map(v=>v.id===id?{...v,w:Math.max(1,Math.min(3,w)),h:Math.max(1,Math.min(3,h))}:v),'Widget resized. Layout saved.');
}
function renderLibrary(){
  const query=$('widgetSearch').value.trim().toLowerCase();
  const candidates=WIDGET_CATALOG.filter(w=>(w.name+' '+w.group+' '+w.description).toLowerCase().includes(query));
  $('widgetLibrary').innerHTML=['Driving','Timing','Race','Car','Strategy'].map(group=>{
    const widgets=candidates.filter(w=>w.group===group);if(!widgets.length)return '';
    return `<section class="library-group"><h3>${group}</h3><div class="library-grid">${widgets.map(w=>{const added=preferences.widgets.some(v=>v.id===w.id);return `<button class="library-widget" data-toggle-widget="${w.id}" aria-pressed="${added}"><span class="library-widget-icon" aria-hidden="true">${added?'✓':'＋'}</span><span><b>${escapeHTML(w.name)}</b><small>${escapeHTML(w.description)}</small></span><span class="library-widget-state">${added?'Added':'Add'}</span></button>`}).join('')}</div></section>`;
  }).join('')||'<p class="settings-note">No matching widgets. Try speed, tyre, fuel or timing.</p>';
}
function showWidgets(){renderLibrary();$('widgetDialog').showModal()}
$('addWidgetsButton').addEventListener('click',showWidgets);
$('doneWidgets').addEventListener('click',()=>$('widgetDialog').close());
$('widgetSearch').addEventListener('input',renderLibrary);
$('widgetLibrary').addEventListener('click',e=>{
  const b=e.target.closest('[data-toggle-widget]');if(!b)return;const id=b.dataset.toggleWidget,added=preferences.widgets.some(w=>w.id===id);
  commitWidgets(added?preferences.widgets.filter(w=>w.id!==id):[...preferences.widgets,{id,w:1,h:1}],added?'Widget removed.':'Widget added.');
  renderLibrary();$('widgetLibrary').querySelector(`[data-toggle-widget="${id}"]`)?.focus();
});
$('presetSelect').innerHTML=WIDGET_PRESETS.map(p=>`<option value="${p.id}">${escapeHTML(p.name)} · ${p.widgets.length} widgets</option>`).join('');
$('applyPreset').addEventListener('click',()=>commitWidgets(presetWidgets($('presetSelect').value),'Preset applied. Use Undo to restore your layout.'));
$('clearLayout').addEventListener('click',()=>commitWidgets([],'Layout cleared. Add widgets or choose Undo.'));
$('undoLayout').addEventListener('click',()=>{if(!undoStack.length)return;preferences.widgets=undoStack.pop();save();$('undoLayout').disabled=!undoStack.length;scheduleRender();toast('Previous layout restored.')});
function renderSavedLayouts(){
  $('savedLayoutsList').innerHTML=preferences.savedLayouts.map((p,i)=>`<div class="saved-layout-row"><span><b>${escapeHTML(p.name)}</b><small>${p.widgets.length} widgets</small></span><button class="button" data-load-layout="${i}" aria-label="Load ${escapeHTML(p.name)}">Load</button><button class="button quiet" data-delete-layout="${i}" aria-label="Delete ${escapeHTML(p.name)}">Delete</button></div>`).join('')||'<p class="settings-note">No named layouts yet. Your current workspace is already autosaved.</p>';
}
$('savedLayoutsButton').addEventListener('click',()=>{renderSavedLayouts();$('layoutsDialog').showModal()});
$('doneLayouts').addEventListener('click',()=>$('layoutsDialog').close());
$('saveLayoutForm').addEventListener('submit',e=>{
  e.preventDefault();const name=$('layoutName').value.trim();if(!name){$('layoutName').focus();return}
  if(preferences.savedLayouts.length>=8){toast('Eight layouts saved. Delete one to make space.');return}
  preferences.savedLayouts.push({id:'saved-'+Date.now(),name,widgets:preferences.widgets.map(w=>({...w}))});save();$('layoutName').value='';renderSavedLayouts();toast('Layout saved as '+name+'.');
});
$('savedLayoutsList').addEventListener('click',e=>{
  const load=e.target.closest('[data-load-layout]'),remove=e.target.closest('[data-delete-layout]');
  if(load){const saved=preferences.savedLayouts[Number(load.dataset.loadLayout)];if(saved){commitWidgets(saved.widgets,'Loaded '+saved.name+'.');$('layoutsDialog').close()}}
  if(remove){preferences.savedLayouts.splice(Number(remove.dataset.deleteLayout),1);save();renderSavedLayouts();toast('Saved layout deleted.');$('layoutName').focus()}
});
$('dashboard').addEventListener('click',e=>{
  const move=e.target.closest('[data-move]'),remove=e.target.closest('[data-remove]');
  if(move)moveModule(move.dataset.move,Number(move.dataset.direction));
  if(remove){const index=preferences.widgets.findIndex(w=>w.id===remove.dataset.remove);commitWidgets(preferences.widgets.filter(w=>w.id!==remove.dataset.remove),'Widget removed. Use Undo to restore it.');if(preferences.widgets.length)focusWidget(preferences.widgets[Math.min(index,preferences.widgets.length-1)].id);else $('addWidgetsButton').focus()}
  if(e.target.closest('[data-add-widget]'))showWidgets();
  if(e.target.closest('[data-use-sample]')){sourceChosen=true;$('sourceSelect').value='demo';setSource()}
});
$('dashboard').addEventListener('change',e=>{
  const b=e.target.closest('[data-size]');if(!b)return;const widget=preferences.widgets.find(w=>w.id===b.dataset.size);if(!widget)return;
  sizeModule(widget.id,b.dataset.axis==='w'?Number(b.value):widget.w,b.dataset.axis==='h'?Number(b.value):widget.h);
});
$('dashboard').addEventListener('keydown',e=>{
  const handle=e.target.closest('[data-drag],[data-resize]');if(!handle||!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;
  e.preventDefault();const id=handle.dataset.drag||handle.dataset.resize,widget=preferences.widgets.find(w=>w.id===id);if(!widget)return;
  if(handle.dataset.drag)moveModule(id,['ArrowLeft','ArrowUp'].includes(e.key)?-1:1);
  else{sizeModule(id,widget.w+(e.key==='ArrowLeft'?-1:e.key==='ArrowRight'?1:0),widget.h+(e.key==='ArrowUp'?-1:e.key==='ArrowDown'?1:0));focusWidget(id,'resize')}
});
// Pointer events work with a mouse, pen or touch. A handle opts out of scrolling;
// the rest of every card keeps native touch scrolling. Changes commit on release.
$('dashboard').addEventListener('pointerdown',e=>{
  const handle=e.target.closest('[data-drag],[data-resize]');if(!handle||e.button!==0||gesture)return;
  const id=handle.dataset.drag||handle.dataset.resize,card=handle.closest('[data-module]'),widget=preferences.widgets.find(w=>w.id===id);
  if(!widget)return;e.preventDefault();handle.focus({preventScroll:true});handle.setPointerCapture(e.pointerId);
  const grid=card.parentElement,cols=getComputedStyle(grid).gridTemplateColumns.split(' ').length;
  gesture={id,kind:handle.dataset.drag?'move':'resize',pointer:e.pointerId,handle,card,startX:e.clientX,startY:e.clientY,x:e.clientX,y:e.clientY,widget:{...widget},candidate:{...widget},target:null,active:false,unitWidth:(grid.clientWidth+12)/cols,unitHeight:352};
  requestAnimationFrame(dragScroll);
});
function clearTargets(){for(const c of $('dashboard').querySelectorAll('.drop-target'))c.classList.remove('drop-target')}
function updateGesture(e){
  if(!gesture||e.pointerId!==gesture.pointer)return;const g=gesture,dx=e.clientX-g.startX,dy=e.clientY-g.startY;g.x=e.clientX;g.y=e.clientY;
  if(!g.active&&Math.hypot(dx,dy)<6)return;g.active=true;g.card.classList.add('is-dragging');
  if(g.kind==='resize'){
    g.candidate=resizedWidget(g.widget,dx,dy,g.unitWidth,g.unitHeight);
    g.card.style.setProperty('--widget-width',g.candidate.w);g.card.style.setProperty('--widget-height',g.candidate.h);
    g.card.dataset.width=g.candidate.w;g.card.dataset.height=g.candidate.h;
  }else{
    clearTargets();const target=document.elementFromPoint(e.clientX,e.clientY)?.closest('[data-module]');g.target=target&&target!==g.card?target.dataset.module:null;if(g.target)target.classList.add('drop-target');
  }
}
document.addEventListener('pointermove',updateGesture);
function finishGesture(e,cancel=false){
  if(!gesture||e.pointerId!==gesture.pointer)return;const g=gesture;gesture=null;clearTargets();g.card.classList.remove('is-dragging');
  if(g.handle.hasPointerCapture(g.pointer))g.handle.releasePointerCapture(g.pointer);
  if(!cancel&&g.active){if(g.kind==='move'&&g.target)moveModule(g.id,0,g.target);if(g.kind==='resize')sizeModule(g.id,g.candidate.w,g.candidate.h)}
  scheduleRender();focusWidget(g.id,g.kind==='resize'?'resize':'drag');
}
document.addEventListener('pointerup',e=>finishGesture(e));
document.addEventListener('pointercancel',e=>finishGesture(e,true));
document.addEventListener('lostpointercapture',e=>finishGesture(e,true));
window.addEventListener('blur',()=>{if(gesture)finishGesture({pointerId:gesture.pointer},true)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&gesture){e.preventDefault();finishGesture({pointerId:gesture.pointer},true);toast('Layout change cancelled.')}});
function dragScroll(){
  if(gesture?.active&&gesture.kind==='move'){
    const edge=65,y=gesture.y,speed=y<edge?-Math.ceil((edge-y)/5):y>innerHeight-edge?Math.ceil((y-innerHeight+edge)/5):0;
    if(speed){window.scrollBy(0,speed);updateGesture({pointerId:gesture.pointer,clientX:gesture.x,clientY:y})}
  }
  if(gesture)requestAnimationFrame(dragScroll);
}
window.addEventListener('resize',applyDisplay);window.visualViewport?.addEventListener('resize',applyDisplay);
window.addEventListener('hashchange',()=>{if(LAYOUTS.includes(location.hash.slice(1)))chooseLayout(location.hash.slice(1))});
window.addEventListener('message',e=>{if(embedded&&e.origin===location.origin&&e.source===parent&&e.data?.type==='pitbox:driver-state'){
  latestParent=e.data.state;parentReceivedAt=Date.now();
  if(!sourceChosen&&normalizeState(latestParent).fresh){sourceChosen=true;$('sourceSelect').value='live';setSource()}
  if(source==='embedded')acceptState(latestParent);
}});
window.addEventListener('pagehide',()=>{if(gesture)finishGesture({pointerId:gesture.pointer},true);disconnect();releaseWake()});
window.addEventListener('pageshow',e=>{if(e.persisted&&source==='live')connect()});
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')acquireWake();else releaseWake()});
window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();installPrompt=e;$('installButton').hidden=false});
$('installButton').addEventListener('click',async()=>{if(installPrompt){await installPrompt.prompt();installPrompt=null;$('installButton').hidden=true}});
if('serviceWorker'in navigator&&location.protocol==='https:'&&!installed)navigator.serviceWorker.register('./sw.js').catch(()=>{});
setInterval(()=>{if(source!=='demo'&&lastFresh&&Date.now()-receivedAt>=3500)scheduleRender()},500);
document.body.classList.toggle('embedded',embedded);document.body.classList.remove('preferences-loading');$('preferencesLoading').hidden=true;applyDisplay();setSource();
if(preferenceStore.issue)toast(preferenceStore.issue);
if(embedded)parent.postMessage({type:'pitbox:driver-ready'},location.origin);
}
initializeDashboard().catch(()=>{const status=document.getElementById('preferencesLoading');status.hidden=false;status.textContent='The dashboard could not start. Reload this page to try again.'});
