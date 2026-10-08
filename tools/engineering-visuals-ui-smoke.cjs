/* Render A/B charts from saved real UDP acceptance outputs, without a server
   mutation or production data. Supply PITBOX_ENGINEERING_FIXTURE. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..'),fixturePath=process.env.PITBOX_ENGINEERING_FIXTURE;
assert.ok(fixturePath,'Supply the saved engineering-acceptance.py telemetry.json output.');
const fixture=JSON.parse(fs.readFileSync(fixturePath,'utf8'));
assert.equal(fixture.runs.length,2);assert.ok(fixture.comparison.pairs.length>0);
const output=path.resolve(process.env.PITBOX_ENGINEERING_VISUALS_EVIDENCE||'.codex-ui-test-data/engineering-visuals');
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
(async()=>{fs.mkdirSync(output,{recursive:true});const results=[],browser=await chromium.launch({headless:true,...(process.env.PITBOX_BROWSER_CHANNEL?{channel:process.env.PITBOX_BROWSER_CHANNEL}:{})});
try{for(const [width,height]of [[800,1280],[390,844]]){
  const context=await browser.newContext({viewport:{width,height}}),page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());if(url.origin!=='http://pitbox.test')return route.abort();
    if(url.pathname==='/')return route.fulfill({contentType:'text/html',body:html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')});
    const file=path.resolve(root,'.'+url.pathname);if(!file.startsWith(path.join(root,'static')+path.sep)||!fs.existsSync(file))return route.abort();
    return route.fulfill({path:file,contentType:file.endsWith('.mjs')?'text/javascript':undefined});
  });
  await page.goto('http://pitbox.test/');await page.evaluate(async()=>{
    document.getElementById('bootOverlay')?.remove();for(const main of document.querySelectorAll('main.page')){main.hidden=main.id!=='analysis';main.classList.toggle('active',main.id==='analysis');}
    for(const panel of document.querySelectorAll('.analysis-view'))panel.hidden=panel.id!=='test-engineer';
    window.renderVisuals=(await import('/static/js/engineering-visuals.mjs')).comparisonVisuals;
  });
  for(const [scenario,result,selected]of [['matched',fixture.comparison,{a:fixture.runs[0],b:fixture.runs[1]}],['cross-session',fixture.cross_comparison,{a:fixture.runs[0],b:fixture.cross_runs[0]}],...(fixture.stint_comparison?[['stint',fixture.stint_comparison,{a:fixture.runs[0],b:fixture.runs[1]}]]:[])]){
    await page.evaluate(({result,selected})=>{const host=document.getElementById('engineeringComparison');host.replaceChildren(renderVisuals(result,selected));host.scrollIntoView();},{result,selected});
    const counts=await page.locator('.engineering-pace-dot').evaluateAll(dots=>dots.reduce((sum,dot)=>sum+Number(dot.dataset.count),0));
    assert.equal(counts,result.mode==='setup'?result.pairs.length*2:result.clean_lap_counts.a+result.clean_lap_counts.b);
    const sectors=await page.locator('.engineering-chart-value').allTextContents();
    assert.equal(sectors[1],(result.sector_deltas_s[1]<0?'−':'+')+Math.abs(result.sector_deltas_s[1]).toFixed(3)+' s');
    const boxes=await page.locator('.engineering-chart').evaluateAll(elements=>elements.map(e=>({width:e.clientWidth,scroll:e.scrollWidth})));
    for(const box of boxes)assert.ok(box.scroll<=box.width+1,`${scenario} chart overflow at ${width}`);
    await page.locator('.engineering-visuals').screenshot({path:path.join(output,`${scenario}-${width}.png`)});
    for(let chart=0;chart<2;chart++)await page.locator('.engineering-chart').nth(chart).screenshot({path:path.join(output,`${scenario}-${width}-chart-${chart}.png`)});
    results.push({width,scenario,counts,sectors,boxes,errors:[...errors]});assert.deepEqual(errors,[]);
  }
  await context.close();
}}finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(results,null,2));}
console.log(JSON.stringify({result:'passed',cases:results.length,output}));})().catch(error=>{console.error(error);process.exitCode=1});
