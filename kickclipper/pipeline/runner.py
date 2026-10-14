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
from . import (audio_analysis, chat, downloader, highlights, renderer, subtitles,
               timeline, transcriber, vision)

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
        job = db.get_job(job_id) or job

        # 1b. Chat del VOD en segundo plano (mientras se descarga y transcribe)
        sections = options.get("sections") or None
        chat_box: dict = {}
        chat_thread = _start_chat(job_id, job, sections, chat_box)

        source = downloader.download(
            job["url"], workdir,
            on_progress=_progress(job_id, "downloading"),
            sections=sections,
        )
        _check_cancel(job_id)

        # 2. Audio + transcripción (se reutiliza si ya existe: reintentos baratos)
        _set(job_id, status="transcribing", stage_progress=0)
        wav = downloader.extract_audio(source)
        transcript = workdir / "transcript.json"
        if transcript.exists():
            segments = json.loads(transcript.read_text(encoding="utf-8"))
        else:
            segments = transcriber.transcribe(wav, _progress(job_id, "transcribing"))
            transcript.write_text(json.dumps(segments, ensure_ascii=False), encoding="utf-8")
        if not segments:
            raise RuntimeError("La transcripción no produjo texto (¿VOD sin voz?)")
        _check_cancel(job_id)

        # 3. Análisis de audio (+ chat)
        _set(job_id, status="analyzing", stage_progress=0.2)
        feats_path = workdir / "features.npz"
        try:
            features = audio_analysis.AudioFeatures.load(feats_path)
        except Exception:
            features = audio_analysis.analyze(wav)
            features.save(feats_path)
        excitement = features.excitement
        _set(job_id, status="analyzing", stage_progress=0.7)
        if chat_thread is not None:
            chat_thread.join(timeout=600)
        chat_curve = chat.spike_curve(chat_box.get("data"), len(excitement))
        if chat_curve is not None:
            log.info("Señal de chat disponible para el job %s", job_id)
        _check_cancel(job_id)

        # 4. Selección de highlights (heurística + LLM)
        _set(job_id, status="scoring", stage_progress=0)
        duration = segments[-1]["end"] if sections else (job.get("duration") or segments[-1]["end"])
        candidates = highlights.detect(
            segments, excitement, duration, _progress(job_id, "scoring"),
            formats=formats, chat=chat_curve, channel=channel,
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
                meta={
                    "hook_text": c.get("hook_text", ""),
                    "keywords": c.get("keywords", []),
                    "category": c.get("category", ""),
                    "peak": c.get("peak"),
                    "signals": c.get("signals", {}),
                    "llm_scores": c.get("llm_scores", {}),
                    "chat": chat_curve is not None,
                },
            )
            clip_rows.append(clip)
        bus.publish({"type": "clips", "job_id": job_id, "clips": clip_rows})

        # Miniaturas de los candidatos (baratas, mejoran mucho la UI)
        (workdir / "clips").mkdir(exist_ok=True)
        for clip in clip_rows:
            peak = (clip.get("meta") or {}).get("peak")
            at = peak if peak and clip["start"] < peak < clip["end"] else (clip["start"] + clip["end"]) / 2
            try:
                renderer.thumbnail(source, at, workdir / "clips" / f"{clip['id']}.jpg")
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


def _start_chat(job_id: str, job: dict, sections: str | None,
                box: dict) -> threading.Thread | None:
    """Lee el chat del VOD en un hilo aparte; nunca hace fallar el job."""
    if not settings.chat_signal or not chat.vod_uuid(job["url"]):
        return None
    offset, length = 0.0, float(job.get("duration") or 0)
    if sections:
        start, end = downloader._parse_section(sections)
        offset = start
        if end != float("inf"):
            length = end - start
        elif length:
            length -= start
    if length <= 0:
        return None

    def work() -> None:
        try:
            box["data"] = chat.activity(job["url"], length, offset,
                                        cancelled=lambda: job_id in _cancelled)
        except Exception:
            log.exception("Lectura del chat falló")

    th = threading.Thread(target=work, daemon=True, name=f"kc-chat-{job_id}")
    th.start()
    return th


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
    meta = clip.get("meta") or {}
    fmt = o.get("format", "short")
    streamers.validate_format(fmt)
    start = float(o.get("start", clip["start"]))
    end = float(o.get("end", clip["end"]))
    _validate_duration(fmt, end - start)
    title = o.get("title", clip["title"])
    # Texto gancho de los primeros segundos (en clips antiguos, el título)
    hook_text = o.get("hook_text") or meta.get("hook_text") or (title if fmt == "short" else "")
    show_hook = o.get("show_hook", o.get("show_title", fmt == "short"))
    # los subtítulos no son necesarios en los videos; en shorts sí por defecto
    use_subs = o.get("subtitles", fmt != "video")
    style = o.get("caption_style") or subtitles.default_style(fmt)
    if not use_subs:
        style = "none"
    layout = o.get("layout") or ("auto" if fmt == "short" else "fit")
    if layout not in renderer.LAYOUTS and layout != "fit":
        raise ValueError(f"Layout desconocido: {layout}")
    jumpcut = bool(o.get("jumpcut", settings.jumpcut)) and fmt == "short"
    hook_preview = bool(o.get("hook_preview", False)) and fmt == "short"

    db.update_clip(clip_id, status="rendering", render_options=o)
    bus.publish({"type": "clip", "clip": db.get_clip(clip_id)})

    tmp_out = workdir / "clips" / f"{clip_id}.mp4"
    tmp_out.parent.mkdir(exist_ok=True)
    clip_len = end - start
    words = [
        {"w": w["w"], "s": max(w["s"] - start, 0.0), "e": min(w["e"] - start, clip_len)}
        for w in (clip.get("words") or [])
        if w["e"] > start and w["s"] < end
    ]

    def on_p(frac: float, msg: str) -> None:
        bus.publish({"type": "clip_progress", "clip_id": clip_id,
                     "progress": frac, "message": msg})

    try:
        # Encuadre: análisis de caras (se cachea por rango del clip)
        plan: dict = {}
        resolved = layout
        if fmt == "short" and layout in ("auto", "split", "speaker", "follow"):
            cached = o.get("_plan") if o.get("_plan_range") == [start, end] else None
            if cached is None:
                on_p(0.0, "Analizando encuadre (caras)")
                try:
                    cached = vision.plan(vision.analyze(source, start, end), words, clip_len)
                except Exception:
                    log.exception("Análisis visual falló; se usa fondo difuminado")
                    cached = {"layout": "blur"}
                o["_plan"], o["_plan_range"] = cached, [start, end]
            plan = cached
            if layout == "auto":
                resolved = plan.get("layout", "blur")
            elif layout != plan.get("layout"):
                log.info("Layout %s pedido pero no detectado (%s): fondo difuminado",
                         layout, plan.get("layout"))
                resolved = "blur"
        o["layout_used"] = resolved

        # Línea de tiempo: jump cuts y cold open
        energy = None
        wav = workdir / "audio.wav"
        if jumpcut and wav.exists():
            try:
                energy = audio_analysis.fine_energy(wav, start, end)
            except Exception:
                log.warning("No se pudo medir la energía fina; jump cuts solo por voz")
        preview = None
        peak = meta.get("peak")
        if hook_preview and peak is not None and 6.0 < peak - start < clip_len - 1.0:
            p = peak - start
            preview = (p - 1.4, p + 0.9)
        tl = timeline.build(clip_len, words, energy, jumpcut=jumpcut,
                            min_gap=settings.jumpcut_min_gap, preview=preview)

        # Hablante de cada palabra (colores) y encuadre en tiempo de salida
        if resolved == "speaker":
            for w in words:
                w["spk"] = vision.speaker_at(plan, (w["s"] + w["e"]) / 2)
        out_plan = _plan_to_output(plan, resolved, tl)

        renderer.render_clip(
            source, start, end, tmp_out,
            fmt=fmt, layout=resolved, plan=out_plan, timeline=tl,
            words=tl.map_words(words) if style != "none" else [],
            hook=hook_text if show_hook else "",
            caption_style=style, keywords=meta.get("keywords") or [],
            on_progress=on_p,
        )
        renderer.thumbnail(source, (start + end) / 2, tmp_out.with_suffix(".jpg"))
        final = _export(tmp_out, channel, fmt, title or clip["transcript"][:50], clip_id)
        # junto al mp4 queda un .txt con título, descripción, hashtags y redes
        final.with_suffix(".txt").write_text(
            publish_text(channel, title, clip), encoding="utf-8"
        )
        db.update_clip(clip_id, status="rendered", file=str(final),
                       start=start, end=end, title=title, render_options=o)
    except Exception:
        db.update_clip(clip_id, status="error")
        bus.publish({"type": "clip", "clip": db.get_clip(clip_id)})
        raise
    updated = db.get_clip(clip_id)
    bus.publish({"type": "clip", "clip": updated})
    return updated


def _plan_to_output(plan: dict, layout: str, tl: timeline.Timeline) -> dict:
    """Traduce los tiempos del plan de encuadre a la línea de tiempo de salida."""
    if layout == "speaker":
        shots = tl.map_spans(plan.get("shots") or [])
        merged: list[dict] = []
        for sh in shots:
            if (merged and abs(merged[-1]["t1"] - sh["t0"]) < 1e-3
                    and merged[-1]["mode"] == sh["mode"]
                    and merged[-1].get("rect") == sh.get("rect")
                    and merged[-1].get("rects") == sh.get("rects")):
                merged[-1]["t1"] = sh["t1"]
            else:
                merged.append(dict(sh))
        return {**plan, "shots": merged}
    if layout == "follow" and plan.get("keys"):
        keys = []
        for k in plan["keys"]:
            ts = tl.map_time(k["t"])
            if ts:
                keys.append({**k, "t": ts[-1]})
        keys.sort(key=lambda k: k["t"])
        return {**plan, "keys": keys}
    return plan


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
