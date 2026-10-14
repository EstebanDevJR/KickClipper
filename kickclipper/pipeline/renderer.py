"""Render final con ffmpeg.

Shorts (1080×1920) con uno de estos layouts (ver vision.py):
- "blur":    vídeo completo centrado sobre un fondo difuminado del propio vídeo.
- "crop":    recorte central 9:16.
- "split":   facecam arriba + gameplay abajo, subtítulos en la costura.
- "follow":  recorte 9:16 que sigue a la persona (paneo suave).
- "speaker": recorte sobre el hablante activo, con corte seco al cambiar y
             pantalla dividida cuando hablan dos a la vez.
Videos (1920×1080) se escalan con bandas si hace falta.

La línea de tiempo del clip (jump cuts, cold open) se aplica primero; los
encuadres y subtítulos ya vienen en tiempo de salida. El audio se normaliza a
-14 LUFS. En macOS usa el encoder por hardware (h264_videotoolbox); en otros
sistemas libx264.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from ..config import settings
from . import subtitles
from .timeline import Timeline

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

LAYOUTS = ("auto", "blur", "crop", "split", "follow", "speaker")


def render_clip(
    source: Path,
    start: float,
    end: float,
    out: Path,
    *,
    fmt: str = "short",
    layout: str = "blur",
    plan: dict | None = None,
    timeline: Timeline | None = None,
    words: list[dict] | None = None,
    hook: str = "",
    caption_style: str | None = None,
    keywords: list[str] | tuple[str, ...] = (),
    on_progress: ProgressFn | None = None,
) -> Path:
    """Renderiza [start, end] del vídeo fuente.

    words: palabras en tiempo de SALIDA (ya mapeadas con la línea de tiempo).
    plan: plan de encuadre de vision.plan (con tiempos ya en tiempo de salida).
    """
    clip_len = end - start
    tl = timeline or Timeline([(0.0, clip_len)])
    out_dur = tl.duration
    plan = plan or {}
    workdir = out.parent
    workdir.mkdir(parents=True, exist_ok=True)

    style = caption_style or subtitles.default_style(fmt)
    ass_name = None
    if (words and style != "none") or hook:
        ass_name = f"{out.stem}.ass"
        subtitles.build_ass(
            words or [], workdir / ass_name, duration=out_dur, fmt=fmt, style=style,
            hook=hook, keywords=keywords,
            caption_y=plan.get("caption_y") if layout == "split" else None,
            # en split la cara está arriba: el gancho va bajo la costura
            hook_y=plan["caption_y"] + 0.075 if layout == "split" and plan.get("caption_y") else None,
        )
        _ensure_fonts(workdir)

    has_audio = _has_audio(source)
    graph = build_graph(fmt=fmt, layout=layout, plan=plan, timeline=tl, clip_len=clip_len,
                        ass_name=ass_name, has_audio=has_audio)

    if settings.use_videotoolbox:
        venc = ["-c:v", "h264_videotoolbox", "-b:v", settings.video_bitrate,
                "-profile:v", "high", "-allow_sw", "1"]
    else:
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-maxrate", "12M", "-bufsize", "24M", "-profile:v", "high"]

    cmd = [
        settings.ffmpeg_bin, "-y", "-hide_banner",
        "-ss", f"{start:.3f}", "-t", f"{clip_len:.3f}", "-i", str(source),
        "-filter_complex", graph,
        "-map", "[vout]", *(["-map", "[aout]"] if has_audio else []),
        *venc,
        "-g", str(settings.output_fps * 2), "-pix_fmt", "yuv420p",
        *(["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if has_audio else []),
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]

    log.info("Renderizando %s (%.1fs, layout %s)", out.name, out_dur, layout)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=workdir)
    assert proc.stdout is not None
    for line in proc.stdout:
        m = re.match(r"out_time_us=(\d+)", line.strip()) or re.match(r"out_time_ms=(\d+)", line.strip())
        if m and on_progress:
            done = int(m.group(1)) / 1_000_000
            on_progress(min(done / max(out_dur, 0.1), 0.999), "Renderizando")
    proc.wait()
    if ass_name:
        (workdir / ass_name).unlink(missing_ok=True)  # archivo intermedio
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"ffmpeg falló ({proc.returncode}): {err[-2000:]}")
    if on_progress:
        on_progress(1.0, "Clip renderizado")
    return out


def build_graph(*, fmt: str, layout: str, plan: dict, timeline: Timeline, clip_len: float,
                ass_name: str | None, has_audio: bool = True) -> str:
    """Construye el -filter_complex completo (separado para poder testearlo)."""
    fps = settings.output_fps
    parts: list[str] = []

    # 1. Línea de tiempo: tramos del original en orden de salida
    if timeline.is_plain(clip_len):
        parts.append(f"[0:v]fps={fps},setpts=PTS-STARTPTS[vt]")
        if has_audio:
            parts.append("[0:a]aresample=48000,asetpts=PTS-STARTPTS[at]")
    else:
        k = len(timeline.pieces)
        parts.append(f"[0:v]fps={fps},split={k}" + "".join(f"[vs{i}]" for i in range(k)))
        if has_audio:
            parts.append("[0:a]aresample=48000,asplit=" + str(k)
                         + "".join(f"[as{i}]" for i in range(k)))
        for i, (a, b) in enumerate(timeline.pieces):
            parts.append(f"[vs{i}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[vp{i}]")
            if has_audio:
                parts.append(f"[as{i}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[ap{i}]")
        ins = "".join(f"[vp{i}]" + (f"[ap{i}]" if has_audio else "") for i in range(k))
        parts.append(f"{ins}concat=n={k}:v=1:a={1 if has_audio else 0}[vt]"
                     + ("[at]" if has_audio else ""))

    # 2. Encuadre
    parts.append(_layout_chain(fmt, layout, plan))

    # 3. Subtítulos / texto gancho (fuentes incluidas en ./fonts)
    if ass_name:
        parts.append(f"[vl]ass={ass_name}:fontsdir=fonts,setsar=1[vout]")
    else:
        parts.append("[vl]setsar=1[vout]")

    # 4. Audio a -14 LUFS (pico real -1,5 dBTP)
    if has_audio:
        if settings.loudnorm:
            parts.append("[at]loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[aout]")
        else:
            parts.append("[at]anull[aout]")
    return ";".join(parts)


def _layout_chain(fmt: str, layout: str, plan: dict) -> str:
    if fmt == "video":
        w, h = settings.video_width, settings.video_height
        return (f"[vt]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[vl]")

    w, h = settings.output_width, settings.output_height
    if layout == "crop":
        return f"[vt]scale=-2:{h}:flags=lanczos,crop={w}:{h}[vl]"

    if layout == "split" and plan.get("cam") and plan.get("game"):
        cam_h = int(round(h * plan.get("cam_fraction", 0.38) / 2)) * 2
        game_h = h - cam_h
        return (
            f"[vt]split=2[cs][gs];"
            f"[cs]{_crop(plan['cam'])},scale={w}:{cam_h}:flags=lanczos,setsar=1[cv];"
            f"[gs]{_crop(plan['game'])},scale={w}:{game_h}:flags=lanczos,setsar=1[gv];"
            f"[cv][gv]vstack=inputs=2,"
            f"drawbox=x=0:y={cam_h - 3}:w=iw:h=6:color=black@0.85:t=fill[vl]"
        )

    if layout == "follow" and plan.get("rect"):
        rx, ry, rw, rh = plan["rect"]
        keys = plan.get("keys") or []
        x_expr = _linear_expr(keys, "x", rx) if len(keys) >= 2 else f"{rx}"
        return (f"[vt]crop=w='iw*{rw}':h='ih*{rh}':x='iw*({x_expr})':y='ih*{ry}',"
                f"scale={w}:{h}:flags=lanczos[vl]")

    if layout == "speaker" and plan.get("shots"):
        return _speaker_chain(plan["shots"], w, h)

    # blur (por defecto): el fondo se difumina a baja resolución (rápido)
    bw, bh = w // 8, h // 8
    return (
        f"[vt]split=2[bg][fg];"
        f"[bg]scale={bw}:{bh}:force_original_aspect_ratio=increase,crop={bw}:{bh},"
        f"gblur=sigma=4,scale={w}:{h}:flags=bicubic,eq=brightness=-0.10:saturation=1.15[bgb];"
        f"[fg]scale={w}:-2:flags=lanczos[fgs];"
        f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2[vl]"
    )


def _speaker_chain(shots: list[dict], w: int, h: int) -> str:
    singles = [s for s in shots if s["mode"] == "single"]
    stacks = [s for s in shots if s["mode"] == "stack"]
    if not singles:  # todo en pantalla dividida: se usa el primer plano doble
        singles = [{"t0": 0, "t1": 1e9, "rect": _stack_as_single(stacks[0])}]
    rw, rh = singles[0]["rect"][2], singles[0]["rect"][3]
    xs = _step_expr([(s["t0"], s["t1"], s["rect"][0]) for s in singles])
    ys = _step_expr([(s["t0"], s["t1"], s["rect"][1]) for s in singles])
    single = (f"crop=w='iw*{rw}':h='ih*{rh}':x='iw*({xs})':y='ih*({ys})',"
              f"scale={w}:{h}:flags=lanczos,setsar=1")
    if not stacks:
        return f"[vt]{single}[vl]"

    half = h // 2
    chains = []
    for idx, label in ((0, "ta"), (1, "tb")):
        r = stacks[0]["rects"][idx]
        sx = _step_expr([(s["t0"], s["t1"], s["rects"][idx][0]) for s in stacks])
        sy = _step_expr([(s["t0"], s["t1"], s["rects"][idx][1]) for s in stacks])
        chains.append(
            f"[{label}i]crop=w='iw*{r[2]}':h='ih*{r[3]}':x='iw*({sx})':y='ih*({sy})',"
            f"scale={w}:{half}:flags=lanczos,setsar=1[{label}]"
        )
    enable = "+".join(f"between(t,{s['t0']:.3f},{s['t1']:.3f})" for s in stacks)
    return (
        f"[vt]split=3[si][tai][tbi];"
        f"[si]{single}[sv];"
        + ";".join(chains) + ";"
        f"[ta][tb]vstack=inputs=2,drawbox=x=0:y={half - 3}:w=iw:h=6:color=black@0.85:t=fill[stk];"
        f"[sv][stk]overlay=0:0:enable='{enable}'[vl]"
    )


def _stack_as_single(stack: dict) -> list[float]:
    r = stack["rects"][0]
    return [r[0], r[1], r[2] / 2, r[3]]


def _crop(rect: list[float]) -> str:
    x, y, rw, rh = rect
    return f"crop=w='iw*{rw}':h='ih*{rh}':x='iw*{x}':y='ih*{y}'"


def _step_expr(steps: list[tuple[float, float, float]]) -> str:
    """Expresión ffmpeg constante a trozos: valor de cada tramo según t."""
    steps = sorted(steps)
    if not steps:
        return "0"
    expr = f"{steps[-1][2]}"
    for t0, t1, v in reversed(steps[:-1]):
        expr = f"if(lt(t,{t1:.3f}),{v},{expr})"
    return expr


def _linear_expr(keys: list[dict], field: str, default: float) -> str:
    """Interpolación lineal entre fotogramas clave [{t, x}] (tiempo de salida)."""
    if not keys:
        return f"{default}"
    expr = f"{keys[-1][field]}"
    for a, b in reversed(list(zip(keys, keys[1:]))):
        t0, t1 = a["t"], b["t"]
        if t1 - t0 < 1e-3:
            continue
        seg = f"{a[field]}+({b[field]}-{a[field]})*(t-{t0:.3f})/{t1 - t0:.3f}"
        expr = f"if(lt(t,{t1:.3f}),{seg},{expr})"
    return f"if(lt(t,{keys[0]['t']:.3f}),{keys[0][field]},{expr})"


def _ensure_fonts(workdir: Path) -> None:
    """Copia las fuentes incluidas junto al .ass (rutas sin caracteres raros)."""
    dst = workdir / "fonts"
    if dst.exists():
        return
    try:
        shutil.copytree(settings.fonts_dir, dst)
    except Exception:
        log.warning("No se pudieron copiar las fuentes; libass usará las del sistema")
        dst.mkdir(exist_ok=True)


_audio_cache: dict[str, bool] = {}


def _has_audio(source: Path) -> bool:
    key = str(source)
    if key not in _audio_cache:
        proc = subprocess.run([settings.ffmpeg_bin, "-hide_banner", "-i", key],
                              capture_output=True, text=True)
        _audio_cache[key] = "Audio:" in proc.stderr
    return _audio_cache[key]


def thumbnail(source: Path, t: float, out: Path) -> Path:
    """Extrae un fotograma como miniatura JPEG."""
    subprocess.run(
        [settings.ffmpeg_bin, "-y", "-ss", f"{t:.2f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)],
        check=True, capture_output=True,
    )
    return out
