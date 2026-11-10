/* The dashboard keeps its strategy when a live frame says it is unchanged.
   The server sends the strategy once per revision and a marker after that;
   the page substitutes the strategy it holds and acknowledges with its
   revision. Shipped index.html, a fake socket, no backend. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),staticRoot=path.join(root,'static');
const instruction='Box lap 14 for HARD.';
const strategy={available:true,confidence:'medium',pit_loss_s:22,neutralisation:{phase:'green'},
  recommended:{instruction,box_lap:14,box_laps:[14],compounds:['MEDIUM','HARD'],stops_remaining:1,feasible:true,legal:true,
    projected_finish_position:6,projected_rejoin_position:9,monte_carlo:{p75_s:3000,uncertainty_s:4}},
  plans:[],compound_rule:{applies:true,dry_count:2},observed_compound_rule:{applies:true,dry_count:1}};
const base={connected:true,telemetry_stale:false,session_uid:7,session_generation:1,track_name:'Monza',
  session_type:'Race',mode_profile:'race',current_lap:10,total_laps:30,player_position:6,player_car_index:0,
  weather:'Clear',tyre:{compound:'MEDIUM',age_laps:9,wear:[20,21,19,20]},drivers:[],radio_log:[],traces:[],
  analysis:{target:{}}};
const stamped=frame=>({...frame,packet_group_freshness:Object.fromEntries(['1','6','7','10'].map(id=>[id,Date.now()/1000]))});

(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await (await browser.newContext({viewport:{width:1280,height:800}})).newPage();
  const errors=[];page.on('pageerror',e=>errors.push(String(e)));
  await page.addInitScript(()=>{window.__sockets=[];window.WebSocket=class{constructor(){this.sent=[];this.readyState=1;window.__sockets.push(this)}
    send(text){this.sent.push(text)}close(){}addEventListener(){}removeEventListener(){}};});
  await page.route('**/*',async route=>{
   const url=new URL(route.request().url());if(url.origin!=='http://pitbox.test')return route.abort();
   if(url.pathname==='/api/v1/usage')return route.fulfill({json:{decided:true,enabled:false}});
   if(url.pathname.startsWith('/api/'))return route.fulfill({json:url.pathname==='/api/state'?stamped(base):{available:false,items:[],settings:[],tracks:[],strategies:[],rivals:[]}});
   const target=url.pathname==='/'?path.join(staticRoot,'index.html'):path.resolve(staticRoot,url.pathname.replace(/^\/static\//,''));
   if(!target.startsWith(staticRoot+path.sep)||!fs.existsSync(target)||fs.statSync(target).isDirectory())return route.abort();
   const type=target.endsWith('.html')?'text/html':/\.m?js$/.test(target)?'text/javascript':target.endsWith('.css')?'text/css':undefined;
   return route.fulfill({path:target,...(type?{contentType:type}:{})});
  });
  await page.goto('http://pitbox.test/');
  const decline=page.getByRole('button',{name:'No thanks',exact:true});if(await decline.isVisible())await decline.click();
  if(await page.locator('#onboardingDialog').isVisible())await page.locator('#onboardingClose').click();
  const deliver=frame=>page.evaluate(f=>{const socket=window.__sockets.at(-1);socket.onmessage({data:JSON.stringify(f)});return socket.sent.at(-1)},stamped(frame));

  // A full frame: the page holds revision 3 and says so.
  let ack=JSON.parse(await deliver({...base,strategy,strategy_revision:3}));
  assert.deepEqual(ack,{ack:1,strategy:3});
  await page.getByRole('tab',{name:'DRIVE',exact:true}).click();
  assert.match(await page.locator('#strategyMain').innerText(),/Box lap 14/);

  // Unchanged: the marker stands in for the strategy, which stays on screen.
  ack=JSON.parse(await deliver({...base,current_lap:11,strategy:{revision:3,unchanged:true},strategy_revision:3}));
  assert.deepEqual(ack,{ack:1,strategy:3});
  assert.match(await page.locator('#strategyMain').innerText(),/Box lap 14/);

  // A marker for a revision the page does not hold: keep what is shown, ask for the strategy.
  ack=JSON.parse(await deliver({...base,strategy:{revision:9,unchanged:true},strategy_revision:9}));
  assert.deepEqual(ack,{ack:1,strategy:null});
  assert.match(await page.locator('#strategyMain').innerText(),/Box lap 14/);

  // A new revision replaces it.
  const moved={...strategy,recommended:{...strategy.recommended,instruction:'Box lap 16 for HARD.',box_lap:16,box_laps:[16]}};
  ack=JSON.parse(await deliver({...base,strategy:moved,strategy_revision:10}));
  assert.deepEqual(ack,{ack:1,strategy:10});
  assert.match(await page.locator('#strategyMain').innerText(),/Box lap 16/);
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({result:'passed'}));
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
