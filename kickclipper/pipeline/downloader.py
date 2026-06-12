"""Descarga de VODs/streams de Kick usando yt-dlp (API de Python).

Soporta URLs de VOD (kick.com/<canal>/videos/<uuid>), clips oficiales y,
si se pasa la URL de un canal en vivo, graba el directo hasta que se corte.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import yt_dlp

from ..config import settings

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]  # (progreso 0..1, mensaje)


def probe(url: str) -> dict:
    """Obtiene metadatos sin descargar (título, canal, duración, miniatura)."""
    opts = {"quiet": True, "no_warnings": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "title": info.get("title") or "",
        "channel": info.get("channel") or info.get("uploader") or "",
        "duration": float(info.get("duration") or 0),
        "thumbnail": info.get("thumbnail") or "",
        "is_live": bool(info.get("is_live")),
    }


def download(
    url: str,
    dest_dir: Path,
    on_progress: ProgressFn | None = None,
    max_height: int = 1080,
    sections: str | None = None,
) -> Path:
    """Descarga el video y devuelve la ruta del archivo resultante.

    sections: rango opcional tipo "*00:10:00-00:25:00" para pruebas o
    procesar solo una parte del VOD.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / "source.%(ext)s")

    def hook(d: dict) -> None:
        if on_progress is None:
            return
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = (done / total) if total else 0.0
            speed = d.get("_speed_str") or ""
            on_progress(min(frac, 0.999), f"Descargando {speed}".strip())
        elif d.get("status") == "finished":
            on_progress(1.0, "Descarga completa")

    opts: dict = {
        "outtmpl": out_tmpl,
        "ffmpeg_location": settings.ffmpeg_bin,
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "progress_hooks": [hook],
        "quiet": True,
        "no_warnings": True,
        "concurrent_fragment_downloads": 8,
        "retries": 10,
        "fragment_retries": 10,
    }
    if sections:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [
            tuple(_parse_section(sections))
        ])
        # Cortar en keyframes para que el archivo sea reproducible sin re-encodear
        opts["force_keyframes_at_cuts"] = False

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    for ext in ("mp4", "mkv", "webm", "ts"):
        candidate = dest_dir / f"source.{ext}"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"yt-dlp no produjo archivo en {dest_dir}")


def _parse_section(spec: str) -> tuple[float, float]:
    """Convierte "HH:MM:SS-HH:MM:SS" (o segundos) a tupla (inicio, fin)."""
    spec = spec.strip().lstrip("*")
    start_s, _, end_s = spec.partition("-")

    def to_sec(t: str) -> float:
        parts = [float(p) for p in t.strip().split(":")]
        sec = 0.0
        for p in parts:
            sec = sec * 60 + p
        return sec

    return to_sec(start_s), to_sec(end_s) if end_s else float("inf")


def extract_audio(video: Path, on_progress: ProgressFn | None = None) -> Path:
    """Extrae audio mono 16 kHz WAV (formato que esperan Whisper y el análisis)."""
    import subprocess

    wav = video.parent / "audio.wav"
    if wav.exists():
        return wav
    cmd = [
        settings.ffmpeg_bin, "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(wav),
    ]
    if on_progress:
        on_progress(0.0, "Extrayendo audio")
    subprocess.run(cmd, check=True, capture_output=True)
    if on_progress:
        on_progress(1.0, "Audio extraído")
    return wav
