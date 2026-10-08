/* Populated analysis geometry: long recorded options, caveats and event data. */
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const out=path.resolve(process.env.PITBOX_LAP_LAYOUT_EVIDENCE||path.join(root,'.codex-ui-test-data/lap-lab-layout'));
(async()=>{
 fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({...(process.env.PITBOX_BROWSER_CHANNEL?{channel:process.env.PITBOX_BROWSER_CHANNEL}:{}),headless:true});
 const results=[];
 try{for(const width of [390,800,1366]){
  const page=await browser.newPage({viewport:{width,height:1000}}),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.origin!=='http://pitbox.test')return route.abort();
   if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(root,'static/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')});
   const file=path.resolve(root,'.'+url.pathname);
   return file.startsWith(path.join(root,'static')+path.sep)&&fs.existsSync(file)?route.fulfill({path:file}):route.abort();
  });
  await page.goto('http://pitbox.test');
  await page.evaluate(()=>{
   document.getElementById('bootOverlay')?.remove();
   document.querySelectorAll('.page').forEach(el=>{el.hidden=el.id!=='analysis';el.classList.toggle('active',el.id==='analysis');});
   document.querySelectorAll('.analysis-view').forEach(el=>{el.hidden=el.id!=='lap-lab';});
   for(const id of ['candidateLapSelect','referenceLapSelect']){
    const select=document.getElementById(id);select.disabled=false;
    select.replaceChildren(new Option('Suggested · Player · Lap 2 · 1:31.665 · comparable_with_caveats','recorded'));
   }
   document.getElementById('candidateLapMeta').textContent='1:34.338 · MEDIUM · 100%';
   document.getElementById('referenceLapMeta').textContent='1:31.665 · same track/layout · valid lap · typed trace';
   document.getElementById('comparisonCompatibility').textContent='comparable with caveats reference · fuel loads differ materially';
   document.getElementById('cursorDataTable').innerHTML='<tr><td>throttle</td><td>0.886932373046875</td><td>Unavailable</td><td>observed</td></tr>';
  });
  await page.evaluate(()=>document.fonts.ready);
  const geometry=await page.locator('#lap-lab').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,controls:[...el.querySelectorAll('.comparison-header select,.comparison-header button,.comparison-header .state-chip')].map(node=>({id:node.id,right:node.getBoundingClientRect().right,left:node.getBoundingClientRect().left,width:node.clientWidth,scroll:node.scrollWidth,height:node.getBoundingClientRect().height}))}));
  await page.locator('.comparison-header').screenshot({path:path.join(out,`recorded-header-${width}.png`)});
  results.push({view:'lap-lab',width,geometry,errors});
  assert(geometry.scroll<=geometry.width+1,`Populated Lap Lab overflows at ${width}: ${geometry.scroll}`);
  assert(geometry.controls.every(control=>control.left>=0&&control.right<=width),JSON.stringify(geometry.controls));
  assert.equal(await page.locator('#comparisonCompatibility').innerText(),'comparable with caveats reference · fuel loads differ materially');
  await page.addScriptTag({content:fs.readFileSync(path.join(root,'static/js/workspaces.js'),'utf8')
   .replace(/^const HAS_DOM =[^\n]+/m,'const HAS_DOM = false;').replace(/^export \{[^\n]+\};?\s*$/m,'')+
   '\nwindow.recordingQA={state,renderComparison,analyzeLapAlone}; drawComparisonTrace=()=>{}; drawComparisonMap=()=>{}; updateInstruments=()=>{};'});
  await page.route('**/api/v1/laps/corrected/analysis',route=>route.fulfill({json:{lap_number:4,lap_time_ms:92000,coverage_ratio:0,metric_coverage:{speed:1},top_speed_kph:null,minimum_speed_kph:null,segments:[]}}));
  await page.evaluate(async()=>{
   recordingQA.state.candidateLapId='corrected';
   recordingQA.state.laps=[{id:'corrected',coverage_ratio:0,engineering_json:JSON.stringify({telemetry_timing_mismatch:true})}];
   recordingQA.state.lapTrace={coverage:1};recordingQA.renderComparison();await recordingQA.analyzeLapAlone();
  });
  assert.equal(await page.locator('#traceCoverage').innerText(),'Lap coverage unverified');
  assert.equal(await page.locator('#traceCoverage').getAttribute('data-state'),'warning');
  assert.match(await page.locator('#lapLabStatus').innerText(),/Official lap timing was corrected from session history.*Partial recording/);
  assert.equal(await page.locator('#lapLabStatus').getAttribute('data-tone'),'warning');
  assert.doesNotMatch(await page.locator('#soloAnalysisSummary').innerText(),/0 kph|0%/);
  const qualityGeometry=await page.locator('#lap-lab').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth}));
  assert(qualityGeometry.scroll<=qualityGeometry.width+1);
  await page.locator('#lapLabStatus').screenshot({path:path.join(out,`corrected-recording-warning-${width}.png`)});
  results.push({view:'corrected-recording',width,geometry:qualityGeometry,errors});
  await page.evaluate(()=>{
   document.getElementById('lap-lab').hidden=true;document.getElementById('review').hidden=false;
   const event=document.createElement('div');event.className='card';
   const body=document.createElement('div');body.className='small muted';
   body.textContent=JSON.stringify({vehicle_idx:10,speed_kph:308.8,overall_fastest:false,driver_fastest:false,session_fastest_vehicle_idx:3,session_fastest_speed_kph:318.2});
   event.append(body);document.getElementById('historyEvents').append(event);
   document.getElementById('reviewLaps').innerHTML='<table class="table"><tr><th>Track</th><th>Lap</th><th>Time</th><th>Tyre</th></tr><tr><td>Singapore</td><td>16</td><td>2:07.972</td><td>HARD</td></tr></table>';
  });
  const history=await page.locator('#review').evaluate(el=>({width:el.clientWidth,scroll:el.scrollWidth,payload:document.querySelector('#historyEvents .card').textContent,controls:[...el.querySelectorAll('.controls > *')].map(node=>({right:node.getBoundingClientRect().right,left:node.getBoundingClientRect().left}))}));
  results.push({view:'history',width,geometry:history,errors});
  await page.locator('#review').screenshot({path:path.join(out,`recorded-history-${width}.png`)});
  assert(history.scroll<=history.width+1,`Populated History overflows at ${width}: ${history.scroll}`);
  if(width<=1024)assert(history.controls.every(control=>control.left>=0&&control.right<=width));
  assert.equal(JSON.parse(history.payload).session_fastest_speed_kph,318.2,'Wrapping preserves the recorded event payload');
  assert.deepEqual(errors,[]);
  await page.close();
 }}finally{await browser.close();fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));}
 console.log(JSON.stringify({status:'passed',cases:results.length,output:out}));
})().catch(error=>{console.error(error);process.exitCode=1;});
