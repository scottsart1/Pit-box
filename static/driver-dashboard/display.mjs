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
  if(!value||typeof value!=='object')return p;
  for(const k of ['contrast','tires','resources','sectors','radio'])if(typeof value[k]==='boolean')p[k]=value[k];
  const choices={units:['kmh','mph'],scale:['1','1.1','1.2'],accent:['blue','cyan','violet'],profile:DISPLAY_PROFILES.map(x=>x.id),layout:['auto',...LAYOUTS]};
  for(const [k,allowed] of Object.entries(choices))if(allowed.includes(value[k]))p[k]=value[k];
  if(Array.isArray(value.order)&&value.order.length===6&&new Set(value.order).size===6&&value.order.every(id=>p.order.includes(id)))p.order=[...value.order];
  return p;
}
