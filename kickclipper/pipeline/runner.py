"""Orquestador: ejecuta el pipeline completo de un job en un hilo de
trabajo y publica el progreso en el bus de eventos.

Etapas: downloading → transcribing → analyzing → scoring → rendering → done
"""
from __future__ import annotations

import json
import logging
import shutil
import threading
import queue
from pathlib import Path

import re
import unicodedata

from .. import db, streamers
from ..config import settings
from ..events import bus
from . import audio_analysis, downloader, highlights, renderer, transcriber

log = logging.getLogger(__name__)

_queue: "queue.Queue[str]" = queue.Queue()
_cancelled: set[str] = set()
_worker_started = False
_lock = threading.Lock()


def job_dir(job_id: str) -> Path:
    return settings.jobs_dir / job_id


def enqueue(job_id: str) -> None:
    global _worker_started
    with _lock:
        if not _worker_started:
            threading.Thread(target=_worker_loop, daemon=True, name="kc-worker").start()
            _worker_started = True
    _queue.put(job_id)


def cancel(job_id: str) -> None:
    _cancelled.add(job_id)


def _worker_loop() -> None:
    while True:
        job_id = _queue.get()
        if job_id in _cancelled:
            _cancelled.discard(job_id)
            _set(job_id, status="cancelled")
            continue
        try:
            _run_job(job_id)
        except Exception as exc:
            log.exception("Job %s falló", job_id)
            _set(job_id, status="error", error=str(exc)[:1000])


def _set(job_id: str, **fields) -> None:
    db.update_job(job_id, **fields)
    bus.publish({"type": "job", "job": db.get_job(job_id)})


def _progress(job_id: str, stage: str):
    def fn(frac: float, msg: str) -> None:
        db.update_job(job_id, status=stage, stage_progress=frac)
        bus.publish({
            "type": "progress", "job_id": job_id,
            "stage": stage, "progress": frac, "message": msg,
        })
    return fn


def _check_cancel(job_id: str) -> None:
    if job_id in _cancelled:
        raise _Cancelled()


class _Cancelled(Exception):
    pass


def _run_job(job_id: str) -> None:
    job = db.get_job(job_id)
    if job is None:
        return
    options = job.get("options") or {}
    workdir = job_dir(job_id)
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # 0. Canal del VOD (cualquier canal de kick.com) y formatos pedidos
        channel = streamers.validate_url(job["url"])
        formats = streamers.validate_formats(options.get("formats"))

        # 1. Metadatos + descarga
        _set(job_id, status="downloading", stage_progress=0)
        try:
            meta = downloader.probe(job["url"])
            _set(job_id, title=meta["title"], channel=meta["channel"],
                 duration=meta["duration"], thumbnail=meta["thumbnail"])
        except Exception:
            log.warning("No se pudieron obtener metadatos de %s", job["url"])

        source = downloader.download(
            job["url"], workdir,
            on_progress=_progress(job_id, "downloading"),
            sections=options.get("sections") or None,
        )
        _check_cancel(job_id)

        # 2. Audio + transcripción
        _set(job_id, status="transcribing", stage_progress=0)
        wav = downloader.extract_audio(source)
        segments = transcriber.transcribe(wav, _progress(job_id, "transcribing"))
        (workdir / "transcript.json").write_text(
            json.dumps(segments, ensure_ascii=False), encoding="utf-8"
        )
        if not segments:
            raise RuntimeError("La transcripción no produjo texto (¿VOD sin voz?)")
        _check_cancel(job_id)

        # 3. Análisis de audio
        _set(job_id, status="analyzing", stage_progress=0.5)
        excitement = audio_analysis.excitement_curve(wav)
        _check_cancel(job_id)

        # 4. Selección de highlights (heurística + LLM)
        _set(job_id, status="scoring", stage_progress=0)
        duration = job.get("duration") or segments[-1]["end"]
        candidates = highlights.detect(
            segments, excitement, duration, _progress(job_id, "scoring"),
            formats=formats,
        )
        if not candidates:
            raise RuntimeError("No se encontraron momentos clipeables")
        _check_cancel(job_id)

        db.delete_clips_for_job(job_id)
        for stale in (workdir / "clips").glob("*"):
            stale.unlink(missing_ok=True)  # miniaturas/temporales de corridas previas
        clip_rows = []
        for c in candidates:
            words = [w for s in c["segments"] for w in s["words"]]
            fmt = c.get("format", "short")
            clip = db.create_clip(
                job_id,
                start=round(c["start"], 2), end=round(c["end"], 2),
                score=round(c["final"], 2),
                heuristic_score=round(c["heuristic"], 3),
                llm_score=round(c["llm_score"], 1),
                title=c.get("title", ""), reason=c.get("reason", ""),
                description=c.get("description", ""),
                hashtags=c.get("hashtags", []),
                transcript=" ".join(s["text"] for s in c["segments"])[:2000],
                words=words,
                # los videos largos no llevan subtítulos por defecto
                render_options={"format": fmt, "subtitles": fmt != "video"},
            )
            clip_rows.append(clip)
        bus.publish({"type": "clips", "job_id": job_id, "clips": clip_rows})

        # Miniaturas de los candidatos (baratas, mejoran mucho la UI)
        (workdir / "clips").mkdir(exist_ok=True)
        for clip in clip_rows:
            try:
                renderer.thumbnail(
                    source, (clip["start"] + clip["end"]) / 2,
                    workdir / "clips" / f"{clip['id']}.jpg",
                )
            except Exception:
                pass

        # 5. Render automático de los mejores (opcional, por defecto sí)
        auto_render = int(options.get("auto_render", 3))
        if auto_render:
            _set(job_id, status="rendering", stage_progress=0)
            for i, clip in enumerate(clip_rows[:auto_render]):
                _check_cancel(job_id)
                try:
                    render_clip(clip["id"])
                except Exception:
                    log.exception("Render del clip %s falló", clip["id"])
                _progress(job_id, "rendering")((i + 1) / auto_render, "Renderizando clips")

        _set(job_id, status="done", stage_progress=1)
    except _Cancelled:
        _cancelled.discard(job_id)
        _set(job_id, status="cancelled")


def render_clip(clip_id: str, overrides: dict | None = None) -> dict:
    """Renderiza (o re-renderiza) un clip; usable desde el worker o la API."""
    clip = db.get_clip(clip_id)
    if clip is None:
        raise ValueError("Clip no existe")
    job = db.get_job(clip["job_id"])
    workdir = job_dir(clip["job_id"])
    source = next(
        (p for p in (workdir / f"source.{e}" for e in ("mp4", "mkv", "webm", "ts")) if p.exists()),
        None,
    )
    if source is None:
        raise RuntimeError("El video fuente ya no existe (¿job borrado?)")

    channel = streamers.channel_from_url(job["url"]) if job else None
    if channel is None:
        raise ValueError("No se pudo determinar el canal del job")

    o = {**(clip.get("render_options") or {}), **(overrides or {})}
    fmt = o.get("format", "short")
    streamers.validate_format(fmt)
    start = float(o.get("start", clip["start"]))
    end = float(o.get("end", clip["end"]))
    _validate_duration(fmt, end - start)
    title = o.get("title", clip["title"])
    layout = o.get("layout", "blur")
    # los subtítulos no son necesarios en los videos; en shorts sí por defecto
    use_subs = o.get("subtitles", fmt != "video")

    db.update_clip(clip_id, status="rendering", render_options=o)
    bus.publish({"type": "clip", "clip": db.get_clip(clip_id)})

    tmp_out = workdir / "clips" / f"{clip_id}.mp4"
    tmp_out.parent.mkdir(exist_ok=True)
    words = clip.get("words") or []

    def on_p(frac: float, msg: str) -> None:
        bus.publish({"type": "clip_progress", "clip_id": clip_id,
                     "progress": frac, "message": msg})

    try:
        renderer.render_clip(
            source, start, end,
            words if use_subs else [],
            title if o.get("show_title", True) else "",
            tmp_out, on_p, layout=layout, fmt=fmt,
        )
        renderer.thumbnail(source, (start + end) / 2, tmp_out.with_suffix(".jpg"))
        final = _export(tmp_out, channel, fmt, title or clip["transcript"][:50], clip_id)
        # junto al mp4 queda un .txt con título, descripción, hashtags y redes
        final.with_suffix(".txt").write_text(
            publish_text(channel, title, clip), encoding="utf-8"
        )
        db.update_clip(clip_id, status="rendered", file=str(final),
                       start=start, end=end, title=title)
    except Exception:
        db.update_clip(clip_id, status="error")
        bus.publish({"type": "clip", "clip": db.get_clip(clip_id)})
        raise
    updated = db.get_clip(clip_id)
    bus.publish({"type": "clip", "clip": updated})
    return updated


def publish_text(channel: str, title: str, clip: dict) -> str:
    """Texto listo para publicar: título, descripción, redes y hashtags."""
    parts = []
    if title or clip.get("title"):
        parts.append(f"TÍTULO:\n{title or clip['title']}")
    if clip.get("description"):
        parts.append(f"DESCRIPCIÓN:\n{clip['description']}")
    links = streamers.socials(channel)
    if links:
        parts.append(
            f"Sigue a {channel} en sus redes:\n"
            + "\n".join(f"{red}: {url}" for red, url in links.items())
        )
    tags = " ".join(f"#{h}" for h in (clip.get("hashtags") or []))
    if tags:
        parts.append(tags)
    return "\n\n".join(parts) + "\n"


def _validate_duration(fmt: str, dur: float) -> None:
    """Un short dura como máximo 1 minuto; un video, entre 5 y 10 minutos."""
    min_s, max_s, _ = settings.clip_limits(fmt)
    if min_s - 0.5 <= dur <= max_s + 0.5:
        return
    if fmt == "video":
        raise ValueError(
            f"Un video debe durar entre {min_s / 60:.0f} y {max_s / 60:.0f} minutos "
            f"(este corte dura {dur / 60:.1f} min)."
        )
    raise ValueError(
        f"Un short debe durar entre {min_s:.0f} s y {max_s:.0f} s "
        f"(este corte dura {dur:.0f} s)."
    )


def _export(tmp: Path, channel: str, fmt: str, title: str, clip_id: str) -> Path:
    """Mueve el mp4 final a output/<streamer>/<shorts|videos>/ con nombre limpio.

    La carpeta de salida contiene SOLO los mp4 listos para subir; todos los
    archivos intermedios viven (y se limpian) en data/jobs/.
    """
    folder = settings.output_dir / channel / streamers.FORMAT_LABEL[fmt]
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"{_slugify(title)}_{clip_id}.mp4"
    if final.exists():
        final.unlink()
    shutil.move(str(tmp), final)
    return final


def _slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "clip"


def delete_job_files(job_id: str) -> None:
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
