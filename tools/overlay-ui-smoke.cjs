/* Actual OBS overlay DOM + transport transitions at phone, tablet and desktop sizes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const output=path.resolve(process.env.PITBOX_OVERLAY_EVIDENCE||path.join(root,'.codex-ui-test-data/overlay'));
const html=fs.readFileSync(path.join(root,'static/overlay.html'),'utf8');
const identity='9007199254740993:0:0:1';
const frame=(extra={})=>({connected:true,session_identity:identity,race_control_phase:'green',fia_flag:'green',
  packet_group_freshness:{'1':100,'2':100,'7':100,'10':100},player_position:4,live_delta_s:0,
  tyre:{compound:'MEDIUM',age_laps:0,wear:[0,10,null,20]},fuel_laps_delta:0,
  strategy:{available:true,recommended:{instruction:'BOX NOW for HARD',projected_finish_position:3,projected_finish_wear_pct:40}},
  radio_log:[{role:'engineer',text:'Box this lap.'}],...extra});
const red=()=>frame({race_control_phase:'red_flag',strategy:{...frame().strategy,red_flag_restart:{active:true,session_identity:identity,
  primary:{instruction:'Fit a fresh MEDIUM set during suspension; stop lap 20 for HARD.'},
  alternative:{instruction:'Fit fresh HARD during suspension and run to the finish.'}}}});
(async()=>{
  fs.mkdirSync(output,{recursive:true});const results=[];
  const browser=await chromium.launch({headless:true,...(process.env.PITBOX_BROWSER_CHANNEL?{channel:process.env.PITBOX_BROWSER_CHANNEL}:{})});
  try{
    for(const [width,height] of [[390,844],[800,1280],[1366,900]]){
      const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage(),errors=[];
      page.on('pageerror',e=>errors.push(e.message));
      await context.addInitScript(()=>{
        window.qaClock=101000;Date.now=()=>window.qaClock;
        window.qaSockets=[];window.WebSocket=class {constructor(){window.qaSockets.push(this);}};
      });
      let pendingRest;
      await page.route('**/*',route=>{
        const url=new URL(route.request().url());
        if(url.pathname==='/overlay')return route.fulfill({contentType:'text/html',body:html});
        if(url.pathname==='/api/state'){pendingRest=route;return;}
        if(url.pathname==='/static/driver-dashboard/model.mjs')return route.fulfill({contentType:'text/javascript',path:path.join(root,'static/driver-dashboard/model.mjs')});
        return route.abort();
      });
      await page.goto('http://pitbox.test/overlay');
      await page.waitForFunction(()=>window.qaSockets.length===1);
      const send=state=>page.evaluate(s=>window.qaSockets.at(-1).onmessage({data:JSON.stringify(s)}),state);
      const texts=()=>page.evaluate(()=>Object.fromEntries(['status','pos','delta','tyre','wear','fuel','strat','alternative','strategyNotice','stratMeta','radio'].map(id=>[id,document.getElementById(id).textContent])));
      const capture=async name=>{
        assert.deepEqual(errors,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
        await page.screenshot({path:path.join(output,`${name}-${width}.png`),omitBackground:true});
        results.push({width,height,case:name,output:await texts(),errors:[...errors]});
      };
      // REST must paint without a socket frame, but cannot later overwrite one.
      while(!pendingRest)await page.waitForTimeout(10);
      await pendingRest.fulfill({contentType:'application/json',body:JSON.stringify(frame())});
      await page.waitForFunction(()=>document.getElementById('pos').textContent==='P4');
      assert.equal((await texts()).delta,'+0.00');assert.equal((await texts()).fuel,'+0.0 laps');assert.equal((await texts()).wear,'0% 10% — 20%');
      assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgba(0, 0, 0, 0)');
      await capture('rest-live-zero');
      await send(frame({packet_group_freshness:{}}));
      for(const id of ['pos','delta','tyre','wear','fuel'])assert.equal((await texts())[id],'—');
      assert.equal((await texts()).strat,'Current strategy unavailable');await capture('missing-packets');
      await send(red());assert.match((await texts()).strat,/fresh MEDIUM set/);assert.equal((await texts()).radio,'—');await capture('red-current');
      await send({...red(),connected:false,telemetry_stale:true});
      assert.equal((await texts()).status,'Last confirmed · provisional');assert.match((await texts()).alternative,/HARD/);
      for(const id of ['pos','delta','tyre','wear','fuel'])assert.equal((await texts())[id],'—');
      assert.match((await texts()).strategyNotice,/Confirm the suspension, tyre sets and track conditions/);await capture('red-stale');
      await send({...red(),connected:false,session_identity:'9007199254740992:0:0:1'});
      assert.equal((await texts()).alternative,'');assert.equal((await texts()).strat,'Current strategy unavailable');await capture('red-wrong-session');
      await send(frame({radio_log:[]}));assert.equal((await texts()).alternative,'');assert.equal((await texts()).radio,'—');await capture('restart');
      await page.evaluate(()=>window.qaSockets.at(-1).onclose());
      assert.equal((await texts()).status,'Telemetry unavailable');assert.equal((await texts()).pos,'—');await capture('socket-loss');
      await send(red());await page.evaluate(()=>window.qaClock+=3600);
      await page.waitForFunction(()=>document.getElementById('status').textContent==='Last confirmed · provisional');await capture('silent-expiry');
      await page.evaluate(()=>window.qaClock=101000);
      for(const connected of [true,false]){
        await send({...red(),connected,final_classification:{position:1,laps:31}});
        assert.equal((await texts()).pos,'P1');assert.equal((await texts()).status,'Session complete · P1');
        assert.match((await texts()).strat,/^Session complete/);assert.equal((await texts()).alternative,'');assert.equal((await texts()).radio,'—');
        for(const id of ['delta','tyre','wear','fuel'])assert.equal((await texts())[id],'—');
        assert.equal(await page.locator('#strategyProjection').isVisible(),false);await capture(`finish-${connected?'fresh':'stale'}`);
      }
      await send(frame({session_identity:'9007199254740994:0:0:2',final_classification:{},radio_log:[]}));
      assert.equal((await texts()).pos,'P4');assert.equal((await texts()).radio,'—');await capture('new-session');
      // Delayed REST is an old snapshot, and must lose to a newer socket state.
      pendingRest=null;await page.goto('http://pitbox.test/overlay?bg=1');await page.waitForFunction(()=>window.qaSockets.length===1);
      await send(frame({player_position:2,radio_log:[]}));
      while(!pendingRest)await page.waitForTimeout(10);
      await pendingRest.fulfill({contentType:'application/json',body:JSON.stringify(frame({player_position:9}))});
      await page.waitForTimeout(50);assert.equal((await texts()).pos,'P2');
      assert.equal(await page.locator('body').evaluate(el=>getComputedStyle(el).backgroundColor),'rgb(7, 11, 15)');await capture('late-rest-and-solid-background');
      await context.close();
    }
    fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({passed:true,cases:results.length,results},null,2));
    console.log(`Overlay browser checks passed: ${results.length} cases.`);
  }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
