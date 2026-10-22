/* The installable app's service worker (Phase 8.6). It has one job: when a
 * page can't be loaded because the server can't be reached (the VPN is off,
 * or the phone isn't on the office network), show a page that says so and
 * how to fix it, instead of the browser's own error.
 *
 * It keeps nothing else. API calls, the app's code and every conversation
 * go to the network as if it weren't here: it only answers page loads, and
 * only when they fail. Registered in production only (components/
 * service-worker.tsx). */

const CACHE = "as-offline-v1";
const OFFLINE_PAGE = "/offline.html";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.add(new Request(OFFLINE_PAGE, { cache: "reload" })))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(() =>
      caches.match(OFFLINE_PAGE).then((page) => page ?? Response.error()),
    ),
  );
});
