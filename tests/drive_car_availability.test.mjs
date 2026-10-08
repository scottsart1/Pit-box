import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html=fs.readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
const start=html.indexOf('function renderLiveCar('),end=html.indexOf('function renderRaceControl(',start);
assert.ok(start>0&&end>start);
const nodes=new Map(['compound','age','fl','fr','rl','rr','flTemp','frTemp','rlTemp','rrTemp','fuel','ers','speed','damageRow','weather','carDataStatus','stintClock'].map(id=>[id,{textContent:'',innerHTML:'',className:''}]));
const context=vm.createContext({$:id=>nodes.get(id),damageText:()=> 'No recorded damage'});
vm.runInContext(html.slice(start,end),context);
const state=()=>({connected:true,packet_group_freshness:{'1':100,'6':100,'7':100,'10':100},tyre:{compound:'MEDIUM',age_laps:0,wear:[0,10,20,30],inner_temps_c:[90,91,92,93]},fuel_laps_delta:0,ers_pct:0,speed_kph:0,gear:0,weather:'Clear',rain_next_15_pct:0,weather_forecast:[{time_offset_min:15,rain_pct:0}]});
const draw=s=>context.renderLiveCar(s,101000);
const text=id=>nodes.get(id).textContent;

test('Before telemetry, Drive never fabricates zero wear, temperature, fuel, ERS or speed',()=>{
  draw({});for(const id of ['age','fl','fr','rl','rr','flTemp','frTemp','rlTemp','rrTemp','fuel','ers','speed'])assert.equal(text(id),'—',id);
  assert.equal(nodes.get('damageRow').innerHTML,'Damage data unavailable');
  assert.equal(text('carDataStatus'),'Current car data unavailable');
});
test('Current measured zeros remain valid and reverse gear is labelled R',()=>{
  const s=state();draw(s);assert.equal(text('fl'),'0%');assert.equal(text('flTemp'),'90°C');
  assert.equal(text('fuel'),'+0.0 laps');assert.equal(text('ers'),'0%');assert.equal(text('age'),'0 laps');
  assert.equal(text('speed'),'0 km/h · N');assert.equal(text('carDataStatus'),'Live telemetry');
  s.gear=-1;draw(s);assert.equal(text('speed'),'0 km/h · R');
});
test('Each packet family expires independently while other measurements remain current',()=>{
  const s=state();s.packet_group_freshness['10']=90;draw(s);
  assert.equal(text('fl'),'—');assert.equal(text('flTemp'),'90°C');assert.equal(text('fuel'),'+0.0 laps');
  assert.equal(nodes.get('damageRow').innerHTML,'Damage data unavailable');assert.match(text('carDataStatus'),/some car readings unavailable/);
  s.packet_group_freshness['6']=90;draw(s);assert.equal(text('speed'),'—');assert.equal(text('flTemp'),'—');
  s.packet_group_freshness['7']=90;draw(s);assert.equal(text('compound'),'UNKNOWN');assert.equal(text('fuel'),'—');assert.equal(text('ers'),'—');
  s.packet_group_freshness['1']=90;draw(s);assert.equal(text('weather'),'Unavailable');
});
test('Disconnected or stale snapshots cannot retain old car measurements',()=>{
  for(const patch of [{connected:false},{telemetry_stale:true}]){
    draw(state());draw({...state(),...patch});for(const id of ['fl','flTemp','fuel','ers','speed','stintClock'])assert.equal(text(id),'—',id);
  }
  assert.match(html,/ws\.onclose=\(\)=>\{renderCompactFooter\(lastState\|\|\{\},false\);renderLiveCar\(/);
});
test('Missing individual fields are unavailable, and replay/sample provenance stays explicit',()=>{
  const s=state();s.tyre.wear=[0,null,'10',NaN];delete s.ers_pct;delete s.fuel_laps_delta;draw(s);
  assert.equal(text('fl'),'0%');for(const id of ['fr','rl','rr','fuel','ers'])assert.equal(text(id),'—',id);
  draw({...state(),source_mode:'replay'});assert.equal(text('carDataStatus'),'Recorded replay');
  draw({...state(),source_mode:'demo'});assert.equal(text('carDataStatus'),'Sample data');
});
test('Rain probability requires an observed sample at the stated 15-minute horizon',()=>{
  for(const weather_forecast of [undefined,[],[{time_offset_min:0,rain_pct:0}],[{time_offset_min:10,rain_pct:40}],[{time_offset_min:30,rain_pct:80}],[{time_offset_min:15,rain_pct:null}],[{time_offset_min:15,rain_pct:'0'}],[{time_offset_min:15,rain_pct:101}]]){
    draw({...state(),weather_forecast});
    assert.equal(text('weather'),'Clear · 15-min rain forecast unavailable');
  }
  draw(state());assert.equal(text('weather'),'Clear · rain at 15 min 0%');
  draw({...state(),rain_next_15_pct:0,weather_forecast:[{time_offset_min:0,rain_pct:0},{time_offset_min:15,rain_pct:60},{time_offset_min:30,rain_pct:90}]});
  assert.equal(text('weather'),'Clear · rain at 15 min 60%');
});
test('Unavailable rain forecasts retain independently known track conditions and expire with the session',()=>{
  const s={...state(),weather_forecast:[],strategy:{weather_crossover:{wetness:.4,trend:'wetting'}}};
  draw(s);assert.equal(text('weather'),'Clear · 15-min rain forecast unavailable · track wet (wetting)');
  s.packet_group_freshness['1']=90;draw(s);assert.equal(text('weather'),'Unavailable');
});
