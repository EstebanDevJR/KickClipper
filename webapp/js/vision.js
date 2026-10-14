// Análisis visual de un clip para elegir el encuadre vertical (port de vision.py).
//
// 1. Se muestrean fotogramas (5 fps) y se detectan caras con MediaPipe.
//    Para encontrar facecams pequeñas en una esquina se detecta también sobre
//    recortes en mosaico del fotograma.
// 2. Las caras se siguen entre fotogramas; para las grandes se mide la
//    apertura de la mandíbula (blendshape `jawOpen` de FaceLandmarker): su
//    variación indica quién está hablando.
// 3. Con eso se decide el layout: split (facecam + gameplay), speaker (hablante
//    activo con pantalla dividida en crosstalk), follow (una persona) o blur.

import * as MB from "mediabunny";
import { yieldToUI } from "./dsp.js";

export { plan, speakerAt, planToOutput, splitFromRect, CAM_FRACTION, MIN_SHOT } from "./vision-plan.js";

const MP_URL = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/vision_bundle.mjs";
const MP_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@1.0.1/wasm";
const DETECTOR_MODEL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_full_range/float16/1/blaze_face_full_range.tflite";
const DETECTOR_FALLBACK = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite";
const LANDMARKER_MODEL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";


let tasks = null;

async function loadTasks() {
  if (tasks) return tasks;
  const { FilesetResolver, FaceDetector, FaceLandmarker } = await import(MP_URL);
  const fileset = await FilesetResolver.forVisionTasks(MP_WASM);
  const make = async (Cls, opts) => {
    for (const delegate of ["GPU", "CPU"]) {
      try { return await Cls.createFromOptions(fileset, { ...opts, baseOptions: { ...opts.baseOptions, delegate } }); } catch (err) { console.warn(`MediaPipe ${delegate} falló`, err); }
    }
    return null;
  };
  let detector = await make(FaceDetector, { baseOptions: { modelAssetPath: DETECTOR_MODEL }, runningMode: "IMAGE", minDetectionConfidence: 0.55 });
  if (!detector) detector = await make(FaceDetector, { baseOptions: { modelAssetPath: DETECTOR_FALLBACK }, runningMode: "IMAGE", minDetectionConfidence: 0.55 });
  const landmarker = await make(FaceLandmarker, { baseOptions: { modelAssetPath: LANDMARKER_MODEL }, runningMode: "IMAGE", numFaces: 1, outputFaceBlendshapes: true });
  if (!detector) throw new Error("No se pudo cargar el detector de caras");
  tasks = { detector, landmarker };
  return tasks;
}

export async function visionAvailable() {
  try { await loadTasks(); return true; } catch { return false; }
}

/** Caras en un canvas (fotograma completo + mosaico 3×3 solapado para caras pequeñas). */
function detectFaces(detector, canvas) {
  const W = canvas.width, H = canvas.height;
  const found = [];
  const run = (c, ox, oy, sx, sy) => {
    const res = detector.detect(c);
    for (const d of res.detections || []) {
      const b = d.boundingBox;
      found.push({ x: (ox + b.originX * sx) / W, y: (oy + b.originY * sy) / H, w: (b.width * sx) / W, h: (b.height * sy) / H,
        score: d.categories?.[0]?.score ?? 0.5 });
    }
  };
  run(canvas, 0, 0, 1, 1);
  const tw = Math.round(W / 2.2), th = Math.round(H / 2.2);
  const tile = new OffscreenCanvas(tw, th);
  const tctx = tile.getContext("2d");
  for (let gy = 0; gy < 3; gy++) for (let gx = 0; gx < 3; gx++) {
    const ox = Math.round(((W - tw) * gx) / 2), oy = Math.round(((H - th) * gy) / 2);
    tctx.drawImage(canvas, ox, oy, tw, th, 0, 0, tw, th);
    run(tile, ox, oy, 1, 1);
  }
  return nms(found, 0.3);
}

function iou(a, b) {
  const x1 = Math.max(a.x, b.x), y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.w, b.x + b.w), y2 = Math.min(a.y + a.h, b.y + b.h);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  return inter / (a.w * a.h + b.w * b.h - inter + 1e-9);
}

function nms(boxes, thr) {
  boxes.sort((a, b) => b.score - a.score);
  const kept = [];
  for (const b of boxes) if (!kept.some((k) => iou(k, b) > thr || contains(k, b) || contains(b, k))) kept.push(b);
  return kept;
}

const contains = (a, b) => b.x >= a.x - 0.01 && b.y >= a.y - 0.01 && b.x + b.w <= a.x + a.w + 0.01 && b.y + b.h <= a.y + a.h + 0.01;

function jawOpen(landmarker, canvas, box) {
  if (!landmarker) return null;
  const W = canvas.width, H = canvas.height;
  const size = Math.max(box.w * W, box.h * H) * 1.7;
  const cx = (box.x + box.w / 2) * W, cy = (box.y + box.h / 2) * H;
  const crop = new OffscreenCanvas(192, 192);
  crop.getContext("2d").drawImage(canvas, cx - size / 2, cy - size / 2, size, size, 0, 0, 192, 192);
  const res = landmarker.detect(crop);
  const cats = res.faceBlendshapes?.[0]?.categories;
  if (!cats) return null;
  const jaw = cats.find((c) => c.categoryName === "jawOpen")?.score ?? 0;
  const close = cats.find((c) => c.categoryName === "mouthClose")?.score ?? 0;
  return Math.max(0, jaw - 0.5 * close);
}

/** Caras y actividad de boca a lo largo de [start, end] (segundos relativos al medio). */
export async function analyzeClip(track, start, end, { t0 = 0, fps = 5, onProgress, signal } = {}) {
  const { detector, landmarker } = await loadTasks();
  const srcW = await track.getDisplayWidth(), srcH = await track.getDisplayHeight();
  const width = Math.min(960, srcW);
  const sink = new MB.CanvasSink(track, { width });
  const times = [];
  for (let t = start; t < end; t += 1 / fps) times.push(t0 + t);
  const tracks = [];
  let frames = 0;
  for await (const wrapped of sink.canvasesAtTimestamps(times)) {
    if (signal?.aborted) throw new DOMException("Cancelado", "AbortError");
    const t = frames / fps;
    frames++;
    if (!wrapped) continue;
    const canvas = wrapped.canvas;
    const faces = detectFaces(detector, canvas);
    assign(tracks, faces, t, fps, (box) => (box.w * box.h >= 0.006 ? jawOpen(landmarker, canvas, box) : null));
    if (frames % 10 === 0) { onProgress?.(frames / times.length, "Analizando encuadre"); await yieldToUI(); }
  }
  return {
    src: [srcW, srcH], fps, frames, duration: end - start,
    tracks: tracks.filter((tr) => tr.t.length >= 2).map(({ id, t, box, act }) => ({ id, t, box, act })),
  };
}

function assign(tracks, faces, t, fps, jawFn) {
  const used = new Set();
  for (const f of faces) {
    const box = [f.x, f.y, f.w, f.h];
    let best = null, bestD = Infinity;
    for (const tr of tracks) {
      if (used.has(tr.id) || t - tr.lastT > 1.0) continue;
      const b = tr.box[tr.box.length - 1];
      const d = Math.hypot(b[0] + b[2] / 2 - (f.x + f.w / 2), b[1] + b[3] / 2 - (f.y + f.h / 2));
      if (d < Math.max(b[2], b[3]) * 0.8 && d < bestD) { best = tr; bestD = d; }
    }
    if (!best) { best = { id: tracks.length, t: [], box: [], act: [], jaw: null, lastT: -1 }; tracks.push(best); }
    used.add(best.id);
    const jaw = jawFn(f);
    let act = 0;
    if (jaw !== null) {
      const consecutive = t - best.lastT <= 1.5 / fps;
      act = (best.jaw !== null && consecutive ? Math.abs(jaw - best.jaw) * 2 : 0) + jaw * 0.5;
      best.jaw = jaw;
    }
    best.t.push(Math.round(t * 1000) / 1000);
    best.box.push(box.map((v) => Math.round(v * 1e4) / 1e4));
    best.act.push(Math.round(act * 1e4) / 1e4);
    best.lastT = t;
  }
}
