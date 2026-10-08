import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../static/js/workspaces.js',import.meta.url),'utf8').replace(/^export \{[^\n]+\};?\s*$/m,'');
function harness(){
  const element=()=>({textContent:'',className:'',dataset:{},children:[],hidden:false,disabled:false,
    append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;}});
  const nodes=new Map();
  const context=vm.createContext({URLSearchParams,document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element}});
  vm.runInContext(source,context);
  for(const name of ['drawComparisonTrace','drawComparisonMap','updateInstruments'])context[name]=()=>{};
  const state=vm.runInContext('state',context);
  state.candidateLapId='corrected';state.laps=[{id:'corrected',coverage_ratio:0,engineering_json:JSON.stringify({telemetry_timing_mismatch:true})}];state.lapTrace={coverage:1};
  return {context,state,nodes};
}
test('A retained trace cannot overrule canonical history uncertainty with 100 percent coverage',()=>{
  const {context,nodes}=harness();context.renderComparison();
  assert.equal(nodes.get('traceCoverage').textContent,'Lap coverage unverified');
  assert.equal(nodes.get('traceCoverage').dataset.state,'warning');
});
test('Known partial coverage caps finite samples while missing coverage remains unavailable',()=>{
  const {context}=harness();
  assert.equal(context.lapRecordingQuality({coverage_ratio:.35},1).coverage,.35);
  assert.equal(context.lapRecordingQuality({coverage_ratio:1},.5).coverage,.5);
  for(const value of [null,undefined,'1',NaN,Infinity,-1,2]){
    const quality=context.lapRecordingQuality({coverage_ratio:value,engineering_json:'bad json'},1);
    assert.equal(quality.coverage,null);assert.match(quality.label,/unavailable/);
  }
  assert.equal(context.lapRecordingQuality({coverage_ratio:1},1).warning,'');
});
test('Single-lap analysis explains corrected timing and keeps unobserved measurements unavailable',async()=>{
  const {context,nodes}=harness();
  context.fetch=async()=>({ok:true,text:async()=>JSON.stringify({lap_number:4,lap_time_ms:92000,coverage_ratio:0,metric_coverage:{speed:1,braking:1,full_throttle:1},top_speed_kph:null,minimum_speed_kph:null,full_throttle_pct:null,braking_pct:null,braking_events:null,segments:[]})});
  await context.analyzeLapAlone();
  const status=nodes.get('lapLabStatus');
  assert.match(status.textContent,/Official lap timing was corrected from session history/);
  assert.match(status.textContent,/Partial recording/);assert.equal(status.dataset.tone,'warning');
  const text=node=>[node.textContent,...node.children.map(text)].join(' ');
  const rendered=text(nodes.get('soloAnalysisSummary'));
  assert.match(rendered,/Unavailable/);assert.doesNotMatch(rendered,/0 kph|0%|0 braking events/);
});
test('No trace leaves playback unavailable and cannot keep a previous healthy coverage badge',()=>{
  const {context,state,nodes}=harness();
  state.laps[0]={id:'corrected',coverage_ratio:1};context.renderComparison();
  assert.equal(nodes.get('traceCoverage').dataset.state,'healthy');
  state.lapTrace=null;context.renderComparison();
  assert.equal(nodes.get('traceCoverage').textContent,'Coverage unavailable');
  assert.equal(nodes.get('traceCoverage').dataset.state,'neutral');
});
