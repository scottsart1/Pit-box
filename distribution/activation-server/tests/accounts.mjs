import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import test from 'node:test';
const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const schema = await readFile(new URL('../migrations/0009_app_accounts.sql', import.meta.url), 'utf8');
function fixture() {
  const db = new DatabaseSync(':memory:'); db.exec('PRAGMA foreign_keys = ON'); db.exec(schema);
  const sent = [];
  function execute(item) {
    const s = db.prepare(item.sql);
    return s.columns().length ? {success:true, results:s.all(...item.values)} : {success:true, results:[], meta:s.run(...item.values)};
  }
  const env = {ACCOUNT_PEPPER:'test-pepper-with-more-than-32-bytes-of-entropy', ACCOUNT_FROM:'verify@example.test',
    ACCOUNT_EMAIL:{send: async message => { sent.push(message); return {messageId:randomUUID()}; }},
    OWNER_DASHBOARD_TOKEN:'owner-key-test-with-at-least-32-characters', DOWNLOAD_REPORT_TOKEN:'different-report-key-with-32-characters',
    OWNER_RATE_LIMITER:{limit:async()=>({success:true})}, DB:{
      prepare(sql) {return {sql,values:[],bind(...values){this.values=values;return this;},async first(){return execute(this).results[0] ?? null;},async run(){return execute(this);}};},
      async batch(items){db.exec('BEGIN');try {const results=items.map(execute);db.exec('COMMIT');return results;}catch(e){db.exec('ROLLBACK');throw e;}}
    }};
  return {db,env,sent};
}
const base = () => ({email:'driver@example.test', device_secret:'a'.repeat(64), pin:'0427', launch_id:randomUUID(), version:'5.7.1-android.44', share_starts:true});
const send = (env,path,body,headers={}) => worker.fetch(new Request('https://local.test/account/'+path, {method:'POST',headers:{'content-type':'application/json',...headers},body:JSON.stringify(body)}),env);
async function enroll(f,data=base()) {
  const challenge=await send(f.env,'challenge',{email:data.email,device_secret:data.device_secret});
  assert.equal(challenge.status,200); const {challenge_id}=await challenge.json();
  const code=f.sent.at(-1).text.match(/code is (\d{6})/)[1];
  const body={...data,challenge_id,code}; const response=await send(f.env,'verify',body);
  assert.equal(response.status,200); return {data,body,account:(await response.json()).account_id};
}
test('verified account and device unlock; raw email, PIN, code and device secret are not stored',async()=>{
  const f=fixture(); const {data,account}=await enroll(f);
  assert.equal((await send(f.env,'unlock',data)).status,200);
  const stored=JSON.stringify(f.db.prepare('SELECT * FROM app_accounts').all());
  for(const secret of [data.email,data.pin,data.device_secret]) assert.ok(!stored.includes(secret));
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_challenges').get().n,0);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_starts').get().n,1,'retry deduplicates launch');
  assert.equal((await send(f.env,'unlock',{...data,launch_id:randomUUID()})).status,200);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_starts WHERE account_id=?').get(account).n,2);
  f.db.close();
});
test('unknown/unverified devices cannot unlock even with correct PIN',async()=>{
  const f=fixture(),{data}=await enroll(f);
  assert.equal((await send(f.env,'unlock',{...data,device_secret:'b'.repeat(64)})).status,401);
  assert.equal((await send(f.env,'unlock',{...data,email:'nobody@example.test'})).status,401);
  f.db.close();
});
test('parallel bad PIN requests share atomic account budget and successful starts do not exhaust it',async()=>{
  const f=fixture(),{data}=await enroll(f);
  for(let i=0;i<8;i++) assert.equal((await send(f.env,'unlock',{...data,launch_id:randomUUID()})).status,200);
  const responses=await Promise.all(Array.from({length:12},()=>send(f.env,'unlock',{...data,pin:'1234'})));
  assert.equal(responses.filter(r=>r.status===401).length,5);
  assert.equal(responses.filter(r=>r.status===429).length,7);
  assert.equal((await send(f.env,'unlock',data)).status,429); f.db.close();
});
test('email challenge is device-bound, expires, permits five guesses, and cannot be replayed',async()=>{
  const f=fixture(),{body}=await enroll(f);
  assert.equal((await send(f.env,'verify',body)).status,401);
  const data=base(); const c=await (await send(f.env,'challenge',{email:data.email,device_secret:data.device_secret})).json();
  const code=f.sent.at(-1).text.match(/code is (\d{6})/)[1];
  const valid={...data,challenge_id:c.challenge_id,code};
  assert.equal((await send(f.env,'verify',{...valid,device_secret:'b'.repeat(64)})).status,401);
  for(let i=0;i<5;i++) assert.equal((await send(f.env,'verify',{...valid,code:code==='000000'?'000001':'000000'})).status,401);
  assert.equal((await send(f.env,'verify',valid)).status,401);
  f.db.prepare('UPDATE account_challenges SET attempts=0, expires_at=0').run();
  assert.equal((await send(f.env,'verify',valid)).status,401);f.db.close();
});
test('recovery preserves account identity and revokes previously registered devices',async()=>{
  const f=fixture(),first=await enroll(f);
  const second=await enroll(f,{...first.data,device_secret:'b'.repeat(64),pin:'9081',launch_id:randomUUID()});
  assert.equal(first.account,second.account);
  assert.equal((await send(f.env,'unlock',{...first.data,pin:'9081'})).status,401);
  assert.equal((await send(f.env,'unlock',second.data)).status,200); f.db.close();
});
test('declining account analytics does not prevent unlock or create starts; owner-only report is separate',async()=>{
  const f=fixture(),{data}=await enroll(f,{...base(),share_starts:false});
  assert.equal((await send(f.env,'unlock',data)).status,200);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_starts').get().n,0);
  const report=token=>worker.fetch(new Request('https://local.test/owner/accounts',{headers:{authorization:'Bearer '+token}}),f.env);
  assert.equal((await report(f.env.DOWNLOAD_REPORT_TOKEN)).status,401);
  const result=await report(f.env.OWNER_DASHBOARD_TOKEN);assert.equal(result.status,200);
  assert.equal((await result.json()).summary.starts,0);f.db.close();
});
test('missing service config and send failures fail closed; unexpected fields and browser origins rejected',async()=>{
  const f=fixture(),data=base();
  assert.equal((await send({...f.env,ACCOUNT_PEPPER:''},'unlock',data)).status,503);
  assert.equal((await send(f.env,'unlock',{...data,api_key:'should-never-be-sent'})).status,400);
  assert.equal((await send(f.env,'unlock',data,{origin:'https://evil.example'})).status,403);
  assert.equal((await send(f.env,'unlock',{...data,pin:'１２３４'})).status,400);
  f.env.ACCOUNT_EMAIL.send=async()=>{throw new Error('sender unavailable');};
  assert.equal((await send(f.env,'challenge',{email:data.email,device_secret:data.device_secret})).status,503);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_challenges').get().n,0);f.db.close();
});
test('scheduled cleanup removes expired security counters and starts beyond 180 days',async()=>{
  const f=fixture();await enroll(f);
  f.db.exec("UPDATE account_starts SET started_at=0; INSERT INTO account_limits VALUES ('expired',1,0);");
  // Existing anonymous cleanup needs its existing tables, kept separate.
  f.db.exec(await readFile(new URL('../migrations/0007_optional_usage.sql',import.meta.url),'utf8'));
  const work=[];await worker.scheduled({},f.env,{waitUntil:p=>work.push(p)});await Promise.all(work);
  assert.equal(f.db.prepare('SELECT COUNT(*) n FROM account_starts').get().n,0);
  assert.equal(f.db.prepare("SELECT COUNT(*) n FROM account_limits WHERE id='expired'").get().n,0);f.db.close();
});

test('adding a device with the same PIN preserves both devices and links launches to one account',async()=>{
  const f=fixture(),first=await enroll(f);
  const second=await enroll(f,{...first.data,device_secret:'b'.repeat(64),launch_id:randomUUID()});
  assert.equal(first.account,second.account);
  assert.equal((await send(f.env,'unlock',first.data)).status,200);
  assert.equal((await send(f.env,'unlock',second.data)).status,200);
  assert.equal(f.db.prepare('SELECT COUNT(DISTINCT account_id) n FROM account_starts').get().n,1);f.db.close();
});

test('optional analytics outages cannot block a valid unlock',async()=>{
  const f=fixture(),{data}=await enroll(f);
  f.db.exec('DROP TABLE account_starts');
  assert.equal((await send(f.env,'unlock',data)).status,200);f.db.close();
});
test('credential fields reject type coercion and do not accept numeric PINs',async()=>{
  const f=fixture(),data=base();
  for(const mutation of [{pin:1234},{device_secret:[data.device_secret]},{version:123}]) {
    assert.equal((await send(f.env,'unlock',{...data,...mutation})).status,400);
  }f.db.close();
});
