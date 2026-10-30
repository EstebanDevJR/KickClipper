"""Servidor web: API REST + SSE + frontend estático."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import db, hardware, streamers
from .config import settings
from .events import bus
from .pipeline import highlights, renderer, runner, subtitles, vision

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="KickClipper")
WEB_DIR = Path(__file__).parent / "web"


@app.on_event("startup")
async def _startup() -> None:
    bus.attach_loop(asyncio.get_running_loop())
    db.get_conn()
    runner.recover()


# ---------- Modelos ----------

class NewJob(BaseModel):
    url: str
    sections: str | None = None     # "HH:MM:SS-HH:MM:SS" opcional
    auto_render: int = 3
    formats: list[str] | None = None  # ["short"], ["video"] o ambos
    llm_model: str | None = None      # modelo de Ollama instalado; "none" = sin IA


class RenderRequest(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    # "auto" (detecta facecam / hablantes) | "split" | "speaker" | "follow" | "blur" | "crop"
    layout: str | None = None
    format: str | None = None        # "short" (9:16) | "video" (16:9)
    subtitles: bool | None = None
    caption_style: str | None = None  # "viral" | "karaoke" | "clean"
    hook_text: str | None = None      # texto gancho de los primeros segundos
    show_hook: bool | None = None
    show_title: bool | None = None    # compatibilidad: equivale a show_hook
    jumpcut: bool | None = None       # recortar pausas
    hook_preview: bool | None = None  # cold open: adelanto del clímax al inicio
    cam_rect: list[float] | None = None  # cámara marcada a mano [x, y, w, h] (0..1)
    clear_cam: bool | None = None     # olvida la cámara marcada a mano


class PullRequest(BaseModel):
    name: str


# ---------- Jobs ----------

@app.get("/api/jobs")
def api_jobs():
    return db.list_jobs()


@app.post("/api/jobs")
def api_create_job(body: NewJob):
    try:
        streamers.validate_url(body.url)
        formats = streamers.validate_formats(body.formats)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    job = db.create_job(body.url, {
        "sections": body.sections, "auto_render": body.auto_render,
        "formats": formats,
        "llm_model": (body.llm_model or "").strip()[:200] or None,
    })
    runner.enqueue(job["id"])
    return job


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    return job


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str):
    runner.cancel(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404)
    db.update_job(job_id, status="queued", error="", stage_progress=0)
    runner.enqueue(job_id)
    return db.get_job(job_id)


@app.delete("/api/jobs/{job_id}")
def api_delete_job(job_id: str):
    runner.cancel(job_id)
    db.delete_job(job_id)
    runner.delete_job_files(job_id)
    return {"ok": True}


# ---------- Clips ----------

@app.get("/api/jobs/{job_id}/clips")
def api_clips(job_id: str):
    return db.list_clips(job_id)


@app.post("/api/clips/{clip_id}/render")
async def api_render_clip(clip_id: str, body: RenderRequest):
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(404)
    overrides = {k: v for k, v in body.model_dump().items() if v is not None}
    if overrides.pop("clear_cam", False):
        overrides["cam_rect"] = None
    if overrides.get("cam_rect") is not None:
        r = overrides["cam_rect"]
        if len(r) != 4 or not all(0 <= float(v) <= 1 for v in r):
            raise HTTPException(400, "cam_rect debe ser [x, y, ancho, alto] entre 0 y 1")
    try:
        return await asyncio.to_thread(runner.render_clip, clip_id, overrides)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, str(exc))


@app.get("/api/clips/{clip_id}/video")
def api_clip_video(clip_id: str):
    clip = db.get_clip(clip_id)
    if not clip or not clip.get("file") or not Path(clip["file"]).exists():
        raise HTTPException(404)
    return FileResponse(clip["file"], media_type="video/mp4",
                        filename=f"{(clip['title'] or clip_id)[:50]}.mp4")


@app.get("/api/clips/{clip_id}/frame")
def api_clip_frame(clip_id: str, t: float | None = None):
    """Fotograma del vídeo fuente (para marcar la cámara a mano)."""
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(404)
    workdir = runner.job_dir(clip["job_id"])
    source = next((p for p in (workdir / f"source.{e}" for e in ("mp4", "mkv", "webm", "ts")) if p.exists()), None)
    if source is None:
        raise HTTPException(404, "El vídeo fuente ya no existe")
    at = t if t is not None else (clip["start"] + clip["end"]) / 2
    out = workdir / "clips" / f"{clip_id}_frame.jpg"
    out.parent.mkdir(exist_ok=True)
    try:
        renderer.thumbnail(source, at, out, width=960)
    except Exception as exc:
        raise HTTPException(500, str(exc))
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/clips/{clip_id}/thumb")
def api_clip_thumb(clip_id: str):
    clip = db.get_clip(clip_id)
    if not clip:
        raise HTTPException(404)
    thumb = runner.job_dir(clip["job_id"]) / "clips" / f"{clip_id}.jpg"
    if not thumb.exists():
        raise HTTPException(404)
    return FileResponse(thumb, media_type="image/jpeg")


# ---------- Estado / eventos ----------

@app.get("/api/status")
async def api_status():
    """Comprueba dependencias externas (Ollama, ffmpeg)."""
    import shutil as sh

    ffmpeg_ok = (
        Path(settings.ffmpeg_bin).exists() if "/" in settings.ffmpeg_bin
        else sh.which(settings.ffmpeg_bin) is not None
    )
    models = await asyncio.to_thread(highlights.ollama_models)
    return {
        "ffmpeg": ffmpeg_ok,
        "ollama": models is not None,
        "ollama_models": models or [],
        # el de la configuración si está instalado; si no, el que se usará en su lugar
        "ollama_model": highlights.pick_model(settings.ollama_model, models or []) or settings.ollama_model,
        "ollama_pulling": _pulling,
        **await asyncio.to_thread(hardware.summary),
        "whisper_engine": settings.whisper_engine,
        "whisper_model": settings.whisper_model
        if settings.whisper_engine == "mlx" else settings.faster_whisper_model,
        "formats": list(streamers.FORMATS),
        "layouts": list(renderer.LAYOUTS),
        "caption_styles": list(subtitles.STYLES),
        "caption_style": settings.caption_style,
        "vision": vision.available(),
        "video_encoder": await asyncio.to_thread(renderer.video_encoder) if ffmpeg_ok else "",
        "chat_signal": settings.chat_signal,
        "jumpcut": settings.jumpcut,
        "streamer_socials": {ch: streamers.socials(ch) for ch in settings.streamer_socials},
        "output_dir": str(settings.output_dir),
    }


# ---------- Modelos de Ollama ----------

_pulling: str | None = None  # modelo que se está descargando (uno a la vez)
_MODEL_NAME = re.compile(r"^[\w.\-/]+(:[\w.\-]+)?$")


@app.post("/api/models/pull")
def api_pull_model(body: PullRequest):
    """Descarga un modelo con `ollama pull`; el progreso llega por /api/events."""
    global _pulling
    name = body.name.strip()
    if not _MODEL_NAME.match(name) or len(name) > 200:
        raise HTTPException(400, "Nombre de modelo no válido")
    if _pulling:
        raise HTTPException(409, f"Ya se está descargando {_pulling}")
    _pulling = name
    threading.Thread(target=_pull, args=(name,), daemon=True, name="kc-pull").start()
    return {"ok": True}


def _pull(name: str) -> None:
    global _pulling
    import httpx

    def send(**fields) -> None:
        bus.publish({"type": "model_pull", "name": name, **fields})

    try:
        with httpx.stream("POST", f"{settings.ollama_url}/api/pull",
                          json={"model": name, "stream": True}, timeout=httpx.Timeout(30, read=600)) as r:
            r.raise_for_status()
            last = -1.0
            for line in r.iter_lines():
                if not line:
                    continue
                msg = json.loads(line)
                if msg.get("error"):
                    raise RuntimeError(msg["error"])
                total, done = msg.get("total"), msg.get("completed")
                frac = done / total if total and done else None
                # un evento por cada 1 % como mucho: la descarga manda cientos por segundo
                if frac is None or frac - last >= 0.01 or frac >= 1:
                    last = frac if frac is not None else last
                    send(status=msg.get("status", ""), progress=frac)
        send(status="listo", progress=1.0, done=True)
    except Exception as exc:
        logging.getLogger(__name__).exception("Fallo descargando %s", name)
        send(status="error", error=str(exc)[:300], done=True)
    finally:
        _pulling = None


@app.get("/api/events")
async def api_events():
    q = bus.subscribe()

    async def gen():
        try:
            while True:
                try:
                    data = await asyncio.wait_for(q.get(), timeout=25)
                    yield {"event": "update", "data": data}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": "{}"}
        finally:
            bus.unsubscribe(q)

    return EventSourceResponse(gen())


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")


def main() -> None:
    import uvicorn
    uvicorn.run("kickclipper.server:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
