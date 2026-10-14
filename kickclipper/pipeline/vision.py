"""Análisis visual de un clip para elegir el encuadre vertical.

1. Se muestrean fotogramas del clip (6 fps, 640 px de ancho) y se detectan
   caras con YuNet (OpenCV), que funciona bien incluso con las caras pequeñas
   de una facecam en una esquina.
2. Las caras se siguen entre fotogramas y, para cada una, se mide el
   movimiento de la boca (diferencia de la zona de la boca entre fotogramas +
   variación de la distancia nariz-boca, que crece al abrir la mandíbula).
3. Con eso se decide el layout (ver docs/GUIA_CLIPS_VIRALES.md §5):
   - "split": facecam pequeña y fija + gameplay → cámara arriba, juego abajo.
   - "speaker": dos o más personas → recorte sobre quien habla, con corte
     seco al cambiar de hablante y pantalla dividida si hablan a la vez.
   - "follow": una persona en primer plano → recorte 9:16 que la sigue.
   - "blur": sin caras → vídeo completo sobre fondo difuminado.

Todas las coordenadas del plan van normalizadas (0..1) respecto al vídeo
fuente, así el plan es independiente de la resolución.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import settings

log = logging.getLogger(__name__)

MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_NAME = "face_detection_yunet_2023mar.onnx"

ANALYSIS_WIDTH = 640
ANALYSIS_FPS = 6.0
CAM_FRACTION = 0.38       # alto de la cámara en el layout split
STACK_ASPECT = 1080 / 960  # cada mitad de la pantalla dividida
VERTICAL_ASPECT = 9 / 16

MIN_SHOT = 1.2      # s: duración mínima de un plano por hablante
SWITCH_HOLD = 0.5   # s: el nuevo hablante debe sostenerse esto antes de cortar
CROSSTALK_HOLD = 1.0


def available() -> bool:
    if not settings.face_detection:
        return False
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def _model_path() -> Path | None:
    path = settings.models_dir / MODEL_NAME
    if path.exists() and path.stat().st_size > 100_000:
        return path
    try:
        import httpx

        path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Descargando detector de caras YuNet (230 KB)…")
        with httpx.Client(timeout=60, follow_redirects=True) as client:
            resp = client.get(MODEL_URL)
            resp.raise_for_status()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(resp.content)
        tmp.replace(path)
        return path
    except Exception:
        log.exception("No se pudo descargar el detector de caras")
        return None


def probe_size(source: Path) -> tuple[int, int] | None:
    """(ancho, alto) del vídeo usando el propio ffmpeg (no siempre hay ffprobe)."""
    proc = subprocess.run([settings.ffmpeg_bin, "-hide_banner", "-i", str(source)],
                          capture_output=True, text=True)
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr)
    return (int(m.group(1)), int(m.group(2))) if m else None


# ---------- análisis ----------

@dataclass
class _Track:
    tid: int
    t: list[float] = field(default_factory=list)
    box: list[list[float]] = field(default_factory=list)   # x, y, w, h normalizados
    act: list[float] = field(default_factory=list)
    patch: np.ndarray | None = None
    jaw: float | None = None
    last_t: float = -1.0


def analyze(source: Path, start: float, end: float, fps: float = ANALYSIS_FPS) -> dict | None:
    """Caras y actividad de boca a lo largo del clip [start, end] del vídeo."""
    if not available():
        return None
    import cv2

    try:
        cv2.setLogLevel(2)  # solo errores
    except Exception:
        pass
    model = _model_path()
    size = probe_size(source)
    if model is None or size is None:
        return None
    src_w, src_h = size
    w = ANALYSIS_WIDTH
    h = int(round(src_h * w / src_w / 2)) * 2
    detector = cv2.FaceDetectorYN.create(str(model), "", (w, h), 0.6, 0.3, 30)

    cmd = [
        settings.ffmpeg_bin, "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}",
        "-i", str(source), "-an", "-vf", f"fps={fps},scale={w}:{h}",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert proc.stdout is not None
    frame_bytes = w * h * 3
    tracks: list[_Track] = []
    n_frames = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            t = n_frames / fps
            n_frames += 1
            img = np.frombuffer(raw, np.uint8).reshape(h, w, 3)
            _, faces = detector.detect(img)
            if faces is None:
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            _assign(tracks, faces, gray, t, w, h, fps)
    finally:
        proc.stdout.close()
        proc.wait()
    if n_frames == 0:
        return None
    return {
        "src": [src_w, src_h], "fps": fps, "frames": n_frames, "duration": end - start,
        "tracks": [
            {"id": tr.tid, "t": tr.t, "box": tr.box, "act": tr.act}
            for tr in tracks if len(tr.t) >= 2
        ],
    }


def _assign(tracks: list[_Track], faces: np.ndarray, gray: np.ndarray, t: float,
            w: int, h: int, fps: float) -> None:
    import cv2

    used: set[int] = set()
    for f in faces:
        x, y, fw, fh = (float(v) for v in f[:4])
        box = [x / w, y / h, fw / w, fh / h]
        best, best_d = None, 1e9
        for tr in tracks:
            if tr.tid in used or t - tr.last_t > 1.0:
                continue
            bx = tr.box[-1]
            d = np.hypot(bx[0] + bx[2] / 2 - (box[0] + box[2] / 2),
                         bx[1] + bx[3] / 2 - (box[1] + box[3] / 2))
            if d < max(bx[2], bx[3]) * 0.8 and d < best_d:
                best, best_d = tr, d
        if best is None:
            best = _Track(tid=len(tracks))
            tracks.append(best)
        used.add(best.tid)

        # Zona de la boca a partir de las comisuras (landmarks 10-13 de YuNet)
        rmx, rmy, lmx, lmy = (float(v) for v in f[10:14])
        nose_y = float(f[9])
        mcx, mcy = (rmx + lmx) / 2, (rmy + lmy) / 2
        mw = max(abs(lmx - rmx), fw * 0.25)
        x0, x1 = int(mcx - mw * 0.8), int(mcx + mw * 0.8)
        y0, y1 = int(mcy - mw * 0.35), int(mcy + mw * 0.65)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        act = 0.0
        jaw = (mcy - nose_y) / max(fh, 1.0)
        if x1 - x0 >= 4 and y1 - y0 >= 4:
            patch = cv2.resize(gray[y0:y1, x0:x1], (32, 16)).astype(np.float32)
            patch = (patch - patch.mean()) / (patch.std() + 8.0)
            consecutive = t - best.last_t <= 1.5 / fps
            if best.patch is not None and consecutive:
                act = float(np.abs(patch - best.patch).mean())
                if best.jaw is not None:
                    act += 6.0 * abs(jaw - best.jaw)
            best.patch = patch
        best.jaw = jaw
        best.t.append(round(t, 3))
        best.box.append([round(v, 4) for v in box])
        best.act.append(round(act, 4))
        best.last_t = t


# ---------- plan de encuadre ----------

def plan(analysis: dict | None, words: list[dict] | None = None,
         duration: float | None = None) -> dict:
    """Decide el layout del clip. words: palabras con tiempos relativos al clip."""
    if not analysis or not analysis.get("tracks"):
        return {"layout": "blur"}
    src_w, src_h = analysis["src"]
    dur = duration or analysis["duration"]
    n_frames = max(analysis["frames"], 1)
    summary = []
    for tr in analysis["tracks"]:
        boxes = np.asarray(tr["box"])
        presence = len(tr["t"]) / n_frames
        med = np.median(boxes, axis=0)
        centers = boxes[:, :2] + boxes[:, 2:] / 2
        summary.append({
            **tr, "presence": presence, "med": med.tolist(),
            "area": float(med[2] * med[3]),
            "motion": float(centers.std(axis=0).max()) if len(centers) > 1 else 0.0,
            "cx": float(med[0] + med[2] / 2), "cy": float(med[1] + med[3] / 2),
        })

    faces = [s for s in summary if s["presence"] >= 0.25 and s["area"] >= 0.002]
    # Personas en primer plano (podcast, IRL): caras grandes
    people = sorted((s for s in faces if s["area"] >= 0.012),
                    key=lambda s: s["presence"] * s["area"], reverse=True)
    # Facecam: cara fija, no enorme y pegada a un borde/esquina del encuadre
    cams = [s for s in faces
            if s["presence"] >= 0.35 and s["area"] < 0.03 and s["motion"] < 0.03
            and (s["cx"] < 0.34 or s["cx"] > 0.66 or s["cy"] < 0.3 or s["cy"] > 0.62)]

    if len(people) >= 2:
        return _speaker_plan(people[:4], words or [], dur, src_w, src_h, analysis["fps"])
    if cams:
        cam = max(cams, key=lambda s: s["presence"] * s["area"])
        if not people or people[0] is cam or people[0]["area"] < 3 * cam["area"]:
            return _split_plan(cam, src_w, src_h)
    if people:
        return _follow_plan(people[0], src_w, src_h)
    return {"layout": "blur"}


def _rect_around(face: list[float], scale_h: float, aspect_px: float, src_w: int, src_h: int,
                 face_pos: float = 0.42, min_h: float = 0.2) -> list[float]:
    """Rectángulo (normalizado) con relación de aspecto `aspect_px` alrededor de una cara.

    scale_h: alto del recorte en múltiplos del alto de la cara.
    face_pos: altura (0..1 dentro del recorte) a la que queda el centro de la cara.
    """
    fx, fy, fw, fh = face
    rh = float(np.clip(fh * scale_h, min_h, 1.0))
    rw = rh * src_h * aspect_px / src_w
    if rw > 1.0:  # el recorte no cabe a lo ancho: se limita por el ancho
        rw = 1.0
        rh = rw * src_w / (aspect_px * src_h)
    cx, cy = fx + fw / 2, fy + fh / 2
    x = float(np.clip(cx - rw / 2, 0.0, 1.0 - rw))
    y = float(np.clip(cy - rh * face_pos, 0.0, 1.0 - rh))
    return [round(x, 4), round(y, 4), round(rw, 4), round(rh, 4)]


def _rect_fixed(face: list[float], rh: float, aspect_px: float, src_w: int, src_h: int,
                face_pos: float = 0.42) -> list[float]:
    """Como _rect_around pero con un alto de recorte fijo (normalizado)."""
    fh = max(face[3], 1e-3)
    return _rect_around(face, rh / fh, aspect_px, src_w, src_h, face_pos=face_pos, min_h=0.0)


def _split_plan(cam: dict, src_w: int, src_h: int) -> dict:
    out_w, out_h = settings.output_width, settings.output_height
    cam_h = int(round(out_h * CAM_FRACTION / 2)) * 2
    game_h = out_h - cam_h
    cam_rect = _rect_around(cam["med"], 2.4, out_w / cam_h, src_w, src_h, face_pos=0.45)
    gw = min(1.0, src_h * (out_w / game_h) / src_w)
    game_rect = [round((1 - gw) / 2, 4), 0.0, round(gw, 4), 1.0]
    return {"layout": "split", "cam": cam_rect, "game": game_rect,
            "cam_fraction": cam_h / out_h, "caption_y": cam_h / out_h}


def _follow_plan(person: dict, src_w: int, src_h: int) -> dict:
    boxes = np.asarray(person["box"])
    face_h = float(np.median(boxes[:, 3]))
    rect = _rect_around(person["med"], 4.2, VERTICAL_ASPECT, src_w, src_h, min_h=0.55)
    keys = []
    if person["motion"] > 0.02:
        # La persona se mueve: un fotograma clave por segundo, suavizado
        t = np.asarray(person["t"])
        cx = boxes[:, 0] + boxes[:, 2] / 2
        for sec in np.arange(0, t[-1] + 1e-6, 1.0):
            sel = np.abs(t - sec) <= 1.0
            if not sel.any():
                continue
            c = float(np.median(cx[sel]))
            x = float(np.clip(c - rect[2] / 2, 0.0, 1.0 - rect[2]))
            keys.append({"t": round(float(sec), 2), "x": round(x, 4)})
    return {"layout": "follow", "rect": rect, "keys": keys, "face_h": face_h}


def _speaker_plan(people: list[dict], words: list[dict], dur: float,
                  src_w: int, src_h: int, fps: float) -> dict:
    dt = 1.0 / fps
    grid = np.arange(0.0, max(dur, dt), dt)
    raw = np.zeros((len(people), len(grid)))
    present = np.zeros((len(people), len(grid)), dtype=bool)
    win = max(1, int(round(0.6 / dt)))
    for k, p in enumerate(people):
        idx = np.clip(np.round(np.asarray(p["t"]) / dt).astype(int), 0, len(grid) - 1)
        raw[k, idx] = np.asarray(p["act"])
        present[k, idx] = True
        # suavizado ~0,6 s: hablar es movimiento sostenido de la boca
        raw[k] = np.convolve(raw[k], np.ones(win) / win, mode="same")
    # Normalización: 0 = boca en reposo (p20 de esa cara), 1 = hablando (p90).
    # El rango mínimo es común a todas las caras: si alguien no habla nunca,
    # su ruido no se amplifica hasta parecer habla.
    base, spread = [], []
    for k in range(len(people)):
        vals = raw[k][present[k]] if present[k].any() else np.zeros(1)
        p20, p90 = np.percentile(vals, 20), np.percentile(vals, 90)
        base.append(p20)
        spread.append(p90 - p20)
    floor = 0.35 * max(spread) + 1e-3
    acts = np.full_like(raw, -1.0)
    for k in range(len(people)):
        norm = (raw[k] - base[k]) / max(spread[k], floor)
        acts[k] = np.where(present[k], norm, -1.0)

    speech = np.zeros(len(grid), dtype=bool)
    for w in words:
        speech[int(max(w["s"] - 0.25, 0) / dt) : int((w["e"] + 0.25) / dt) + 1] = True

    default = 0
    states: list[tuple] = []
    for i in range(len(grid)):
        order = np.argsort(acts[:, i])[::-1]
        first, second = order[0], order[1] if len(order) > 1 else order[0]
        a1, a2 = acts[first, i], acts[second, i]
        if speech[i] and a1 > 0.55 and a2 > 0.55 and a2 >= 0.75 * a1:
            states.append(("stack", tuple(sorted((int(first), int(second))))))
        elif speech[i] and a1 > 0.45:
            states.append(("single", int(first)))
        else:
            states.append(None)

    # Histéresis: un cambio solo se acepta si se sostiene y el plano actual
    # ya duró lo mínimo; los huecos sin decisión mantienen el plano actual.
    shots: list[dict] = []
    cur = ("single", default)
    cur_start = 0.0
    pending, pending_since = None, 0.0
    for i, st in enumerate(states):
        t = grid[i]
        if st is None or st == cur:
            pending = None
            continue
        if st != pending:
            pending, pending_since = st, t
        hold = CROSSTALK_HOLD if st[0] == "stack" else SWITCH_HOLD
        if t - pending_since >= hold and t - cur_start >= MIN_SHOT:
            switch_at = max(pending_since, cur_start + MIN_SHOT)
            shots.append({"t0": cur_start, "t1": switch_at, "state": cur})
            cur, cur_start, pending = st, switch_at, None
    shots.append({"t0": cur_start, "t1": dur, "state": cur})
    # Primer plano: si al principio nadie habla claro, quien hable primero
    first_decided = next((s for s in states if s and s[0] == "single"), None)
    if first_decided and shots[0]["state"] == ("single", default):
        shots[0]["state"] = first_decided

    # Mismo tamaño de recorte en todos los planos (cortes limpios entre caras)
    face_h = max(float(p["med"][3]) for p in people)
    single_h = float(np.clip(face_h * 4.0, 0.5, 1.0))
    stack_h = float(np.clip(face_h * 2.8, 0.3, 1.0))
    out = []
    for s in shots:
        if s["t1"] - s["t0"] <= 1e-3:
            continue
        mode, who = s["state"]
        if mode == "single":
            rect = _rect_fixed(people[who]["med"], single_h, VERTICAL_ASPECT, src_w, src_h)
            entry = {"t0": round(s["t0"], 2), "t1": round(s["t1"], 2), "mode": "single",
                     "spk": int(who), "rect": rect}
        else:
            rects = [_rect_fixed(people[k]["med"], stack_h, STACK_ASPECT, src_w, src_h)
                     for k in who]
            entry = {"t0": round(s["t0"], 2), "t1": round(s["t1"], 2), "mode": "stack",
                     "spk": None, "rects": rects}
        if out and out[-1]["mode"] == entry["mode"] and out[-1].get("spk") == entry.get("spk") \
                and entry["mode"] == "single":
            out[-1]["t1"] = entry["t1"]
        else:
            out.append(entry)
    return {"layout": "speaker", "shots": out, "speakers": len(people)}


def speaker_at(plan_: dict, t: float) -> int | None:
    """Índice del hablante en pantalla en el instante t (tiempo relativo al clip)."""
    for s in plan_.get("shots") or []:
        if s["t0"] <= t < s["t1"]:
            return s.get("spk")
    return None
