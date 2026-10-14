"""Job completo con descarga y transcripción simuladas (sin red ni modelos)."""
import shutil

from kickclipper import db
from kickclipper.pipeline import downloader, runner, transcriber

from .conftest import make_video, media_info


def _fake_segments(total=120.0):
    segs = []
    t = 0.0
    i = 0
    while t < total - 3:
        text = "¡NO PUEDE SER! jajaja qué locura" if 60 <= t < 63 else f"frase normal número {i} del stream"
        toks = text.split()
        words = [{"w": w, "s": t + j * 0.5, "e": t + j * 0.5 + 0.4} for j, w in enumerate(toks)]
        segs.append({"text": text, "start": t, "end": t + 3.0, "words": words})
        t += 3.0
        i += 1
    return segs


def test_full_job(tmp_path, ffmpeg, monkeypatch):
    src = make_video(tmp_path / "vod.mp4", seconds=120, size="640x360")

    def fake_download(url, dest_dir, on_progress=None, sections=None, **kw):
        dest_dir.mkdir(parents=True, exist_ok=True)
        out = dest_dir / "source.mp4"
        shutil.copy(src, out)
        if on_progress:
            on_progress(1.0, "ok")
        return out

    monkeypatch.setattr(downloader, "probe", lambda url: {
        "title": "Stream de prueba", "channel": "canal", "duration": 120.0,
        "thumbnail": "", "is_live": False})
    monkeypatch.setattr(downloader, "download", fake_download)
    monkeypatch.setattr(transcriber, "transcribe", lambda wav, on_progress=None: _fake_segments())

    job = db.create_job("https://kick.com/canal/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb",
                        {"formats": ["short"], "auto_render": 1})
    runner._run_job(job["id"])

    done = db.get_job(job["id"])
    assert done["status"] == "done", done.get("error")
    clips = db.list_clips(job["id"])
    assert clips
    assert all(15 <= c["end"] - c["start"] <= 60 for c in clips)
    assert all(c["meta"].get("hook_text") for c in clips)
    rendered = [c for c in clips if c["status"] == "rendered"]
    assert len(rendered) == 1
    assert media_info(rendered[0]["file"])["size"] == (1080, 1920)
    # cachés para reintentos baratos
    wd = runner.job_dir(job["id"])
    assert (wd / "transcript.json").exists() and (wd / "features.npz").exists()
