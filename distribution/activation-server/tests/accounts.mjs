import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import test from 'node:test';
const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const schema = (await Promise.all(['0009_app_accounts.sql','0010_driver_profiles.sql'].map(f => readFile(new URL('../migrations/'+f, import.meta.url), 'utf8')))).join('\n');
function fixture() {
  const db = new DatabaseSync(':memory:'); db.exec('PRAGMA foreign_keys = ON'); db.exec(schema);
  function execute(item) { const s = db.prepare(item.sql); return s.columns().length ? {success:true,results:s.all(...item.values)} : {success:true,results:[],meta:s.run(...item.values)}; }
  const env = {ACCOUNT_PEPPER:'test-pepper-with-more-than-32-bytes-of-entropy',
    OWNER_DASHBOARD_TOKEN:'owner-key-test-with-at-least-32-characters', DOWNLOAD_REPORT_TOKEN:'different-report-key-with-32-characters',
    OWNER_RATE_LIMITER:{limit:async()=>({success:true})}, DB:{
      prepare(sql) {return {sql,values:[],bind(...values){this.values=values;return this;},async first(){return execute(this).results[0] ?? null;},async run(){return execute(this);}};},
      async batch(items){db.exec('BEGIN');try {const results=items.map(execute);db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}
    }};
  return {db,env};
}
const identity = () => ({account_id:randomUUID(),device_secret:'a'.repeat(64)});
const event = auth => ({...auth,event_id:randomUUID(),occurred_at:Math.floor(Date.now()/1000),version:'5.7.1-android.44'});
const send = (env,path,body,headers={}) => worker.fetch(new Request('https://local.test/account/'+path, {method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)}),env);
const report = (f, token=f.env.OWNER_DASHBOARD_TOKEN) => worker.fetch(new Request('https://local.test/owner/accounts',{headers:{authorization:'Bearer '+token}}),f.env);
async function register(f,auth=identity()) {assert.equal((await send(f.env,'profile',auth)).status,200);return auth;}
test('new profiles need neither email nor PIN; credentials never enter stored sign-ins',async()=>{
  const f=fixture(),auth=await register(f),data=event(auth);
  assert.equal((await send(f.env,'sign-in',data)).status,200);
  const stored=JSON.stringify(f.db.prepare('SELECT * FROM driver_profiles').all());
  assert.ok(!stored.includes(auth.device_secret));
  const row=f.db.prepare('SELECT * FROM profile_sign_ins').get();
  assert.deepEqual(Object.keys(row).sort(),['account_id','event_id','occurred_at','received_at','version']);
  assert.equal(row.account_id,auth.account_id);f.db.close();
});
test('all successful sign-ins are recorded, retry IDs are deduplicated, and profiles aggregate separately',async()=>{
  const f=fixture(),a=await register(f),b=await register(f),data=event(a);
  for(let i=0;i<3;i++) assert.equal((await send(f.env,'sign-in',data)).status,200);
  assert.equal((await send(f.env,'sign-in',event(a))).status,200);
  assert.equal((await send(f.env,'sign-in',event(b))).status,200);
  const result=await (await report(f)).json();assert.equal(result.metric,'profile_sign_ins');
  assert.deepEqual(result.summary,{accounts:2,starts:3});
  assert.equal(result.accounts.find(p=>p.account_id===a.account_id).starts,2);f.db.close();
});
test('registration is idempotent and cannot replace another profile credential',async()=>{
  const f=fixture(),auth=await register(f),other={...auth,device_secret:'b'.repeat(64)};
  assert.equal((await send(f.env,'profile',auth)).status,200);
  assert.equal((await send(f.env,'profile',other)).status,401);
  assert.equal((await send(f.env,'sign-in',event(other))).status,401);
  assert.equal((await send(f.env,'sign-in',event(auth))).status,200);f.db.close();
});
test('random profile ID alone cannot report sign-ins; unknown profiles cannot report',async()=>{
  const f=fixture(),auth=identity();
  assert.equal((await send(f.env,'sign-in',event(auth))).status,401);
  await register(f,auth);const data=event(auth);delete data.device_secret;
  assert.equal((await send(f.env,'sign-in',data)).status,400);f.db.close();
});
test('both endpoints explicitly reject email, PIN, names and API keys',async()=>{
  const f=fixture(),auth=await register(f);
  for(const key of ['email','pin','name','api_key','share_starts']) for(const route of ['profile','sign-in'])
    assert.equal((await send(f.env,route,{...(route==='profile'?auth:event(auth)),[key]:'never-send'})).status,400);
  f.db.close();
});
test('offline events retain event time; stale events are acknowledged but not retained',async()=>{
  const f=fixture(),auth=await register(f),old=event(auth);old.occurred_at-=7*86400;
  assert.equal((await send(f.env,'sign-in',old)).status,200);
  assert.equal(f.db.prepare('SELECT occurred_at FROM profile_sign_ins').get().occurred_at,old.occurred_at);
  const expired={...event(auth),occurred_at:0};const response=await send(f.env,'sign-in',expired);
  assert.equal(response.status,200);assert.equal((await response.json()).expired,true);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM profile_sign_ins').get().n,1);f.db.close();
});
test('outage returns retryable error instead of acknowledging missing records',async()=>{
  const f=fixture(),auth=await register(f);f.db.exec('DROP TABLE profile_sign_ins');
  assert.equal((await send(f.env,'sign-in',event(auth))).status,503);
  assert.equal((await report(f)).status,503);f.db.close();
});
test('owner report excludes download-report key and never returns device credentials',async()=>{
  const f=fixture(),auth=await register(f);await send(f.env,'sign-in',event(auth));
  assert.equal((await report(f,f.env.DOWNLOAD_REPORT_TOKEN)).status,401);
  const response=await report(f);assert.equal(response.status,200);
  const body=await response.text();assert.ok(!body.includes('device_hash'));assert.ok(!body.includes(auth.device_secret));f.db.close();
});
test('types, sizes, origins, future timestamps and removed verification routes fail closed',async()=>{
  const f=fixture(),auth=await register(f),data=event(auth);
  for(const mutation of [{device_secret:[auth.device_secret]},{occurred_at:'123'},{occurred_at:Date.now()},{version:1},{event_id:'../driver'}])
    assert.equal((await send(f.env,'sign-in',{...data,...mutation})).status,400);
  assert.equal((await send(f.env,'profile',auth,{origin:'https://evil.example'})).status,403);
  assert.equal((await send(f.env,'profile',{...auth,extra:'x'.repeat(3000)})).status,400);
  for(const route of ['challenge','verify','unlock']) assert.equal((await send(f.env,route,auth)).status,404);
  assert.equal((await send({...f.env,ACCOUNT_PEPPER:''},'profile',auth)).status,503);f.db.close();
});
test('concurrent duplicate sign-ins cannot inflate totals or cross profile ownership',async()=>{
  const f=fixture(),a=await register(f),b=await register(f),data=event(a);
  const responses=await Promise.all(Array.from({length:12},()=>send(f.env,'sign-in',data)));
  assert.ok(responses.every(r=>r.status===200));
  assert.equal((await send(f.env,'sign-in',{...data,...b})).status,409);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM profile_sign_ins').get().n,1);f.db.close();
});
test('registration has an atomic per-IP budget without limiting idempotent retries',async()=>{
  const f=fixture(),auth=await register(f);
  for(let i=0;i<19;i++) await register(f);
  assert.equal((await send(f.env,'profile',identity())).status,429);
  assert.equal((await send(f.env,'profile',auth)).status,200);f.db.close();
});
test('scheduled retention prunes old sign-ins and expired rate limits',async()=>{
  const f=fixture(),auth=await register(f);await send(f.env,'sign-in',event(auth));
  f.db.exec("UPDATE profile_sign_ins SET occurred_at=0; INSERT INTO account_limits VALUES ('expired',1,0)");
  f.db.exec(await readFile(new URL('../migrations/0007_optional_usage.sql',import.meta.url),'utf8'));
  const work=[];await worker.scheduled({},f.env,{waitUntil:p=>work.push(p)});await Promise.all(work);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM profile_sign_ins').get().n,0);
  assert.equal(f.db.prepare("SELECT COUNT(*) n FROM account_limits WHERE id='expired'").get().n,0);f.db.close();
});
