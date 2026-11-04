'use strict';
// Only the application shell is cached. Prompts, API responses and media never are.
const CACHE = 'leam-vid-shell-v2';
const SHELL = ['/', '/app.js?v=2', '/style.css?v=2', '/manifest.webmanifest', '/icon.svg', '/icon-192.png', '/icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith('leam-vid-shell-') && key !== CACHE).map(key => caches.delete(key)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin || !SHELL.includes(url.pathname + url.search)) return;
  event.respondWith(fetch(event.request).then(async response => {
    if (response.ok && response.type === 'basic') {
      const copy = response.clone();
      const cache = await caches.open(CACHE);
      await cache.put(event.request, copy);
    }
    return response;
  }).catch(() => caches.match(event.request)));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(self.clients.matchAll({type:'window', includeUncontrolled:true}).then(windows => {
    const studio = windows.find(client => new URL(client.url).origin === self.location.origin);
    return studio ? studio.focus() : self.clients.openWindow('/');
  }));
});
