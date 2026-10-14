// DSP mínimo: FFT radix-2, remuestreo lineal en streaming y utilidades.

const fftCache = new Map();

function plan(n) {
  if (fftCache.has(n)) return fftCache.get(n);
  const levels = Math.log2(n);
  if (!Number.isInteger(levels)) throw new Error("FFT: n debe ser potencia de 2");
  const rev = new Uint32Array(n);
  for (let i = 0; i < n; i++) {
    let r = 0;
    for (let b = 0; b < levels; b++) r |= ((i >> b) & 1) << (levels - 1 - b);
    rev[i] = r;
  }
  const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
  for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos((2 * Math.PI * i) / n); sin[i] = -Math.sin((2 * Math.PI * i) / n); }
  const hann = new Float64Array(n);
  for (let i = 0; i < n; i++) hann[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (n - 1));
  const p = { rev, cos, sin, hann };
  fftCache.set(n, p);
  return p;
}

/** Espectro de potencia (n/2 + 1 bins) de `x` (longitud n) con ventana de Hann. */
export function powerSpectrum(x, n = x.length) {
  const { rev, cos, sin, hann } = plan(n);
  const re = new Float64Array(n), im = new Float64Array(n);
  for (let i = 0; i < n; i++) re[rev[i]] = (x[i] || 0) * hann[i];
  for (let size = 2; size <= n; size <<= 1) {
    const half = size >> 1, step = n / size;
    for (let start = 0; start < n; start += size) {
      for (let k = 0; k < half; k++) {
        const wr = cos[k * step], wi = sin[k * step];
        const a = start + k, b = a + half;
        const tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
        re[b] = re[a] - tr; im[b] = im[a] - ti;
        re[a] += tr; im[a] += ti;
      }
    }
  }
  const out = new Float64Array(n / 2 + 1);
  for (let i = 0; i <= n / 2; i++) out[i] = re[i] * re[i] + im[i] * im[i];
  return out;
}

/** Remuestreador lineal con estado (para bloques de audio consecutivos). */
export class Resampler {
  constructor(fromRate, toRate) {
    this.step = fromRate / toRate;
    this.t = 0;     // posición de la próxima muestra de salida, relativa a input[0]
    this.prev = 0;  // última muestra del bloque anterior (índice -1)
    // Paso bajo (2 polos) antes de diezmar: evita aliasing de lo que pasa de Nyquist
    this.alpha = this.step > 1 ? 1 - Math.exp((-2 * Math.PI * (0.45 * toRate)) / fromRate) : 1;
    this.y1 = 0; this.y2 = 0;
  }

  push(raw) {
    const n = raw.length;
    if (!n) return new Float32Array(0);
    let input = raw;
    if (this.alpha < 1) {
      input = new Float32Array(n);
      let y1 = this.y1, y2 = this.y2;
      const a = this.alpha;
      for (let i = 0; i < n; i++) { y1 += a * (raw[i] - y1); y2 += a * (y1 - y2); input[i] = y2; }
      this.y1 = y1; this.y2 = y2;
    }
    const out = new Float32Array(Math.max(0, Math.ceil((n - this.t) / this.step) + 1));
    let k = 0;
    let t = this.t;
    while (t <= n - 1) {
      const i = Math.floor(t);
      const frac = t - i;
      const a = i < 0 ? this.prev : input[i];
      const b = i + 1 <= n - 1 ? input[i + 1] : input[n - 1];
      out[k++] = a + (b - a) * frac;
      t += this.step;
    }
    this.t = t - n;
    this.prev = input[n - 1];
    return out.subarray(0, k);
  }
}

/** Mezcla a mono un AudioSample de mediabunny (Float32). */
export function sampleToMono(sample) {
  const n = sample.numberOfFrames;
  const nCh = sample.numberOfChannels;
  const mono = new Float32Array(n);
  const buf = new Float32Array(n);
  for (let c = 0; c < nCh; c++) {
    sample.copyTo(buf, { planeIndex: c, format: "f32-planar" });
    for (let i = 0; i < n; i++) mono[i] += buf[i] / nCh;
  }
  return mono;
}

export const yieldToUI = () => new Promise((r) => setTimeout(r, 0));
