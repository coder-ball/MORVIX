// MORVIX dashboard service worker: makes the dashboard installable as an app.
// Network first; the cached app shell is only used when the local server is not running,
// so the parent sees a clear "start MORVIX" message instead of a browser error page.
const CACHE = 'morvix-shell-v3.7.5';  // bump together with the ?v= tags in index.html
const SHELL = ['/', '/style.css?v=3.7.5', '/app.js?v=3.7.5', '/manifest.json', '/icons/icon-192.png'];

self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return; // API calls are never cached
  e.respondWith(fetch(e.request).then(r => {
    if (r.ok) { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); }
    return r;
  }).catch(() => caches.match(e.request).then(r => r || caches.match('/'))));
});
