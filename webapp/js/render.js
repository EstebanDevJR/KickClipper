// Render de un clip en el navegador con WebCodecs (mediabunny).
//
// Vídeo: para cada fotograma de salida (30 fps) se pide el fotograma fuente
// que le corresponde según la línea de tiempo (jump cuts, cold open), se
// compone en el canvas (1080×1920 los shorts, 1920×1080 los videos) y se
// codifica. Audio: se decodifica el tramo,
// se recompone con la misma línea de tiempo y se normaliza a -14 LUFS.
// Salida: MP4 H.264/AAC si el navegador puede codificar H.264 (Chrome/Edge);
// si no, WebM VP9/Opus.

import * as MB from "mediabunny";
import { frameSource } from "./compositor.js";
import { normalize } from "./loudness.js";
import { Resampler } from "./dsp.js";

const OUT_RATE = 48000; // Opus solo admite 48 kHz; AAC también va bien a 48 kHz

let outputChoice = null;

/** Elige contenedor y códecs según lo que el navegador sepa codificar. */
export async function chooseOutput(force = null) {
  if (outputChoice && !force) return outputChoice;
  const size = { width: 1080, height: 1920, bitrate: 8e6 };
  if (force !== "webm" && await MB.canEncodeVideo("avc", size)) {
    if (!(await MB.canEncodeAudio("aac", { numberOfChannels: 2, sampleRate: 48000 }))) {
      // AAC por WebAssembly cuando el sistema no trae codificador (p.ej. Linux)
      const { registerAacEncoder } = await import("@mediabunny/aac-encoder");
      registerAacEncoder();
    }
    outputChoice = { ext: "mp4", mime: "video/mp4", video: "avc", audio: "aac", format: () => new MB.Mp4OutputFormat({ fastStart: "in-memory" }) };
  } else {
    const video = (await MB.canEncodeVideo("vp9", size)) ? "vp9" : "av1";
    outputChoice = { ext: "webm", mime: "video/webm", video, audio: "opus", format: () => new MB.WebMOutputFormat() };
  }
  return outputChoice;
}

/**
 * source: { input, videoTrack, audioTrack, t0 } — t0 = primer timestamp del medio.
 * start/end: segundos (relativos al inicio del medio) del tramo del clip.
 * timeline: Timeline con tramos relativos a start.
 * compositor: createCompositor(...).
 */
export async function renderClip({ source, start, end, timeline, compositor, fps = 30, bitrate = 8e6, onProgress, signal } = {}) {
  const choice = await chooseOutput();
  const output = new MB.Output({ format: choice.format(), target: new MB.BufferTarget() });
  const videoSource = new MB.CanvasSource(compositor.canvas, { codec: choice.video, bitrate, keyFrameInterval: 2 });
  output.addVideoTrack(videoSource, { frameRate: fps });
  let audioSource = null;
  if (source.audioTrack) {
    audioSource = new MB.AudioSampleSource({ codec: choice.audio, bitrate: 160e3 });
    output.addAudioTrack(audioSource);
  }
  await output.start();

  const t0 = source.t0 || 0;
  try {
    // ---- audio ----
    if (audioSource) {
      onProgress?.(0, "Procesando audio");
      const decoded = await decodeAudio(source.audioTrack, t0 + start, t0 + end);
      let { channels } = decoded;
      if (decoded.sampleRate !== OUT_RATE) {
        channels = channels.map((ch) => new Resampler(decoded.sampleRate, OUT_RATE).push(ch).slice());
      }
      const sampleRate = OUT_RATE;
      const outCh = buildAudio(channels, sampleRate, timeline);
      normalize(outCh, sampleRate);
      const chunk = 4096;
      const total = outCh[0].length;
      for (let off = 0; off < total; off += chunk) {
        const n = Math.min(chunk, total - off);
        const data = new Float32Array(n * outCh.length);
        outCh.forEach((ch, c) => data.set(ch.subarray(off, off + n), c * n));
        const sample = new MB.AudioSample({ data, format: "f32-planar", numberOfChannels: outCh.length, sampleRate, timestamp: off / sampleRate });
        await audioSource.add(sample);
        sample.close();
      }
    }

    // ---- vídeo ----
    const total = Math.max(1, Math.round(timeline.duration * fps));
    const sink = new MB.VideoSampleSink(source.videoTrack);
    function* times() {
      for (let i = 0; i < total; i++) yield t0 + start + timeline.sourceAt((i + 0.5) / fps);
    }
    let i = 0;
    let last = null;
    for await (const sample of sink.samplesAtTimestamps(times())) {
      if (signal?.aborted) throw new DOMException("Render cancelado", "AbortError");
      const tOut = i / fps;
      if (sample) {
        compositor.draw(frameSource(sample), tOut);
        if (last && last !== sample) last.close();
        last = sample;
      } else if (last) {
        compositor.draw(frameSource(last), tOut);
      }
      await videoSource.add(tOut, 1 / fps);
      i++;
      if (i % 10 === 0) onProgress?.(i / total, "Renderizando");
    }
    last?.close();
    await output.finalize();
  } catch (err) {
    await output.cancel().catch(() => {});
    throw err;
  }
  onProgress?.(1, "Clip renderizado");
  return { blob: new Blob([output.target.buffer], { type: choice.mime }), ext: choice.ext, mime: choice.mime };
}

/** Decodifica [a, b] a canales planos Float32 (máx. 2 canales). */
export async function decodeAudio(track, a, b) {
  const sampleRate = await track.getSampleRate();
  const nIn = await track.getNumberOfChannels();
  const nCh = Math.min(2, Math.max(1, nIn));
  const len = Math.max(1, Math.round((b - a) * sampleRate));
  const channels = Array.from({ length: nCh }, () => new Float32Array(len));
  const sink = new MB.AudioSampleSink(track);
  for await (const s of sink.samples(a, b)) {
    const n = s.numberOfFrames;
    let dst = Math.round((s.timestamp - a) * sampleRate);
    let srcOff = 0;
    if (dst < 0) { srcOff = -dst; dst = 0; }
    const count = Math.min(n - srcOff, len - dst);
    if (count > 0) {
      for (let c = 0; c < nCh; c++) {
        const buf = new Float32Array(n);
        s.copyTo(buf, { planeIndex: Math.min(c, s.numberOfChannels - 1), format: "f32-planar" });
        channels[c].set(buf.subarray(srcOff, srcOff + count), dst);
      }
    }
    s.close();
  }
  return { channels, sampleRate };
}

/** Recompone el audio según los tramos de la línea de tiempo (con micro-fundidos). */
export function buildAudio(channels, sampleRate, timeline) {
  const total = Math.round(timeline.duration * sampleRate);
  const out = channels.map(() => new Float32Array(total));
  const fade = Math.round(0.006 * sampleRate);
  let pos = 0;
  for (const [a, b] of timeline.pieces) {
    const s0 = Math.round(a * sampleRate), s1 = Math.min(Math.round(b * sampleRate), channels[0].length);
    const n = Math.max(0, Math.min(s1 - s0, total - pos));
    channels.forEach((ch, c) => {
      out[c].set(ch.subarray(s0, s0 + n), pos);
      // fundidos de 6 ms en los cortes: evitan chasquidos
      for (let i = 0; i < Math.min(fade, n); i++) {
        const g = i / fade;
        out[c][pos + i] *= g;
        out[c][pos + n - 1 - i] *= g;
      }
    });
    pos += n;
  }
  return out;
}

/** Miniatura JPEG (dataURL) de un instante del vídeo. */
export async function thumbnail(track, t, width = 320) {
  const sink = new MB.CanvasSink(track, { width });
  const wrapped = await sink.getCanvas(t);
  if (!wrapped) return "";
  const c = wrapped.canvas;
  if (c.convertToBlob) {
    const blob = await c.convertToBlob({ type: "image/jpeg", quality: 0.75 });
    return URL.createObjectURL(blob);
  }
  return c.toDataURL("image/jpeg", 0.75);
}
