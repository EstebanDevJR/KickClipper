// Detección de momentos clipeables (port de kickclipper/pipeline/highlights.py,
// solo formato short). Ver docs/GUIA_CLIPS_VIRALES.md §2-3.
//
// 1. Curva de momentos: excitación del audio + picos del chat (si hay).
// 2. Candidatas: ventanas preparación → pico → reacción y ventanas deslizantes,
//    alineadas a frases.
// 3. Heurística barata sobre todas; 4. LLM local opcional sobre las mejores;
// 5. Ajuste fino (intro sin gancho, cola larga) y supresión de solapamientos.

import { findPeaks, percentile, median, smooth } from "./signals.js";

export const LIMITS = { min: 15, max: 60, target: 40 };

const REACTION_RE = /(jaja|jeje|kkkk|xd+|lmao|lol|no me jodas|no puede ser|qué (es esto|pasó|haces)|increíble|insano|brutal|[eé]pico|hostia|wtf|oh my god|omg|dios mío|what the|clip|clipealo|clipéalo|me muero|literal|na+h?\b|bro+\b|loco\b|gan[éeoa]|perd[íio]|mat[éeoa]|ace\b|clutch|victoria|derrota|no no no|vamos+|let'?s go|holy|nooo+|s[ií]{2,}|qu[eé]{2,}|madre m[ií]a|imposible|locura|est[aá]s loco|meu deus|caralho|nossa)/gi;
const EXCLAIM_RE = /[!?¡¿]/g;
const FILLER_RE = /^\W*(bueno|eh+|em+|este|o sea|a ver|ok(ay)?|vale|vamos a ver|chat|hola|buenas|so\b|um+|uh+|well|like|alright|anyway|y nada|pues)\b/i;
export const CATEGORIES = ["humor", "reaccion", "epico", "fail", "drama", "historia", "opinion", "rage", "wholesome", "otro"];

const count = (re, text) => (text.match(re) || []).length;

export class Context {
  constructor(segments, excitement, chat, duration, limits = LIMITS) {
    const lastEnd = segments.length ? segments[segments.length - 1].end : 0;
    const n = Math.ceil(Math.max(duration, lastEnd)) + 1;
    this.n = n;
    this.limits = limits;
    this.segments = segments;
    this.exc = fit(excitement, n);
    this.chat = chat && chat.length ? fit(chat, n) : null;
    this.moment = new Float64Array(n);
    for (let i = 0; i < n; i++) this.moment[i] = this.chat ? 0.65 * this.exc[i] + 0.35 * this.chat[i] : this.exc[i];
    this.words = new Float64Array(n);
    for (const s of segments) for (const w of s.words) {
      const i = Math.floor(w.s);
      if (i >= 0 && i < n) this.words[i] += 1;
    }
    this.segStarts = segments.map((s) => s.start);
    this.segEnds = segments.map((s) => s.end);
    const sm = smooth(this.moment, 3);
    const thr = n > 10 ? Math.max(percentile(sm, 75), median(sm) + 0.1) : 0;
    this.peaks = findPeaks(sm, 20, thr, 48).sort((a, b) => a - b);
  }
}

function fit(arr, n) {
  const out = new Float64Array(n);
  if (!arr) return out;
  out.set(arr.subarray ? arr.subarray(0, Math.min(n, arr.length)) : arr.slice(0, n));
  return out;
}

/** Ventanas deslizantes alineadas a frases (solo dentro de tramos continuos de transcripción). */
export function buildWindows(segments, limits = LIMITS) {
  const { min, max, target } = limits;
  const stop = target * 1.3;
  const windows = [];
  for (let i = 0; i < segments.length; i++) {
    const start = segments[i].start;
    for (let k = i; k < segments.length; k++) {
      // no saltar huecos grandes (regiones no transcritas)
      if (k > i && segments[k].start - segments[k - 1].end > 8) break;
      const length = segments[k].end - start;
      if (length > max) break;
      if (length < min) continue;
      windows.push(makeWindow(segments, i, k));
      if (length >= stop) break;
    }
  }
  return windows;
}

/** Ventanas preparación → pico → reacción alrededor de cada pico. */
export function peakWindows(segments, ctx, limits = LIMITS) {
  const { min, max } = limits;
  const windows = [];
  for (const p of ctx.peaks) {
    const starts = [], ends = [];
    ctx.segStarts.forEach((s, i) => { if (s >= p - (max - 6) && s <= p - 1.5) starts.push(i); });
    ctx.segEnds.forEach((e, i) => { if (e >= p + 2 && e <= p + 15) ends.push(i); });
    if (!starts.length || !ends.length) continue;
    const closest = (idxs, arr, target) => idxs.reduce((best, i) => (Math.abs(arr[i] - target) < Math.abs(arr[best] - target) ? i : best), idxs[0]);
    const pickS = new Set([4, 9, 16, 26, 40].map((d) => closest(starts, ctx.segStarts, p - d)));
    const pickE = new Set([3, 6, 11].map((d) => closest(ends, ctx.segEnds, p + d)));
    for (const i of pickS) for (const k of pickE) {
      const length = segments[k].end - segments[i].start;
      if (k >= i && length >= min && length <= max) {
        const w = makeWindow(segments, i, k);
        w.peak = p;
        windows.push(w);
      }
    }
  }
  return windows;
}

export function makeWindow(segments, i, k) {
  return { start: segments[i].start, end: segments[k].end, i0: i, i1: k, segments: segments.slice(i, k + 1), format: "short" };
}

export function heuristic(w, ctx) {
  const { max, target } = ctx.limits;
  const s = w.start, e = w.end;
  const dur = Math.max(e - s, 1);
  const i0 = Math.floor(s), i1 = Math.max(Math.ceil(e), i0 + 1);
  const exc = ctx.exc.subarray(i0, i1);
  let aMean = 0, aMax = 0;
  if (exc.length) { aMean = exc.reduce((a, b) => a + b, 0) / exc.length; aMax = Math.max(...exc); }
  const audio = 0.5 * aMean + 0.5 * aMax;

  let chat = null;
  if (ctx.chat) {
    const c = ctx.chat.subarray(i0, i1);
    chat = c.length ? Math.max(...c) : 0;
  }
  const text = w.segments.map((x) => x.text).join(" ");
  const first = w.segments.length ? w.segments[0].text : "";
  const h3 = ctx.exc.subarray(i0, Math.min(i0 + 3, ctx.n));
  const hookAudio = h3.length ? h3.reduce((a, b) => a + b, 0) / h3.length : 0;
  const hookLex = count(REACTION_RE, first) || count(EXCLAIM_RE, first) ? 1 : 0;
  const filler = FILLER_RE.test(first) ? 1 : 0;
  const hook = Math.max(0, 0.55 * hookAudio + 0.45 * hookLex - 0.3 * filler);

  const words = ctx.words.subarray(i0, i1);
  const density = Math.min(words.reduce((a, b) => a + b, 0) / dur / 3, 1);
  const reactions = Math.min(count(REACTION_RE, text) / 3, 1);
  const exclaims = Math.min(count(EXCLAIM_RE, text) / 5, 1);
  let deadN = 0;
  for (let i = 0; i < exc.length; i++) if (words[i] === 0 && exc[i] < 0.3) deadN++;
  const dead = exc.length ? deadN / exc.length : 0;
  const tail = ctx.exc.subarray(Math.max(i1 - 4, i0), i1);
  const ending = tail.length ? Math.min(Math.max(...tail) / (aMax + 1e-6), 1) : 0;
  const lenBonus = Math.max(1 - Math.abs(dur - target) / max, 0);
  const peak = ctx.peaks.some((p) => s + 0.15 * dur <= p && p <= e - 0.1 * dur) ? 1 : 0;

  const weights = { audio: 0.22, chat: 0.16, hook: 0.12, density: 0.10, reactions: 0.12, exclaims: 0.05, ending: 0.08, len: 0.07, peak: 0.08 };
  const vals = { audio, chat, hook, density, reactions, exclaims, ending, len: lenBonus, peak };
  if (chat === null) { weights.audio += weights.chat; delete weights.chat; delete vals.chat; }
  let score = -0.20 * dead;
  for (const k of Object.keys(weights)) score += weights[k] * vals[k];
  w.heuristic = score;
  w.signals = { ...Object.fromEntries(Object.entries(vals).map(([k, v]) => [k, round(v, 3)])), dead_air: round(dead, 3) };
}

export function dedupe(windows, overlap) {
  const kept = [];
  for (const w of windows) {
    const clash = kept.some((k) => {
      const inter = Math.min(w.end, k.end) - Math.max(w.start, k.start);
      const shorter = Math.min(w.end - w.start, k.end - k.start);
      return inter > 0 && inter / shorter > overlap;
    });
    if (!clash) kept.push(w);
  }
  return kept;
}

/**
 * Selección completa. `llm` es opcional: { score(window, ctx) → Promise<dict|null> }.
 * Devuelve las candidatas finales ordenadas.
 */
export async function detect(segments, excitement, duration, { chat = null, channel = "", llm = null,
  maxClips = 8, maxLlm = 16, limits = LIMITS, onProgress = null } = {}) {
  if (!segments.length) return [];
  const ctx = new Context(segments, excitement, chat, duration, limits);
  let windows = buildWindows(segments, limits).concat(peakWindows(segments, ctx, limits));
  if (!windows.length) return [];
  windows.forEach((w) => heuristic(w, ctx));
  windows.sort((a, b) => b.heuristic - a.heuristic);
  const shortlist = dedupe(windows, 0.5).slice(0, Math.max(maxLlm, maxClips));

  let llmOk = 0;
  for (let n = 0; n < shortlist.length; n++) {
    const w = shortlist[n];
    let data = null;
    if (llm && n < maxLlm) {
      try { data = await llm.score(w, ctx, channel); } catch (err) { console.warn("LLM falló", err); }
      onProgress?.((n + 1) / Math.min(shortlist.length, maxLlm), `IA evaluando momento ${n + 1}`);
    }
    if (data) { applyLlm(w, data); w.llmScores.model = llm.name; llmOk++; } else heuristicMetadata(w, ctx, channel);
  }

  for (const w of shortlist) { refine(w, ctx); heuristic(w, ctx); }
  const hs = shortlist.map((w) => w.heuristic);
  const hMin = Math.min(...hs), hMax = Math.max(...hs);
  const llmVals = shortlist.filter((w) => w.llmOk).map((w) => w.llmScore);
  const llmMean = llmVals.length ? llmVals.reduce((a, b) => a + b, 0) / llmVals.length : 0;
  for (const w of shortlist) {
    const hn = (w.heuristic - hMin) / (hMax - hMin + 1e-9);
    if (w.llmOk) w.final = 0.4 * hn * 10 + 0.6 * w.llmScore;
    else if (llmVals.length) { w.final = 0.4 * hn * 10 + 0.6 * llmMean; w.llmScore = 0; }
    else { w.final = hn * 10; w.llmScore = 0; }
  }
  shortlist.sort((a, b) => b.final - a.final);
  return dedupe(shortlist, 0.3).slice(0, maxClips);
}

// ---------- LLM ----------

export function numberedLines(w, ctx) {
  const segs = ctx.segments;
  const lo = Math.max(0, w.i0 - 2), hi = Math.min(segs.length - 1, w.i1 + 2);
  const lines = [], index = [];
  for (let i = lo, n = 1; i <= hi; i++, n++) {
    const tag = i < w.i0 ? "[ANTES] " : i > w.i1 ? "[DESPUÉS] " : "";
    lines.push(`${n}. ${tag}(+${Math.round(segs[i].start - w.start)}s) ${segs[i].text}`);
    index.push(i);
  }
  return { lines: lines.join("\n"), index };
}

export function signalsText(w) {
  const sig = w.signals || {};
  const parts = [`audio ${(sig.audio ?? 0).toFixed(2)}`];
  if ("chat" in sig) parts.push(`chat ${sig.chat.toFixed(2)}`);
  if (w.peak != null) parts.push(`pico de emoción en +${Math.round(w.peak - w.start)}s`);
  parts.push(`silencios ${Math.round((sig.dead_air ?? 0) * 100)}%`);
  return parts.join(", ") + " (0 = nada, 1 = máximo)";
}

export function applyLlm(w, data) {
  const num = (k) => { const v = Number(data[k]); return Number.isFinite(v) ? Math.min(Math.max(v, 0), 10) : 0; };
  const score = num("score"), hook = num("hook"), standalone = num("standalone"), payoff = num("payoff");
  w.llmOk = true;
  w.llmScores = { score, hook, standalone, payoff };
  w.llmScore = 0.45 * score + 0.20 * hook + 0.15 * standalone + 0.20 * payoff;
  w.title = clean(data.title, 80);
  w.hookText = clean(data.hook_text, 60).split(" ").slice(0, 7).join(" ");
  w.description = clean(data.description, 600);
  w.reason = clean(data.reason, 300);
  w.hashtags = (data.hashtags || []).map((h) => String(h).replace(/\s+/g, "").replace(/^#/, "")).filter(Boolean).slice(0, 5);
  w.keywords = (data.keywords || []).map((k) => String(k).trim()).filter(Boolean).slice(0, 5);
  const cat = String(data.category || "").trim().toLowerCase();
  w.category = CATEGORIES.includes(cat) ? cat : "otro";
  const sl = parseInt(data.start_line, 10), el = parseInt(data.end_line, 10);
  if (Number.isInteger(sl) && Number.isInteger(el) && w.lineIndex) w.lines = [sl, el];
}

export function heuristicMetadata(w, ctx, channel) {
  let best = null, bestScore = -1;
  for (const s of w.segments) {
    const sc = count(REACTION_RE, s.text) + count(EXCLAIM_RE, s.text) * 0.5 + ctx.exc[Math.min(Math.floor(s.start), ctx.n - 1)];
    if (sc > bestScore && s.text.length > 6) { best = s; bestScore = sc; }
  }
  const text = ((best || w.segments[0])?.text || "").trim();
  w.title ??= text.slice(0, 60);
  w.hookText ??= text.split(/\s+/).slice(0, 5).join(" ");
  w.description ??= "";
  w.reason ??= "Elegido por señales automáticas (audio, chat y transcripción)";
  w.hashtags ??= [...(channel ? [channel] : []), "kick", "stream", "clips"];
  w.keywords ??= [];
  w.category ??= "otro";
  w.llmOk = false;
}

const clean = (v, limit) => String(v ?? "").replace(/"/g, "").split(/\s+/).filter(Boolean).join(" ").slice(0, limit);
const round = (v, d) => Math.round(v * 10 ** d) / 10 ** d;

// ---------- ajuste fino ----------

export function refine(w, ctx) {
  const { min, max } = ctx.limits;
  const segs = ctx.segments;
  let moved = false;
  if (w.lines && w.lineIndex) {
    const [a, b] = w.lines;
    if (a >= 1 && a <= b && b <= w.lineIndex.length) {
      const i = w.lineIndex[a - 1], k = w.lineIndex[b - 1];
      const length = segs[k].end - segs[i].start;
      if (length >= min - 0.5 && length <= max + 0.5) { setBounds(w, segs, i, k); moved = true; }
    }
  }
  if (!moved) tightenStart(w, ctx, min);
  trimTail(w, ctx, min);
}

function setBounds(w, segs, i, k) {
  Object.assign(w, { start: segs[i].start, end: segs[k].end, i0: i, i1: k, segments: segs.slice(i, k + 1) });
}

function meanExc(ctx, seg) {
  const a = Math.floor(seg.start), b = Math.max(Math.floor(seg.end), a + 1);
  const arr = ctx.exc.subarray(a, b);
  return arr.length ? arr.reduce((x, y) => x + y, 0) / arr.length : 0;
}

export function tightenStart(w, ctx, min) {
  const segs = ctx.segments;
  let i = w.i0;
  const k = w.i1;
  let dropped = 0;
  while (dropped < 2 && i < k) {
    const seg = segs[i], nxt = segs[i + 1];
    if (w.end - nxt.start < min || nxt.start - w.start > 8) break;
    if (w.peak != null && nxt.start > w.peak - 2) break;
    const weak = FILLER_RE.test(seg.text) || (meanExc(ctx, seg) < 0.35 && !count(REACTION_RE, seg.text) && !count(EXCLAIM_RE, seg.text));
    if (!weak) break;
    i++; dropped++;
  }
  if (i !== w.i0) setBounds(w, segs, i, k);
}

function trimTail(w, ctx, min) {
  if (w.peak == null) return;
  const segs = ctx.segments;
  let k = w.i1;
  const i = w.i0;
  while (k > i && w.end - w.peak > 12) {
    const prevEnd = segs[k - 1].end;
    if (prevEnd - w.start < min || prevEnd < w.peak + 3) break;
    const last = segs[k];
    if (meanExc(ctx, last) > 0.5 || count(REACTION_RE, last.text)) break;
    k--;
    setBounds(w, segs, i, k);
  }
}

/**
 * Regiones a transcribir alrededor de los picos (en navegador no se transcribe
 * todo el VOD salvo que sea corto): [p - 45, p + 20] fusionadas, con un tope
 * de minutos totales.
 */
export function regionsToTranscribe(moment, duration, { maxMinutes = 30, maxPeaks = 30 } = {}) {
  if (duration <= maxMinutes * 60) return [[0, duration]];
  const sm = smooth(moment, 3);
  const thr = Math.max(percentile(sm, 70), median(sm) + 0.05);
  const peaks = findPeaks(sm, 25, thr, maxPeaks);
  let regions = [];
  let total = 0;
  for (const p of peaks) {
    const r = [Math.max(0, p - 45), Math.min(duration, p + 20)];
    regions.push(r);
    regions = mergeRegions(regions);
    total = regions.reduce((acc, [a, b]) => acc + b - a, 0);
    if (total >= maxMinutes * 60) break;
  }
  return mergeRegions(regions);
}

export function mergeRegions(regions) {
  const sorted = [...regions].sort((a, b) => a[0] - b[0]);
  const out = [];
  for (const r of sorted) {
    if (out.length && r[0] <= out[out.length - 1][1] + 5) out[out.length - 1][1] = Math.max(out[out.length - 1][1], r[1]);
    else out.push([...r]);
  }
  return out;
}
