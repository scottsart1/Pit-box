const CACHE='pitbox-dashboard-5.5.1';
const FILES=['./','./index.html','./dashboard.css','./dashboard.js','./i18n.mjs','./display.mjs','./model.mjs','./render.mjs','./preferences.mjs','./manifest.webmanifest','./icon.svg','./icon-192.png','./icon-512.png','./downloads/driver-dashboard.html'];
// A language without a catalog shows English, so a missing one must not stop installation.
const CATALOGS=['./i18n/fr.json','./i18n/de.json','./i18n/es.json'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(FILES).then(()=>Promise.allSettled(CATALOGS.map(p=>cache.add(p)))))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('pitbox-dashboard-')&&k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',event=>{const url=new URL(event.request.url);if(event.request.method!=='GET'||url.origin!==self.location.origin||![...FILES,...CATALOGS].some(p=>new URL(p,self.registration.scope).pathname===url.pathname))return;event.respondWith(fetch(event.request).then(response=>{if(response.ok){const copy=response.clone();caches.open(CACHE).then(cache=>cache.put(event.request,copy))}return response}).catch(()=>caches.match(event.request,{ignoreSearch:true}))) });
