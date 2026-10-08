/* Shipped Drive/Strategy UI against isolated, deterministic telemetry. No backend writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),staticRoot=path.join(root,'static');
const output=process.env.PITBOX_DRIVER_FOCUS_EVIDENCE||path.join(root,'.codex-ui-test-data/driver-focus-5.3.2');
const recommendation={instruction:'A second compound is mandatory — box lap 11 for SOFT.',
  compounds:['MEDIUM','SOFT','MEDIUM'],box_lap:11,box_laps:[11,19],stops_remaining:2,
  feasible:true,legal:true,finish_projection_valid:true,inventory_status:'known',inventory_feasible:true,
  tyre_reason:'Your current wear model projects 100% by the finish; the soft stint projects 76%.',
  rationale:'Projects P18 after rejoining P22, with about 4.0 positions recoverable.',
  change_condition:'The call changes for a safety car, red flag, wet crossover, new damage, or a hard tyre-wear limit breach.',
  projected_finish_position:18,projected_rejoin_position:22,projected_points:0,projected_max_wear_pct:76,
  monte_carlo:{p75_s:3000,uncertainty_s:4,calibrated:false},
  stint_models:[{usable_life_laps:13,operational_wear_limit_pct:85,feasible:true,deg_s_per_lap:.03,deg_source:'inferred_from_hard'},
    {usable_life_laps:8},{usable_life_laps:13}]};
const fixture={connected:true,telemetry_stale:false,session_uid:532,session_generation:1,track_id:42,track_name:'Madrid',formula:13,
  session_type:'Race',mode_profile:'race',current_lap:3,total_laps:29,player_position:17,player_car_index:0,
  weather:'Overcast',rain_next_15_pct:15,weather_forecast:[{time_offset_min:15,rain_pct:15}],game_presence:'receiving',speed_kph:150,gear:4,ers_pct:60,fuel_laps_delta:3.4,
  tyre:{compound:'MEDIUM',age_laps:2,wear:[13,9,12,10],inner_temps_c:[105,100,108,104]},
  damage:{gearbox:1,engine:1},analysis:{deg_model:{current_compound:'MEDIUM',current_slope_s_per_lap:null,compounds:{}},target:{}},
  strategy:{available:true,confidence:'low',recommended:recommendation,plans:[recommendation],pit_loss_s:23,
    observed_compound_rule:{applies:true,dry_count:1,change_outstanding:true},compound_rule:{applies:true,dry_count:2},
    neutralisation:{phase:'green'},weather_crossover:{wetness:0},model_summary:{compounds:{
      MEDIUM:{laps_observed:5,wear_sample_size:4,pace_sample_size:0,wear_source:'personal_track_history',pace_source:'track_default',pace_excluded_laps:{inconsistent_stint_pace:4}},
      HARD:{laps_observed:6,wear_sample_size:5,pace_sample_size:6,wear_source:'personal_track_history',pace_source:'personal_track_history'},
      SOFT:{laps_observed:0,wear_sample_size:0,pace_sample_size:0,wear_source:'inferred_from_medium_hard',pace_source:'inferred_from_medium_hard'}
    }}},drivers:[],radio_log:[],traces:[]};
// These fixtures include observed session, telemetry, status and damage
// packets. Stamp each supplied frame at delivery time, rather than letting
// screenshots or slower CI navigation turn its measurements into stale data.
const receivedFrame=state=>({...state,packet_group_freshness:Object.fromEntries(
  ['1','6','7','10'].map(id=>[id,Date.now()/1000]))});

(async()=>{
 fs.mkdirSync(output,{recursive:true});
 const browser=await chromium.launch({headless:true,...(process.env.PITBOX_BROWSER_CHANNEL?{channel:process.env.PITBOX_BROWSER_CHANNEL}:{})});
 const results=[];let page;
 try{for(const [width,height] of [[1280,800],[800,1280],[390,844]]){
  const context=await browser.newContext({viewport:{width,height}});page=await context.newPage();const errors=[];
  const renderFrame=state=>page.evaluate(s=>render(s),receivedFrame(state));
  page.on('pageerror',e=>errors.push(String(e)));
  await page.addInitScript(()=>{window.WebSocket=class{constructor(){this.readyState=0}close(){}send(){}addEventListener(){}removeEventListener(){}};});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());if(url.origin!=='http://pitbox.test')return route.abort();
   if(url.pathname==='/api/v1/usage')return route.fulfill({json:{decided:true,enabled:false}});
   if(url.pathname.startsWith('/api/'))return route.fulfill({json:url.pathname==='/api/state'?receivedFrame(fixture):{available:false,items:[],settings:[],tracks:[],strategies:[],rivals:[]}});
   const target=url.pathname==='/'?path.join(staticRoot,'index.html'):path.resolve(staticRoot,url.pathname.replace(/^\/static\//,''));
   if(!target.startsWith(staticRoot+path.sep)||!fs.existsSync(target)||fs.statSync(target).isDirectory())return route.abort();
   const type=target.endsWith('.html')?'text/html':/\.m?js$/.test(target)?'text/javascript':target.endsWith('.css')?'text/css':undefined;
   return route.fulfill({path:target,...(type?{contentType:type}:{})});
  });
  await page.goto('http://pitbox.test/');
  const decline=page.getByRole('button',{name:'No thanks',exact:true});if(await decline.isVisible())await decline.click();
  if(await page.locator('#onboardingDialog').isVisible())await page.locator('#onboardingClose').click();
  await renderFrame(fixture);
  await page.getByRole('tab',{name:'DRIVE',exact:true}).click();
  assert.equal(await page.locator('#wearProjection').innerText(),'Est. 11 laps of tyre life');
  assert.equal(await page.locator('#tyreStop').innerText(),'8 laps to planned stop');
  assert.equal(await page.locator('#strategyTyreReason').isVisible(),false);
  assert.equal(await page.locator('#tyreDegBasis').isVisible(),false);
  assert.match(await page.locator('#compoundRule').innerText(),/1\/2.*change required/);
  const primary=await page.locator('#strategyCard').innerText();
  assert.match(primary,/box lap 11/);assert.doesNotMatch(primary,/100%|76%|P75|Projects P18|Changes if/);
  await page.locator('#strategyCard').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,`drive-${width}.png`)});
  await page.locator('#strategyCard summary').click();
  assert.match(await page.locator('#strategyTyreReason').innerText(),/100%/);
  await renderFrame(fixture);
  assert.equal(await page.locator('#strategyTyreReason').isVisible(),true,'Live frames preserve the open disclosure.');
  await page.locator('#strategyCard summary').click();

  await page.getByRole('tab',{name:'STRATEGY',exact:true}).click();
  await page.locator('#stratInstruction').waitFor({state:'visible'});
  const header=await page.locator('#strategy .strategy-header').boundingBox();
  const recompute=await page.locator('#stratRecompute').boundingBox();
  const confidence=await page.locator('#stratConfidence').boundingBox();
  assert.ok(header.height<=100,'The compact header must leave room for the current call.');
  assert.ok(recompute.height>=44,'Recompute retains a touch-sized target.');
  assert.ok(confidence.width<header.width*.7,'Confidence stays compact, not a full-width strip.');
  assert.equal(await page.locator('#stratInstruction').evaluate(el=>el.tagName),'H2');
  assert.match(await page.locator('#stratWhy').innerText(),/11 laps.*8 laps to planned stop/);
  assert.equal(await page.locator('#stratMeta').isVisible(),false);
  assert.equal(await page.locator('#stratPlanRows').isVisible(),false);
  assert.equal(await page.locator('#stratPlannerForm').isVisible(),false);
  assert.equal(await page.locator('#stratWhatIfForm').isVisible(),false);
  assert.match(await page.locator('#stratRule').innerText(),/1\/2.*change is still required/);
  await page.locator('#stratInstruction').scrollIntoViewIfNeeded();
  await page.screenshot({path:path.join(output,`strategy-${width}.png`)});
  await page.getByText('Why this call',{exact:true}).click();
  assert.match(await page.locator('#stratReasoning').innerText(),/100%/);
  assert.equal(await page.locator('#stratConfidence').innerText(),'Plan confidence: low');
  assert.match(await page.locator('#stratLearning').innerText(),/MEDIUM: 5 eligible laps/);
  assert.match(await page.locator('#stratLearning').innerText(),/HARD: 6 eligible laps/);
  assert.match(await page.locator('#stratLearning').innerText(),/inconsistent pace \(4\)/);
  await page.screenshot({path:path.join(output,`strategy-evidence-${width}.png`)});
  await page.getByText('Why this call',{exact:true}).click();
  const warning=structuredClone(fixture);warning.tyre.wear[0]=86;warning.weather='Light rain';warning.damage.front_left_wing=60;
  warning.strategy.weather_crossover={wetness:.4,worth_stopping:true,compound:'INTER'};
  warning.strategy.neutralisation={phase:'safety_car',pit_entry_status:'closed'};
  warning.strategy_intent={active:true,direction:'stay_out',intent:'overcut'};
  await renderFrame(warning);
  assert.match(await page.locator('#stratInstruction').innerText(),/overcut.*staying out/);
  assert.match(await page.locator('#stratWhy').innerText(),/Tyres at wear limit/);
  assert.doesNotMatch(await page.locator('#stratWhy').innerText(),/11 laps of tyre life/);
  assert.match(await page.locator('#stratChange').innerText(),/wear limit reached.*Weather favours inter/);
  assert.match(await page.locator('#stratNotice').innerText(),/safety car.*pit entry closed/);
  await page.screenshot({path:path.join(output,`strategy-warnings-${width}.png`)});
  await page.getByRole('tab',{name:'DRIVE',exact:true}).click();
  await renderFrame(warning);
  assert.match(await page.locator('#damageRow').innerText(),/FW-L 60%/);
  assert.match(await page.locator('#strategyWarning').innerText(),/wear limit reached.*Weather favours inter/);
  for(const kind of ['missing','expired']){
    const incomplete=receivedFrame(warning);
    if(kind==='missing')delete incomplete.packet_group_freshness['10'];
    else incomplete.packet_group_freshness['10']-=6;
    await page.evaluate(s=>render(s),incomplete);
    assert.equal(await page.locator('#damageRow').innerText(),'Damage data unavailable',`${kind} damage packets must remain unavailable`);
    assert.equal(await page.locator('#flTemp').innerText(),'105°C','A missing damage packet does not invalidate current temperature telemetry');
  }
  await page.evaluate(s=>render({...s,connected:false,telemetry_stale:true}),warning);
  assert.equal(await page.locator('#wearProjection').innerText(),'Tyre data unavailable');
  assert.equal(await page.locator('#strategyWarning').innerText(),'Tyre data unavailable');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  assert.deepEqual(errors,[]);
  results.push({width,height,driver_call:'pass',details_hidden_until_opened:'pass',disclosure_survives_updates:'pass',observed_compound_rule:'pass',wear_rain_damage_and_pit_warnings:'pass',current_missing_and_expired_damage_packets:'pass',stale_data:'pass',overflow:false});
  await context.close();page=null;
 }}catch(e){if(page)await page.screenshot({path:path.join(output,'failure.png')}).catch(()=>{});throw e;}
 finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
 console.log(JSON.stringify(results,null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
