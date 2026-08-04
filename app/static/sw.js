// Minimal service worker — caches ONLY the static app shell (HTML/icons/manifest).
// It intentionally never intercepts or caches API calls (/api/, /portal/... data endpoints)
// so patient data is never stored in the Cache Storage / offline cache.

const CACHE_NAME = "portal-shell-v1";
const SHELL_ASSETS = [
  "/portal/",
  "/portal/manifest.json",
  "/portal/favicon-32x32.png",
  "/portal/favicon-16x16.png",
  "/portal/icons/icon-192.png",
  "/portal/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin GET requests for the static shell itself.
  // Everything else (API calls, auth, patient data) goes straight to the network.
  const isShellRequest =
    event.request.method === "GET" &&
    url.origin === self.location.origin &&
    (url.pathname === "/portal/" ||
      SHELL_ASSETS.includes(url.pathname));

  if (!isShellRequest) return;

  event.respondWith(
    caches.match(event.request).then((cached) => {
      const network = fetch(event.request)
        .then((response) => {
          if (response && response.status === 200) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          }
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
});
