// Tests de los módulos puros de la versión web: node --test webapp/tests
import { test } from "node:test";
import assert from "node:assert/strict";

import { contrast, findPeaks, combineExcitement, rollingMedian } from "../js/signals.js";
import { buildTimeline, Timeline } from "../js/timeline.js";
import { detect, Context, makeWindow, tightenStart, numberedLines, applyLlm, refine, regionsToTranscribe,
  VIDEO_LIMITS, buildVideoWindows, formatOf, videoRegions } from "../js/highlights.js";
import { buildPrompt } from "../js/llm.js";
import { plan, speakerAt, planToOutput, CAM_FRACTION, MIN_SHOT } from "../js/vision-plan.js";
import { cards, buildCaptionTrack, splitTwo } from "../js/captions.js";
import { integratedLoudness, normalize } from "../js/loudness.js";
import { chatActivity, spikeCurve, parseKickTime } from "../js/chat.js";
import { toSegments, cleanSegments } from "../js/segments.js";
import { Resampler, powerSpectrum } from "../js/dsp.js";
import { bookmarklet, readKickPayload } from "../js/kickbridge.js";

const W = (spec) => spec.map(([w, s, e]) => ({ w, s, e }));

// ---------- señales ----------

test("contrast destaca un pico local", () => {
  const x = [...Array(100).fill(1), 5, ...Array(100).fill(1)];
  const c = contrast(x, 31);
  assert.equal(c.indexOf(Math.max(...c)), 100);
  assert.ok(c[100] > 0.9 && Math.max(...c.slice(0, 90)) < 0.1);
});

test("findPeaks respeta distancia mínima e ignora curvas planas", () => {
  const x = new Float64Array(100);
  x[10] = 1; x[12] = 0.9; x[50] = 0.8; x[90] = 0.7;
  assert.deepEqual(findPeaks(x, 5), [10, 50, 90]);
  assert.deepEqual(findPeaks(new Float64Array(50).fill(0.3), 5), []);
});

test("rollingMedian con ventana larga", () => {
  const x = Float64Array.from({ length: 5000 }, (_, i) => i);
  assert.ok(Math.abs(rollingMedian(x, 301)[2500] - 2500) < 5);
});

test("combineExcitement marca el segundo del grito", () => {
  const n = 120;
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2 ** 31) / 2 ** 31);
  const rmsDb = Float64Array.from({ length: n }, () => -40 + rnd());
  rmsDb[60] = -8; rmsDb[61] = -9;
  const zeros = new Float64Array(n);
  const flux = Float64Array.from({ length: n }, () => rnd() * 0.1);
  flux[60] = 1;
  const exc = combineExcitement({ rmsDb, flux, bright: zeros, laugh: zeros });
  const peak = exc.indexOf(Math.max(...exc));
  assert.ok(peak >= 58 && peak <= 63, `pico en ${peak}`);
});

// ---------- línea de tiempo ----------

test("jump cut solo en pausas tranquilas", () => {
  const words = W([["uno", 0, 1], ["dos", 3, 4], ["tres", 6, 7]]);
  const hop = 0.05;
  const energy = new Float64Array(160).fill(-60);
  for (let i = 0; i < 20; i++) energy[i] = -20;
  for (let i = 60; i < 80; i++) energy[i] = -20;
  for (let i = 84; i < 116; i++) energy[i] = -15; // acción en la segunda pausa
  for (let i = 120; i < 140; i++) energy[i] = -20;
  const tl = buildTimeline(8, words, { energy, hop, jumpcut: true, pad: 0.2 });
  assert.deepEqual(tl.mapTime(2), []);
  assert.ok(tl.mapTime(5).length);
  assert.deepEqual(tl.mapTime(7.9), []);
});

test("cold open duplica palabras al inicio", () => {
  const words = W([["ojo", 1, 1.4], ["GOL", 8, 8.5]]);
  const tl = buildTimeline(10, words, { preview: [7.6, 9] });
  const gols = tl.mapWords(words).filter((w) => w.w === "GOL");
  assert.equal(gols.length, 2);
  assert.ok(gols[0].s < 1);
  assert.ok(Math.abs(tl.duration - 11.4) < 1e-9);
  assert.ok(Math.abs(tl.sourceAt(0.5) - 8.1) < 1e-9);
  assert.ok(Math.abs(tl.sourceAt(1.4 + 2) - 2) < 1e-9);
});

test("mapSpans divide tramos entre cortes", () => {
  const tl = new Timeline([[0, 2], [4, 6]]);
  const spans = tl.mapSpans([{ t0: 1, t1: 5 }]);
  assert.deepEqual(spans.map((s) => [s.t0, s.t1]), [[1, 2], [2, 3]]);
});

// ---------- highlights ----------

function segments(n = 200, special = {}) {
  const segs = [];
  for (let i = 0; i < n; i++) {
    const s = i * 3;
    const text = special[i] || "pues nada aquí estamos jugando un rato tranquilo";
    const toks = text.split(" ");
    segs.push({ text, start: s, end: s + 3, words: toks.map((t, j) => ({ w: t, s: s + (j * 3) / toks.length, e: s + ((j + 0.9) * 3) / toks.length })) });
  }
  return segs;
}

test("detect sin LLM encuentra el pico de audio", async () => {
  const segs = segments(200, { 100: "¡NO PUEDE SER! jajaja qué locura", 101: "¡clipéalo chat!" });
  const exc = new Float64Array(600).fill(0.1);
  for (let i = 300; i < 306; i++) exc[i] = 1;
  const clips = await detect(segs, exc, 600);
  const best = clips[0];
  assert.ok(best.start <= 300 && best.end > 300);
  assert.ok(best.end - best.start >= 15 && best.end - best.start <= 60);
  assert.ok(best.title && best.hookText);
  assert.equal(best.llmScore, 0);
});

test("detect usa el pico del chat", async () => {
  const segs = segments();
  const exc = new Float64Array(600).fill(0.2);
  const chat = new Float64Array(600);
  for (let i = 450; i < 456; i++) chat[i] = 1;
  const clips = await detect(segs, exc, 600, { chat });
  assert.ok(clips[0].start < 452 && clips[0].end > 452);
  assert.ok(clips[0].signals.chat > 0.5);
});

test("detect con LLM falso aplica líneas de inicio/fin", async () => {
  const segs = segments();
  const exc = new Float64Array(600).fill(0.1);
  for (let i = 300; i < 306; i++) exc[i] = 1;
  const llm = { async score(w) { return { score: 9, hook: 9, standalone: 8, payoff: 9, title: "Título IA", hook_text: "NO PUEDE SER", description: "d", hashtags: ["#a"], keywords: ["ser"], category: "humor", reason: "r", start_line: 2, end_line: 8 }; } };
  const clips = await detect(segs, exc, 600, { llm, maxLlm: 4 });
  const top = clips.find((c) => c.llmOk);
  assert.ok(top);
  assert.equal(top.title, "Título IA");
  assert.equal(top.category, "humor");
  assert.deepEqual(top.hashtags, ["a"]);
});

test("tightenStart quita relleno inicial", () => {
  const segs = segments(200, { 50: "bueno chat a ver", 51: "eh pues", 52: "¡NO PUEDE SER!" });
  const ctx = new Context(segs, new Float64Array(600).fill(0.1), null, 600);
  const w = makeWindow(segs, 50, 60);
  tightenStart(w, ctx, 15);
  assert.equal(w.i0, 52);
});

test("líneas numeradas y refinamiento por LLM", () => {
  const segs = segments();
  const ctx = new Context(segs, new Float64Array(600).fill(0.1), null, 600);
  const w = makeWindow(segs, 40, 50);
  const { lines, index } = numberedLines(w, ctx);
  assert.ok(lines.startsWith("1. [ANTES]"));
  w.lineIndex = index;
  applyLlm(w, { score: 8, hook: 9, standalone: 7, payoff: 8, title: "x", hook_text: "a b", description: "", hashtags: [], reason: "", start_line: "1", end_line: 9 });
  refine(w, ctx);
  assert.equal(w.i0, 38);
  assert.equal(w.i1, 46);
});

test("regiones a transcribir alrededor de picos", () => {
  const moment = new Float64Array(4 * 3600).fill(0.1);
  for (const p of [1000, 5000, 9000]) for (let i = p; i < p + 5; i++) moment[i] = 1;
  const regions = regionsToTranscribe(moment, 4 * 3600, { maxMinutes: 30 });
  assert.ok(regions.length >= 3);
  for (const p of [1000, 5000, 9000]) assert.ok(regions.some(([a, b]) => a < p && p < b));
  assert.deepEqual(regionsToTranscribe(moment.slice(0, 600), 600), [[0, 600]]);
});

// ---------- plan de encuadre ----------

const track = (id, box, n = 60, act = null, fps = 6) => ({
  id, t: Array.from({ length: n }, (_, i) => i / fps), box: Array.from({ length: n }, () => box), act: act || Array(n).fill(0.1),
});
const analysis = (tracks, n = 60, fps = 6) => ({ src: [1920, 1080], fps, frames: n, duration: n / fps, tracks });

test("plan split para facecam en esquina", () => {
  const p = plan(analysis([track(0, [0.82, 0.7, 0.06, 0.11])]));
  assert.equal(p.layout, "split");
  const [x, y, w, h] = p.cam;
  assert.ok(x <= 0.82 && x + w >= 0.88);
  assert.ok(Math.abs((w * 1920) / (h * 1080) - 1080 / Math.round(1920 * CAM_FRACTION)) < 0.05);
});

test("plan blur sin caras y follow con una cara grande", () => {
  assert.equal(plan(analysis([])).layout, "blur");
  assert.equal(plan(analysis([track(0, [0.4, 0.2, 0.15, 0.27])])).layout, "follow");
});

test("plan speaker cambia de hablante con la boca", () => {
  const n = 120;
  const a = Array(n).fill(0.05), b = Array(n).fill(0.05);
  for (let i = 0; i < 60; i++) a[i] = 0.5 + 0.2 * ((i * 7) % 10) / 10;
  for (let i = 60; i < n; i++) b[i] = 0.5 + 0.2 * ((i * 3) % 10) / 10;
  const words = Array.from({ length: 50 }, (_, i) => ({ w: "x", s: i * 0.4, e: i * 0.4 + 0.3 }));
  const p = plan(analysis([track(0, [0.15, 0.25, 0.12, 0.22], n, a), track(1, [0.7, 0.25, 0.12, 0.22], n, b)], n), words, 20);
  assert.equal(p.layout, "speaker");
  const singles = p.shots.filter((s) => s.mode === "single");
  assert.deepEqual(singles.map((s) => s.spk), [0, 1]);
  assert.ok(singles[1].t0 >= 9.5 && singles[1].t0 <= 11.5);
  assert.ok(p.shots.every((s) => s.t1 - s.t0 >= MIN_SHOT - 1e-6));
  assert.equal(speakerAt(p, 2), 0);
  assert.equal(speakerAt(p, 18), 1);
  const tl = new Timeline([[0, 5], [8, 20]]);
  const out = planToOutput(p, "speaker", tl);
  assert.ok(Math.abs(out.shots[out.shots.length - 1].t1 - tl.duration) < 1e-6);
});

test("plan speaker en crosstalk usa pantalla dividida", () => {
  const n = 120;
  const act = Array(n).fill(0.05);
  for (let i = 30; i < 90; i++) act[i] = 0.6;
  const words = Array.from({ length: 50 }, (_, i) => ({ w: "x", s: i * 0.4, e: i * 0.4 + 0.3 }));
  const p = plan(analysis([track(0, [0.15, 0.25, 0.12, 0.22], n, act.map((v) => v + 0.01)), track(1, [0.7, 0.25, 0.12, 0.22], n, act)], n), words, 20);
  assert.ok(p.shots.some((s) => s.mode === "stack"));
});

// ---------- subtítulos ----------

test("tarjetas virales de 1-3 palabras y cambio de hablante", () => {
  const words = "no puede ser lo que acaba de pasar".split(" ").map((w, i) => ({ w, s: i * 0.4, e: i * 0.4 + 0.35, spk: i < 4 ? 0 : 1 }));
  const cs = cards(words, { maxWords: 3, maxChars: 16, maxSecs: 1.6, gap: 0.45 });
  assert.ok(cs.every((c) => c.words.length <= 3));
  assert.ok(cs.every((c) => new Set(c.words.map((w) => w.spk)).size === 1));
  const track = buildCaptionTrack(words, { style: "viral", keywords: ["pasar"] });
  assert.ok(track.multiSpeaker && track.keywords.has("pasar"));
  assert.deepEqual(splitTwo("EL PEOR CLUTCH DE LA HISTORIA", 20), ["EL PEOR CLUTCH", "DE LA HISTORIA"]);
});

// ---------- audio ----------

test("normalización a -14 LUFS", () => {
  const fs = 48000;
  const n = fs * 5;
  const ch = Float32Array.from({ length: n }, (_, i) => 0.1 * Math.sin((2 * Math.PI * 1000 * i) / fs));
  const before = integratedLoudness([ch], fs);
  normalize([ch], fs, { target: -14 });
  const after = integratedLoudness([ch], fs);
  assert.ok(before < -20);
  assert.ok(Math.abs(after + 14) < 1.0, `quedó en ${after.toFixed(2)} LUFS`);
  let peak = 0;
  for (const v of ch) peak = Math.max(peak, Math.abs(v));
  assert.ok(peak <= 10 ** (-1.5 / 20) + 1e-3);
});

test("remuestreo 48k → 16k conserva la duración y el tono", () => {
  const r = new Resampler(48000, 16000);
  let total = 0;
  const outs = [];
  for (let b = 0; b < 10; b++) {
    const block = Float32Array.from({ length: 4800 }, (_, i) => Math.sin((2 * Math.PI * 440 * (b * 4800 + i)) / 48000));
    const o = r.push(block);
    outs.push(o);
    total += o.length;
  }
  assert.ok(Math.abs(total - 16000) <= 2);
  const x = new Float32Array(1024);
  let off = 0;
  for (const o of outs) { x.set(o.subarray(0, Math.min(o.length, 1024 - off)), off); off += o.length; if (off >= 1024) break; }
  const p = powerSpectrum(x, 1024);
  const peak = p.indexOf(Math.max(...p));
  assert.ok(Math.abs(peak * (16000 / 1024) - 440) < 20);
});

// ---------- chat ----------

test("chat: velocidad, picos y retraso", async () => {
  const start = parseKickTime("2026-01-01 20:00:00");
  const bridge = {
    available: true,
    async kickJson(path) {
      const iso = decodeURIComponent(path.split("start_time=")[1]);
      const t0 = (new Date(iso) - start) / 1000;
      const messages = [];
      for (let k = 0; k < 25; k++) {
        const rate = t0 + k * 0.1 >= 300 && t0 + k * 0.1 < 320 ? 10 : 1;
        messages.push({ created_at: new Date(new Date(iso).getTime() + (k / rate) * 1000).toISOString(), content: rate === 10 ? "KEKW jajaja" : "hola" });
      }
      return { data: { messages } };
    },
  };
  const chat = await chatActivity(bridge, { channel_id: 1, start: "2026-01-01 20:00:00" }, 900, { delayMs: 0 });
  assert.ok(chat.rate[310] > 5 * chat.rate[100]);
  const spike = spikeCurve(chat, 900, 6);
  const peak = spike.indexOf(Math.max(...spike));
  assert.ok(peak >= 290 && peak < 320, `pico en ${peak}`);
});

// ---------- transcripción ----------

test("palabras a frases y limpieza de alucinaciones", () => {
  const words = W([["Hola", 0, 0.3], ["chat.", 0.4, 1.6], ["¿Qué", 3, 3.2], ["tal?", 3.3, 3.6]]);
  const segs = toSegments(words);
  assert.equal(segs.length, 2);
  assert.equal(segs[0].text, "Hola chat.");
  const cleaned = cleanSegments([{ text: "Subtítulos realizados por la comunidad de Amara.org" }, { text: "hola" }, { text: "hola" }, { text: "hola" }]);
  assert.deepEqual(cleaned.map((s) => s.text), ["hola", "hola"]);
});

// ---------- marcador de Kick ----------

test("bookmarklet y lectura del payload", () => {
  const href = bookmarklet("https://kickclipper.vercel.app/");
  assert.ok(href.startsWith("javascript:"));
  const code = decodeURIComponent(href.slice("javascript:".length));
  assert.ok(code.includes("/api/v1/video/") && code.includes("kc:'fetch-res'"));
  assert.doesNotThrow(() => new Function(code)); // sintaxis válida
  const payload = { src: "https://stream.kick.com/x/master.m3u8", channel: "canal", channel_id: 7 };
  const loc = { hash: `#kick=${encodeURIComponent(JSON.stringify(payload))}` };
  assert.deepEqual(readKickPayload(loc), payload);
  assert.equal(readKickPayload({ hash: "" }), null);
});

test("cámara marcada a mano: split con el aspecto del panel", async () => {
  const { splitFromRect } = await import("../js/vision-plan.js");
  const p = splitFromRect([0.05, 0.6, 0.2, 0.3], 1920, 1080);
  assert.equal(p.layout, "split");
  const [x, y, w, h] = p.cam;
  assert.ok(Math.abs((w * 1920) / (h * 1080) - 1080 / Math.round(1920 * CAM_FRACTION)) < 0.02);
  assert.ok(x <= 0.05 + 1e-6 && x + w >= 0.25 - 1e-6 && y >= 0 && y + h <= 1.0001);
});

// ---------- videos 16:9 ----------

const speech = (seconds, every = 3) => Array.from({ length: Math.floor(seconds / every) }, (_, i) => ({
  text: `frase ${i} del stream`, start: i * every, end: i * every + every - 0.2,
  words: [{ w: "frase", s: i * every, e: i * every + 1 }],
}));

test("formatOf: 5 min o más es un video", () => {
  assert.equal(formatOf(VIDEO_LIMITS), "video");
  assert.equal(formatOf({ min: 15, max: 60, target: 40 }), "short");
});

test("buildVideoWindows: 5-10 min sin cruzar huecos de transcripción", () => {
  const segs = speech(1500);
  const wins = buildVideoWindows(segs);
  assert.ok(wins.length > 10 && wins.length < 400);
  for (const w of wins) {
    assert.equal(w.format, "video");
    assert.ok(w.end - w.start >= 300 && w.end - w.start <= 600);
  }
  // dos tramos transcritos de 8 min separados: ninguna ventana los une
  const gap = [...speech(480), ...speech(480).map((s) => ({ ...s, start: s.start + 2000, end: s.end + 2000 }))];
  assert.ok(buildVideoWindows(gap).every((w) => w.end <= 480 || w.start >= 2000));
});

test("videoRegions: el tramo más activo, sin solapes y con tope de minutos", () => {
  const n = 4 * 3600;
  const moment = new Float64Array(n).fill(0.1);
  moment.fill(0.9, 7200, 7800);
  const regions = videoRegions(moment, n, { maxMinutes: 30 });
  assert.equal(regions.length, 2);
  assert.ok(regions.some(([a, b]) => a <= 7200 && b >= 7800));
  assert.deepEqual(videoRegions(moment.slice(0, 900), 900, { maxMinutes: 30 }), [[0, 900]]);
});

test("detect en modo video devuelve videos y el prompt es el de video", async () => {
  const segs = speech(1200);
  const exc = new Float64Array(1200).fill(0.2);
  exc.fill(0.9, 500, 520);
  const found = await detect(segs, exc, 1200, { limits: VIDEO_LIMITS, maxClips: 2 });
  assert.ok(found.length >= 1 && found.every((w) => w.format === "video"));
  for (const w of found) assert.ok(w.end - w.start >= 290 && w.end - w.start <= 600);
  const prompt = buildPrompt(found[0], new Context(segs, exc, null, 1200, VIDEO_LIMITS), "canal", VIDEO_LIMITS);
  assert.match(prompt, /videos de YouTube/);
  assert.equal(found[0].lineIndex, undefined);
});
