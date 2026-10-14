// KickClipper Web — controlador de la interfaz.
// Todo el procesamiento ocurre en este navegador: abrir la fuente, analizar el
// audio y el chat, transcribir, elegir momentos (con IA local opcional),
// encuadrar y renderizar.

import { openSource } from "./source.js";
import { analyzeAudio, fineEnergy } from "./audio.js";
import { chatActivity, spikeCurve } from "./chat.js";
import { MODELS, defaultDevice, setTranscriber, transcribeRegions } from "./transcribe.js";
import { LIMITS, detect, regionsToTranscribe } from "./highlights.js";
import { WEBLLM_MODELS, createLLM, llmOptions } from "./llm.js";
import { analyzeClip, plan as visionPlan, planToOutput, speakerAt } from "./vision.js";
import { buildTimeline } from "./timeline.js";
import { buildCaptionTrack, loadFonts } from "./captions.js";
import { createCompositor } from "./compositor.js";
import { chooseOutput, renderClip, thumbnail } from "./render.js";
import { KickBridge, bookmarklet, readKickPayload } from "./kickbridge.js";

const $ = (id) => document.getElementById(id);
const STAGES = ["open", "audio", "transcribe", "select", "render"];
const LAYOUT_LABEL = { split: "cámara + gameplay", speaker: "foco en quien habla", follow: "sigue a la persona", blur: "fondo difuminado", crop: "recorte central" };
const SIGNAL_LABEL = { audio: "audio", chat: "chat", hook: "gancho", reactions: "reacciones", density: "habla" };

const state = {
  tab: "file", file: null, kick: null, bridge: null, source: null, clips: [], running: false,
  abort: null, modalClip: null, analysis: null, renderChain: Promise.resolve(), limits: { ...LIMITS },
};

// ---------------- utilidades ----------------

const esc = (t) => String(t ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
function fmtT(sec) {
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function parseRange(text, duration) {
  if (!text?.trim()) return [0, duration];
  const toSec = (t) => t.trim().split(":").reduce((acc, p) => acc * 60 + Number(p), 0);
  const [a, b] = text.split("-");
  const start = Math.max(0, toSec(a));
  const end = b ? Math.min(duration, toSec(b)) : duration;
  if (!(end > start)) throw new Error("Rango no válido");
  return [start, end];
}
const slugify = (t) => String(t || "clip").normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase().slice(0, 60) || "clip";
const store = {
  get(k, d) { try { return localStorage.getItem(`kc.${k}`) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`kc.${k}`, v); } catch { /* sin almacenamiento */ } },
};

// ---------------- arranque ----------------

async function init() {
  if (globalThis.__KC_TEST__?.transcriber) setTranscriber(globalThis.__KC_TEST__.transcriber);
  setupTabs();
  setupDropzone();
  setupSettings();
  setupModal();
  $("go-btn").addEventListener("click", () => run().catch(() => {}));
  $("cancel-btn").addEventListener("click", () => state.abort?.abort());
  $("bookmarklet").href = bookmarklet(location.origin + location.pathname);
  $("bookmarklet").addEventListener("click", (e) => { e.preventDefault(); alert("Arrastra este botón a tu barra de marcadores y púlsalo en la página de un VOD de Kick."); });

  state.kick = readKickPayload();
  if (state.kick) {
    state.bridge = new KickBridge();
    history.replaceState(null, "", location.pathname + location.search);
    showKickVod();
    selectTab("kick");
  }
  await checkCapabilities();
  loadFonts();
}

async function checkCapabilities() {
  const hasCodecs = typeof VideoEncoder !== "undefined" && typeof VideoDecoder !== "undefined";
  setLed("gear-webcodecs", hasCodecs);
  if (!hasCodecs) {
    const box = $("unsupported");
    box.hidden = false;
    box.innerHTML = "Tu navegador no soporta <b>WebCodecs</b>, necesario para leer y renderizar vídeo. Usa Chrome, Edge u Opera recientes en ordenador (o la versión de escritorio de KickClipper).";
    $("go-btn").disabled = true;
    return;
  }
  try {
    const out = await chooseOutput();
    setLed("gear-h264", true);
    $("d-h264").textContent = out.ext === "mp4" ? "MP4 · H.264 + AAC" : "WebM · VP9 (sin H.264 en este navegador)";
  } catch { setLed("gear-h264", false); }
  const opts = await llmOptions();
  setLed("gear-webgpu", opts.webgpu);
  $("d-webgpu").textContent = opts.webgpu ? "IA acelerada por GPU" : "no disponible (IA más lenta)";

  const wsel = $("whisper-input");
  wsel.innerHTML = Object.entries(MODELS).map(([k, m]) => `<option value="${k}" ${k === "turbo" && !opts.webgpu ? "disabled" : ""}>${esc(m.label)}</option>`).join("");
  wsel.value = store.get("whisper", "base");
  if (wsel.selectedOptions[0]?.disabled) wsel.value = "base";

  const lsel = $("llm-input");
  const extra = [];
  if (opts.webgpu) for (const [id, label] of Object.entries(WEBLLM_MODELS)) extra.push(`<option value="webllm:${id}">${esc(label)}</option>`);
  if (opts.chrome) extra.push(`<option value="chrome">IA integrada de Chrome</option>`);
  lsel.insertAdjacentHTML("beforeend", extra.join(""));
  lsel.value = store.get("llm", "");
  if (!lsel.value) lsel.value = "";
  setLed("gear-ai", opts.webgpu || opts.chrome);
  $("d-ai").textContent = opts.chrome ? "Chrome IA / WebLLM" : opts.webgpu ? "WebLLM (descarga ~1 GB)" : "sin GPU: solo señales";
}

function setLed(id, ok) {
  const led = document.querySelector(`#${id} .led`);
  led.classList.toggle("on", !!ok);
  led.classList.toggle("off", !ok);
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => selectTab(b.dataset.tab)));
}

function selectTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-body").forEach((b) => { b.hidden = b.dataset.body !== tab; });
}

function setupDropzone() {
  const dz = $("dropzone");
  $("file-input").addEventListener("change", (e) => setFile(e.target.files[0]));
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("over"); });
  dz.addEventListener("dragleave", () => dz.classList.remove("over"));
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("over"); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); });
}

function setFile(file) {
  if (!file) return;
  state.file = file;
  $("file-name").textContent = `${file.name} · ${(file.size / 2 ** 20).toFixed(0)} MB`;
}

function setupSettings() {
  for (const id of ["clips-input", "dur-input", "lang-input", "depth-input", "autorender-input"]) {
    const el = $(id);
    const saved = store.get(id, null);
    if (saved !== null && [...el.options].some((o) => o.value === saved)) el.value = saved;
    el.addEventListener("change", () => store.set(id, el.value));
  }
  $("whisper-input").addEventListener("change", (e) => store.set("whisper", e.target.value));
  $("llm-input").addEventListener("change", (e) => store.set("llm", e.target.value));
}

function showKickVod() {
  const k = state.kick;
  $("kick-vod").hidden = false;
  $("kick-thumb").src = k.thumb || "";
  $("kick-title").textContent = k.title || "VOD de Kick";
  $("kick-meta").textContent = `${k.channel || "canal"}${k.duration ? " · " + fmtT(k.duration) : ""}`;
  const ok = state.bridge?.available;
  const el = $("kick-bridge");
  el.className = `kick-bridge ${ok ? "ok" : "off"}`;
  el.textContent = ok ? "● puente con Kick activo: chat disponible" : "● pestaña de Kick cerrada: sin chat (vuelve a pulsar el marcador)";
}

// ---------------- pipeline ----------------

function setStage(stage, frac, msg) {
  const idx = STAGES.indexOf(stage);
  document.querySelectorAll("#run-pipe .pipe-step").forEach((el, i) => {
    el.classList.toggle("donestep", i < idx);
    el.classList.toggle("active", i === idx);
    el.querySelector(".fill").style.width = i < idx ? "100%" : i === idx ? `${Math.round(frac * 100)}%` : "0%";
  });
  if (msg !== undefined) { $("run-msg").textContent = msg; $("run-msg").classList.remove("error"); }
}

function sourceInput() {
  if (state.tab === "file") {
    if (!state.file) throw new Error("Elige un archivo de vídeo");
    return { file: state.file, name: state.file.name };
  }
  if (state.tab === "kick") {
    if (!state.kick) throw new Error("Usa el marcador desde la página del VOD en kick.com");
    return { url: state.kick.src, fetchFn: state.bridge?.fetchFn, name: state.kick.title };
  }
  const url = $("url-input").value.trim();
  if (!/^https?:\/\//.test(url)) throw new Error("Pega una URL http(s) válida");
  return { url, name: url.split("/").pop() };
}

export async function run() {
  if (state.running) return;
  const ctrl = new AbortController();
  state.abort = ctrl;
  state.running = true;
  $("go-btn").disabled = true;
  $("run").hidden = false;
  $("curve").hidden = true;
  $("run-title").textContent = "Procesando";
  const signal = ctrl.signal;
  try {
    // 1. Fuente
    setStage("open", 0.2, "Abriendo vídeo");
    state.source?.dispose?.();
    const src = await openSource(sourceInput());
    state.source = src;
    if (!src.canDecode) throw new Error("Este navegador no puede decodificar el vídeo (prueba con Chrome o Edge, o convierte a H.264).");
    if (!src.analysisAudio) throw new Error("El vídeo no tiene audio que analizar");
    const [a, b] = parseRange($("range-input").value, src.duration);
    const [minD, maxD] = $("dur-input").value.split("-").map(Number);
    state.limits = { min: minD, max: maxD, target: Math.round(minD + (maxD - minD) * 0.45) };
    $("run-title").textContent = `${src.name} · ${fmtT(b - a)}`;

    // 2. Audio + chat (en paralelo)
    setStage("audio", 0, "Analizando audio");
    const kick = state.tab === "kick" ? state.kick : null;
    const [feats, chat] = await Promise.all([
      analyzeAudio(src.analysisAudio, { start: a, end: b, t0: src.t0, signal, onProgress: (f, m) => setStage("audio", f, m) }),
      kick && state.bridge?.available
        ? chatActivity(state.bridge, kick, b - a, { offset: a, signal }).catch(() => null)
        : Promise.resolve(null),
    ]);
    if (!feats) throw new Error("No se pudo leer el audio");
    const exc = feats.excitement;
    const chatCurve = spikeCurve(chat, exc.length);
    const moment = chatCurve ? exc.map((v, i) => 0.65 * v + 0.35 * chatCurve[i]) : exc;
    const depth = Number($("depth-input").value);
    const regions = regionsToTranscribe(moment, b - a, { maxMinutes: depth });
    drawCurve(exc, chatCurve, regions);

    // 3. Transcripción (solo alrededor de los picos en VODs largos)
    setStage("transcribe", 0, "Preparando Whisper");
    const model = MODELS[$("whisper-input").value] || MODELS.base;
    const absRegions = regions.map(([x, y]) => [x + a, y + a]);
    const segments = await transcribeRegions(src.analysisAudio, absRegions, {
      t0: src.t0, model: model.id, device: defaultDevice(), language: $("lang-input").value || null,
      signal, onProgress: (f, m) => setStage("transcribe", f, m),
    });
    if (!segments.length) throw new Error("No se detectó voz en el vídeo");

    // 4. Selección (heurística + IA local opcional), en tiempo relativo al rango
    setStage("select", 0, "Eligiendo momentos");
    let llm = null;
    const llmChoice = $("llm-input").value;
    if (llmChoice) {
      try {
        const [kind, id] = llmChoice.split(":");
        llm = await createLLM(kind, { model: id, limits: state.limits, onProgress: (f, m) => setStage("select", f * 0.5, m) });
      } catch (err) {
        console.warn("IA local no disponible", err);
        setStage("select", 0.5, "La IA local no se pudo cargar: se usan señales automáticas");
      }
    }
    const rel = segments.map((s) => ({ ...s, start: s.start - a, end: s.end - a, words: s.words.map((w) => ({ ...w, s: w.s - a, e: w.e - a })) }));
    const found = await detect(rel, exc, b - a, {
      chat: chatCurve, channel: kick?.channel || "", llm, maxClips: Number($("clips-input").value),
      limits: state.limits, onProgress: (f, m) => setStage("select", 0.5 + f * 0.5, m),
    });
    if (!found.length) throw new Error("No se encontraron momentos clipeables");
    state.clips = found.map((w, i) => toClip(w, a, i));
    state.analysis = { a, b, exc, chatCurve, regions };
    drawCurve(exc, chatCurve, regions, state.clips.map((c) => [c.start - a, c.end - a]));
    renderGrid();
    makeThumbs();

    // 5. Render automático de los mejores
    const auto = Number($("autorender-input").value);
    if (auto > 0) {
      for (let i = 0; i < Math.min(auto, state.clips.length); i++) {
        if (signal.aborted) break;
        const n = Math.min(auto, state.clips.length);
        setStage("render", i / n, `Renderizando clip ${i + 1}/${n}`);
        await queueRender(state.clips[i], {}, signal, (f, m) => setStage("render", (i + f) / n, `Clip ${i + 1}/${n}: ${m} ${Math.round(f * 100)}%`))
          .catch((err) => console.warn("Render falló", err));
      }
    }
    setStage("render", 1, `Listo: ${state.clips.length} clips`);
    document.querySelectorAll("#run-pipe .pipe-step").forEach((el) => { el.classList.add("donestep"); el.classList.remove("active"); });
  } catch (err) {
    const msg = err?.name === "AbortError" ? "Cancelado" : `Error: ${err?.message || err}`;
    $("run-msg").textContent = msg;
    $("run-msg").classList.add("error");
    console.error(err);
    throw err;
  } finally {
    state.running = false;
    $("go-btn").disabled = false;
  }
}

function toClip(w, offset, i) {
  const words = w.segments.flatMap((s) => s.words).map((x) => ({ ...x, s: x.s + offset, e: x.e + offset }));
  return {
    id: `c${Date.now().toString(36)}${i}`,
    start: w.start + offset, end: w.end + offset, score: w.final,
    title: w.title || "", hookText: w.hookText || "", description: w.description || "",
    hashtags: w.hashtags || [], keywords: w.keywords || [], category: w.category || "",
    reason: w.reason || "", signals: w.signals || {}, llmScores: w.llmScores || null,
    peak: w.peak != null ? w.peak + offset : null, words,
    transcript: w.segments.map((s) => s.text).join(" "),
    opts: { layout: "auto", captionStyle: "viral", showHook: true, jumpcut: true, preview: false },
    status: "candidate", thumb: "", blobUrl: "", fileName: "", layoutUsed: "",
  };
}

async function makeThumbs() {
  const src = state.source;
  for (const c of state.clips) {
    try {
      const at = c.peak != null && c.peak > c.start && c.peak < c.end ? c.peak : (c.start + c.end) / 2;
      c.thumb = await thumbnail(src.analysisVideo, src.t0 + at);
      renderGrid();
    } catch (err) { console.warn("Miniatura falló", err); }
  }
}

function drawCurve(exc, chat, regions, clips = []) {
  const cv = $("curve");
  cv.hidden = false;
  const ctx = cv.getContext("2d");
  const W = cv.width, H = cv.height, n = exc.length;
  ctx.clearRect(0, 0, W, H);
  ctx.fillStyle = "rgba(255,210,74,0.10)";
  for (const [x, y] of regions) ctx.fillRect((x / n) * W, 0, ((y - x) / n) * W, H);
  ctx.fillStyle = "rgba(83,252,24,0.22)";
  for (const [x, y] of clips) ctx.fillRect((x / n) * W, 0, Math.max(2, ((y - x) / n) * W), H);
  const line = (arr, color) => {
    ctx.beginPath();
    const step = Math.max(1, Math.floor(n / W));
    for (let i = 0; i < n; i += step) {
      let m = 0;
      for (let j = i; j < Math.min(i + step, n); j++) m = Math.max(m, arr[j]);
      const x = (i / n) * W, y = H - 4 - m * (H - 8);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
  };
  line(exc, "#53fc18");
  if (chat) line(chat, "#ffd24a");
}

// ---------------- render de un clip ----------------

function queueRender(clip, overrides, signal, onProgress = null) {
  const job = state.renderChain.then(() => renderCandidate(clip, overrides, signal, onProgress));
  state.renderChain = job.catch(() => {});
  return job;
}

export async function renderCandidate(clip, overrides = {}, signal = null, onRunProgress = null) {
  const src = state.source;
  if (!src) throw new Error("No hay vídeo abierto");
  const o = { ...clip.opts, ...overrides };
  if (overrides.title !== undefined) clip.title = overrides.title;
  if (overrides.hookText !== undefined) clip.hookText = overrides.hookText;
  delete o.title;
  delete o.hookText;
  clip.opts = o;
  const start = Number(o.start ?? clip.start), end = Number(o.end ?? clip.end);
  const clipLen = end - start;
  if (!(clipLen >= 3 && clipLen <= 185)) throw new Error("El clip debe durar entre 3 s y 3 min");
  clip.status = "rendering";
  renderGrid();
  const progress = (f, m) => {
    clip.progress = f;
    onRunProgress?.(f, m);
    if (state.modalClip === clip) {
      $("m-progress").hidden = false;
      $("m-progress-bar").style.width = `${Math.round(f * 100)}%`;
      $("m-progress-label").textContent = `${m} ${Math.round(f * 100)}%`;
    }
    const chip = document.querySelector(`.clip[data-id="${clip.id}"] .clip-status-chip`);
    if (chip) chip.textContent = `render ${Math.round(f * 100)}%`;
  };
  try {
    const words = clip.words.filter((w) => w.e > start && w.s < end)
      .map((w) => ({ w: w.w, s: Math.max(w.s - start, 0), e: Math.min(w.e - start, clipLen) }));

    // Encuadre (análisis de caras cacheado por rango)
    let plan = {};
    let resolved = o.layout;
    if (["auto", "split", "speaker", "follow"].includes(o.layout)) {
      const key = `${start}-${end}`;
      if (clip.planKey !== key) {
        progress(0, "Analizando encuadre");
        try {
          const analysis = await analyzeClip(src.analysisVideo, start, end, { t0: src.t0, signal, onProgress: (f) => progress(f * 0.15, "Analizando encuadre") });
          clip.plan = visionPlan(analysis, words, clipLen);
        } catch (err) {
          if (err?.name === "AbortError") throw err;
          console.warn("Análisis visual falló", err);
          clip.plan = { layout: "blur" };
        }
        clip.planKey = key;
      }
      plan = clip.plan;
      resolved = o.layout === "auto" ? plan.layout : (o.layout === plan.layout ? o.layout : "blur");
    }

    // Línea de tiempo: jump cuts y cold open
    let energy = null;
    if (o.jumpcut) { try { energy = await fineEnergy(src.analysisAudio, start, end, src.t0); } catch { energy = null; } }
    const preview = o.preview && clip.peak != null && clip.peak - start > 6 && clip.peak - start < clipLen - 1
      ? [clip.peak - start - 1.4, clip.peak - start + 0.9] : null;
    const tl = buildTimeline(clipLen, words, { energy, jumpcut: !!o.jumpcut, preview });
    if (resolved === "speaker") for (const w of words) w.spk = speakerAt(plan, (w.s + w.e) / 2);

    await loadFonts();
    const captions = buildCaptionTrack(tl.mapWords(words), { style: o.captionStyle, keywords: clip.keywords });
    const hook = o.showHook ? clip.hookText || clip.title : "";
    const compositor = createCompositor({ layout: resolved, plan: planToOutput(plan, resolved, tl), captions, hook });
    const result = await renderClip({
      source: { videoTrack: src.videoTrack, audioTrack: src.audioTrack, t0: src.t0 },
      start, end, timeline: tl, compositor, signal,
      onProgress: (f, m) => progress(0.15 + f * 0.85, m),
    });
    if (clip.blobUrl) URL.revokeObjectURL(clip.blobUrl);
    clip.blobUrl = URL.createObjectURL(result.blob);
    clip.fileName = `${slugify(clip.title)}.${result.ext}`;
    clip.size = result.blob.size;
    clip.layoutUsed = resolved;
    clip.renderedDuration = tl.duration;
    clip.status = "rendered";
  } catch (err) {
    clip.status = "error";
    clip.error = String(err?.message || err);
    throw err;
  } finally {
    renderGrid();
    if (state.modalClip === clip) refreshModal(clip);
  }
  return clip;
}

// ---------------- grid de clips ----------------

function renderGrid() {
  const zone = $("clips-zone");
  const grid = $("clip-grid");
  if (!state.clips.length) { zone.hidden = true; return; }
  zone.hidden = false;
  $("clips-count").textContent = `${state.clips.length} detectados`;
  grid.innerHTML = "";
  state.clips.forEach((clip, i) => {
    const el = document.createElement("div");
    el.className = "clip";
    el.dataset.id = clip.id;
    el.style.animationDelay = `${i * 40}ms`;
    const chip = { candidate: "candidato", rendering: "render…", rendered: "✓ listo", error: "error" }[clip.status] || "";
    el.innerHTML = `
      <div class="clip-thumb">
        <div class="no-render">SIN RENDER</div>
        ${clip.thumb ? `<img class="thumb-169" src="${esc(clip.thumb)}" alt="">` : ""}
        <span class="clip-score">★ ${clip.score.toFixed(1)}</span>
        <span class="clip-status-chip ${clip.status}">${chip}</span>
        ${clip.category ? `<span class="clip-fmt">${esc(clip.category.toUpperCase())}</span>` : ""}
        <span class="clip-dur">${Math.round(clip.end - clip.start)}s</span>
      </div>
      <div class="clip-body">
        <div class="clip-title">${esc(clip.title || clip.transcript.slice(0, 70))}</div>
        <div class="clip-range">${fmtT(clip.start)} → ${fmtT(clip.end)}</div>
      </div>`;
    el.addEventListener("click", () => openModal(clip));
    grid.appendChild(el);
  });
}

// ---------------- modal ----------------

function setupModal() {
  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
  $("m-copy-title").addEventListener("click", () => copy($("m-copy-title"), $("m-title").value));
  $("m-copy-desc").addEventListener("click", () => copy($("m-copy-desc"), $("m-desc").value));
  $("m-render").addEventListener("click", async () => {
    const clip = state.modalClip;
    if (!clip) return;
    $("m-render").disabled = true;
    $("m-progress").hidden = false;
    $("m-progress-bar").style.width = "2%";
    try {
      await queueRender(clip, {
        title: $("m-title").value, hookText: $("m-hook").value.trim(),
        start: parseFloat($("m-start").value), end: parseFloat($("m-end").value),
        layout: $("m-layout").value, captionStyle: $("m-capstyle").value,
        showHook: $("m-showhook").checked, jumpcut: $("m-jumpcut").checked, preview: $("m-preview").checked,
      });
    } catch (err) {
      alert(`Error al renderizar: ${err.message || err}`);
    } finally {
      $("m-render").disabled = false;
      $("m-progress").hidden = true;
    }
  });
}

function publishText(clip) {
  const parts = [];
  if (clip.description) parts.push(clip.description);
  const channel = state.kick?.channel;
  if (channel) parts.push(`Sigue a ${channel}: https://kick.com/${channel}`);
  const tags = (clip.hashtags || []).map((h) => `#${h}`).join(" ");
  if (tags) parts.push(tags);
  return parts.join("\n\n");
}

function openModal(clip) {
  state.modalClip = clip;
  const o = clip.opts;
  $("m-title").value = clip.title;
  $("m-hook").value = clip.hookText;
  $("m-start").value = (o.start ?? clip.start).toFixed(1);
  $("m-end").value = (o.end ?? clip.end).toFixed(1);
  $("m-layout").value = o.layout;
  $("m-capstyle").value = o.captionStyle;
  $("m-showhook").checked = o.showHook;
  $("m-jumpcut").checked = o.jumpcut;
  $("m-preview").checked = o.preview;
  $("m-desc").value = publishText(clip);
  $("m-progress").hidden = clip.status !== "rendering";
  refreshModal(clip);
  $("modal").hidden = false;
}

function refreshModal(clip) {
  const ready = clip.status === "rendered" && clip.blobUrl;
  const video = $("modal-video");
  $("player-placeholder").hidden = !!ready;
  video.hidden = !ready;
  $("m-download").hidden = !ready;
  if (ready) {
    if (video.src !== clip.blobUrl) video.src = clip.blobUrl;
    $("m-download").href = clip.blobUrl;
    $("m-download").download = clip.fileName;
    $("m-download").textContent = `⬇ DESCARGAR (${(clip.size / 2 ** 20).toFixed(1)} MB)`;
  } else {
    video.removeAttribute("src");
  }
  const sig = clip.signals || {};
  const bars = Object.entries(SIGNAL_LABEL).filter(([k]) => typeof sig[k] === "number")
    .map(([k, l]) => `<div class="sig"><span>${l}</span><i style="--v:${Math.round(sig[k] * 100)}%"></i></div>`).join("");
  const chips = [];
  if (clip.category) chips.push(`<span class="chip">${esc(clip.category)}</span>`);
  if (clip.layoutUsed) chips.push(`<span class="chip">encuadre: ${esc(LAYOUT_LABEL[clip.layoutUsed] || clip.layoutUsed)}</span>`);
  if (clip.llmScores) chips.push(`<span class="chip">IA · gancho ${clip.llmScores.hook} · se entiende ${clip.llmScores.standalone} · remate ${clip.llmScores.payoff}</span>`);
  if (clip.status === "error") chips.push(`<span class="chip">error: ${esc(clip.error)}</span>`);
  $("m-signals").innerHTML = (chips.length ? `<div class="chips">${chips.join("")}</div>` : "") + bars;
  $("m-reason").textContent = clip.reason || "";
}

function closeModal() {
  $("modal").hidden = true;
  $("modal-video").pause?.();
  state.modalClip = null;
}

async function copy(btn, text) {
  try { await navigator.clipboard.writeText(text); } catch { return; }
  const original = btn.textContent;
  btn.textContent = "✓ copiado";
  setTimeout(() => { btn.textContent = original; }, 1200);
}

// Acceso para depuración y tests automáticos
globalThis.KC = { state, run, renderCandidate };

init();
