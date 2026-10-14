// Composición de cada fotograma 9:16 (equivalente a los grafos de renderer.py).
//
// Layouts: blur (vídeo completo sobre fondo difuminado), crop (recorte
// central), split (facecam arriba + gameplay abajo), follow (recorte que
// sigue a la persona) y speaker (hablante activo, con pantalla dividida en
// crosstalk). Encima se dibujan subtítulos y texto gancho.

import { drawCaptions, drawHook, SAFE_TOP } from "./captions.js";

export const OUT_W = 1080;
export const OUT_H = 1920;

/**
 * plan: plan de encuadre con tiempos ya en tiempo de SALIDA.
 * captions: pista de buildCaptionTrack; hook: texto gancho.
 */
export function createCompositor({ layout = "blur", plan = {}, captions = null, hook = "", hookSeconds = 3,
  W = OUT_W, H = OUT_H } = {}) {
  const canvas = typeof OffscreenCanvas !== "undefined" ? new OffscreenCanvas(W, H) : Object.assign(document.createElement("canvas"), { width: W, height: H });
  const ctx = canvas.getContext("2d", { alpha: false });
  const small = new OffscreenCanvas(Math.round(W / 10), Math.round(H / 10));
  const sctx = small.getContext("2d", { alpha: false });
  const camFraction = plan.cam_fraction || 0.38;
  const camH = Math.round((H * camFraction) / 2) * 2;
  const captionY = layout === "split" && plan.caption_y ? plan.caption_y : 0.62;
  const hookY = layout === "split" && plan.caption_y ? plan.caption_y + 0.075 : SAFE_TOP + 0.01;

  function drawRect(src, rect, dx, dy, dw, dh) {
    const sw = src.width, sh = src.height;
    let [x, y, w, h] = rect;
    x = clamp(x, 0, 1); y = clamp(y, 0, 1);
    w = clamp(w, 0.01, 1 - x); h = clamp(h, 0.01, 1 - y);
    src.draw(ctx, x * sw, y * sh, w * sw, h * sh, dx, dy, dw, dh);
  }

  function blurBackground(src) {
    const sw = src.width, sh = src.height;
    const k = Math.max(small.width / sw, small.height / sh);
    const dw = sw * k, dh = sh * k;
    src.draw(sctx, (small.width - dw) / 2, (small.height - dh) / 2, dw, dh);
    ctx.save();
    ctx.filter = "blur(14px) saturate(1.15) brightness(0.85)";
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(small, -40, -40, W + 80, H + 80);
    ctx.restore();
  }

  function separator(y) {
    ctx.fillStyle = "rgba(0,0,0,0.85)";
    ctx.fillRect(0, y - 3, W, 6);
  }

  function shotAt(t) {
    const shots = plan.shots || [];
    for (const s of shots) if (t >= s.t0 && t < s.t1) return s;
    return shots.length ? shots[shots.length - 1] : null;
  }

  function followX(t) {
    const keys = plan.keys || [];
    if (keys.length < 2) return plan.rect[0];
    if (t <= keys[0].t) return keys[0].x;
    for (let i = 1; i < keys.length; i++) {
      if (t < keys[i].t) {
        const a = keys[i - 1], b = keys[i];
        return a.x + ((b.x - a.x) * (t - a.t)) / Math.max(b.t - a.t, 1e-3);
      }
    }
    return keys[keys.length - 1].x;
  }

  /** Dibuja el fotograma de salida del instante t. `src`: objeto con draw(ctx, ...)/width/height. */
  function draw(src, t) {
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, W, H);
    const sw = src.width, sh = src.height;
    if (layout === "split" && plan.cam && plan.game) {
      drawRect(src, plan.cam, 0, 0, W, camH);
      drawRect(src, plan.game, 0, camH, W, H - camH);
      separator(camH);
    } else if (layout === "follow" && plan.rect) {
      const [, y, w, h] = plan.rect;
      drawRect(src, [clamp(followX(t), 0, 1 - w), y, w, h], 0, 0, W, H);
    } else if (layout === "speaker" && plan.shots?.length) {
      const shot = shotAt(t);
      if (shot.mode === "stack") {
        drawRect(src, shot.rects[0], 0, 0, W, H / 2);
        drawRect(src, shot.rects[1], 0, H / 2, W, H / 2);
        separator(H / 2);
      } else {
        drawRect(src, shot.rect, 0, 0, W, H);
      }
    } else if (layout === "crop") {
      const k = Math.max(W / sw, H / sh);
      const cw = W / k, ch = H / k;
      src.draw(ctx, (sw - cw) / 2, (sh - ch) / 2, cw, ch, 0, 0, W, H);
    } else {
      blurBackground(src);
      const dh = (sh * W) / sw;
      src.draw(ctx, 0, (H - dh) / 2, W, dh);
    }
    if (captions) drawCaptions(ctx, captions, t, { W, H, y: captionY });
    if (hook) drawHook(ctx, hook, t, { W, H, y: hookY, duration: hookSeconds });
  }

  return { canvas, ctx, draw };
}

/** Adaptador para dibujar un VideoSample de mediabunny o cualquier CanvasImageSource. */
export function frameSource(sampleOrImage) {
  if (sampleOrImage && typeof sampleOrImage.draw === "function" && "displayWidth" in sampleOrImage) {
    return {
      width: sampleOrImage.displayWidth,
      height: sampleOrImage.displayHeight,
      draw: (ctx, ...args) => sampleOrImage.draw(ctx, ...args),
    };
  }
  const img = sampleOrImage;
  return {
    width: img.videoWidth || img.displayWidth || img.width,
    height: img.videoHeight || img.displayHeight || img.height,
    draw: (ctx, ...args) => ctx.drawImage(img, ...args),
  };
}

const clamp = (v, a, b) => Math.min(Math.max(v, a), b);
