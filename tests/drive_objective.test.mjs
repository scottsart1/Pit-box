import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html=fs.readFileSync(new URL('../static/index.html',import.meta.url),'utf8');
const start=html.indexOf('function renderRival('),end=html.indexOf('function damageText(',start);
assert.ok(start>0&&end>start);
const spa={session_uid:'spa',track_id:10,session_type:'Practice 1',mode_profile:'practice',
  restart_epoch:0,timeline_epoch:0,session_generation:1,player_position:2,current_lap:0};
const hold={available:true,already_there:true,position:2,message:'You are already in the target position.'};
const unavailable={available:false,reason:'No classified position yet.'};
const reply=(body,ok=true)=>({ok,json:async()=>body});
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no});return{promise,resolve,reject};};

function harness(){
  const ids=['objectiveTarget','objectiveHero','objectiveBody','raceFlow','rivalCompare','rivalAhead','rivalBehind'];
  const nodes=new Map(ids.map(id=>[id,{textContent:'',innerHTML:'',className:'',open:true}]));
  const context=vm.createContext({$:id=>nodes.get(id),lastState:{...spa},lastAuxLap:0,
    lastRivalKey:'',rivalCache:{},objectiveTarget:10,esc:value=>String(value??''),fetch:async()=>reply(hold),sectorStrip:()=>''});
  vm.runInContext(html.slice(start,end),context);
  context.observeDriveAuxiliarySession(context.lastState);
  const observe=state=>{context.lastState=state;context.observeDriveAuxiliarySession(state);};
  const neutral=()=>{assert.equal(nodes.get('objectiveHero').textContent,'—');assert.equal(nodes.get('objectiveHero').className,'hero muted');};
  return{context,nodes,observe,neutral};
}

test('No claimed position appears before the first classified objective',()=>{
  assert.match(html,/<div id="objectiveHero" class="hero muted">—<\/div>/);
});

test('Unavailable objective clears the old Hold P2 hero and success tone',async()=>{
  const h=harness();await h.context.loadObjective();
  assert.equal(h.nodes.get('objectiveHero').textContent,'Hold P2');
  assert.equal(h.nodes.get('objectiveHero').className,'hero good');
  h.context.fetch=async()=>reply(unavailable);
  await h.context.loadObjective();h.neutral();
  assert.equal(h.nodes.get('objectiveBody').textContent,unavailable.reason);
  assert.equal(h.nodes.get('objectiveTarget').textContent,10);
});

for(const failure of ['network','http','json'])test(`${failure} failure clears a previous successful objective`,async()=>{
  const h=harness();await h.context.loadObjective();
  h.context.fetch=async()=>{
    if(failure==='network')throw new Error('Request failed');
    if(failure==='http')return reply(hold,false);
    return{ok:true,json:async()=>{throw new Error('Invalid JSON');}};
  };
  await h.context.loadObjective();h.neutral();
  assert.equal(h.nodes.get('objectiveBody').textContent,'Objective unavailable.');
});

test('A new session clears the card immediately and refreshes even on the same lap',async()=>{
  const h=harness();await h.context.loadObjective();h.context.lastAuxLap=0;
  const monza={...spa,session_uid:'monza',track_id:11,session_type:'Practice 2',session_generation:2,player_position:0};
  h.observe(monza);h.neutral();assert.equal(h.context.lastAuxLap,-1);
  assert.equal(h.nodes.get('objectiveBody').textContent,'Waiting for a classified position.');
  // Execute the actual render prelude to verify session observation is wired
  // before the rest of Drive rendering, even if a later card cannot render.
  const renderStart=html.indexOf('function render(s){');
  const renderPrelude=html.slice(renderStart,html.indexOf('renderRaceControl(s);',renderStart));
  h.context.dismissBoot=()=>{};h.context.renderCompactFooter=()=>{};
  h.context.observeSetupSession=()=>{};
  h.context.window={dispatchEvent:()=>{}};h.context.CustomEvent=class{};
  vm.runInContext(renderPrelude+'}',h.context);
  h.context.lastAuxLap=0;h.context.render({...monza,session_generation:3});
  assert.equal(h.context.lastAuxLap,-1);h.neutral();
});

for(const change of [{session_uid:'different'},{track_id:11},{session_type:'Practice 2'},
  {restart_epoch:1},{timeline_epoch:1},{session_generation:2},{player_position:0}]){
  test(`Delayed old response is ignored when ${Object.keys(change)[0]} changes`,async()=>{
    const h=harness(),pending=deferred();
    h.context.fetch=()=>pending.promise;const old=h.context.loadObjective();
    h.observe({...spa,...change});h.neutral();
    pending.resolve(reply(hold));await old;h.neutral();
  });
}

test('A late response cannot reappear after A to B to A with repeated identity',async()=>{
  const h=harness(),pending=deferred();h.context.fetch=()=>pending.promise;
  const old=h.context.loadObjective();
  h.observe({...spa,session_uid:'B',track_id:11});h.observe({...spa});
  pending.resolve(reply(hold));await old;h.neutral();
});

test('Late old-session failure cannot erase the new session objective',async()=>{
  const h=harness(),pending=deferred();h.context.fetch=()=>pending.promise;
  const old=h.context.loadObjective();
  h.observe({...spa,session_uid:'B',session_generation:2,player_position:4});
  h.context.fetch=async()=>reply({...hold,position:4});await h.context.loadObjective();
  pending.reject(new Error('Old network failure'));await old;
  assert.equal(h.nodes.get('objectiveHero').textContent,'Hold P4');
  assert.equal(h.nodes.get('objectiveHero').className,'hero good');
});

test('Changing target while a response is pending keeps only the new target result',async()=>{
  const h=harness(),pending=deferred();h.context.fetch=()=>pending.promise;
  const old=h.context.loadObjective();h.context.objectiveTarget=1;
  h.context.fetch=async url=>{assert.match(url,/target=1$/);return reply({available:true,already_there:false,
    feasible_on_pace:'marginal',positions_needed:1,cars_in_the_way:[]});};
  await h.context.loadObjective();pending.resolve(reply(hold));await old;
  assert.equal(h.nodes.get('objectiveTarget').textContent,1);
  assert.equal(h.nodes.get('objectiveHero').textContent,'MARGINAL · 1 place');
  assert.equal(h.nodes.get('objectiveHero').className,'hero warn');
});

test('Delayed race flow cannot restore the retired session running order',async()=>{
  const h=harness(),pending=deferred();h.context.fetch=()=>pending.promise;
  const old=h.context.loadRaceFlow();
  h.observe({...spa,session_uid:'monza',track_id:11,session_generation:2});
  pending.resolve(reply({available:true,cars_stopped:12}));await old;
  assert.equal(h.nodes.get('raceFlow').textContent,'Waiting for the running order.');
  assert.doesNotMatch(h.nodes.get('raceFlow').innerHTML,/12 stopped/);
});

test('Late race-flow errors cannot erase a fresh session response',async()=>{
  const h=harness(),pending=deferred();h.context.fetch=()=>pending.promise;
  const old=h.context.loadRaceFlow();h.observe({...spa,session_uid:'monza'});
  h.context.fetch=async()=>reply({available:true,cars_stopped:3});await h.context.loadRaceFlow();
  pending.reject(new Error('Old failure'));await old;
  assert.match(h.nodes.get('raceFlow').innerHTML,/3 stopped/);
});

test('Rival cache refreshes across a session change on the same lap and sector',async()=>{
  const h=harness(),requests=[];
  h.context.fetch=async url=>{requests.push(url);return reply({available:true,driver:'Spa car'});};
  await h.context.loadRivals();assert.equal(requests.length,2);
  await h.context.loadRivals();assert.equal(requests.length,2);
  h.observe({...spa,session_uid:'monza',track_id:11});
  assert.equal(Object.keys(h.context.rivalCache).length,0);
  assert.doesNotMatch(h.nodes.get('rivalAhead').innerHTML,/Spa car/);
  h.context.fetch=async url=>{requests.push(url);return reply({available:true,driver:'Monza car'});};
  await h.context.loadRivals();assert.equal(requests.length,4);
  assert.match(h.nodes.get('rivalAhead').innerHTML,/Monza car/);
  assert.match(h.nodes.get('rivalBehind').innerHTML,/Monza car/);
});

for(const rejected of [false,true])test(`Retired rival ${rejected?'failure':'reply'} cannot update either current rival panel`,async()=>{
  const h=harness(),pending=deferred();let calls=0;
  h.context.fetch=()=>{calls++;return pending.promise;};
  const old=h.context.loadRivals();h.observe({...spa,session_uid:'monza',track_id:11});
  h.context.fetch=async()=>reply({available:true,driver:'Current car'});await h.context.loadRivals();
  if(rejected)pending.reject(new Error('Old failure'));else pending.resolve(reply({available:true,driver:'Old car'}));
  await old;assert.equal(calls,1,'The retired request must not start its second rival lookup.');
  assert.match(h.nodes.get('rivalAhead').innerHTML,/Current car/);
  assert.match(h.nodes.get('rivalBehind').innerHTML,/Current car/);
  assert.equal(h.context.rivalCache.ahead.driver,'Current car');
  assert.equal(h.context.rivalCache.behind.driver,'Current car');
});
