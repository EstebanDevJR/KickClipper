// Web Worker: Whisper en el navegador con transformers.js (WebGPU o WASM).
// Recibe audio mono 16 kHz y devuelve palabras con marca de tiempo.

const TRANSFORMERS_URL = "https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.0/dist/transformers.min.js";

let asr = null;
let loadedKey = "";

async function load({ model, device }) {
  const key = `${model}|${device}`;
  if (asr && loadedKey === key) return asr;
  const { pipeline, env } = await import(TRANSFORMERS_URL);
  env.allowLocalModels = false;
  const dtype = device === "webgpu"
    ? { encoder_model: "fp32", decoder_model_merged: "q4" }
    : "q8";
  const progress = (p) => {
    if (p.status === "progress" && p.total) {
      postMessage({ type: "load", file: p.file, progress: p.loaded / p.total });
    }
  };
  asr = await pipeline("automatic-speech-recognition", model, { device, dtype, progress_callback: progress });
  loadedKey = key;
  return asr;
}

self.onmessage = async (e) => {
  const { id, type } = e.data;
  try {
    if (type === "load") {
      await load(e.data);
      postMessage({ id, type: "ready" });
      return;
    }
    if (type === "transcribe") {
      const pipe = await load(e.data);
      const out = await pipe(e.data.audio, {
        return_timestamps: "word",
        chunk_length_s: 30,
        stride_length_s: 5,
        language: e.data.language || null,
        task: "transcribe",
      });
      const words = (out.chunks || [])
        .map((c) => ({ w: String(c.text || "").trim(), s: c.timestamp?.[0], e: c.timestamp?.[1] }))
        .filter((w) => w.w && Number.isFinite(w.s));
      postMessage({ id, type: "result", words, text: out.text || "" });
    }
  } catch (err) {
    postMessage({ id, type: "error", error: String(err?.message || err) });
  }
};
