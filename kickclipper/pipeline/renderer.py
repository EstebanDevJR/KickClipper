"""Render final: corta el fragmento y lo convierte a vertical 9:16
(1080x1920) con fondo desenfocado, subtítulos quemados y título.

En macOS usa el encoder por hardware (h264_videotoolbox); en otros
sistemas cae a libx264.
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Callable

from ..config import settings
from . import subtitles

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


def render_clip(
    source: Path,
    start: float,
    end: float,
    words: list[dict],
    title: str,
    out: Path,
    on_progress: ProgressFn | None = None,
    layout: str = "blur",  # "blur" (fondo difuminado) | "crop" (recorte central)
    fmt: str = "short",    # "short" (9:16) | "video" (16:9)
) -> Path:
    duration = end - start
    ass_path = out.with_suffix(".ass")
    if words or title:
        subtitles.build_ass(words, start, end, title, ass_path, fmt=fmt)
        ass_escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        ass_part = f",ass='{ass_escaped}'"
    else:
        ass_part = ""  # sin subtítulos ni rótulo no hace falta el filtro ass

    if fmt == "video":
        w, h = settings.video_width, settings.video_height
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
            f"{ass_part}"
        )
        filter_args = ["-vf", vf]
    elif layout == "crop":
        w, h = settings.output_width, settings.output_height
        vf = (
            f"scale=-2:{h}:flags=lanczos,crop={w}:{h}"
            f"{ass_part}"
        )
        filter_args = ["-vf", vf]
    else:
        w, h = settings.output_width, settings.output_height
        graph = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale={w}:{h}:force_original_aspect_ratio=increase,"
            f"crop={w}:{h},gblur=sigma=24,eq=brightness=-0.08[bgb];"
            f"[fg]scale={w}:-2:flags=lanczos[fgs];"
            f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2{ass_part}[v]"
        )
        filter_args = ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?"]

    if settings.use_videotoolbox:
        venc = ["-c:v", "h264_videotoolbox", "-b:v", settings.video_bitrate,
                "-profile:v", "high", "-allow_sw", "1"]
    else:
        venc = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]

    cmd = [
        settings.ffmpeg_bin, "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
        *filter_args,
        *venc,
        "-r", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        str(out),
    ]

    log.info("Renderizando clip %s (%.1fs)", out.name, duration)
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        m = re.match(r"out_time_ms=(\d+)", line.strip())
        if m and on_progress:
            done = int(m.group(1)) / 1_000_000
            on_progress(min(done / duration, 0.999), "Renderizando")
    proc.wait()
    if proc.returncode != 0:
        err = proc.stderr.read() if proc.stderr else ""
        raise RuntimeError(f"ffmpeg falló ({proc.returncode}): {err[-2000:]}")
    ass_path.unlink(missing_ok=True)  # archivo intermedio, no debe quedar basura
    if on_progress:
        on_progress(1.0, "Clip renderizado")
    return out


def thumbnail(source: Path, t: float, out: Path) -> Path:
    """Extrae un fotograma como miniatura JPEG."""
    subprocess.run(
        [settings.ffmpeg_bin, "-y", "-ss", f"{t:.2f}", "-i", str(source),
         "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", str(out)],
        check=True, capture_output=True,
    )
    return out
