import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

await import('../static/js/i18n.js');

/* An agreed stay-out tactic is about the next stop. Once that stop has been
   made the server serialises strategy_intent_stands=false, and the dashboard
   must stop headlining "Running the overcut" just as the radio stops
   honouring it. */

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
const context=vm.createContext({setHTML:(el,html)=>{el.innerHTML=html},$:get,document,esc:v=>String(v).replaceAll('<','&lt;'),i18n:globalThis.i18n});
const slice=(start,end)=>html.slice(html.indexOf(start),html.indexOf(end,html.indexOf(start)));
vm.runInContext(slice('function confirmedSessionFinish(','function renderTyreDegradation('),context);
vm.runInContext(slice('function renderStrategy38(','/* ---- Pre-race plan panel'),context);
const source=fs.readFileSync(new URL('../static/js/strategy.js',import.meta.url),'utf8');
const strategy=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
globalThis.document=document;
globalThis.confirmedSessionFinish=context.confirmedSessionFinish;
globalThis.redFlagTelemetryStale=context.redFlagTelemetryStale;

const state=(stands)=>({connected:true,session_identity:'1:0:0:0',race_control_phase:'green',current_lap:35,total_laps:50,
  packet_group_freshness:{'1':Date.now()/1000,'2':Date.now()/1000,'7':Date.now()/1000,'10':Date.now()/1000},
  strategy_intent:{active:true,direction:'stay_out',intent:'go_long',set_at_lap:13},strategy_intent_stands:stands,
  strategy_hold:{active:false},tyre:{compound:'HARD',wear:[20,21,22,23],age_laps:13},
  strategy:{available:true,confidence:'medium',recommended:{instruction:'Box lap 38 for MEDIUM.',box_lap:38,box_laps:[38],compounds:['HARD','MEDIUM'],feasible:true,legal:true},
    plans:[{instruction:'Box lap 38 for MEDIUM.',stops_remaining:1,compounds:['HARD','MEDIUM'],box_laps:[38],feasible:true,legal:true}]}});

test('A tactic whose stop has been made no longer owns the strategy headline',()=>{
  const spent=state(false);
  strategy.renderCall(spent);
  assert.equal(get('stratInstruction').textContent,'Box lap 38 for MEDIUM.');
  context.renderStrategy38(spent);
  assert.equal(get('strategyMain').textContent,'Box lap 38 for MEDIUM.');
  assert.doesNotMatch(get('strategyChange').textContent,/Alternative call/);
});

test('A tactic that still stands keeps the headline',()=>{
  const standing=state(true);
  strategy.renderCall(standing);
  assert.equal(get('stratInstruction').textContent,'Running the go long — staying out');
  context.renderStrategy38(standing);
  assert.equal(get('strategyMain').textContent,'Running the go long — staying out');
});
