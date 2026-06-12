"""Servidor web: API REST + SSE + frontend estático."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from . import db, streamers
from .config import settings
from .events import bus
from .pipeline import runner

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

app = FastAPI(title="KickClipper")
WEB_DIR = Path(__file__).parent / "web"


@app.on_event("startup")
async def _startup() -> None:
    bus.attach_loop(asyncio.get_running_loop())
    db.get_conn()


# ---------- Modelos ----------

class NewJob(BaseModel):
    url: str
    sections: str | None = None     # "HH:MM:SS-HH:MM:SS" opcional
    auto_render: int = 3
    formats: list[str] | None = None  # ["short"], ["video"] o ambos


class RenderRequest(BaseModel):
    start: float | None = None
    end: float | None = None
    title: str | None = None
    layout: str | None = None        # "blur" | "crop"
    format: str | None = None        # "short" (9:16) | "video" (16:9)
    subtitles: bool | None = None
    show_title: bool | None = None


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
    import httpx

    ffmpeg_ok = (
        Path(settings.ffmpeg_bin).exists() if "/" in settings.ffmpeg_bin
        else sh.which(settings.ffmpeg_bin) is not None
    )
    ollama_ok, models = False, []
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            ollama_ok = r.status_code == 200
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        pass
    return {
        "ffmpeg": ffmpeg_ok,
        "ollama": ollama_ok,
        "ollama_models": models,
        "ollama_model": settings.ollama_model,
        "whisper_engine": settings.whisper_engine,
        "whisper_model": settings.whisper_model
        if settings.whisper_engine == "mlx" else settings.faster_whisper_model,
        "formats": list(streamers.FORMATS),
        "streamer_socials": {ch: streamers.socials(ch) for ch in settings.streamer_socials},
        "output_dir": str(settings.output_dir),
    }


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
