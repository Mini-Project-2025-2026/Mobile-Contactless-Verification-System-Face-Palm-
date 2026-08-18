// Minimal service worker: cache the app shell for installability; network-first.
const CACHE = "av-shell-v1";
const SHELL = ["/app", "/app/manifest.webmanifest", "/app/icon-192.png"];
self.addEventListener("install", (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener("activate", (e) => { e.waitUntil(self.clients.claim()); });
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (u.pathname.startsWith("/api/")) return; // never cache API calls
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request).then((r) => r || caches.match("/app"))));
});
