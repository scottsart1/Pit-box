import test from 'node:test';
import assert from 'node:assert/strict';
import {comparisonCohorts,comparisonVisuals} from '../static/js/engineering-visuals.mjs';
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.dataset={};this.style={};this.attributes={};this._text='';}
  set textContent(value){this._text=String(value);this.children=[];}
  get textContent(){return this._text+this.children.map(c=>c.textContent).join('');}
  append(...children){this.children.push(...children);}
  setAttribute(name,value){this.attributes[name]=value;}
}
globalThis.document={createElement:tag=>new Element(tag)};
const all=node=>[node,...node.children.flatMap(all)];
const byClass=(node,cls)=>all(node).filter(n=>(n.className||'').split(' ').includes(cls));
const selected={a:{laps:[{id:'a1',lap_num:1,lap_time_ms:90100},{id:'a2',lap_num:2,lap_time_ms:90700},{id:'a3',lap_num:3,lap_time_ms:95000}]},b:{laps:[{id:'b1',lap_num:6,lap_time_ms:89800},{id:'b2',lap_num:7,lap_time_ms:90200}]}};
const result=()=>({mode:'stint',enough_evidence:false,clean_lap_counts:{a:2,b:2},sector_lap_counts:{a:2,b:1},sector_deltas_s:[-.3,.15,0],excluded:{a:[{lap_id:'a3',reasons:['traffic']}],b:[]}});

test('Stint dots use exactly the clean comparison cohort and omit excluded traffic laps',()=>{
  const cohorts=comparisonCohorts(result(),selected);assert.deepEqual(cohorts.a.map(l=>l.id),['a1','a2']);
  const view=comparisonVisuals(result(),selected),dots=byClass(view,'engineering-pace-dot');
  assert.equal(dots.length,4);assert.doesNotMatch(view.textContent,/1:35/);
  assert.match(view.textContent,/A 2 \/ B 2 used · excluded A 1 \/ B 0/);
  assert.match(view.textContent,/A range 1:30.100–1:30.700 · spread 0.600 s/);
  assert.match(view.textContent,/B range 1:29.800–1:30.200 · spread 0.400 s/);
});
test('Sector bars have a shared zero, preserve signs, and scale to the actual delta',()=>{
  const view=comparisonVisuals(result(),selected),bars=byClass(view,'engineering-sector-bar');
  assert.deepEqual(bars.map(b=>b.dataset.direction),['quicker','slower','equal']);
  assert.deepEqual(bars.map(b=>b.style.left),['0%','50%','50%']);
  assert.deepEqual(bars.map(b=>b.style.width),['50%','25%','0%']);
  assert.match(view.textContent,/−0.300 s/);assert.match(view.textContent,/\+0.150 s/);assert.match(view.textContent,/0.000 s/);
  assert.match(view.textContent,/Complete-sector laps: A 2 \/ B 1/);
});
test('Missing or malformed sector values never become zero bars',()=>{
  const view=comparisonVisuals({...result(),sector_deltas_s:[null,'0',Infinity]},selected);
  assert.equal(byClass(view,'engineering-sector-bar').length,0);
  assert.equal(byClass(view,'engineering-chart-value').filter(v=>v.textContent==='Unavailable').length,3);
});
test('Matched comparison dots contain paired lap IDs only, preserving sample-count meaning',()=>{
  const r={...result(),mode:'setup',pairs:[{a_lap_id:'a2',b_lap_id:'b1',delta_s:-.9}]};
  const cohorts=comparisonCohorts(r,selected);assert.deepEqual(cohorts.a.map(l=>l.id),['a2']);assert.deepEqual(cohorts.b.map(l=>l.id),['b1']);
  const view=comparisonVisuals(r,selected);assert.equal(byClass(view,'engineering-pace-dot').length,2);
  assert.match(view.textContent,/1 matched pairs · A 1 \/ B 1 used/);
  assert.match(view.textContent,/Matched-lap pace and consistency/);
});
test('A mismatched loaded report cannot silently replace the accepted cohort',()=>{
  const r={...result(),clean_lap_counts:{a:3,b:2}};
  assert.equal(comparisonCohorts(r,selected).a,null);
  const view=comparisonVisuals(r,selected);assert.equal(byClass(view,'engineering-pace-dot').length,0);
  assert.match(view.textContent,/loaded laps do not reproduce the accepted comparison cohort/);
});
test('Identical lap times remain finite and dots share a common position',()=>{
  const inputs={a:{laps:[{id:'a',lap_num:1,lap_time_ms:90000}]},b:{laps:[{id:'b',lap_num:4,lap_time_ms:90000}]}};
  const view=comparisonVisuals({...result(),clean_lap_counts:{a:1,b:1},excluded:{},sector_deltas_s:[0,0,0]},inputs);
  assert.deepEqual(byClass(view,'engineering-pace-dot').map(dot=>dot.style.left),['50%','50%']);
  assert.doesNotMatch(view.textContent,/NaN|Infinity/);
});
test('Chart context stays explicit about preliminary, unmatched and cross-session evidence',()=>{
  const view=comparisonVisuals({...result(),cross_session:true},selected);
  assert.match(view.textContent,/Preliminary evidence/);assert.match(view.textContent,/Conditions are not matched/);
  assert.match(view.textContent,/does not prove a setup or tyre caused/);assert.match(view.textContent,/game and car equivalence are unverified/);
  for(const dot of byClass(view,'engineering-pace-dot'))assert.match(dot.attributes['aria-label'],/^[AB] lap \d+: \d:\d{2}\.\d{3}$/);
});
test('Identical laps use an explicit count instead of silently hiding overlapping dots',()=>{
  const inputs={a:{laps:[{id:'a1',lap_num:1,lap_time_ms:90000},{id:'a2',lap_num:2,lap_time_ms:90000}]},b:{laps:[{id:'b1',lap_num:4,lap_time_ms:90000}]}};
  const view=comparisonVisuals({...result(),clean_lap_counts:{a:2,b:1},excluded:{}},inputs),dots=byClass(view,'engineering-pace-dot');
  assert.deepEqual(dots.map(dot=>dot.dataset.count),['2','1']);assert.match(view.textContent,/×2/);
  assert.match(dots[0].attributes['aria-label'],/A laps 1, 2: 1:30.000/);
});
