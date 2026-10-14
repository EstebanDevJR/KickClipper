// Prueba de extremo a extremo de la versión web en Chromium (Playwright).
//
//   KC_MEDIA=video.webm node webapp/tests/e2e.mjs
//
// Variables:
//   KC_MEDIA       vídeo de prueba (VP9/Opus si el Chromium no trae H.264)
//   KC_HLS         (opcional) ruta de un master.m3u8 relativa a la carpeta de KC_MEDIA
//   KC_SKIP_FILE   si está definida, no prueba KC_MEDIA como archivo local
//   KC_KICK_HLS    (opcional) master.m3u8 (relativo a KC_MEDIA) para simular un VOD
//                  de Kick abierto con el marcador; KC_KICK_RENDER=1 renderiza el mejor
//   KC_LIBS        carpeta node_modules con mediabunny, @mediabunny/aac-encoder y
//                  @mediapipe/tasks-vision (se sirven en lugar del CDN)
//   KC_MODELS      carpeta con los modelos de MediaPipe (si no hay acceso a internet)
//   KC_PLAYWRIGHT  ruta del módulo playwright (por defecto "playwright")
//   KC_OUT         carpeta donde guardar los clips y capturas
//
// La transcripción se sustituye por un stub (no descarga Whisper).
//
// Preparación (fuera del repo):
//   npm i --prefix /tmp/kc-e2e playwright mediabunny@1.60.0 @mediabunny/aac-encoder@1.60.0 \
//         @mediapipe/tasks-vision@1.0.1
//   KC_LIBS=/tmp/kc-e2e/node_modules KC_PLAYWRIGHT=/tmp/kc-e2e/node_modules/playwright/index.mjs ...
// El Chromium de Playwright no trae H.264/AAC: usa vídeos de prueba VP9/Opus.

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(here, "..");
const LIBS = path.resolve(process.env.KC_LIBS || path.join(ROOT, "node_modules"));
const MODELS = process.env.KC_MODELS ? path.resolve(process.env.KC_MODELS) : null;
const MEDIA = process.env.KC_MEDIA ? path.resolve(process.env.KC_MEDIA) : null;
const MEDIA_DIR = MEDIA ? path.dirname(MEDIA) : null;
const OUT = path.resolve(process.env.KC_OUT || path.join(ROOT, "tests", "out"));
const { chromium } = await import(process.env.KC_PLAYWRIGHT || "playwright");

const MIME = { ".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css",
  ".ttf": "font/ttf", ".wasm": "application/wasm", ".webm": "video/webm", ".mp4": "video/mp4",
  ".m3u8": "application/vnd.apple.mpegurl", ".m4s": "video/iso.segment", ".json": "application/json",
  ".tflite": "application/octet-stream", ".task": "application/octet-stream" };

const served = new Map(); // bytes servidos por carpeta (para ver qué variante HLS se descarga)
const countBytes = (url, n) => {
  const key = url.split("/").slice(0, -1).join("/") || "/";
  served.set(key, (served.get(key) || 0) + n);
};

function serve(dir, prefix = "") {
  return (req, res) => {
    const url = decodeURIComponent(new URL(req.url, "http://x").pathname);
    if (!url.startsWith(prefix)) return false;
    let file = path.join(dir, url.slice(prefix.length));
    if (file.endsWith("/")) file += "index.html";
    if (!file.startsWith(dir) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return false;
    const stat = fs.statSync(file);
    const type = MIME[path.extname(file)] || "application/octet-stream";
    const range = req.headers.range?.match(/bytes=(\d+)-(\d*)/);
    if (range) {
      const start = Number(range[1]), end = range[2] ? Number(range[2]) : stat.size - 1;
      countBytes(url, end - start + 1);
      res.writeHead(206, { "Content-Type": type, "Content-Range": `bytes ${start}-${end}/${stat.size}`, "Content-Length": end - start + 1, "Accept-Ranges": "bytes" });
      fs.createReadStream(file, { start, end }).pipe(res);
    } else {
      countBytes(url, stat.size);
      res.writeHead(200, { "Content-Type": type, "Content-Length": stat.size, "Accept-Ranges": "bytes" });
      fs.createReadStream(file).pipe(res);
    }
    return true;
  };
}

const handlers = [serve(ROOT, "/app/"), ...(MEDIA_DIR ? [serve(MEDIA_DIR, "/media/")] : [])];
const server = http.createServer((req, res) => {
  for (const h of handlers) if (h(req, res)) return;
  res.writeHead(404); res.end();
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const base = `http://localhost:${server.address().port}`;

const browser = await chromium.launch({ args: ["--enable-unsafe-webgpu", "--autoplay-policy=no-user-gesture-required"] });
const context = await browser.newContext({ viewport: { width: 1360, height: 900 } });
const page = await context.newPage();
page.on("console", (m) => { if (["error", "warning"].includes(m.type())) console.log(`[navegador:${m.type()}]`, m.text().slice(0, 300)); });
page.on("pageerror", (e) => console.log("[navegador:pageerror]", e.message));

// CDN → node_modules local
await context.route("https://cdn.jsdelivr.net/npm/**", async (route) => {
  const rest = new URL(route.request().url()).pathname.replace(/^\/npm\//, "");
  const m = rest.match(/^((?:@[^/]+\/)?[^/@]+)@[^/]+\/(.*)$/);
  const file = m && path.join(LIBS, m[1], m[2]);
  if (file && fs.existsSync(file)) return route.fulfill({ path: file, contentType: MIME[path.extname(file)] || "application/octet-stream" });
  return route.abort();
});
if (MODELS) {
  await context.route("https://storage.googleapis.com/**", (route) => {
    const file = path.join(MODELS, path.basename(new URL(route.request().url()).pathname));
    return fs.existsSync(file) ? route.fulfill({ path: file }) : route.abort();
  });
}
await context.route(/fonts\.(googleapis|gstatic)\.com/, (route) => route.fulfill({ body: "", contentType: "text/css" }));

// Whisper simulado: palabras cada 0,45 s, con exclamaciones de vez en cuando
await context.addInitScript(() => {
  globalThis.__KC_TEST__ = {
    transcriber: {
      async load() {},
      async transcribe(audio) {
        const dur = audio.length / 16000;
        const words = [];
        let i = 0;
        for (let t = 0.2; t < dur - 0.5; t += 0.45, i++) {
          const w = i % 23 === 0 ? "¡NO PUEDE SER!" : i % 11 === 0 ? "jajaja" : `palabra${i}`;
          words.push({ w, s: t, e: t + 0.35 });
        }
        return words;
      },
    },
  };
});

fs.mkdirSync(OUT, { recursive: true });
let failures = 0;
const check = (cond, msg) => { console.log(`${cond ? "✓" : "✗"} ${msg}`); if (!cond) failures++; };

async function runCase(name, setup) {
  console.log(`\n=== ${name} ===`);
  await page.goto(`${base}/app/index.html`);
  await page.waitForFunction(() => globalThis.KC);
  await setup();
  await page.selectOption("#autorender-input", "1");
  await page.selectOption("#clips-input", "4");
  const t0 = Date.now();
  await page.click("#go-btn");
  await page.waitForFunction(() => /Listo|Error|Cancelado/.test(document.getElementById("run-msg").textContent), null, { timeout: 15 * 60_000, polling: 1000 });
  const msg = await page.textContent("#run-msg");
  console.log(`   ${msg} (${((Date.now() - t0) / 1000).toFixed(0)} s)`);
  check(/Listo/.test(msg), `${name}: pipeline completo`);
  const info = await page.evaluate(() => KC.state.clips.map((c) => ({
    start: c.start, end: c.end, score: c.score, status: c.status, layout: c.layoutUsed, title: c.title,
    plan: c.plan?.layout, size: c.size, dur: c.renderedDuration, error: c.error,
  })));
  console.log(info);
  check(info.length > 0, `${name}: hay candidatos`);
  const rendered = info.find((c) => c.status === "rendered");
  check(!!rendered, `${name}: el mejor clip se renderizó`);
  await page.screenshot({ path: path.join(OUT, `${name}-ui.png`) });
  if (rendered) {
    const b64 = await page.evaluate(async () => {
      const c = KC.state.clips.find((x) => x.status === "rendered");
      const buf = new Uint8Array(await (await fetch(c.blobUrl)).arrayBuffer());
      let s = "";
      for (let i = 0; i < buf.length; i += 0x8000) s += String.fromCharCode(...buf.subarray(i, i + 0x8000));
      return { data: btoa(s), name: c.fileName };
    });
    const file = path.join(OUT, `${name}-${b64.name}`);
    fs.writeFileSync(file, Buffer.from(b64.data, "base64"));
    console.log(`   clip guardado en ${file}`);
    // abrir el modal para la captura
    await page.click(".clip");
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, `${name}-modal.png`) });
  }
  return info;
}

if (MEDIA && !process.env.KC_SKIP_FILE) {
  const info = await runCase("archivo", async () => {
    await page.setInputFiles("#file-input", MEDIA);
  });
  check(info[0]?.start <= 41 && info[0]?.end >= 41, "archivo: el mejor clip contiene el pico de audio (s 40-43)");
  check(info.find((c) => c.status === "rendered")?.layout === "split", "archivo: facecam detectada → layout split");
}
if (process.env.KC_HLS) {
  served.clear();
  const info = await runCase("hls", async () => {
    await page.click('[data-tab="url"]');
    await page.fill("#url-input", `${base}/media/${process.env.KC_HLS}`);
  });
  const media = [...served].filter(([k]) => k.startsWith("/media/"));
  console.log("   bytes servidos por carpeta:", Object.fromEntries(media.map(([k, v]) => [k, `${(v / 2 ** 20).toFixed(1)} MB`])));
  check(info.find((c) => c.status === "rendered")?.layout === "split", "hls: facecam detectada → layout split");
}

// ---- VOD de Kick con el marcador: kick.com y su CDN simulados ----
// El CDN no envía cabeceras CORS para otros orígenes, así que la app tiene que
// pedir el vídeo (y el chat) a través de la pestaña de Kick.
if (process.env.KC_KICK_HLS) {
  console.log("\n=== kick (marcador + puente) ===");
  const hlsDir = path.join(MEDIA_DIR, path.dirname(process.env.KC_KICK_HLS));
  const master = path.basename(process.env.KC_KICK_HLS);
  const uuid = "5c697a87-afce-4256-b01f-3c8fe71ef5cb";
  const startTime = "2026-01-01 20:00:00";
  const ctx = context;
  let cdnBridged = 0, cdnDirect = 0, chatCalls = 0;
  await ctx.route("https://stream.kick.com/**", async (route) => {
    const req = route.request();
    const origin = req.headers().origin || "";
    const rel = new URL(req.url()).pathname.replace(/^\/vod\//, "");
    const file = path.join(hlsDir, rel);
    if (!fs.existsSync(file)) return route.fulfill({ status: 404 });
    // Playwright no aplica CORS a respuestas simuladas: se aborta la petición
    // directa, que es lo que ve fetch() cuando CORS la bloquea (TypeError).
    if (origin !== "https://kick.com") { cdnDirect++; return route.abort("accessdenied"); }
    cdnBridged++;
    return route.fulfill({ status: 200, body: fs.readFileSync(file),
      headers: { "Content-Type": MIME[path.extname(file)] || "application/octet-stream", "Access-Control-Allow-Origin": origin } });
  });
  await ctx.route("https://kick.com/**", async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname === `/api/v1/video/${uuid}`) {
      return route.fulfill({ json: { source: `https://stream.kick.com/vod/${master}`, created_at: "2026-01-01T20:00:00Z",
        livestream: { session_title: "Stream de prueba", start_time: startTime, duration: 70000, thumbnail: "",
          channel: { id: 4242, slug: "canalprueba" } } } });
    }
    if (u.pathname === "/api/v2/channels/4242/messages") {
      chatCalls++;
      const at = new Date(u.searchParams.get("start_time"));
      const t0 = (at - new Date(startTime.replace(" ", "T") + "Z")) / 1000;
      const messages = [];
      for (let k = 0; k < 25; k++) {
        const hot = t0 + k * 0.1 >= 44 && t0 + k * 0.1 < 50;
        const rate = hot ? 12 : 1;
        messages.push({ created_at: new Date(at.getTime() + (k / rate) * 1000).toISOString(), content: hot ? "KEKW KEKW jajaja" : "hola" });
      }
      return route.fulfill({ json: { data: { messages } } });
    }
    return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Kick</title><h1>VOD</h1>" });
  });

  const kickPage = await ctx.newPage();
  await kickPage.goto(`https://kick.com/canalprueba/videos/${uuid}`);
  const appUrl = `${base}/app/index.html`;
  const { bookmarklet } = await import(new URL("../js/kickbridge.js", import.meta.url));
  const code = bookmarklet(appUrl);
  const popupPromise = ctx.waitForEvent("page");
  await kickPage.evaluate((c) => { (0, eval)(decodeURIComponent(c.slice("javascript:".length))); }, code);
  const app = await popupPromise;
  app.on("console", (m) => { if (m.type() === "error") console.log("[app:error]", m.text().slice(0, 300)); });
  await app.waitForFunction(() => globalThis.KC && document.getElementById("kick-title").textContent);
  check((await app.textContent("#kick-title")) === "Stream de prueba", "kick: la app recibe los datos del VOD");
  check(/activo/.test(await app.textContent("#kick-bridge")), "kick: puente con la pestaña de Kick activo");
  await app.selectOption("#autorender-input", process.env.KC_KICK_RENDER || "0");
  await app.click("#go-btn");
  await app.waitForFunction(() => /Listo|Error|Cancelado/.test(document.getElementById("run-msg").textContent), null, { timeout: 15 * 60_000, polling: 1000 });
  const msg = await app.textContent("#run-msg");
  console.log(`   ${msg}`);
  check(/Listo/.test(msg), "kick: pipeline completo");
  const info = await app.evaluate(() => KC.state.clips.map((c) => ({ start: c.start, end: c.end, chat: c.signals.chat, status: c.status, layout: c.layoutUsed })));
  console.log(info);
  console.log(`   CDN: ${cdnBridged} peticiones por el puente, ${cdnDirect} directas (bloqueadas por CORS); chat: ${chatCalls} peticiones`);
  check(cdnBridged > 0, "kick: el vídeo se descargó a través del puente");
  check(chatCalls > 0 && info.some((c) => typeof c.chat === "number"), "kick: se usó la señal del chat");
  check(info[0] && info[0].start <= 44 && info[0].end >= 44, "kick: el mejor clip contiene el pico del chat/audio");
  await app.screenshot({ path: path.join(OUT, "kick-ui.png") });
}

await browser.close();
server.close();
console.log(failures ? `\n${failures} comprobaciones fallaron` : "\nTodo OK");
process.exit(failures ? 1 : 0);
