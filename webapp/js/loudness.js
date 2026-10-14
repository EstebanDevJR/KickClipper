// Normalización de sonoridad a -14 LUFS (ITU-R BS.1770) con limitador de picos.
// Equivalente a loudnorm=I=-14:TP=-1.5 de la versión de escritorio.

/** Coeficientes del filtro K (dos biquads) para cualquier frecuencia de muestreo. */
function kFilters(fs) {
  // Estante alto (pre-filtro)
  let f0 = 1681.974450955533, G = 3.999843853973347, Q = 0.7071752369554196;
  let K = Math.tan((Math.PI * f0) / fs);
  const Vh = 10 ** (G / 20), Vb = Vh ** 0.4996667741545416;
  let a0 = 1 + K / Q + K * K;
  const shelf = {
    b: [(Vh + (Vb * K) / Q + K * K) / a0, (2 * (K * K - Vh)) / a0, (Vh - (Vb * K) / Q + K * K) / a0],
    a: [(2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0],
  };
  // Paso alto (RLB)
  f0 = 38.13547087602444; Q = 0.5003270373238773;
  K = Math.tan((Math.PI * f0) / fs);
  a0 = 1 + K / Q + K * K;
  const hp = { b: [1, -2, 1], a: [(2 * (K * K - 1)) / a0, (1 - K / Q + K * K) / a0] };
  return [shelf, hp];
}

function biquad(x, { b, a }) {
  const y = new Float32Array(x.length);
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  for (let i = 0; i < x.length; i++) {
    const v = b[0] * x[i] + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2;
    x2 = x1; x1 = x[i]; y2 = y1; y1 = v; y[i] = v;
  }
  return y;
}

/** Sonoridad integrada (LUFS) de canales planos Float32Array[]. */
export function integratedLoudness(channels, fs) {
  const [shelf, hp] = kFilters(fs);
  const filtered = channels.map((ch) => biquad(biquad(ch, shelf), hp));
  const block = Math.round(0.4 * fs), hop = Math.round(0.1 * fs);
  const n = channels[0].length;
  const z = [];
  for (let s = 0; s + block <= n; s += hop) {
    let sum = 0;
    for (const ch of filtered) {
      let acc = 0;
      for (let i = s; i < s + block; i++) acc += ch[i] * ch[i];
      sum += acc / block;
    }
    z.push(sum);
  }
  if (!z.length) return -70;
  const lufs = (v) => -0.691 + 10 * Math.log10(v + 1e-12);
  const abs = z.filter((v) => lufs(v) > -70);
  if (!abs.length) return -70;
  const rel = lufs(abs.reduce((a, b) => a + b, 0) / abs.length) - 10;
  const gated = abs.filter((v) => lufs(v) > rel);
  return lufs(gated.reduce((a, b) => a + b, 0) / Math.max(gated.length, 1));
}

/**
 * Aplica ganancia hacia `target` LUFS y un limitador con 5 ms de anticipación
 * para que ningún pico supere `ceilingDb`. Modifica los canales in situ.
 */
export function normalize(channels, fs, { target = -14, ceilingDb = -1.5, maxGainDb = 15 } = {}) {
  const measured = integratedLoudness(channels, fs);
  if (measured <= -69) return { measured, gainDb: 0 };
  const gainDb = Math.max(-20, Math.min(target - measured, maxGainDb));
  const gain = 10 ** (gainDb / 20);
  const ceil = 10 ** (ceilingDb / 20);
  const n = channels[0].length;
  const req = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let peak = 0;
    for (const ch of channels) { const v = Math.abs(ch[i] * gain); if (v > peak) peak = v; }
    req[i] = peak > ceil ? ceil / peak : 1;
  }
  // mínimo hacia delante (anticipación) + liberación exponencial
  const look = Math.max(1, Math.round(0.005 * fs));
  const release = 1 - Math.exp(-1 / (0.08 * fs));
  const g = new Float32Array(n);
  const dq = [];
  let cur = 1;
  for (let i = n - 1 + look; i >= 0; i--) {
    // ventana [i, i + look]: deque de mínimos recorrida de derecha a izquierda
    if (i < n) {
      while (dq.length && req[dq[dq.length - 1]] >= req[i]) dq.pop();
      dq.push(i);
    }
    while (dq.length && dq[0] > i + look) dq.shift();
    if (i < n) g[i] = dq.length ? req[dq[0]] : 1;
  }
  for (let i = 0; i < n; i++) {
    cur = g[i] < cur ? g[i] : cur + (1 - cur) * release;
    if (cur > g[i]) cur = g[i];
    for (const ch of channels) ch[i] = ch[i] * gain * cur;
  }
  return { measured, gainDb };
}
