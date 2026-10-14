// Actividad del chat del VOD (port de kickclipper/pipeline/chat.py).
// Se lee a través del puente con la pestaña de Kick (ver kickbridge.js).

import { contrast, robustNorm, smooth } from "./signals.js";

const HYPE_RE = /(kekw|omegalul|lulw?|icant|lmao|jaja|jeje|\bxd+\b|😂|🤣|💀|pog|\bw+\b|\bl+\b|🔥|clip|no way|no puede ser|wtf|omg|letsgo|let'?s go|\bgg\b)/i;

export function parseKickTime(value) {
  if (!value) return null;
  let text = String(value).trim().replace(" ", "T");
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(text)) text += "Z"; // Kick da UTC sin zona
  const d = new Date(text);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Velocidad del chat y proporción de mensajes de risa/hype por segundo.
 * info: { channel_id, start } del VOD; offset: segundo del VOD donde empieza
 * el rango analizado. Devuelve null si no se pudo leer.
 */
export async function chatActivity(bridge, info, duration, { offset = 0, budget = 300, delayMs = 120, onProgress, signal } = {}) {
  const start = parseKickTime(info?.start);
  if (!bridge?.available || !info?.channel_id || !start || duration <= 0) return null;
  const step = Math.max(5, duration / budget);
  const centers = [], rates = [], hype = [];
  let fails = 0;
  const n = Math.ceil(duration / step);
  for (let k = 0; k < n; k++) {
    if (signal?.aborted) return null;
    const t = k * step;
    const at = new Date(start.getTime() + (offset + t) * 1000);
    const iso = at.toISOString().replace(/\.\d{3}Z$/, ".000Z");
    let msgs;
    try {
      const data = await bridge.kickJson(`/api/v2/channels/${info.channel_id}/messages?start_time=${encodeURIComponent(iso)}`);
      msgs = data?.data?.messages || [];
      fails = 0;
    } catch (err) {
      if (++fails >= 3) { console.info("Chat no disponible:", err.message); return rates.length >= 3 ? build(duration, centers, rates, hype) : null; }
      continue;
    }
    const stamps = [];
    let nh = 0;
    for (const m of msgs) {
      const d = parseKickTime(m.created_at);
      if (!d) continue;
      stamps.push((d.getTime() - start.getTime()) / 1000 - offset);
      if (HYPE_RE.test(String(m.content || ""))) nh++;
    }
    if (stamps.length >= 2) {
      const span = Math.max(Math.max(...stamps) - Math.min(...stamps), 1);
      centers.push(stamps.reduce((a, b) => a + b, 0) / stamps.length);
      rates.push(stamps.length / span);
      hype.push(nh / stamps.length);
    } else {
      centers.push(t); rates.push(stamps.length / step); hype.push(0);
    }
    if (k % 5 === 0) onProgress?.((k + 1) / n, `Leyendo chat ${k + 1}/${n}`);
    await new Promise((r) => setTimeout(r, delayMs));
  }
  return rates.length >= 3 ? build(duration, centers, rates, hype) : null;
}

function build(duration, centers, rates, hype) {
  const order = centers.map((_, i) => i).sort((a, b) => centers[a] - centers[b]);
  const c = order.map((i) => centers[i]), r = order.map((i) => rates[i]), h = order.map((i) => hype[i]);
  const n = Math.ceil(duration);
  return { rate: interp(n, c, r), hype: interp(n, c, h) };
}

function interp(n, xs, ys) {
  const out = new Float64Array(n);
  let j = 0;
  for (let i = 0; i < n; i++) {
    while (j < xs.length - 1 && xs[j + 1] < i) j++;
    if (i <= xs[0]) out[i] = ys[0];
    else if (i >= xs[xs.length - 1]) out[i] = ys[ys.length - 1];
    else { const t = (i - xs[j]) / (xs[j + 1] - xs[j] || 1); out[i] = ys[j] + (ys[j + 1] - ys[j]) * t; }
  }
  return out;
}

/** Curva 0..1 de "el chat explotó", adelantada `lag` s (el chat va con retraso). */
export function spikeCurve(chat, length, lag = 6) {
  if (!chat) return null;
  const rate = chat.rate.map((v) => Math.log1p(v));
  const c = contrast(rate, 301), h = robustNorm(chat.hype);
  let spike = c.map((v, i) => 0.75 * v + 0.25 * h[i]);
  const shift = Math.round(lag);
  if (shift > 0) {
    const shifted = new Float64Array(spike.length);
    for (let i = 0; i < spike.length; i++) shifted[i] = spike[Math.min(i + shift, spike.length - 1)];
    spike = shifted;
  }
  spike = smooth(spike, 5);
  const out = new Float64Array(length);
  for (let i = 0; i < Math.min(length, spike.length); i++) out[i] = Math.min(Math.max(spike[i], 0), 1);
  return out;
}
