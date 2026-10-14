// Subtítulos y texto gancho dibujados en canvas (equivalente a subtitles.py).
//
// - "viral": 1-3 palabras en MAYÚSCULAS (Anton), contorno grueso, "pop" al
//   aparecer, palabra activa resaltada, palabras clave en verde y un color
//   por hablante.
// - "karaoke": hasta 4 palabras, palabra activa en ámbar.
// - "clean": frases de 1-2 líneas sin animación.
// Todo dentro de la zona segura 9:16 (arriba 288 px, abajo 672 px sobre 1920).

export const STYLES = ["viral", "karaoke", "clean", "none"];
export const SAFE_TOP = 288 / 1920;
export const SAFE_BOTTOM = 672 / 1920;

const WHITE = "#ffffff";
const YELLOW = "#ffe14d";
const AMBER = "#ffd24a";
const GREEN = "#53fc18";
export const SPEAKER_COLORS = [YELLOW, "#4de3ff", "#ff6bd6", "#ff9f43"];

const STRONG_RE = /^(noo+|s[ií]{2,}|wtf|omg|bro{2,}|dios|hostia|joder|brutal|insano|incre[ií]ble|ja(ja)+|je(je)+|gg|ace|clutch|imposible|locura|literal(ly)?)$/i;

let fontsReady = null;

/** Carga las fuentes incluidas (Anton, Poppins ExtraBold) una sola vez. */
export function loadFonts(base = "fonts/") {
  if (!fontsReady) {
    const faces = [
      new FontFace("KC Anton", `url(${base}Anton-Regular.ttf)`),
      new FontFace("KC Poppins", `url(${base}Poppins-ExtraBold.ttf)`, { weight: "800" }),
    ];
    fontsReady = Promise.all(faces.map((f) => f.load().then((ff) => { document.fonts.add(ff); return ff; })))
      .catch((err) => { console.warn("Fuentes no cargadas, se usan las del sistema", err); });
  }
  return fontsReady;
}

const norm = (t) => String(t).toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[^a-z0-9]+/g, "");
const cleanWord = (t) => String(t).replace(/[.,;:"“”«»]+/g, "").trim();

/** Agrupa palabras en tarjetas cortando en pausas, puntuación, longitud y cambio de hablante. */
export function cards(words, { maxWords, maxChars, maxSecs, gap }) {
  const out = [];
  let cur = [];
  for (const w of words) {
    if (!String(w.w || "").trim()) continue;
    if (cur.length) {
      const chars = cur.reduce((a, x) => a + x.w.length, 0) + w.w.length;
      const ended = /[.?!…]$/.test(cur[cur.length - 1].w);
      if (cur.length >= maxWords || chars > maxChars || w.e - cur[0].s > maxSecs || w.s - cur[cur.length - 1].e > gap
        || ended || w.spk !== cur[cur.length - 1].spk) {
        out.push(cur); cur = [];
      }
    }
    cur.push(w);
  }
  if (cur.length) out.push(cur);
  return out.map((ws) => ({ words: ws, s: ws[0].s, e: ws[ws.length - 1].e }));
}

/** Prepara las tarjetas de un clip (palabras ya en tiempo de salida). */
export function buildCaptionTrack(words, { style = "viral", keywords = [] } = {}) {
  if (style === "none" || !words?.length) return { style: "none", cards: [] };
  const opts = style === "viral" ? { maxWords: 3, maxChars: 16, maxSecs: 1.6, gap: 0.45 }
    : style === "karaoke" ? { maxWords: 4, maxChars: 28, maxSecs: 2.2, gap: 0.8 }
      : { maxWords: 12, maxChars: 60, maxSecs: 4.5, gap: 0.9 };
  const kw = new Set(keywords.map(norm).filter(Boolean));
  const speakers = new Set(words.map((w) => w.spk).filter((s) => s !== undefined && s !== null));
  return { style, cards: cards(words, opts), keywords: kw, multiSpeaker: speakers.size >= 2 };
}

function isKeyword(word, kw) {
  const n = norm(word);
  if (!n) return false;
  return kw.has(n) || /\d/.test(n) || STRONG_RE.test(cleanWord(word));
}

function findCard(track, t) {
  // búsqueda binaria: última tarjeta que empezó
  const cs = track.cards;
  let lo = 0, hi = cs.length - 1, idx = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cs[mid].s <= t) { idx = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (idx < 0) return null;
  const card = cs[idx];
  return t < card.e + (track.style === "clean" ? 0.15 : 0) ? card : null;
}

/**
 * Dibuja los subtítulos del instante t.
 * y: centro vertical (0..1); se limita a la zona segura.
 */
export function drawCaptions(ctx, track, t, { W, H, y = 0.62, vertical = true } = {}) {
  if (!track || track.style === "none") return;
  const card = findCard(track, t);
  if (!card) return;
  if (vertical) y = Math.min(Math.max(y, SAFE_TOP + 0.06), 1 - SAFE_BOTTOM - 0.02);
  const cy = H * y;
  const style = track.style;
  const viral = style === "viral";
  let size = vertical ? H * (viral ? 0.058 : style === "karaoke" ? 0.042 : 0.036) : H * (viral ? 0.075 : 0.052);
  const family = viral ? "KC Anton, Anton, Impact, sans-serif" : "KC Poppins, Poppins, Arial Black, sans-serif";
  const weight = viral ? "400" : "800";
  const maxW = W - 120;

  let activeWord = card.words[0];
  for (const w of card.words) if (w.s <= t) activeWord = w;

  if (style === "clean") {
    const text = card.words.map((w) => w.w).join(" ");
    drawTextLines(ctx, wrap(ctx, text, `${weight} ${size}px ${family}`, maxW), W / 2, cy, size, family, weight, WHITE);
    return;
  }

  const items = card.words.map((w) => ({ word: w, tx: viral ? cleanWord(w.w).toUpperCase() : w.w })).filter((it) => it.tx);
  if (!items.length) return;
  const texts = items.map((it) => it.tx);
  ctx.font = `${weight} ${size}px ${family}`;
  const space = ctx.measureText(" ").width;
  let widths = texts.map((tx) => ctx.measureText(tx).width);
  let total = widths.reduce((a, b) => a + b, 0) + space * (texts.length - 1);
  if (total > maxW) { // reduce el tamaño para que quepa en una línea
    const k = maxW / total;
    size *= k; widths = widths.map((x) => x * k); total *= k;
    ctx.font = `${weight} ${size}px ${family}`;
  }

  const age = t - card.s;
  const pop = viral && age < 0.1 ? 0.8 + 0.2 * easeOut(age / 0.1) : 1;
  ctx.save();
  ctx.translate(W / 2, cy);
  ctx.scale(pop, pop);
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  let x = -total / 2;
  texts.forEach((tx, i) => {
    const word = items[i].word;
    const isActive = word === activeWord;
    const kw = viral && isKeyword(word.w, track.keywords);
    let color = WHITE;
    if (isActive) color = kw ? GREEN : speakerColor(word, track, viral ? YELLOW : AMBER);
    else if (kw) color = GREEN;
    const scale = isActive && (viral ? i > 0 : true) ? (viral ? 1.1 : 1.08) : 1;
    const cx = x + widths[i] / 2;
    ctx.save();
    ctx.translate(cx, 0);
    ctx.scale(scale, scale);
    ctx.textAlign = "center";
    ctx.shadowColor = "rgba(0,0,0,0.55)";
    ctx.shadowOffsetY = size * 0.05;
    ctx.shadowBlur = size * 0.08;
    ctx.lineWidth = size * (viral ? 0.17 : 0.14);
    ctx.strokeStyle = "#000";
    ctx.strokeText(tx, 0, 0);
    ctx.shadowColor = "transparent";
    ctx.fillStyle = color;
    ctx.fillText(tx, 0, 0);
    ctx.restore();
    x += widths[i] + space;
  });
  ctx.restore();
}

function speakerColor(word, track, fallback) {
  if (track.multiSpeaker && word.spk !== undefined && word.spk !== null) return SPEAKER_COLORS[word.spk % SPEAKER_COLORS.length];
  return fallback;
}

/** Texto gancho: caja blanca con texto oscuro, arriba en la zona segura. */
export function drawHook(ctx, text, t, { W, H, y = SAFE_TOP + 0.01, duration = 3, vertical = true } = {}) {
  if (!text || t > duration) return;
  let size = (vertical ? H : H * 1.4) * 0.033;
  const family = "KC Poppins, Poppins, Arial Black, sans-serif";
  const pad = H * 0.011;
  const maxW = W * 0.84 - pad * 2.4;
  const upper = String(text).toUpperCase();
  // ajuste por ancho real: hasta 3 líneas y, si aún no cabe, letra más pequeña
  let lines;
  for (let i = 0; i < 6; i++) {
    ctx.font = `800 ${size}px ${family}`;
    lines = wrapWords(ctx, upper, maxW, 3);
    if (lines.every((l) => ctx.measureText(l).width <= maxW)) break;
    size *= 0.88;
  }
  const lineH = size * 1.18;
  const widths = lines.map((l) => ctx.measureText(l).width);
  const boxW = Math.max(...widths) + pad * 2.4;
  const boxH = lineH * lines.length + pad * 1.6;
  const fadeIn = Math.min(t / 0.12, 1);
  const fadeOut = Math.min((duration - t) / 0.22, 1);
  const pop = t < 0.14 ? 0.88 + 0.12 * easeOut(t / 0.14) : 1;
  ctx.save();
  ctx.globalAlpha = Math.max(0, Math.min(fadeIn, fadeOut));
  ctx.translate(W / 2, H * y + boxH / 2);
  ctx.scale(pop, pop);
  ctx.shadowColor = "rgba(0,0,0,0.35)";
  ctx.shadowBlur = size * 0.4;
  ctx.shadowOffsetY = size * 0.08;
  ctx.fillStyle = "#ffffff";
  roundRect(ctx, -boxW / 2, -boxH / 2, boxW, boxH, size * 0.28);
  ctx.fill();
  ctx.shadowColor = "transparent";
  ctx.fillStyle = "#101010";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  lines.forEach((l, i) => ctx.fillText(l, 0, -boxH / 2 + pad * 0.8 + lineH * (i + 0.5)));
  ctx.restore();
}

function drawTextLines(ctx, lines, cx, cy, size, family, weight, color) {
  ctx.save();
  ctx.font = `${weight} ${size}px ${family}`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.lineJoin = "round";
  const lh = size * 1.2;
  lines.forEach((l, i) => {
    const y = cy + (i - (lines.length - 1) / 2) * lh;
    ctx.lineWidth = size * 0.14;
    ctx.strokeStyle = "#000";
    ctx.strokeText(l, cx, y);
    ctx.fillStyle = color;
    ctx.fillText(l, cx, y);
  });
  ctx.restore();
}

function wrap(ctx, text, font, maxW) {
  ctx.font = font;
  if (ctx.measureText(text).width <= maxW) return [text];
  return splitTwo(text, Math.ceil(text.length / 2) + 4);
}

/** Reparte palabras en líneas que quepan en maxW (como mucho maxLines; la última se lleva el resto). */
export function wrapWords(ctx, text, maxW, maxLines = 3) {
  const words = text.split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (cur && ctx.measureText(next).width > maxW && lines.length < maxLines - 1) { lines.push(cur); cur = w; }
    else cur = next;
  }
  if (cur) lines.push(cur);
  return lines;
}

export function splitTwo(text, maxLine) {
  if (text.length <= maxLine) return [text];
  const mid = text.length / 2;
  let cut = -1;
  for (let i = 0; i < text.length; i++) if (text[i] === " " && (cut < 0 || Math.abs(i - mid) < Math.abs(cut - mid))) cut = i;
  return cut < 0 ? [text] : [text.slice(0, cut), text.slice(cut + 1)];
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const easeOut = (x) => 1 - (1 - x) * (1 - x);
