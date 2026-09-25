/** Display profiles are layout preferences, never claimed hardware identification. */
export const DISPLAY_PROFILES = [
  {id:'auto', name:'Auto · this screen', family:'auto'},
  {id:'phone', name:'Phone', family:'compact', reference:'360–430 px wide'},
  {id:'fold-cover', name:'Galaxy Fold · cover screen', family:'compact', reference:'Narrow portrait'},
  {id:'fold-open', name:'Galaxy Fold · unfolded', family:'fold', reference:'Near-square display'},
  {id:'galaxy-tab', name:'Samsung Galaxy Tab', family:'tablet', reference:'8–14 inch tablets'},
  {id:'ipad-mini', name:'iPad mini', family:'tablet', reference:'Compact tablet'},
  {id:'ipad', name:'iPad / iPad Air', family:'tablet', reference:'10–11 inch tablets'},
  {id:'ipad-pro', name:'iPad Pro / Air 13', family:'tablet', reference:'Large tablet'},
  {id:'laptop', name:'Laptop · 1366 × 768', family:'laptop', reference:'Compact desktop'},
  {id:'laptop-hd', name:'Laptop · 1440 × 900', family:'laptop', reference:'Standard desktop'},
  {id:'desktop', name:'Desktop · 1920 × 1080', family:'desktop', reference:'Full HD and larger'},
  {id:'ultrawide', name:'Ultrawide monitor', family:'wide', reference:'21:9 and wider'}
];
export const LAYOUTS = ['cockpit','focus','battle','endurance','modular','portrait'];
// One bounded instance of each widget keeps saved layouts portable and predictable.
export const WIDGET_CATALOG = [
  ['instrument','Speed & gear','Driving','Large speed and gear; RPM when available'],
  ['timing','Lap & delta','Timing','Current lap, reference delta and predicted time'],
  ['relative','Relative timing','Race','Your nearest rivals and race gaps'],
  ['laps','Lap times','Timing','Last lap, session best and sector progress'],
  ['session','Race status','Race','Position, lap count and penalties'],
  ['tires','Tyre surface','Car','Four tyre surface temperatures and wear'],
  ['inner','Tyre core','Car','Four tyre inner temperatures'],
  ['pressures','Tyre pressures','Car','Individual tyre pressures in PSI'],
  ['wear','Tyre wear','Car','Wear percentage for all four corners'],
  ['resources','Fuel & energy','Strategy','Fuel margin and ERS battery'],
  ['fuel','Fuel plan','Strategy','Remaining fuel, recent burn and lap margin'],
  ['energy','ERS & aero','Driving','Battery, deployment mode and overtaking aids'],
  ['inputs','Pedal & steering','Driving','Throttle, brake and steering input'],
  ['forces','G forces','Driving','Lateral and longitudinal acceleration'],
  ['damage','Car damage','Car','Wing, floor, diffuser, engine and gearbox damage'],
  ['components','Power unit wear','Car','ICE, turbo, MGU and energy-store wear'],
  ['weather','Track conditions','Strategy','Current weather and air / track temperatures'],
  ['strategy','Next pit stop','Strategy','Recommended lap, next tyre and plan'],
  ['consistency','Lap consistency','Timing','Recent valid laps and average pace'],
  ['position','Position & progress','Race','Race position, lap progress and time remaining'],
  ['penalties','Warnings & penalties','Race','Corner cuts and outstanding penalties'],
  ['ahead','Car ahead','Race','Gap and previous-lap comparison ahead'],
  ['behind','Car behind','Race','Gap and previous-lap comparison behind'],
  ['radio','Engineer message','Race','The latest engineer radio message']
].map(([id,name,group,description])=>Object.freeze({id,name,group,description}));
export const WIDGET_PRESETS = [
  {id:'balanced',name:'Race essentials',description:'A complete race view',widgets:[['instrument',1],['timing',2],['relative',2],['session',1],['tires',1],['resources',1],['laps',1]]},
  {id:'qualifying',name:'Qualifying',description:'Focus on a fast lap',widgets:[['timing',2],['instrument',1],['inputs',1],['laps',1],['inner',1],['consistency',2]]},
  {id:'stint',name:'Long stint',description:'Manage tyres and fuel',widgets:[['strategy',2],['fuel',1],['wear',1],['inner',1],['pressures',1],['consistency',2],['relative',2]]},
  {id:'racecraft',name:'Racecraft',description:'Rivals and race control',widgets:[['relative',2],['timing',2],['session',1],['energy',1],['penalties',1],['radio',2]]},
  {id:'telemetry',name:'Car telemetry',description:'See how the car is working',widgets:[['instrument',1],['inputs',1],['forces',1],['tires',1],['inner',1],['pressures',1],['damage',2],['components',1]]},
  {id:'minimal',name:'Minimal',description:'Only the essentials',widgets:[['timing',2],['relative',2],['session',1]]}
];
export function presetWidgets(id='balanced') {
  return (WIDGET_PRESETS.find(p=>p.id===id)||WIDGET_PRESETS[0]).widgets.map(([id,w])=>({id,w,h:1}));
}
export function cleanWidgets(value,fallback=presetWidgets()) {
  if(!Array.isArray(value))return fallback.map(w=>({...w}));
  const seen=new Set();
  return value.filter(w=>w&&WIDGET_CATALOG.some(c=>c.id===w.id)&&!seen.has(w.id)&&seen.add(w.id)).map(w=>({id:w.id,w:[1,2,3].includes(w.w)?w.w:1,h:[1,2,3].includes(w.h)?w.h:1}));
}
export function reorderWidgets(widgets,id,target) {
  const result=widgets.map(w=>({...w})),from=result.findIndex(w=>w.id===id),to=result.findIndex(w=>w.id===target);
  if(from>=0&&to>=0&&from!==to)result.splice(to,0,result.splice(from,1)[0]);
  return result;
}
export function resizedWidget(widget,dx,dy,unitWidth,unitHeight) {
  return {...widget,w:Math.max(1,Math.min(3,widget.w+Math.round(dx/Math.max(1,unitWidth)))),h:Math.max(1,Math.min(3,widget.h+Math.round(dy/Math.max(1,unitHeight))))};
}
export function classifyViewport({width=1280,height=800,touch=false}={}) {
  const w=Math.max(1,width), h=Math.max(1,height), short=Math.min(w,h), ratio=Math.max(w,h)/short;
  if(w<580 || (h<500 && touch)) return 'compact';
  if(touch && short<1000 && ratio<1.35) return 'fold';
  if(touch && short>=580) return 'tablet';
  if(w/h>2.1 && w>=1500) return 'wide';
  if(w<1600 || h<900) return 'laptop';
  return 'desktop';
}
export function resolveDisplay(profile, metrics) {
  const picked=DISPLAY_PROFILES.find(p=>p.id===profile)||DISPLAY_PROFILES[0];
  const detected=classifyViewport(metrics), family=picked.id==='auto'?detected:picked.family;
  const width=Math.max(1,Math.round(metrics.width)),height=Math.max(1,Math.round(metrics.height));
  // A manual laptop profile must still fit a narrow phone or a split-screen window.
  const compact=width<580 || (height<500 && metrics.touch);
  return {id:picked.id,family,detected,width,height,compact,short:height<760,
    orientation:width>=height?'landscape':'portrait',
    label:picked.id==='auto'?({compact:'Phone / cover screen',fold:'Fold / square tablet',tablet:'Tablet',laptop:'Laptop / compact window',desktop:'Desktop',wide:'Ultrawide'}[detected]):picked.name,
    recommendedLayout:compact?'portrait':family==='fold'?'focus':'cockpit'};
}
export const DEFAULT_PREFERENCES = Object.freeze({units:'kmh',scale:'1',contrast:false,tires:true,resources:true,sectors:true,radio:true,accent:'blue',profile:'auto',layout:'auto',order:['relative','timing','tires','resources','laps','session']});
export function cleanPreferences(value) {
  const p={...DEFAULT_PREFERENCES,order:[...DEFAULT_PREFERENCES.order]};
  p.widgets=presetWidgets();p.savedLayouts=[];
  if(!value||typeof value!=='object')return p;
  for(const k of ['contrast','tires','resources','sectors','radio'])if(typeof value[k]==='boolean')p[k]=value[k];
  const choices={units:['kmh','mph'],scale:['1','1.1','1.2'],accent:['blue','cyan','violet'],profile:DISPLAY_PROFILES.map(x=>x.id),layout:['auto',...LAYOUTS]};
  for(const [k,allowed] of Object.entries(choices))if(allowed.includes(value[k]))p[k]=value[k];
  if(Array.isArray(value.order)&&value.order.length===6&&new Set(value.order).size===6&&value.order.every(id=>p.order.includes(id)))p.order=[...value.order];
  p.widgets=cleanWidgets(value.widgets,Array.isArray(value.order)&&value.order.length===6?p.order.filter(id=>!(id==='tires'&&p.tires===false)&&!(id==='resources'&&p.resources===false)).map(id=>({id,w:1,h:1})):presetWidgets());
  if(Array.isArray(value.savedLayouts))p.savedLayouts=value.savedLayouts.filter(v=>v&&typeof v.name==='string'&&Array.isArray(v.widgets)).slice(0,8).map((v,i)=>({id:'saved-'+i,name:v.name.trim().slice(0,40)||'Saved layout',widgets:cleanWidgets(v.widgets)}));
  return p;
}
