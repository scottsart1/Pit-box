import { i18n } from './i18n.mjs';
/** Display profiles are layout preferences, never claimed hardware identification. */
export const DISPLAY_PROFILES = [
  {id:'auto', get name(){return i18n.t('Auto · this screen')}, family:'auto'},
  {id:'phone', get name(){return i18n.t('Phone')}, family:'compact', reference:'360–430 px wide'},
  {id:'fold-cover', get name(){return i18n.t('Galaxy Fold · cover screen')}, family:'compact', reference:'Narrow portrait'},
  {id:'fold-open', get name(){return i18n.t('Galaxy Fold · unfolded')}, family:'fold', reference:'Near-square display'},
  {id:'galaxy-tab', name:'Samsung Galaxy Tab', family:'tablet', reference:'8–14 inch tablets'},
  {id:'ipad-mini', name:'iPad mini', family:'tablet', reference:'Compact tablet'},
  {id:'ipad', name:'iPad / iPad Air', family:'tablet', reference:'10–11 inch tablets'},
  {id:'ipad-pro', name:'iPad Pro / Air 13', family:'tablet', reference:'Large tablet'},
  {id:'laptop', get name(){return i18n.t('Laptop · {resolution}',{resolution:'1366 × 768'})}, family:'laptop', reference:'Compact desktop'},
  {id:'laptop-hd', get name(){return i18n.t('Laptop · {resolution}',{resolution:'1440 × 900'})}, family:'laptop', reference:'Standard desktop'},
  {id:'desktop', get name(){return i18n.t('Desktop · {resolution}',{resolution:'1920 × 1080'})}, family:'desktop', reference:'Full HD and larger'},
  {id:'ultrawide', get name(){return i18n.t('Ultrawide monitor')}, family:'wide', reference:'21:9 and wider'}
];
export const LAYOUTS = ['cockpit','focus','battle','endurance','modular','portrait'];
// One bounded instance of each widget keeps saved layouts portable and predictable.
// Names and descriptions are read in the screen language; the group is an id.
export const WIDGET_CATALOG = [
  ['instrument',()=>i18n.t('Speed & gear'),'Driving',()=>i18n.t('Large speed and gear; RPM when available')],
  ['timing',()=>i18n.t('Lap & delta'),'Timing',()=>i18n.t('Current lap, reference delta and predicted time')],
  ['relative',()=>i18n.t('Relative timing'),'Race',()=>i18n.t('Your nearest rivals and race gaps')],
  ['laps',()=>i18n.t('Lap times'),'Timing',()=>i18n.t('Last lap, session best and sector progress')],
  ['session',()=>i18n.t('Race status'),'Race',()=>i18n.t('Position, lap count and penalties')],
  ['tires',()=>i18n.t('Tyre surface'),'Car',()=>i18n.t('Four tyre surface temperatures and wear')],
  ['inner',()=>i18n.t('Tyre core'),'Car',()=>i18n.t('Four tyre inner temperatures')],
  ['pressures',()=>i18n.t('Tyre pressures'),'Car',()=>i18n.t('Individual tyre pressures in PSI')],
  ['wear',()=>i18n.t('Tyre wear'),'Car',()=>i18n.t('Wear percentage for all four corners')],
  ['resources',()=>i18n.t('Fuel & energy'),'Strategy',()=>i18n.t('Fuel margin and ERS battery')],
  ['fuel',()=>i18n.t('Fuel plan'),'Strategy',()=>i18n.t('Remaining fuel, recent burn and lap margin')],
  ['energy',()=>i18n.t('ERS & aero'),'Driving',()=>i18n.t('Battery, deployment mode and overtaking aids')],
  ['inputs',()=>i18n.t('Pedal & steering'),'Driving',()=>i18n.t('Throttle, brake and steering input')],
  ['forces',()=>i18n.t('G forces'),'Driving',()=>i18n.t('Lateral and longitudinal acceleration')],
  ['damage',()=>i18n.t('Car damage'),'Car',()=>i18n.t('Wing, floor, diffuser, engine and gearbox damage')],
  ['components',()=>i18n.t('Power unit wear'),'Car',()=>i18n.t('ICE, turbo, MGU and energy-store wear')],
  ['weather',()=>i18n.t('Track conditions'),'Strategy',()=>i18n.t('Current weather and air / track temperatures')],
  ['strategy',()=>i18n.t('Next pit stop'),'Strategy',()=>i18n.t('Recommended lap, next tyre and plan')],
  ['consistency',()=>i18n.t('Lap consistency'),'Timing',()=>i18n.t('Recent valid laps and average pace')],
  ['position',()=>i18n.t('Position & progress'),'Race',()=>i18n.t('Race position, lap progress and time remaining')],
  ['penalties',()=>i18n.t('Warnings & penalties'),'Race',()=>i18n.t('Corner cuts and outstanding penalties')],
  ['ahead',()=>i18n.t('Car ahead'),'Race',()=>i18n.t('Gap and previous-lap comparison ahead')],
  ['behind',()=>i18n.t('Car behind'),'Race',()=>i18n.t('Gap and previous-lap comparison behind')],
  ['radio',()=>i18n.t('Engineer message'),'Race',()=>i18n.t('The latest engineer radio message')]
].map(([id,name,group,description])=>Object.freeze({id,get name(){return name()},group,get description(){return description()}}));
export const WIDGET_PRESETS = [
  {id:'balanced',get name(){return i18n.t('Race essentials')},description:'A complete race view',widgets:[['instrument',1],['timing',2],['relative',2],['session',1],['tires',1],['resources',1],['laps',1]]},
  {id:'qualifying',get name(){return i18n.t('Qualifying')},description:'Focus on a fast lap',widgets:[['timing',2],['instrument',1],['inputs',1],['laps',1],['inner',1],['consistency',2]]},
  {id:'stint',get name(){return i18n.t('Long stint')},description:'Manage tyres and fuel',widgets:[['strategy',2],['fuel',1],['wear',1],['inner',1],['pressures',1],['consistency',2],['relative',2]]},
  {id:'racecraft',get name(){return i18n.t('Racecraft')},description:'Rivals and race control',widgets:[['relative',2],['timing',2],['session',1],['energy',1],['penalties',1],['radio',2]]},
  {id:'telemetry',get name(){return i18n.t('Car telemetry')},description:'See how the car is working',widgets:[['instrument',1],['inputs',1],['forces',1],['tires',1],['inner',1],['pressures',1],['damage',2],['components',1]]},
  {id:'minimal',get name(){return i18n.t('Minimal')},description:'Only the essentials',widgets:[['timing',2],['relative',2],['session',1]]}
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
    label:picked.id==='auto'?({compact:i18n.t('Phone / cover screen'),fold:i18n.t('Fold / square tablet'),tablet:i18n.t('Tablet'),laptop:i18n.t('Laptop / compact window'),desktop:i18n.t('Desktop'),wide:i18n.t('Ultrawide')}[detected]):picked.name,
    recommendedLayout:compact?'portrait':family==='fold'?'focus':'cockpit'};
}
// Screen languages, each named in its own language. A ?lang= link wins (the
// app passes its own language); then the choice made here; then, standalone,
// a French, German or Spanish browser. Inside the app no ?lang= means English.
export const LANGUAGES = [{id:'auto',get name(){return i18n.t('Automatic')}},{id:'en',name:'English'},{id:'fr',name:'Français'},{id:'de',name:'Deutsch'},{id:'es',name:'Español'}];
// A ?lang= value names a catalog file, so only plain letter codes are used.
export function languageCode(value) {
  const code=String(value??'').trim().toLowerCase();
  return /^[a-z]{2,3}$/.test(code)?code:'';
}
export function resolveLanguage({requested=null,chosen='auto',embedded=false,browser=''}={}) {
  const code=languageCode(requested);
  if(code)return code;
  if(chosen!=='auto'&&LANGUAGES.some(l=>l.id===chosen))return chosen;
  if(embedded)return 'en';
  const match=String(browser??'').toLowerCase().match(/^(fr|de|es)(?:-|_|$)/);
  return match?match[1]:'en';
}
export const DEFAULT_PREFERENCES = Object.freeze({units:'kmh',scale:'1',contrast:false,tires:true,resources:true,sectors:true,radio:true,accent:'blue',profile:'auto',layout:'auto',language:'auto',order:['relative','timing','tires','resources','laps','session']});
export function cleanPreferences(value) {
  const p={...DEFAULT_PREFERENCES,order:[...DEFAULT_PREFERENCES.order]};
  p.widgets=presetWidgets();p.savedLayouts=[];
  if(!value||typeof value!=='object')return p;
  for(const k of ['contrast','tires','resources','sectors','radio'])if(typeof value[k]==='boolean')p[k]=value[k];
  const choices={units:['kmh','mph'],scale:['1','1.1','1.2'],accent:['blue','cyan','violet'],profile:DISPLAY_PROFILES.map(x=>x.id),layout:['auto',...LAYOUTS],language:LANGUAGES.map(x=>x.id)};
  for(const [k,allowed] of Object.entries(choices))if(allowed.includes(value[k]))p[k]=value[k];
  if(Array.isArray(value.order)&&value.order.length===6&&new Set(value.order).size===6&&value.order.every(id=>p.order.includes(id)))p.order=[...value.order];
  p.widgets=cleanWidgets(value.widgets,Array.isArray(value.order)&&value.order.length===6?p.order.filter(id=>!(id==='tires'&&p.tires===false)&&!(id==='resources'&&p.resources===false)).map(id=>({id,w:1,h:1})):presetWidgets());
  if(Array.isArray(value.savedLayouts))p.savedLayouts=value.savedLayouts.filter(v=>v&&typeof v.name==='string'&&Array.isArray(v.widgets)).slice(0,8).map((v,i)=>({id:'saved-'+i,name:v.name.trim().slice(0,40)||'Saved layout',widgets:cleanWidgets(v.widgets)}));
  return p;
}
