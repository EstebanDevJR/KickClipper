// Service worker: permite instalar la web como app y abrirla sin conexión.
//
// - Archivos de la app (mismo origen): primero la red, para que cada
//   despliegue llegue al momento; la caché solo se usa sin conexión.
// - Librerías del CDN con versión fija (inmutables): primero la caché.
// - Nada más: los vídeos, Kick, Ollama y los modelos de IA (que ya guardan
//   su propia caché) pasan directos.

const CACHE = "kc-v1";
const SHELL = [
  "./", "index.html", "styles.css", "manifest.webmanifest", "icons/logo.svg", "icons/icon-192.png",
  "fonts/Anton-Regular.ttf", "fonts/Poppins-ExtraBold.ttf",
  ...["app", "audio", "captions", "chat", "compositor", "dsp", "highlights", "kickbridge", "llm", "loudness",
    "render", "segments", "signals", "source", "timeline", "transcribe", "vision", "vision-plan",
    "whisper-worker"].map((m) => `js/${m}.js`),
];
const CDN = /^https:\/\/cdn\.jsdelivr\.net\/npm\/(@[^/]+\/)?[^/@]+@\d[^/]*\//;

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.headers.has("range")) return;
  const url = new URL(req.url);
  if (url.origin === self.location.origin) e.respondWith(networkFirst(req));
  else if (CDN.test(req.url)) e.respondWith(cacheFirst(req));
});

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req);
    if (res.ok && res.type === "basic") cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok) cache.put(req, res.clone());
  return res;
}
