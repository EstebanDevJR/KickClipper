// Palabras → frases y limpieza de la transcripción (módulo puro).

// Alucinaciones típicas de Whisper sobre música/silencio
const HALLUCINATIONS = new Set([
  "subtitulos realizados por la comunidad de amaraorg", "subtitulos por la comunidad de amaraorg",
  "gracias por ver el video", "gracias por ver", "suscribete", "thanks for watching",
  "thank you for watching", "thank you", "musica", "music",
]);

/** Agrupa palabras en frases: corta en puntuación final, pausas o > 12 s. */
export function toSegments(words) {
  const segs = [];
  let cur = [];
  const flush = () => {
    if (!cur.length) return;
    segs.push({ text: cur.map((w) => w.w).join(" "), start: cur[0].s, end: cur[cur.length - 1].e, words: cur });
    cur = [];
  };
  for (const w of words) {
    if (cur.length && (w.s - cur[cur.length - 1].e > 0.8 || w.e - cur[0].s > 12)) flush();
    cur.push(w);
    if (/[.?!…]$/.test(w.w) && w.e - cur[0].s > 1.2) flush();
  }
  flush();
  return segs;
}

const normText = (t) => t.toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9 ]+/g, "").trim();

export function cleanSegments(segments) {
  const out = [];
  let repeat = 0;
  for (const s of segments) {
    const n = normText(s.text);
    if (!n || HALLUCINATIONS.has(n)) continue;
    if (out.length && normText(out[out.length - 1].text) === n) { if (++repeat >= 2) continue; } else repeat = 0;
    out.push(s);
  }
  return out;
}
