import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {normalizeState, finite, positive} from '../static/driver-dashboard/model.mjs';

const html=fs.readFileSync(new URL('../static/overlay.html',import.meta.url),'utf8');
const script=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*;$/m,'');
const identity='9007199254740993:0:0:1';
function state(){return {connected:true,session_identity:identity,race_control_phase:'green',fia_flag:'green',
  packet_group_freshness:{'1':100,'2':100,'7':100,'10':100},player_position:4,live_delta_s:0,
  tyre:{compound:'MEDIUM',age_laps:0,wear:[0,10,null,20]},fuel_laps_delta:0,
  strategy:{available:true,recommended:{instruction:'BOX NOW for HARD',projected_finish_position:3,projected_finish_wear_pct:40}},
  radio_log:[{role:'engineer',text:'Box this lap.'}]};}
function suspended(){const s=state();s.race_control_phase='red_flag';s.strategy.red_flag_restart={active:true,session_identity:identity,
  primary:{instruction:'Fit a fresh MEDIUM set during suspension, then stop lap 20 for HARD.'},
  alternative:{instruction:'Fit fresh HARD during suspension and run to the finish.'}};return s;}
function harness(){
  const nodes=new Map(), sockets=[], timers=[];let time=101000;
  const get=id=>{if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,classList:{add(){},toggle(){}}});return nodes.get(id);};
  const context=vm.createContext({normalizeState,finite,positive,URLSearchParams,
    location:{search:'',protocol:'http:',host:'pitbox.test'},Date:{now:()=>time},
    document:{getElementById:get,body:{classList:{add(){}}}},fetch:()=>new Promise(()=>{}),
    setTimeout:()=>{},setInterval:fn=>timers.push(fn),WebSocket:class{constructor(){sockets.push(this);}}});
  vm.runInContext(script,context);
  return {get,context,sockets,send:s=>sockets.at(-1).onmessage({data:JSON.stringify(s)}),tick:ms=>{time+=ms;timers.forEach(fn=>fn());},
    render:s=>context.render(s,{now:time}),text:id=>get(id).textContent};
}
test('Overlay requires the packet family and preserves observed zeroes and missing wheel values',()=>{
  const h=harness(),s=state();h.render(s);
  assert.equal(h.text('pos'),'P4');assert.equal(h.text('delta'),'+0.00');assert.equal(h.text('fuel'),'+0.0 laps');
  assert.equal(h.text('wear'),'0% 10% — 20%');assert.equal(h.text('strat'),'BOX NOW for HARD');
  h.render({...s,packet_group_freshness:{}});
  for(const id of ['pos','delta','tyre','wear','fuel'])assert.equal(h.text(id),'—',id);
  assert.equal(h.text('strat'),'Current strategy unavailable');assert.equal(h.get('strategyProjection').hidden,true);
  h.render({...s,packet_group_freshness:{'1':200,'2':200,'7':200,'10':200}});
  for(const id of ['pos','delta','tyre','wear','fuel'])assert.equal(h.text(id),'—',id);
});
test('Overlay retains only exact-session red-flag options, clearly provisional and without car values',()=>{
  const h=harness(),s={...suspended(),connected:false,telemetry_stale:true};h.render(s);
  assert.equal(h.text('status'),'Last confirmed · provisional');
  assert.match(h.text('strat'),/fresh MEDIUM set during suspension/);
  assert.match(h.text('alternative'),/Alternative:.*HARD/);
  assert.match(h.text('strategyNotice'),/Confirm the suspension, tyre sets and track conditions/);
  assert.equal(h.text('radio'),'—');assert.equal(h.get('strategyProjection').hidden,true);
  for(const id of ['pos','delta','tyre','wear','fuel'])assert.equal(h.text(id),'—',id);
  for(const session_identity of ['9007199254740992:0:0:1','9007199254740993:1:0:1','9007199254740993:0:1:1','9007199254740993:0:0:2','']){
    h.render({...s,session_identity});assert.equal(h.text('strat'),'Current strategy unavailable');assert.equal(h.text('alternative'),'');
  }
});
test('Overlay makes old suspension packet context provisional even while unrelated packets arrive',()=>{
  const h=harness(),s=suspended();h.render({...s,packet_group_freshness:{'2':100,'10':100}});
  assert.equal(h.text('status'),'Last confirmed · provisional');assert.equal(h.text('tyre'),'—');
  h.render({...s,race_control_phase:'green',red_flag_active:false});
  assert.equal(h.text('strat'),'BOX NOW for HARD');assert.equal(h.text('alternative'),'');assert.equal(h.text('strategyNotice'),'');
});
test('Overlay authoritative finish clears all car readings and actionable strategy even in a fresh frame',()=>{
  const h=harness();for(const connected of [true,false]){
    h.render({...suspended(),connected,final_classification:{position:1,laps:31}});
    assert.equal(h.text('pos'),'P1');assert.equal(h.text('status'),'Session complete · P1');
    assert.match(h.text('strat'),/^Session complete/);assert.equal(h.text('alternative'),'');assert.equal(h.text('radio'),'—');
    assert.equal(h.get('strategyProjection').hidden,true);
    for(const id of ['delta','tyre','wear','fuel'])assert.equal(h.text(id),'—',id);
  }
  h.render({...state(),session_identity:'9007199254740994:0:0:2',final_classification:{},radio_log:[]});
  assert.equal(h.text('pos'),'P4');assert.equal(h.text('radio'),'—');assert.equal(h.text('strat'),'BOX NOW for HARD');
});
test('Overlay socket close and silent expiry clear live output; a confirmed red plan survives provisionally',()=>{
  const h=harness();h.send(state());h.sockets[0].onclose();
  assert.equal(h.text('status'),'Telemetry unavailable');assert.equal(h.text('fuel'),'—');assert.equal(h.text('radio'),'—');
  h.send(suspended());h.tick(3600);assert.equal(h.text('status'),'Last confirmed · provisional');
  assert.match(h.text('strat'),/fresh MEDIUM/);assert.equal(h.text('tyre'),'—');
  h.send({...state(),packet_group_freshness:{'1':104.6,'2':104.6,'7':104.6,'10':104.6}});
  assert.equal(h.text('status'),'Live telemetry');assert.equal(h.text('alternative'),'');
});
