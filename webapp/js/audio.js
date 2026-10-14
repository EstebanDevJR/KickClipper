// Análisis de audio en el navegador (equivalente a audio_analysis.py).
//
// Se decodifica el audio en streaming (WebCodecs), se pasa a mono 16 kHz y se
// calculan por segundo: volumen (dB), flujo espectral, brillo (gritos) y ritmo
// de risa. Con eso se arma la curva de excitación 0..1.

import * as MB from "mediabunny";
import { powerSpectrum, Resampler, sampleToMono, yieldToUI } from "./dsp.js";
import { combineExcitement } from "./signals.js";

const SR = 16000;
const FRAME = 1024;
const BIN_HZ = SR / FRAME;

/**
 * track: pista de audio de mediabunny. start/end en segundos relativos al
 * medio; t0: primer timestamp del medio (se suma para direccionar la pista).
 */
export async function analyzeAudio(track, { start = 0, end, t0 = 0, onProgress, signal } = {}) {
  const rate = await track.getSampleRate();
  const resampler = new Resampler(rate, SR);
  const sink = new MB.AudioSampleSink(track);
  const second = new Float32Array(SR);
  let fill = 0;
  const rmsDb = [], flux = [], bright = [], laugh = [];
  let prevSpec = null;
  const total = Math.max(end - start, 1);
  let lastYield = performance.now();

  const hiLo = Math.round(1500 / BIN_HZ), hiHi = Math.round(5000 / BIN_HZ);
  const loLo = Math.round(100 / BIN_HZ), loHi = Math.round(1000 / BIN_HZ);

  function processSecond(x) {
    let ss = 0;
    for (let i = 0; i < SR; i++) ss += x[i] * x[i];
    rmsDb.push(20 * Math.log10(Math.sqrt(ss / SR + 1e-12) + 1e-12));

    // espectro medio de 15 ventanas de 1024
    const avg = new Float64Array(FRAME / 2 + 1);
    const hop = Math.floor((SR - FRAME) / 14);
    for (let f = 0; f < 15; f++) {
      const p = powerSpectrum(x.subarray(f * hop, f * hop + FRAME), FRAME);
      for (let k = 0; k < avg.length; k++) avg[k] += p[k] / 15;
    }
    let eHi = 0, eLo = 0;
    for (let k = hiLo; k < hiHi; k++) eHi += avg[k];
    for (let k = loLo; k < loHi; k++) eLo += avg[k];
    bright.push(Math.log10((eHi + 1e-9) / (eLo + 1e-9)));

    const mag = avg.map(Math.sqrt);
    const sum = mag.reduce((a, b) => a + b, 0) + 1e-12;
    const norm = mag.map((v) => v / sum);
    let fl = 0;
    if (prevSpec) for (let k = 0; k < norm.length; k++) fl += Math.abs(norm[k] - prevSpec[k]);
    flux.push(fl);
    prevSpec = norm;

    // envolvente a 100 Hz y su modulación a 3,5-7 Hz ("ja-ja-ja")
    const env = new Float64Array(100);
    let mean = 0;
    for (let j = 0; j < 100; j++) {
      let acc = 0;
      for (let i = j * 160; i < (j + 1) * 160; i++) acc += x[i] * x[i];
      env[j] = Math.sqrt(acc / 160 + 1e-12);
      mean += env[j] / 100;
    }
    let band = 0, all = 0;
    for (let f = 1; f <= 50; f++) {
      let re = 0, im = 0;
      for (let j = 0; j < 100; j++) {
        const w = (env[j] - mean) * (0.5 - 0.5 * Math.cos((2 * Math.PI * j) / 99));
        re += w * Math.cos((2 * Math.PI * f * j) / 100);
        im -= w * Math.sin((2 * Math.PI * f * j) / 100);
      }
      const pw = re * re + im * im;
      all += pw;
      if (f >= 4 && f <= 7) band += pw;
    }
    laugh.push(band / (all + 1e-12));
  }

  for await (const sample of sink.samples(t0 + start, t0 + end)) {
    if (signal?.aborted) { sample.close(); throw new DOMException("Cancelado", "AbortError"); }
    const mono = sampleToMono(sample);
    const ts = sample.timestamp - t0;
    sample.close();
    const res = resampler.push(mono);
    let off = 0;
    while (off < res.length) {
      const take = Math.min(SR - fill, res.length - off);
      second.set(res.subarray(off, off + take), fill);
      fill += take; off += take;
      if (fill === SR) { processSecond(second); fill = 0; }
    }
    if (performance.now() - lastYield > 50) {
      onProgress?.(Math.min((ts - start) / total, 0.999), "Analizando audio");
      await yieldToUI();
      lastYield = performance.now();
    }
  }
  if (!rmsDb.length) return null;
  const feats = {
    rmsDb: Float64Array.from(rmsDb), flux: Float64Array.from(flux),
    bright: Float64Array.from(bright), laugh: Float64Array.from(laugh),
  };
  feats.excitement = combineExcitement(feats);
  onProgress?.(1, "Audio analizado");
  return feats;
}

/** Audio mono 16 kHz de [a, b] (para Whisper). */
export async function extractPcm16k(track, a, b, t0 = 0) {
  const rate = await track.getSampleRate();
  const resampler = new Resampler(rate, SR);
  const sink = new MB.AudioSampleSink(track);
  const parts = [];
  let total = 0;
  let started = false;
  for await (const sample of sink.samples(t0 + a, t0 + b)) {
    let mono = sampleToMono(sample);
    if (!started) {
      // recorta el principio del primer bloque si empieza antes de `a`
      const skip = Math.max(0, Math.round((t0 + a - sample.timestamp) * sample.sampleRate));
      mono = mono.subarray(Math.min(skip, mono.length));
      started = true;
    }
    sample.close();
    const res = resampler.push(mono);
    parts.push(res.slice());
    total += res.length;
  }
  const want = Math.round((b - a) * SR);
  const out = new Float32Array(Math.min(total, want));
  let off = 0;
  for (const p of parts) {
    if (off >= out.length) break;
    out.set(p.subarray(0, out.length - off), off);
    off += p.length;
  }
  return out;
}

/** Energía en dB cada `hop` segundos de [a, b] (para decidir los jump cuts). */
export async function fineEnergy(track, a, b, t0 = 0, hop = 0.05) {
  const pcm = await extractPcm16k(track, a, b, t0);
  const step = Math.round(SR * hop);
  const n = Math.floor(pcm.length / step);
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let j = i * step; j < (i + 1) * step; j++) acc += pcm[j] * pcm[j];
    out[i] = 20 * Math.log10(Math.sqrt(acc / step + 1e-12) + 1e-12);
  }
  return out;
}
