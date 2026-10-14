// Línea de tiempo de salida de un clip (port de kickclipper/pipeline/timeline.py).
// Lista ordenada de tramos del original [a, b] (segundos relativos al inicio del
// clip) que forman el vídeo final: permite jump cuts y "cold open".

const MIN_PIECE = 0.12;

export class Timeline {
  constructor(pieces) { this.pieces = pieces; }

  get duration() { return this.pieces.reduce((acc, [a, b]) => acc + (b - a), 0); }

  offsets() {
    const out = [];
    let acc = 0;
    for (const [a, b] of this.pieces) { out.push(acc); acc += b - a; }
    return out;
  }

  /** Tiempo fuente (relativo al clip) correspondiente al instante de salida t. */
  sourceAt(t) {
    const offs = this.offsets();
    for (let i = 0; i < this.pieces.length; i++) {
      const [a, b] = this.pieces[i];
      if (t < offs[i] + (b - a) || i === this.pieces.length - 1) return Math.min(a + (t - offs[i]), b);
    }
    return 0;
  }

  mapInterval(a, b) {
    const res = [];
    const offs = this.offsets();
    this.pieces.forEach(([p0, p1], i) => {
      const lo = Math.max(a, p0), hi = Math.min(b, p1);
      if (hi > lo) res.push([offs[i] + lo - p0, offs[i] + hi - p0]);
    });
    return res;
  }

  mapTime(t) {
    const offs = this.offsets();
    return this.pieces.flatMap(([p0, p1], i) => (p0 <= t && t < p1 ? [offs[i] + t - p0] : []));
  }

  /** Palabras {w,s,e} en tiempo fuente → tiempo de salida (pueden duplicarse en el cold open). */
  mapWords(words) {
    const out = [];
    const offs = this.offsets();
    this.pieces.forEach(([p0, p1], i) => {
      for (const w of words) {
        const lo = Math.max(w.s, p0), hi = Math.min(w.e, p1);
        const dur = Math.max(w.e - w.s, 1e-3);
        if (hi - lo < 0.5 * dur && !(p0 <= w.s && w.s < p1)) continue;
        if (hi <= lo) continue;
        out.push({ ...w, s: offs[i] + lo - p0, e: offs[i] + hi - p0 });
      }
    });
    return out.sort((x, y) => x.s - y.s);
  }

  mapSpans(spans) {
    const out = [];
    for (const sp of spans) for (const [t0, t1] of this.mapInterval(sp.t0, sp.t1)) out.push({ ...sp, t0, t1 });
    return out.sort((x, y) => x.t0 - y.t0);
  }
}

/**
 * words: palabras con tiempos relativos al clip.
 * energy: energía en dB con resolución `hop` (opcional): una pausa de voz solo
 *   se recorta si el audio también está en calma (en gameplay puede ser tensión).
 * preview: tramo [a, b] que se repite al principio (cold open).
 */
export function buildTimeline(clipLen, words = [], { energy = null, hop = 0.05, jumpcut = false,
  minGap = 0.7, pad = 0.18, preview = null } = {}) {
  let keep = [[0, clipLen]];
  if (jumpcut && words.length) {
    for (const [g0, g1] of quietGaps(clipLen, words, energy, hop, minGap)) {
      const a = g0 <= 1e-6 ? 0 : g0 + pad;
      const b = g1 >= clipLen - 1e-6 ? clipLen : g1 - pad;
      keep = subtract(keep, a, b);
    }
    keep = keep.filter(([a, b]) => b - a >= MIN_PIECE);
    if (keep.reduce((acc, [a, b]) => acc + b - a, 0) < 0.35 * clipLen) keep = [[0, clipLen]];
  }
  const pieces = keep.slice();
  if (preview) {
    const p0 = Math.max(0, preview[0]), p1 = Math.min(clipLen, preview[1]);
    if (p1 - p0 >= 0.5) pieces.unshift([p0, p1]);
  }
  return new Timeline(pieces);
}

function quietGaps(clipLen, words, energy, hop, minGap) {
  const ws = [...words].sort((a, b) => a.s - b.s);
  const cands = [];
  if (ws.length && ws[0].s > minGap) cands.push([0, ws[0].s]);
  for (let i = 1; i < ws.length; i++) if (ws[i].s - ws[i - 1].e > minGap) cands.push([ws[i - 1].e, ws[i].s]);
  if (ws.length && clipLen - ws[ws.length - 1].e > minGap) cands.push([ws[ws.length - 1].e, clipLen]);
  if (!energy || !energy.length) return cands;
  const speech = new Uint8Array(energy.length);
  for (const w of ws) for (let i = Math.floor(w.s / hop); i < Math.ceil(w.e / hop) && i < energy.length; i++) speech[i] = 1;
  const speechVals = [];
  energy.forEach((v, i) => { if (speech[i]) speechVals.push(v); });
  const ref = speechVals.length ? median(speechVals) : median(Array.from(energy));
  return cands.filter(([g0, g1]) => {
    const seg = energy.slice(Math.floor(g0 / hop), Math.ceil(g1 / hop));
    if (!seg.length) return true;
    let loud = 0;
    for (const v of seg) if (v > ref - 6) loud++;
    return loud / seg.length <= 0.25; // acción/risas/gritos en la pausa: no se toca
  });
}

function median(arr) {
  const s = Float64Array.from(arr).sort();
  return s.length ? s[Math.floor(s.length / 2)] : 0;
}

function subtract(keep, a, b) {
  if (b - a <= 0) return keep;
  const out = [];
  for (const [k0, k1] of keep) {
    if (b <= k0 || a >= k1) { out.push([k0, k1]); continue; }
    if (a > k0) out.push([k0, a]);
    if (b < k1) out.push([b, k1]);
  }
  return out;
}
