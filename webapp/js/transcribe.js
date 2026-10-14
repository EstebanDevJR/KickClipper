// Transcripción local en el navegador (Whisper en un Web Worker).
//
// En un VOD largo no se transcribe todo: solo las regiones alrededor de los
// picos de audio/chat (ver highlights.regionsToTranscribe), que es donde
// pueden estar los clips. Cada región se pasa a palabras con tiempo absoluto y
// se agrupa en frases (segmentos) como hace la versión de escritorio.

import { extractPcm16k } from "./audio.js";
import { cleanSegments, toSegments } from "./segments.js";

export { cleanSegments, toSegments };

export const MODELS = {
  base: { id: "onnx-community/whisper-base_timestamped", label: "Whisper base (rápido, ~150 MB)" },
  turbo: { id: "onnx-community/whisper-large-v3-turbo_timestamped", label: "Whisper large-v3-turbo (preciso, ~1 GB, requiere WebGPU)" },
};

let worker = null;
let seq = 0;
const pending = new Map();
let loadListener = null;

function getWorker() {
  if (!worker) {
    worker = new Worker(new URL("./whisper-worker.js", import.meta.url), { type: "module" });
    worker.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === "load") { loadListener?.(msg); return; }
      const p = pending.get(msg.id);
      if (!p) return;
      pending.delete(msg.id);
      if (msg.type === "error") p.reject(new Error(msg.error));
      else p.resolve(msg);
    };
  }
  return worker;
}

function call(payload, transfer = []) {
  const id = ++seq;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    getWorker().postMessage({ id, ...payload }, transfer);
  });
}

export function defaultDevice() {
  return typeof navigator !== "undefined" && navigator.gpu ? "webgpu" : "wasm";
}

/** Implementación por defecto; los tests la sustituyen con setTranscriber. */
let impl = {
  async load({ model, device, onLoad }) {
    loadListener = onLoad;
    await call({ type: "load", model, device });
  },
  async transcribe(audio, { model, device, language }) {
    const res = await call({ type: "transcribe", audio, model, device, language }, [audio.buffer]);
    return res.words;
  },
};

export function setTranscriber(custom) { impl = custom; }

/**
 * Transcribe las regiones [[a, b], ...] (segundos relativos al medio).
 * Devuelve segmentos {text, start, end, words:[{w,s,e}]} con tiempos absolutos.
 */
export async function transcribeRegions(track, regions, { t0 = 0, model = MODELS.base.id, device = defaultDevice(),
  language = null, onProgress, signal } = {}) {
  await impl.load({ model, device, onLoad: (m) => onProgress?.(0, `Descargando modelo ${Math.round(m.progress * 100)}%`) });
  const total = regions.reduce((acc, [a, b]) => acc + (b - a), 0);
  let done = 0;
  const segments = [];
  for (const [a, b] of regions) {
    // trozos de ~5 min para no acumular mucho audio en memoria
    for (let c = a; c < b; c += 300) {
      if (signal?.aborted) throw new DOMException("Cancelado", "AbortError");
      const d = Math.min(c + 300, b);
      onProgress?.(done / total, `Transcribiendo ${fmt(c)}–${fmt(d)}`);
      const pcm = await extractPcm16k(track, c, d, t0);
      if (pcm.length < 1600) { done += d - c; continue; }
      const words = (await impl.transcribe(pcm, { model, device, language }))
        .map((w) => ({ w: w.w, s: w.s + c, e: Math.max(w.e ?? w.s, w.s) + c }));
      segments.push(...toSegments(words));
      done += d - c;
    }
  }
  onProgress?.(1, "Transcripción completa");
  return cleanSegments(segments.sort((x, y) => x.start - y.start));
}

const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
