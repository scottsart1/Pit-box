import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {normalizeState} from '../static/driver-dashboard/model.mjs';
import {MODULES,renderDashboard} from '../static/driver-dashboard/render.mjs';
import {cleanPreferences} from '../static/driver-dashboard/display.mjs';

const html=fs.readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
class Element {
  constructor(tag='div'){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.hidden=false;this._text='';}
  set textContent(v){this._text=String(v);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  appendChild(v){this.children.push(v);return v;}
  replaceChildren(...v){this.children=v;this._text='';}
  addEventListener(){}
  removeAttribute(k){delete this[k];}
}
const nodes=new Map();
const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
const document={getElementById:get,createElement:tag=>new Element(tag),body:new Element()};
const context=vm.createContext({$:get,document,esc:v=>String(v).replaceAll('<','&lt;')});
const slice=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));
vm.runInContext(slice('function redFlagRestartDisplay(','function renderTyreDegradation('),context);
vm.runInContext(slice('function renderStrategy38(','/* ---- Pre-race plan panel'),context);
vm.runInContext(slice('function renderRaceControl(','/* Compact status strip'),context);
const source=fs.readFileSync(new URL('../static/js/strategy.js',import.meta.url),'utf8');
const strategy=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
globalThis.document=document;
const state=()=>({connected:true,session_identity:'9038043921811877951:0:0:0',race_control_phase:'red_flag',current_lap:12,total_laps:20,
  strategy_intent:{active:true,direction:'stay_out',intent:'overcut'},strategy_hold:{active:true},
  tyre:{compound:'MEDIUM',wear:[30,32,29,28],age_laps:10},
  strategy:{available:true,confidence:'medium',recommended:{instruction:'BOX NOW for HARD',box_lap:12,box_laps:[12],compounds:['MEDIUM','HARD'],feasible:true,legal:true},
    plans:[{instruction:'BOX NOW',stops_remaining:1,compounds:['MEDIUM','HARD'],box_laps:[12],feasible:true,legal:true}],
    red_flag_restart:{active:true,session_identity:'9038043921811877951:0:0:0',tyre_change_available:true,instruction:'Fit fresh HARD during suspension for the restart.',
      primary:{compound:'HARD',instruction:'Fit fresh HARD during suspension; run to the finish.',later_stops:[]},
      alternative:{compound:'MEDIUM',instruction:'Fit fresh MEDIUM during suspension; stop lap 17 for SOFT.',later_stops:[{lap:17,compound:'SOFT'}]},alternative_reason:'Medium warms up faster; another stop is needed.'}}});

test('Red flag wins over old stay-out intent and BOX instruction on Drive',()=>{
  const s=state();context.renderStrategy38(s);
  assert.match(get('strategyMain').textContent,/Fit fresh HARD during suspension/);
  assert.match(get('strategyChange').textContent,/Alternative:.*MEDIUM/);
  for(const id of ['strategyMain','strategyReason','strategyMeta','strategyRationale','neutralisation'])assert.doesNotMatch(get(id).textContent,/BOX|pit entry|rejoin|staying out/);
  assert.equal(get('strategyIntent').style.display,'none');assert.equal(get('strategyHold').style.display,'none');
  assert.equal(get('stintClock').textContent,'Session suspended');
  assert.equal(context.tyreStintInsight(s).stop,'Tyre change during suspension');
});
test('Red flag banner follows race control without a blanket pit-lane prohibition',()=>{
  for(const signal of [{red_flag_active:true},{race_control_phase:'red_flag'},{fia_flag:'red',race_control_phase:'safety_car'}]){
    context.renderRaceControl(signal);assert.equal(get('raceControlBlip').dataset.phase,'red_flag');
    assert.match(get('raceControlBlip').textContent,/session suspended; follow race control/);
  }
});
test('A pending or infeasible restart plan never falls back to an old pit call',()=>{
  const s=state();delete s.strategy.red_flag_restart;
  context.renderStrategy38(s);strategy.renderCall(s);strategy.renderPlans(s);
  for(const id of ['strategyMain','stratInstruction']){assert.match(get(id).textContent,/checking available sets/);assert.doesNotMatch(get(id).textContent,/BOX|staying out/);}
  assert.equal(get('stratPlanCount').textContent,'0 restart options');
});
test('Strategy offers primary and alternate suspension choices without scheduling an extra pit stop',()=>{
  const s=state();strategy.renderCall(s);strategy.renderPlans(s);
  assert.match(get('stratInstruction').textContent,/Fit fresh HARD/);
  assert.match(get('stratChange').textContent,/Alternative:.*MEDIUM/);
  assert.equal(get('stratMeta').textContent,'');assert.equal(get('stratHold').hidden,true);
  assert.equal(get('stratPlanCount').textContent,'2 restart options');
  assert.match(get('stratPlanRows').textContent,/Primary · HARD.*Alternative · MEDIUM/);
  assert.equal(get('stratPlanRows').children.flatMap(r=>r.children).every(c=>c.children.length===0),true,'No Adopt button sends a suspension fit to the normal pit-plan endpoint');
});
test('A confirmed suspension retains provisional primary and alternative choices through the telemetry gap',()=>{
  const s={...state(),connected:false};context.renderStrategy38(s);strategy.renderCall(s);strategy.renderPlans(s);
  for(const id of ['strategyMain','stratInstruction'])assert.match(get(id).textContent,/Last confirmed · provisional:.*Fit fresh HARD/);
  for(const id of ['strategyChange','stratChange'])assert.match(get(id).textContent,/Alternative:.*MEDIUM/);
  assert.match(get('stratNotice').textContent,/Live telemetry unavailable.*Confirm the suspension, tyre sets and track conditions in game/);
  assert.equal(get('stratPlanCount').textContent,'2 provisional restart options');
  assert.equal(get('stratPlanRows').children.flatMap(row=>row.children).every(cell=>cell.children.length===0),true);
  const model=normalizeState(s),rendered=renderDashboard('cockpit',model,cleanPreferences(null));
  assert.equal(model.fresh,false);assert.equal(model.speed,null);assert.equal(model.fuel,null);assert.equal(model.tyreAge,null);
  assert.equal(model.lap,null);assert.equal(model.position,null);assert.equal(model.nextStop,null);
  assert.match(rendered,/LAST CONFIRMED RED FLAG/);assert.match(rendered,/PROVISIONAL RESTART TYRES/);
  assert.match(rendered,/Fit fresh HARD/);assert.match(rendered,/Alternative:.*MEDIUM/);
  assert.doesNotMatch(rendered,/Waiting for telemetry|NEXT PIT STOP|Laps to stop|BOX NOW/);
});
test('Exact session identity, including adjacent 64-bit IDs and epochs, is required to retain a stale restart plan',()=>{
  for(const identity of [undefined,'9038043921811877952:0:0:0','9038043921811877951:1:0:0','9038043921811877951:0:1:0','9038043921811877951:0:0:1']){
    const s={...state(),connected:false,session_identity:identity};context.renderStrategy38(s);strategy.renderCall(s);strategy.renderPlans(s);
    for(const id of ['strategyMain','stratInstruction']){assert.match(get(id).textContent,/No recorded restart plan/);assert.doesNotMatch(get(id).textContent,/HARD|MEDIUM/);}
    assert.equal(get('stratPlanCount').textContent,'0 provisional restart options');
    const model=normalizeState(s);assert.equal(model.provisionalRestart,false);assert.equal(model.nextCompound,null);
  }
});
test('LGOT or a new non-red session immediately clears retained suspension options',()=>{
  normalizeState({...state(),connected:false});
  for(const patch of [{race_control_phase:'green',red_flag_active:false,fia_flag:'green'},{session_identity:'new-session',race_control_phase:'formation'}]){
    const s={...state(),connected:false,...patch};assert.equal(context.redFlagRestartDisplay(s),null);
    const model=normalizeState(s),rendered=renderDashboard('cockpit',model,cleanPreferences(null));
    assert.equal(model.provisionalRestart,false);assert.doesNotMatch(rendered,/PROVISIONAL RESTART TYRES|Fit fresh HARD|Alternative:.*MEDIUM/);
  }
});
test('Missing or inactive restart advice cannot become a provisional plan',()=>{
  for(const plan of [undefined,{...state().strategy.red_flag_restart,active:false}]){
    const s={...state(),connected:false};s.strategy.red_flag_restart=plan;
    context.renderStrategy38(s);assert.doesNotMatch(get('strategyMain').textContent,/HARD|MEDIUM|BOX/);
    assert.equal(normalizeState(s).provisionalRestart,false);
  }
});

test('Every dashboard layout shows one provisional restart panel without changing selected widgets',()=>{
  const model=normalizeState({...state(),connected:false});
  for(const widgets of [[{id:'strategy',w:2,h:1},{id:'position',w:1,h:1}],[{id:'position',w:1,h:1}]]){
    const preferences=cleanPreferences({widgets}),original=JSON.stringify(preferences);
    for(const layout of ['cockpit','focus','battle','endurance','modular','portrait']){
      const rendered=renderDashboard(layout,model,preferences);
      assert.equal((rendered.match(/PROVISIONAL RESTART TYRES/g)||[]).length,1,layout);
      assert.equal((rendered.match(/Fit fresh HARD/g)||[]).length,1,layout);
      assert.doesNotMatch(rendered,/data-module="strategy"/);
    }
    assert.equal(JSON.stringify(preferences),original,'Temporarily omitted duplicate must not edit saved choices');
    const fresh=renderDashboard('modular',normalizeState(state(),{transport:'demo'}),preferences);
    assert.equal(fresh.includes('data-module="strategy"'),widgets.some(widget=>widget.id==='strategy'));
  }
});
test('A fresh frame cannot reuse identified advice from a previous session',()=>{
  const s={...state(),session_identity:'9038043921811877952:0:0:0'};
  context.renderStrategy38(s);strategy.renderCall(s);
  for(const id of ['strategyMain','stratInstruction'])assert.doesNotMatch(get(id).textContent,/HARD|MEDIUM|BOX/);
  assert.equal(normalizeState(s,{transport:'demo'}).nextCompound,null);
});
test('Green flag restores ordinary plans and removes the suspension-specific table status',()=>{
  strategy.renderPlans(state());const s={...state(),race_control_phase:'green',strategy_intent:{}};delete s.strategy.red_flag_restart;
  strategy.renderCall(s);strategy.renderPlans(s);
  assert.equal(get('stratInstruction').textContent,'BOX NOW for HARD');
  assert.equal(get('stratPlanCount').textContent,'1 plan');assert.equal(get('stratPlanStatus').textContent,'');
});
test('Dashboard labels restart tyres, suppresses pit countdown, and escapes restart text',()=>{
  const s=state();let model=normalizeState(s,{transport:'demo'}),rendered=MODULES.strategy.render(model);
  assert.equal(model.nextStop,null);assert.equal(model.nextCompound,'HARD');
  assert.match(rendered,/RESTART TYRES/);assert.match(rendered,/Alternative:.*MEDIUM/);
  assert.doesNotMatch(rendered,/NEXT PIT STOP|Laps to stop|BOX NOW/);
  s.strategy.red_flag_restart.primary.instruction='<img src=x onerror=bad()> ';model=normalizeState(s,{transport:'demo'});rendered=MODULES.strategy.render(model);
  assert.doesNotMatch(rendered,/<img/);assert.match(rendered,/&lt;img/);
  delete s.strategy.red_flag_restart;assert.doesNotMatch(MODULES.strategy.render(normalizeState(s,{transport:'demo'})),/BOX NOW/);
});
