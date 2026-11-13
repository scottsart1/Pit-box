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
  assert.deepEqual(result.summary,{accounts:2,starts:3,returning:0,new_accounts:2});
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
const ago = (data, days) => ({...data, event_id: randomUUID(), occurred_at: data.occurred_at - days * 86400});
const history = (f, id, token = f.env.OWNER_DASHBOARD_TOKEN) =>
  worker.fetch(new Request('https://local.test/owner/accounts/' + id, {headers: {authorization: 'Bearer ' + token}}), f.env);
test('the owner report follows each profile: returning, new, platform and latest version',async()=>{
  const f=fixture(),phone=await register(f),pc=await register(f),once=await register(f);
  const today=event(phone);
  for(const data of [today, ago(today,3), ago(today,3), ago(today,40)]) assert.equal((await send(f.env,'sign-in',data)).status,200);
  const desktop={...event(pc),version:'5.7.1-windows'};
  for(const data of [desktop, ago(desktop,1)]) assert.equal((await send(f.env,'sign-in',data)).status,200);
  assert.equal((await send(f.env,'sign-in',event(once))).status,200);
  const result=await (await report(f)).json();
  // Phone: 3 sign-ins on 2 days in 30 days, plus one older; PC: 2 days; the third once.
  assert.deepEqual(result.summary,{accounts:3,starts:6,returning:2,new_accounts:2});
  assert.equal(result.window_days,30);
  const byId=Object.fromEntries(result.accounts.map(item=>[item.account_id,item]));
  assert.deepEqual([byId[phone.account_id].starts,byId[phone.account_id].starts_30d,byId[phone.account_id].active_days],[4,3,3]);
  assert.equal(byId[phone.account_id].platform,'android');
  assert.equal(byId[pc.account_id].platform,'windows');
  assert.equal(byId[pc.account_id].version,'5.7.1-windows');
  assert.deepEqual(Object.fromEntries(result.platforms.map(item=>[item.platform,[item.accounts,item.starts]])),{android:[2,4],windows:[1,2]});
  assert.ok(!JSON.stringify(result).includes('device'));
  f.db.close();
});
test('sign-in days and active days follow New York time, including daylight saving',async()=>{
  const f=fixture(),a=await register(f),base=event(a),at=iso=>({...base,event_id:randomUUID(),occurred_at:Date.parse(iso)/1000});
  const day=Math.floor(Date.now()/86400000)*86400000-10*86400000,stamp=(offset)=>new Date(day+offset).toISOString();
  // 03:30 and 03:55 UTC fall on the previous New York day in both EST and EDT; 12:00 UTC on the same day.
  for(const iso of [stamp(3.5*3600000),stamp(3.92*3600000),stamp(12*3600000)]) assert.equal((await send(f.env,'sign-in',at(iso))).status,200);
  const result=await (await report(f)).json(),ny=new Intl.DateTimeFormat('en-CA',{timeZone:'America/New_York'});
  const prev=ny.format(day+3.5*3600000),same=ny.format(day+12*3600000);
  assert.notEqual(prev,same);
  assert.deepEqual(result.daily.map(r=>[r.day,r.starts]),[[same,1],[prev,2]]);
  assert.equal(result.accounts[0].active_days,2);
  assert.equal((await (await history(f,a.account_id)).json()).summary.active_days,2);
  // 2026-03-08 06:59 UTC is still EST (01:59 local); 07:00 UTC is 03:00 EDT. Both are March 8 in New York,
  // and 2026-11-01 05:30 UTC (01:30 EDT) and 06:30 UTC (01:30 EST) are both November 1.
  const db=f.db,days=sql=>db.prepare(sql).all().map(r=>r.d);
  const src=await readFile(new URL('../src/worker.js',import.meta.url),'utf8'),expr=src.match(/const EASTERN_SIGN_IN_DAY = `([\s\S]*?)`;/)[1];
  const q=t=>db.prepare(`SELECT ${expr} AS d FROM (SELECT ? AS occurred_at)`).get(Date.parse(t)/1000).d;
  assert.deepEqual(['2026-03-08T04:59:59Z','2026-03-08T05:00:00Z','2026-03-08T06:59:59Z','2026-03-08T07:00:00Z','2026-11-01T03:59:59Z','2026-11-01T04:00:00Z','2026-11-02T04:59:59Z','2026-11-02T05:00:00Z'].map(q),
    ['2026-03-07','2026-03-08','2026-03-08','2026-03-08','2026-10-31','2026-11-01','2026-11-01','2026-11-02']);
  f.db.close();
});
test('one profile\'s sign-in history is newest first, owner-only and never another profile\'s',async()=>{
  const f=fixture(),a=await register(f),b=await register(f),data=event(a);
  for(const item of [ago(data,2), data, ago(data,1)]) assert.equal((await send(f.env,'sign-in',item)).status,200);
  assert.equal((await send(f.env,'sign-in',event(b))).status,200);
  const response=await history(f,a.account_id);assert.equal(response.status,200);
  const result=await response.json();
  assert.equal(result.metric,'profile_sign_in_history');
  assert.equal(result.account_id,a.account_id);
  assert.deepEqual(result.sign_ins.map(item=>item.occurred_at),[data.occurred_at,data.occurred_at-86400,data.occurred_at-2*86400]);
  assert.deepEqual(result.summary,{starts:3,first_start:data.occurred_at-2*86400,last_start:data.occurred_at,active_days:3});
  assert.ok(result.sign_ins.every(item=>item.platform==='android'&&Number.isSafeInteger(item.received_at)));
  const body=JSON.stringify(result);assert.ok(!body.includes(b.account_id)&&!body.includes('device'));
  assert.equal((await history(f,a.account_id,f.env.DOWNLOAD_REPORT_TOKEN)).status,401);
  assert.equal((await history(f,'not-a-profile')).status,400);
  assert.equal((await worker.fetch(new Request('https://local.test/owner/accounts/'+a.account_id+'?x=1',{headers:{authorization:'Bearer '+f.env.OWNER_DASHBOARD_TOKEN}}),f.env)).status,400);
  const unknown=await (await history(f,randomUUID())).json();
  assert.deepEqual([unknown.summary.starts,unknown.sign_ins.length],[0,0]);
  f.db.close();
});
