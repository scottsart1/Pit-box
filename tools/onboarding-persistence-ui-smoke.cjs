/* Real walkthrough behavior across loopback origins; no keys, mic or backend writes. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'');
const script=fs.readFileSync(path.join(root,'static/js/onboarding.js'),'utf8');
const KEY='pitwall.onboarding.v1';
(async()=>{
 const browser=await chromium.launch({headless:true,...(process.env.PITBOX_BROWSER_CHANNEL?{channel:process.env.PITBOX_BROWSER_CHANNEL}:{})});
 const context=await browser.newContext(),requests=[],writes=[],errors=[];
 let port=38100;const pages=[];
 const view=async(native,options={})=>{
  const page=await context.newPage();pages.push(page);page.on('pageerror',error=>errors.push(error.message));
  const url=`http://127.0.0.1:${++port}`;
  await page.route('**/*',route=>{
   const request=route.request(),target=new URL(request.url());
   if(target.pathname==='/')return route.fulfill({contentType:'text/html',body:html});
   if(target.pathname.startsWith('/api/')){
    if(request.method()!=='GET')writes.push({path:target.pathname,method:request.method()});
    return route.fulfill({json:target.pathname==='/api/state'?{connected:false}: {configured:false}});
   }
   const file=path.resolve(root,'.'+target.pathname);
   if(!file.startsWith(path.join(root,'static')+path.sep)||!fs.existsSync(file))return route.abort();
   return route.fulfill({path:file});
  });
  if(native){
   await page.exposeBinding('qaPreference',async(_,serialized)=>{
    const request=JSON.parse(serialized);requests.push(request);
    if(request.op==='onboarding_load'){
     if(options.hold)await options.hold;
     return {...request,ok:!options.reject,value:options.invalid?'invalid':native.value};
    }
    assert.equal(request.op,'onboarding_save');assert(['finished','skipped'].includes(request.value));
    native.value=request.value;return {...request,ok:true};
   });
   await page.addInitScript(()=>{
    window.PitBoxDashboardPreferences={postMessage(serialized){
     window.qaPreference(serialized).then(response=>{
      this.onmessage?.({data:JSON.stringify(response)});
      if(response.op==='onboarding_save'&&response.ok)window.qaSaveAcknowledged=(window.qaSaveAcknowledged||0)+1;
     });
    }};
   });
  }
  await page.goto(url);
  await page.evaluate(({key,local,blockStorage})=>{
   document.getElementById('bootOverlay').classList.add('done');document.getElementById('usagePrompt').hidden=true;document.getElementById('usageToggle').disabled=false;
   if(local)localStorage.setItem(key,local);
   if(blockStorage)Object.defineProperty(window,'localStorage',{get(){throw new Error('Storage blocked');}});
  },{key:KEY,local:options.local,blockStorage:options.blockStorage});
  await page.addScriptTag({content:script});
  return page;
 };
 const isOpen=page=>page.locator('#onboardingDialog').evaluate(dialog=>dialog.open);
 const click=async(page,id)=>page.locator('#'+id).evaluate(button=>button.click());
 try{
  for(const reason of ['skipped','finished']){
   const native={value:null},first=await view(native);
   await first.waitForFunction(()=>document.getElementById('onboardingDialog').open);
   await first.locator('#onboardingKey').fill('unsaved-test-value');
   if(reason==='skipped')await click(first,'onboardingClose');
   else{for(let i=0;i<4;i++)await click(first,'onboardingSkipStep');}
   await first.waitForFunction(()=>document.getElementById('onboardingKey').value==='');
   await first.waitForFunction(()=>window.qaSaveAcknowledged>=1);assert.equal(native.value,reason);
   const next=await view(native);
   await next.waitForFunction(key=>localStorage.getItem(key)!==null,KEY);
   assert.equal(await isOpen(next),false,'A new port restores the app-owned dismissal');
   assert.equal(await next.evaluate(key=>localStorage.getItem(key),KEY),reason);
   await click(next,'onboardingRestart');assert.equal(await isOpen(next),true,'Settings can still reopen the walkthrough');
   await click(next,'onboardingClose');await first.close();await next.close();
  }
  const migrated={value:null},migration=await view(migrated,{local:'skipped'});
  await migration.waitForFunction(()=>document.getElementById('onboardingSavedStatus').textContent.includes('already viewed'));
  await migration.waitForFunction(()=>window.qaSaveAcknowledged>=1);assert.equal(migrated.value,'skipped');assert.equal(await isOpen(migration),false);await migration.close();
  let release;const held=new Promise(resolve=>release=resolve),delayed=await view({value:'finished'},{hold:held});
  await new Promise(resolve=>setTimeout(resolve,100));assert.equal(await isOpen(delayed),false,'No flash of onboarding before native restoration');
  release();await delayed.waitForFunction(key=>localStorage.getItem(key)==='finished',KEY);assert.equal(await isOpen(delayed),false);await delayed.close();
  for(const mode of ['reject','invalid']){
   const native={value:'finished'},start=requests.length,page=await view(native,{local:'skipped',[mode]:true});
   await new Promise(resolve=>setTimeout(resolve,100));assert.equal(await isOpen(page),false);assert.equal(native.value,'finished');
   assert(!requests.slice(start).some(request=>request.op==='onboarding_save'),'Failed/invalid reads must not overwrite native state');await page.close();
  }
  const blocked=await view({value:'finished'},{blockStorage:true});
  await blocked.waitForFunction(()=>document.getElementById('onboardingSavedStatus').textContent.includes('already viewed'));assert.equal(await isOpen(blocked),false);await blocked.close();
  const browserOnly=await view(null,{local:'skipped'});assert.equal(await isOpen(browserOnly),false);await browserOnly.close();
  const firstBrowser=await view(null);await firstBrowser.waitForFunction(()=>document.getElementById('onboardingDialog').open);
  await click(firstBrowser,'onboardingClose');assert.equal(await firstBrowser.evaluate(key=>localStorage.getItem(key),KEY),'skipped');await firstBrowser.close();
  let resume;const waiting=new Promise(resolve=>resume=resolve),native={value:null},manual=await view(native,{hold:waiting});
  await click(manual,'onboardingRestart');await click(manual,'onboardingClose');resume();
  await manual.waitForFunction(()=>window.qaSaveAcknowledged>=1);assert.equal(native.value,'skipped');assert.equal(await isOpen(manual),false);await manual.close();
  assert(!JSON.stringify(requests).includes('unsaved-test-value'),'The native preference channel must never receive entered keys');
  assert.deepEqual(writes,[],'Opening/skipping/migrating the guide changes no app settings');assert.deepEqual(errors,[]);
  console.log(JSON.stringify({status:'passed',cases:10,checks:'finished/skipped across ports; explicit reopen; migrate existing flag; delayed restore; rejected/invalid reads; blocked browser storage; browser fallback; explicit dismissal during restore; no secret/settings writes'}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
