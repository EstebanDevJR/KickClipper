// Utilidades numéricas para curvas por segundo (audio, chat, excitación).
// Port de kickclipper/pipeline/signals.py: mismas fórmulas en las dos versiones.

export function percentile(arr, p) {
  if (!arr.length) return 0;
  const s = Float64Array.from(arr).sort();
  const idx = (p / 100) * (s.length - 1);
  const lo = Math.floor(idx), hi = Math.ceil(idx);
  return s[lo] + (s[hi] - s[lo]) * (idx - lo);
}

export function median(arr) {
  return percentile(arr, 50);
}

/** Normaliza a 0..1 con percentiles (robusto frente a outliers). */
export function robustNorm(x, loPct = 10, hiPct = 98) {
  const out = new Float64Array(x.length);
  if (!x.length) return out;
  const lo = percentile(x, loPct), hi = percentile(x, hiPct);
  if (hi - lo < 1e-9) return out;
  for (let i = 0; i < x.length; i++) out[i] = Math.min(Math.max((x[i] - lo) / (hi - lo), 0), 1);
  return out;
}

/** Mediana centrada de ancho `win` (bordes replicados; ventana submuestreada si es larga). */
export function rollingMedian(x, win) {
  const n = x.length;
  const out = new Float64Array(n);
  if (!n) return out;
  win = Math.min(win, n);
  if (win % 2 === 0) win -= 1;
  if (win <= 1) { out.set(x); return out; }
  const half = (win - 1) / 2;
  const k = Math.max(1, Math.floor(win / 61));
  const buf = new Float64Array(Math.ceil(win / k));
  for (let i = 0; i < n; i++) {
    let m = 0;
    for (let j = -half; j <= half; j += k) {
      const idx = Math.min(Math.max(i + j, 0), n - 1);
      buf[m++] = x[idx];
    }
    const view = buf.subarray(0, m).sort();
    out[i] = m % 2 ? view[(m - 1) / 2] : (view[m / 2 - 1] + view[m / 2]) / 2;
  }
  return out;
}

/** Suavizado con ventana de Hann normalizada. */
export function smooth(x, width) {
  const n = x.length;
  const out = new Float64Array(n);
  if (width <= 1 || n < 3) { out.set(x); return out; }
  const kernel = [];
  let sum = 0;
  for (let i = 1; i <= width; i++) {
    const v = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (width + 1));
    kernel.push(v); sum += v;
  }
  const half = Math.floor(width / 2);
  for (let i = 0; i < n; i++) {
    let acc = 0;
    for (let j = 0; j < width; j++) {
      const idx = Math.min(Math.max(i + j - half, 0), n - 1);
      acc += x[idx] * kernel[j];
    }
    out[i] = acc / sum;
  }
  return out;
}

/** Cuánto sobresale cada punto de su línea base local, 0..1. */
export function contrast(x, win = 61) {
  const n = x.length;
  const out = new Float64Array(n);
  if (!n) return out;
  const base = rollingMedian(x, win);
  const dev = new Float64Array(n);
  for (let i = 0; i < n; i++) dev[i] = Math.abs(x[i] - base[i]);
  const spread = rollingMedian(dev, win);
  for (let i = 0; i < n; i++) {
    const z = (x[i] - base[i]) / (spread[i] * 1.4826 + 1e-6);
    out[i] = Math.min(Math.max(z / 4, 0), 1);
  }
  return out;
}

/** Máximos separados al menos `minDistance`, ordenados por valor (estrictamente > threshold). */
export function findPeaks(x, minDistance, threshold = null, limit = null) {
  const n = x.length;
  if (!n) return [];
  if (threshold === null) threshold = Math.min(...x);
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => x[b] - x[a] || a - b);
  const taken = new Uint8Array(n);
  const peaks = [];
  for (const i of order) {
    if (x[i] <= threshold) break;
    if (taken[i]) continue;
    peaks.push(i);
    for (let j = Math.max(0, i - minDistance); j <= Math.min(n - 1, i + minDistance); j++) taken[j] = 1;
    if (limit && peaks.length >= limit) break;
  }
  return peaks;
}

/** Curva de excitación 0..1 (mismos pesos que audio_analysis.combine en Python). */
export function combineExcitement({ rmsDb, flux, bright, laugh }) {
  const n = rmsDb.length;
  const loud = robustNorm(rmsDb);
  const c1 = contrast(rmsDb, 61), c5 = contrast(rmsDb, 301);
  const fl = robustNorm(flux), br = robustNorm(bright), la = robustNorm(laugh);
  const score = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const voiced = loud[i] > 0.25 ? 1 : 0;
    score[i] = 0.34 * loud[i] + 0.26 * (0.5 * c1[i] + 0.5 * c5[i]) + 0.18 * fl[i]
      + 0.12 * br[i] * voiced + 0.10 * la[i] * voiced;
  }
  const sm = smooth(score, 5);
  for (let i = 0; i < n; i++) sm[i] = Math.min(Math.max(sm[i], 0), 1);
  return sm;
}

/** (media, máximo) de una curva por segundo dentro de [start, end). */
export function windowStats(curve, start, end) {
  if (!curve || !curve.length) return [0, 0];
  const i0 = Math.max(0, Math.min(Math.floor(start), curve.length - 1));
  const i1 = Math.max(i0 + 1, Math.min(Math.ceil(end), curve.length));
  let sum = 0, max = -Infinity;
  for (let i = i0; i < i1; i++) { sum += curve[i]; if (curve[i] > max) max = curve[i]; }
  return [sum / (i1 - i0), max];
}
