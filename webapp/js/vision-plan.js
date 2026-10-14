// Plan de encuadre a partir del análisis de caras (mismo algoritmo que
// kickclipper/pipeline/vision.py). Módulo puro: sin dependencias del navegador.

export const CAM_FRACTION = 0.38;
const OUT_W = 1080, OUT_H = 1920;
const STACK_ASPECT = 1080 / 960;
const VERTICAL_ASPECT = 9 / 16;
export const MIN_SHOT = 1.2;
const SWITCH_HOLD = 0.5;
const CROSSTALK_HOLD = 1.0;

const median = (arr) => { const s = [...arr].sort((a, b) => a - b); return s.length ? s[Math.floor(s.length / 2)] : 0; };
const pct = (arr, p) => { const s = [...arr].sort((a, b) => a - b); if (!s.length) return 0; const i = (p / 100) * (s.length - 1); const lo = Math.floor(i), hi = Math.ceil(i); return s[lo] + (s[hi] - s[lo]) * (i - lo); };
const std = (arr) => { const m = arr.reduce((a, b) => a + b, 0) / arr.length; return Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / arr.length); };
const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
const r4 = (v) => Math.round(v * 1e4) / 1e4;

export function plan(analysis, words = [], duration = null) {
  if (!analysis || !analysis.tracks?.length) return { layout: "blur" };
  const [srcW, srcH] = analysis.src;
  const dur = duration || analysis.duration;
  const nFrames = Math.max(analysis.frames, 1);
  const summary = analysis.tracks.map((tr) => {
    const med = [0, 1, 2, 3].map((k) => median(tr.box.map((b) => b[k])));
    const cxs = tr.box.map((b) => b[0] + b[2] / 2), cys = tr.box.map((b) => b[1] + b[3] / 2);
    return { ...tr, presence: tr.t.length / nFrames, med, area: med[2] * med[3],
      motion: tr.box.length > 1 ? Math.max(std(cxs), std(cys)) : 0, cx: med[0] + med[2] / 2, cy: med[1] + med[3] / 2 };
  });
  const faces = summary.filter((s) => s.presence >= 0.25 && s.area >= 0.002);
  const people = faces.filter((s) => s.area >= 0.012).sort((a, b) => b.presence * b.area - a.presence * a.area);
  const cams = faces.filter((s) => s.presence >= 0.35 && s.area < 0.03 && s.motion < 0.03
    && (s.cx < 0.34 || s.cx > 0.66 || s.cy < 0.3 || s.cy > 0.62));

  if (people.length >= 2) return speakerPlan(people.slice(0, 4), words, dur, srcW, srcH, analysis.fps);
  if (cams.length) {
    const cam = cams.reduce((a, b) => (b.presence * b.area > a.presence * a.area ? b : a));
    if (!people.length || people[0] === cam || people[0].area < 3 * cam.area) return splitPlan(cam, srcW, srcH);
  }
  if (people.length) return followPlan(people[0], srcW, srcH);
  return { layout: "blur" };
}

export function rectAround(face, scaleH, aspectPx, srcW, srcH, facePos = 0.42, minH = 0.2) {
  const [fx, fy, fw, fh] = face;
  let rh = clamp(fh * scaleH, minH, 1);
  let rw = (rh * srcH * aspectPx) / srcW;
  if (rw > 1) { rw = 1; rh = (rw * srcW) / (aspectPx * srcH); }
  const cx = fx + fw / 2, cy = fy + fh / 2;
  return [r4(clamp(cx - rw / 2, 0, 1 - rw)), r4(clamp(cy - rh * facePos, 0, 1 - rh)), r4(rw), r4(rh)];
}

function rectFixed(face, rh, aspectPx, srcW, srcH, facePos = 0.42) {
  return rectAround(face, rh / Math.max(face[3], 1e-3), aspectPx, srcW, srcH, facePos, 0);
}

export function splitPlan(cam, srcW, srcH) {
  const camH = Math.round((OUT_H * CAM_FRACTION) / 2) * 2;
  const gameH = OUT_H - camH;
  const camRect = rectAround(cam.med, 2.4, OUT_W / camH, srcW, srcH, 0.45);
  const gw = Math.min(1, (srcH * (OUT_W / gameH)) / srcW);
  return { layout: "split", cam: camRect, game: [r4((1 - gw) / 2), 0, r4(gw), 1], cam_fraction: camH / OUT_H, caption_y: camH / OUT_H };
}

/** Layout split con la cámara marcada a mano (ampliada al aspecto del panel). */
export function splitFromRect(camRect, srcW, srcH) {
  const camH = Math.round((OUT_H * CAM_FRACTION) / 2) * 2;
  const aspect = OUT_W / camH;
  let [x, y, w, h] = camRect.map(Number);
  w = Math.max(w, 0.02); h = Math.max(h, 0.02);
  const cx = x + w / 2, cy = y + h / 2;
  if ((w * srcW) / (h * srcH) < aspect) w = (h * srcH * aspect) / srcW;
  else h = (w * srcW) / (aspect * srcH);
  if (w > 1) { w = 1; h = srcW / (aspect * srcH); }
  if (h > 1) { h = 1; w = (srcH * aspect) / srcW; }
  const rect = [r4(clamp(cx - w / 2, 0, 1 - w)), r4(clamp(cy - h / 2, 0, 1 - h)), r4(w), r4(h)];
  const gw = Math.min(1, (srcH * (OUT_W / (OUT_H - camH))) / srcW);
  return { layout: "split", cam: rect, game: [r4((1 - gw) / 2), 0, r4(gw), 1], cam_fraction: camH / OUT_H, caption_y: camH / OUT_H, manual: true };
}

function followPlan(person, srcW, srcH) {
  const rect = rectAround(person.med, 4.2, VERTICAL_ASPECT, srcW, srcH, 0.42, 0.55);
  const keys = [];
  if (person.motion > 0.02) {
    const t = person.t, cx = person.box.map((b) => b[0] + b[2] / 2);
    for (let sec = 0; sec <= t[t.length - 1] + 1e-6; sec += 1) {
      const sel = cx.filter((_, i) => Math.abs(t[i] - sec) <= 1);
      if (!sel.length) continue;
      keys.push({ t: sec, x: r4(clamp(median(sel) - rect[2] / 2, 0, 1 - rect[2])) });
    }
  }
  return { layout: "follow", rect, keys };
}

function speakerPlan(people, words, dur, srcW, srcH, fps) {
  const dt = 1 / fps;
  const n = Math.max(1, Math.ceil(Math.max(dur, dt) / dt));
  const win = Math.max(1, Math.round(0.6 / dt));
  const raw = people.map(() => new Float64Array(n));
  const present = people.map(() => new Uint8Array(n));
  people.forEach((p, k) => {
    p.t.forEach((t, j) => { const i = clamp(Math.round(t / dt), 0, n - 1); raw[k][i] = p.act[j]; present[k][i] = 1; });
    const sm = new Float64Array(n);
    for (let i = 0; i < n; i++) {
      let acc = 0;
      for (let j = i - Math.floor(win / 2); j < i - Math.floor(win / 2) + win; j++) if (j >= 0 && j < n) acc += raw[k][j];
      sm[i] = acc / win;
    }
    raw[k] = sm;
  });
  const base = [], spread = [];
  people.forEach((_, k) => {
    const vals = []; for (let i = 0; i < n; i++) if (present[k][i]) vals.push(raw[k][i]);
    const p20 = pct(vals.length ? vals : [0], 20), p90 = pct(vals.length ? vals : [0], 90);
    base.push(p20); spread.push(p90 - p20);
  });
  const floor = 0.35 * Math.max(...spread) + 1e-3;
  const acts = people.map((_, k) => Float64Array.from(raw[k], (v, i) => (present[k][i] ? (v - base[k]) / Math.max(spread[k], floor) : -1)));

  const speech = new Uint8Array(n);
  for (const w of words) for (let i = Math.floor(Math.max(w.s - 0.25, 0) / dt); i <= Math.floor((w.e + 0.25) / dt) && i < n; i++) speech[i] = 1;

  const states = [];
  for (let i = 0; i < n; i++) {
    const order = people.map((_, k) => k).sort((a, b) => acts[b][i] - acts[a][i]);
    const first = order[0], second = order[1] ?? order[0];
    const a1 = acts[first][i], a2 = acts[second][i];
    if (speech[i] && a1 > 0.55 && a2 > 0.55 && a2 >= 0.75 * a1) states.push(["stack", [Math.min(first, second), Math.max(first, second)].join(",")]);
    else if (speech[i] && a1 > 0.45) states.push(["single", String(first)]);
    else states.push(null);
  }
  const same = (a, b) => a && b && a[0] === b[0] && a[1] === b[1];
  const shots = [];
  let cur = ["single", "0"], curStart = 0, pending = null, pendingSince = 0;
  states.forEach((st, i) => {
    const t = i * dt;
    if (!st || same(st, cur)) { pending = null; return; }
    if (!same(st, pending)) { pending = st; pendingSince = t; }
    const hold = st[0] === "stack" ? CROSSTALK_HOLD : SWITCH_HOLD;
    if (t - pendingSince >= hold && t - curStart >= MIN_SHOT) {
      const at = Math.max(pendingSince, curStart + MIN_SHOT);
      shots.push({ t0: curStart, t1: at, state: cur });
      cur = st; curStart = at; pending = null;
    }
  });
  shots.push({ t0: curStart, t1: dur, state: cur });
  const firstDecided = states.find((s) => s && s[0] === "single");
  if (firstDecided && same(shots[0].state, ["single", "0"])) shots[0].state = firstDecided;

  const faceH = Math.max(...people.map((p) => p.med[3]));
  const singleH = clamp(faceH * 4, 0.5, 1), stackH = clamp(faceH * 2.8, 0.3, 1);
  const out = [];
  for (const s of shots) {
    if (s.t1 - s.t0 <= 1e-3) continue;
    const [mode, who] = s.state;
    let entry;
    if (mode === "single") {
      const k = Number(who);
      entry = { t0: r2(s.t0), t1: r2(s.t1), mode, spk: k, rect: rectFixed(people[k].med, singleH, VERTICAL_ASPECT, srcW, srcH) };
    } else {
      const ks = who.split(",").map(Number);
      entry = { t0: r2(s.t0), t1: r2(s.t1), mode, spk: null, rects: ks.map((k) => rectFixed(people[k].med, stackH, STACK_ASPECT, srcW, srcH)) };
    }
    const prev = out[out.length - 1];
    if (prev && mode === "single" && prev.mode === "single" && prev.spk === entry.spk) prev.t1 = entry.t1;
    else out.push(entry);
  }
  return { layout: "speaker", shots: out, speakers: people.length };
}

const r2 = (v) => Math.round(v * 100) / 100;

export function speakerAt(p, t) {
  for (const s of p.shots || []) if (s.t0 <= t && t < s.t1) return s.spk;
  return null;
}

/** Traduce el plan a tiempo de salida con la línea de tiempo del clip. */
export function planToOutput(p, layout, timeline) {
  if (layout === "speaker") {
    const merged = [];
    for (const sh of timeline.mapSpans(p.shots || [])) {
      const prev = merged[merged.length - 1];
      if (prev && Math.abs(prev.t1 - sh.t0) < 1e-3 && prev.mode === sh.mode
        && JSON.stringify(prev.rect) === JSON.stringify(sh.rect) && JSON.stringify(prev.rects) === JSON.stringify(sh.rects)) prev.t1 = sh.t1;
      else merged.push({ ...sh });
    }
    return { ...p, shots: merged };
  }
  if (layout === "follow" && p.keys?.length) {
    const keys = [];
    for (const k of p.keys) { const ts = timeline.mapTime(k.t); if (ts.length) keys.push({ ...k, t: ts[ts.length - 1] }); }
    return { ...p, keys: keys.sort((a, b) => a.t - b.t) };
  }
  return p;
}
