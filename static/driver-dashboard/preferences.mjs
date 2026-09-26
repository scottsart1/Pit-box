import {cleanPreferences} from './display.mjs';

export const PREFERENCE_STORAGE_KEY='ypb-driver-dashboard-v2';
const PREFERENCE_LIMIT_BYTES=64*1024;

export function decodePreferences(raw){
  if(typeof raw!=='string'||new TextEncoder().encode(raw).length>PREFERENCE_LIMIT_BYTES)return null;
  try{
    const value=JSON.parse(raw);
    return value&&typeof value==='object'&&!Array.isArray(value)?cleanPreferences(value):null;
  }catch{return null}
}

// The Android object is injected only into the current trusted loopback origin.
// It exposes this one preference record, not arbitrary storage or native methods.
export function nativePreferenceClient(bridge,{timeoutMs=4000}={}){
  if(!bridge||typeof bridge.postMessage!=='function')return null;
  const pending=new Map();let sequence=0;
  bridge.onmessage=event=>{
    let response;try{response=JSON.parse(event.data)}catch{return}
    const request=pending.get(response?.id);
    if(!request||response.op!==request.op)return;
    pending.delete(response.id);clearTimeout(request.timer);
    if(response.ok===true)request.resolve(response);
    else request.reject(new Error('Android preference storage rejected the request.'));
  };
  return {
    request(op,value){
      return new Promise((resolve,reject)=>{
        const id='dashboard-'+(++sequence),timer=setTimeout(()=>{
          pending.delete(id);reject(new Error('Android preference storage did not respond.'));
        },timeoutMs);
        pending.set(id,{op,resolve,reject,timer});
        try{bridge.postMessage(JSON.stringify({id,op,...(op==='save'?{value}: {})}))}
        catch(error){clearTimeout(timer);pending.delete(id);reject(error)}
      });
    }
  };
}

export async function restorePreferences({storage=null,bridge=null,timeoutMs=4000}={}){
  let local=null,storageReadable=true;
  try{local=decodePreferences(storage?.getItem(PREFERENCE_STORAGE_KEY))}catch{storageReadable=false}
  const native=nativePreferenceClient(bridge,{timeoutMs});
  let nativeReady=false,issue=null,preferences=local||cleanPreferences(null);
  if(native){
    try{
      const loaded=await native.request('load');
      if(loaded.value===null){
        // Only a confirmed absent native record may be seeded from this origin.
        if(local)await native.request('save',JSON.stringify(local));
      }else{
        const restored=decodePreferences(loaded.value);
        if(!restored)throw new Error('Android preference storage returned invalid data.');
        preferences=restored;
      }
      nativeReady=true;
      try{storage?.setItem(PREFERENCE_STORAGE_KEY,JSON.stringify(preferences))}catch{/* Native storage remains authoritative. */}
    }catch{
      // A timeout or error must never seed the native record with defaults or
      // an old origin's values. Keep that record untouched for the next launch.
      issue='Device storage is unavailable. Layout changes may not survive an app restart.';
    }
  }else if(!storageReadable||!storage){
    issue='Preferences work for this visit; browser storage is unavailable.';
  }
  let saveQueue=Promise.resolve();
  return {
    preferences,
    mode:nativeReady?'android':storage&&storageReadable?'browser':'memory',
    nativeAvailable:!!native,
    issue,
    save(value){
      const serialized=JSON.stringify(cleanPreferences(value));
      let localSaved=false;
      try{if(storage){storage.setItem(PREFERENCE_STORAGE_KEY,serialized);localSaved=true}}catch{/* Native may still save successfully. */}
      // Never let a slow acknowledgement reorder successive layout edits.
      const work=saveQueue.then(async()=>{
        if(nativeReady){
          try{await native.request('save',serialized);return {saved:true,mode:'android'}}
          catch{nativeReady=false;return {saved:localSaved,mode:localSaved?'browser':'memory',issue:localSaved?'Device storage is unavailable. Layout changes may not survive an app restart.':'This change works for this visit; preference storage is unavailable.'}}
        }
        return {saved:localSaved,mode:localSaved?'browser':'memory',issue:localSaved?null:'Preferences work for this visit; browser storage is unavailable.'};
      });
      saveQueue=work.catch(()=>{});
      return work;
    }
  };
}
