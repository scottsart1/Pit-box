import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyViewport,resolveDisplay,cleanPreferences,cleanWidgets,presetWidgets,reorderWidgets,resizedWidget,DISPLAY_PROFILES,LAYOUTS,WIDGET_CATALOG,WIDGET_PRESETS} from '../static/driver-dashboard/display.mjs';
import {demoState,normalizeState,raceFlag,lapTime,escapeHTML} from '../static/driver-dashboard/model.mjs';
import {renderDashboard,MODULES} from '../static/driver-dashboard/render.mjs';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
for(const [name,width,height,touch,family] of [['Fold cover',344,882,true,'compact'],['Fold open',690,829,true,'fold'],['Fold rotated',829,690,true,'fold'],['Galaxy Tab',800,1280,true,'tablet'],['iPad mini',744,1133,true,'tablet'],['iPad Air',820,1180,true,'tablet'],['iPad Pro',1024,1366,true,'tablet'],['laptop',1366,768,false,'laptop'],['desktop',1920,1080,false,'desktop'],['ultrawide',2560,1080,false,'wide'],['phone landscape',844,390,true,'compact'],['split screen',400,900,true,'compact']])test(name+' auto profile',()=>assert.equal(classifyViewport({width,height,touch}),family));
test('Every manual profile still fits a cover screen',()=>{for(const p of DISPLAY_PROFILES){const d=resolveDisplay(p.id,{width:344,height:882,touch:true});assert.equal(d.width,344);assert.equal(d.recommendedLayout,'portrait');assert.equal(d.compact,true)}});
test('Invalid and old saved preferences cannot create missing layouts or duplicate modules',()=>{const p=cleanPreferences({layout:'missing',profile:'old',scale:'300',contrast:'false',order:['relative','relative']});assert.equal(p.layout,'auto');assert.equal(p.profile,'auto');assert.equal(p.scale,'1');assert.equal(p.contrast,false);assert.equal(new Set(p.order).size,6)});
test('Manual layout survives auto profile changes and order is copied',()=>{const input={layout:'battle',profile:'auto',order:['timing','relative','laps','tires','resources','session']};const p=cleanPreferences(input);p.order.reverse();assert.equal(input.order[0],'timing');assert.equal(p.layout,'battle')});
test('Safety precedence beats pit lane, penalties and blue flag',()=>{let s={connected:true,fia_flag:'blue',pit_status:2,unserved_drive_through_penalties:1,race_control_phase:'safety_car'};assert.equal(raceFlag(s).id,'sc');assert.equal(raceFlag({...s,red_flag_active:true}).id,'red');assert.equal(raceFlag(s,{fresh:false}).id,'stale');assert.equal(raceFlag({}).id,'unknown');assert.equal(raceFlag({fia_flag:'invalid'}).id,'unknown')});
test('Race gaps are signed, sorted and exclude retired cars',()=>{const s=demoState();s.drivers.push({...s.drivers[2],car_idx:23,gap_to_player_s:-.001,result_status:4});s.drivers.reverse();const m=normalizeState(s,{transport:'demo'});assert.equal(m.ahead.name,'PIASTRI');assert.equal(m.behind.name,'HAMILTON');assert.equal(m.ahead.gap,-.842)});
test('No synthetic numbers when live packet groups are missing or stale',()=>{const s=demoState();let m=normalizeState(s);assert.equal(m.speed,null);assert.equal(m.fuel,null);assert.deepEqual(m.temps,[null,null,null,null]);s.packet_group_freshness={'6':100,'7':100,'10':100};m=normalizeState(s,{now:101000});assert.equal(m.speed,284);assert.equal(m.fuel,46.2);assert.deepEqual(m.wear,[18,21,16,17]);assert.equal(normalizeState(s,{now:106000}).speed,null);assert.equal(normalizeState(s,{ageMs:3500}).fresh,false)});
test('PB prediction uses its stated time, not the current session best',()=>{const s=demoState();s.live_delta_reference='PB 1:27.000';s.live_delta_s=-.25;assert.equal(normalizeState(s,{transport:'demo'}).predicted,86750);s.live_delta_reference='Previous lap';assert.equal(normalizeState(s,{transport:'demo'}).predicted,null)});
test('Malformed/missing optional data stay unavailable',()=>{const m=normalizeState({connected:true,radio_log:{},speed_kph:Infinity,gear:'6'});assert.equal(m.speed,null);assert.equal(m.gear,null);assert.equal(m.average,null);assert.equal(m.predicted,null);assert.equal(lapTime(null),'—');assert.equal(lapTime(89999),'1:29.999')});
test('All six layouts support every scenario and escape telemetry text',()=>{for(const l of LAYOUTS)for(const scenario of ['green','yellow','double-yellow','sc','vsc','red','blue','penalty','pit','finish','stale']){const s=demoState(scenario);s.track_name='<img src=x onerror=alert(1)>';s.drivers[2].name='<svg onload=alert(2)>';s.radio_log[0].text='<script>evil()</script>';const html=renderDashboard(l,normalizeState(s,{transport:'demo'}),cleanPreferences(null));assert.ok(html.includes('flag-banner'));assert.ok(!html.includes('<img'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<svg'));assert.ok(!html.includes('NaN'));if(scenario==='stale'){assert.ok(html.includes('NO CURRENT DATA'));assert.ok(!html.includes('0.842'));assert.ok(!html.includes('P5'))}}});
test('Unavailable fuel is not fabricated from a default',()=>{const s=demoState();delete s.fuel_kg;delete s.fuel_laps_delta;const m=normalizeState(s,{transport:'demo'});assert.equal(m.fuel,null);assert.equal(m.fuelMargin,null)});
test('Receiving car telemetry without current flag packets never claims green',()=>{const s=demoState();s.packet_group_freshness={'6':100};assert.equal(normalizeState(s,{now:101000}).flag.id,'unknown');s.packet_group_freshness['1']=100;s.packet_group_freshness['7']=100;assert.equal(normalizeState(s,{now:101000}).flag.id,'green');s.packet_group_freshness['1']=90;assert.equal(normalizeState(s,{now:101000}).flag.id,'unknown')});

test('All catalog widgets and presets are complete, unique and independently editable',()=>{
  assert.equal(WIDGET_CATALOG.length,24);
  assert.equal(new Set(WIDGET_CATALOG.map(w=>w.id)).size,24);
  for(const widget of WIDGET_CATALOG)assert.equal(typeof MODULES[widget.id].render,'function',widget.id);
  for(const preset of WIDGET_PRESETS){
    const widgets=presetWidgets(preset.id);
    assert.deepEqual(cleanWidgets(widgets),widgets);
    assert.equal(new Set(widgets.map(w=>w.id)).size,widgets.length);
    widgets[0].w=3;assert.notDeepEqual(presetWidgets(preset.id),widgets);
  }
});

test('Saved layout migration preserves old order and explicit empty layouts',()=>{
  const order=['timing','relative','laps','tires','resources','session'];
  const migrated=cleanPreferences({order,tires:false});
  assert.deepEqual(migrated.widgets.map(w=>w.id),order.filter(id=>id!=='tires'));
  assert.deepEqual(cleanPreferences({widgets:[]}).widgets,[]);
  assert.deepEqual(cleanPreferences({widgets:'corrupt'}).widgets,presetWidgets());
});

test('Untrusted saved widgets are bounded, deduplicated and cloned',()=>{
  const original=[{id:'inputs',w:999,h:-1},{id:'inputs',w:1,h:1},{id:'<script>',w:1,h:1},{id:'timing',w:2,h:3},null];
  const widgets=cleanWidgets(original);
  assert.deepEqual(widgets,[{id:'inputs',w:1,h:1},{id:'timing',w:2,h:3}]);
  widgets[1].w=1;assert.equal(original[3].w,2);
  const saved=Array.from({length:12},(_,i)=>({name:'A'.repeat(80),widgets:original}));
  const prefs=cleanPreferences({savedLayouts:saved});
  assert.equal(prefs.savedLayouts.length,8);assert.equal(prefs.savedLayouts[0].name.length,40);
  assert.deepEqual(cleanPreferences(JSON.parse(JSON.stringify(prefs))),prefs);
});

test('Reordering supports both directions without changing sizes or the input layout',()=>{
  const widgets=[{id:'inputs',w:1,h:2},{id:'timing',w:2,h:1},{id:'relative',w:3,h:2}];
  const result=reorderWidgets(widgets,'inputs','relative');
  assert.deepEqual(result.map(w=>w.id),['timing','relative','inputs']);
  assert.deepEqual(result[2],widgets[0]);assert.deepEqual(widgets.map(w=>w.id),['inputs','timing','relative']);
  assert.deepEqual(reorderWidgets(result,'inputs','timing'),widgets);
  assert.deepEqual(reorderWidgets(widgets,'missing','timing'),widgets);
  assert.deepEqual(reorderWidgets(widgets,'timing','missing'),widgets);
});

test('Pointer resizing snaps to cells and clamps to a portable one-to-three cell size',()=>{
  const original={id:'timing',w:1,h:1};
  assert.deepEqual(resizedWidget(original,330,352,330,352),{id:'timing',w:2,h:2});
  assert.deepEqual(resizedWidget(original,10,10,330,352),original);
  assert.deepEqual(resizedWidget(original,99999,99999,330,352),{id:'timing',w:3,h:3});
  assert.deepEqual(resizedWidget(original,-99999,-99999,330,352),original);
  assert.deepEqual(original,{id:'timing',w:1,h:1});
});

test('New telemetry widgets respect packet families and missing fields',()=>{
  const state=demoState();state.packet_group_freshness={'7':100};
  let m=normalizeState(state,{now:101000});
  assert.equal(m.fuel,46.2);assert.equal(m.throttle,null);assert.equal(m.weather,'Unavailable');
  assert.equal(m.lateral,null);assert.deepEqual(m.inner,[null,null,null,null]);assert.equal(m.damage.floor,null);
  assert.equal(m.aero,null);assert.equal(m.overtake,null);assert.equal(m.penalties,null);
  state.packet_group_freshness={'0':100,'1':100,'2':100,'6':100,'7':100,'10':100,'16':100};
  m=normalizeState(state,{now:101000});
  assert.equal(m.throttle,.92);assert.equal(m.brake,0);assert.equal(m.lateral,-1.4);assert.equal(m.weather,'Light cloud');
  assert.equal(m.penalties,0);assert.equal(m.driveThrough,0);assert.equal(m.aero,'STRAIGHT');assert.equal(m.overtake,true);
  assert.deepEqual(m.pressures,[23.8,23.9,21.3,21.4]);assert.equal(m.damage.front_left_wing,2);assert.equal(m.components.ice,18);
  assert.equal(m.deployed,1.8);assert.equal(m.harvested,2.2);
  m=normalizeState(state,{now:106000});assert.equal(m.throttle,null);assert.equal(m.overtake,null);assert.equal(m.damage.floor,null);
  delete state.throttle;state.tyre.pressures_psi=[NaN,'24',23];
  m=normalizeState(state,{transport:'demo'});assert.equal(m.throttle,null);assert.deepEqual(m.pressures,[null,null,23,null]);
  state.regulations_2026=false;m=normalizeState(state,{transport:'demo'});assert.equal(m.aero,null);assert.equal(m.overtake,null);
});

test('Sample, live and recorded sources remain visibly distinct on every layout',()=>{
  const state=demoState();
  assert.equal(normalizeState(state,{transport:'demo'}).provenance,'sample');
  assert.equal(normalizeState(state,{transport:'embedded'}).provenance,'sample');
  state.source_mode='replay';assert.equal(normalizeState(state).provenance,'replay');
  state.source_mode='live';assert.equal(normalizeState(state).provenance,'live');
  for(const layout of LAYOUTS){
    const html=renderDashboard(layout,normalizeState(demoState(),{transport:'demo'}),cleanPreferences());
    assert.match(html,/SAMPLE DATA/);assert.ok(!html.includes('LIVE TELEMETRY'));
    const replay=renderDashboard(layout,normalizeState({...demoState(),source_mode:'replay'}),cleanPreferences());
    assert.match(replay,/RECORDED REPLAY/);assert.ok(!replay.includes('LIVE TELEMETRY'));
  }
});

test('Every widget renders unknown data honestly and escapes telemetry strings',()=>{
  const widgets=WIDGET_CATALOG.map(w=>({id:w.id,w:1,h:1})),prefs=cleanPreferences({widgets});
  const state=demoState();state.weather='<img src=x>';state.strategy.recommended.instruction='<script>bad()</script>';state.radio_log[0].text='<svg onload=evil()>Hi';
  const html=renderDashboard('modular',normalizeState(state,{transport:'demo'}),prefs);
  for(const widget of widgets)assert.ok(html.includes(`data-module="${widget.id}"`));
  assert.ok(!/<(?:img|script|svg)\b/.test(html));assert.ok(!html.includes('NaN'));assert.ok(!html.includes('undefined'));
  const blank=renderDashboard('modular',normalizeState(null),prefs);
  assert.ok(blank.includes('NO CURRENT DATA'));assert.ok(blank.includes('data-module="inputs"'));
  assert.ok(!blank.includes('P5'));assert.ok(!blank.includes('0.842'));assert.ok(!blank.includes('23.8'));
  assert.ok(!blank.includes('NaN'));assert.ok(!blank.includes('undefined'));
  for(const id of ['inputs','inner','pressures','wear','damage','components'])assert.ok(MODULES[id].render(normalizeState(null),prefs).includes('—'));
});

test('Stale custom dashboards retain editing controls and drop all previously received race values',()=>{
  const stale=normalizeState({...demoState(),source_mode:'live'},{ageMs:3500});
  assert.equal(stale.position,null);assert.equal(stale.speed,null);assert.equal(stale.ahead,null);assert.deepEqual(stale.recent,[]);
  const html=renderDashboard('modular',stale,cleanPreferences());
  assert.ok(html.includes('data-drag="timing"'));assert.ok(html.includes('data-resize="timing"'));assert.ok(html.includes('data-size="timing"'));
  assert.ok(html.includes('NO LIVE DATA'));assert.ok(!html.includes('284'));assert.ok(!html.includes('P5'));
  const empty=renderDashboard('modular',stale,cleanPreferences({widgets:[]}));assert.ok(empty.includes('Add your first widget'));
});

test('All widgets have labelled keyboard movement, resizing and removal controls',()=>{
  const prefs=cleanPreferences({widgets:WIDGET_CATALOG.map(w=>({id:w.id,w:2,h:2}))});
  const html=renderDashboard('modular',normalizeState(demoState(),{transport:'demo'}),prefs);
  for(const {id,name:rawName} of WIDGET_CATALOG){const name=escapeHTML(rawName);
    assert.ok(html.includes(`data-drag="${id}" aria-label="Move ${name}`));
    assert.ok(html.includes(`data-resize="${id}" aria-label="Resize ${name}`));
    assert.ok(html.includes(`aria-label="${name} width"`));assert.ok(html.includes(`aria-label="${name} height"`));
    assert.ok(html.includes(`data-remove="${id}" aria-label="Remove ${name}`));
  }
});

test('The downloadable dashboard is self-contained and its bundled script parses',()=>{
  const html=readFileSync(new URL('../static/driver-dashboard/downloads/driver-dashboard.html',import.meta.url),'utf8');
  assert.ok(!/<script[^>]+src=/.test(html));assert.ok(!/<link[^>]+rel="stylesheet"/.test(html));
  assert.ok(!html.includes('type="module"'));assert.match(html,/SAMPLE DATA/);assert.match(html,/widgetLibrary/);
  const script=html.match(/<script>([\s\S]*?)<\/script>/)?.[1];assert.ok(script);
  assert.doesNotThrow(()=>new vm.Script(script));
});
