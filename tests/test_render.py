import subprocess

import pytest

from kickclipper import db
from kickclipper.pipeline import renderer, runner, timeline

from .conftest import make_video, media_info


def _words(n=12, step=0.3, start=0.2):
    return [{"w": f"palabra{i}", "s": start + i * step, "e": start + i * step + 0.25} for i in range(n)]


SPLIT_PLAN = {"layout": "split", "cam": [0.7, 0.6, 0.25, 0.35], "game": [0.24, 0.0, 0.52, 1.0],
              "cam_fraction": 0.38, "caption_y": 0.38}
SPEAKER_PLAN = {"layout": "speaker", "shots": [
    {"t0": 0.0, "t1": 1.5, "mode": "single", "spk": 0, "rect": [0.1, 0.0, 0.316, 1.0]},
    {"t0": 1.5, "t1": 2.5, "mode": "stack", "spk": None,
     "rects": [[0.05, 0.1, 0.4, 0.6], [0.55, 0.1, 0.4, 0.6]]},
    {"t0": 2.5, "t1": 4.0, "mode": "single", "spk": 1, "rect": [0.6, 0.0, 0.316, 1.0]},
]}
FOLLOW_PLAN = {"layout": "follow", "rect": [0.3, 0.0, 0.316, 1.0],
               "keys": [{"t": 0.0, "x": 0.2}, {"t": 2.0, "x": 0.5}, {"t": 4.0, "x": 0.4}]}


def test_graph_plain_blur():
    g = renderer.build_graph(fmt="short", layout="blur", plan={}, clip_len=5.0,
                             timeline=timeline.Timeline([(0.0, 5.0)]), ass_name="x.ass")
    assert "gblur" in g and "overlay" in g and "ass=x.ass:fontsdir=fonts" in g
    assert "loudnorm=I=-14" in g
    assert "concat" not in g


def test_graph_timeline_concat():
    tl = timeline.Timeline([(3.0, 4.0), (0.0, 2.0), (2.5, 5.0)])
    g = renderer.build_graph(fmt="short", layout="crop", plan={}, clip_len=5.0, timeline=tl,
                             ass_name=None)
    assert "split=3" in g and "asplit=3" in g and "concat=n=3:v=1:a=1" in g
    assert "trim=start=3.000:end=4.000" in g


def test_graph_speaker_expressions():
    g = renderer.build_graph(fmt="short", layout="speaker", plan=SPEAKER_PLAN, clip_len=4.0,
                             timeline=timeline.Timeline([(0.0, 4.0)]), ass_name=None)
    assert "if(lt(t," in g and "enable='between(t,1.500,2.500)'" in g and "vstack" in g


@pytest.mark.parametrize("layout,plan", [
    ("blur", {}), ("crop", {}), ("split", SPLIT_PLAN), ("speaker", SPEAKER_PLAN),
    ("follow", FOLLOW_PLAN),
])
def test_render_layouts(tmp_path, ffmpeg, layout, plan):
    src = make_video(tmp_path / "src.mp4", seconds=6)
    words = _words()
    tl = timeline.build(4.0, words, jumpcut=True, min_gap=0.2, pad=0.05)
    out = tmp_path / "clips" / "out.mp4"
    renderer.render_clip(src, 1.0, 5.0, out, layout=layout, plan=plan, timeline=tl,
                         words=tl.map_words(words), hook="esto es un gancho",
                         caption_style="viral", keywords=["palabra3"])
    info = media_info(out)
    assert info["size"] == (1080, 1920)
    assert info["audio"]
    assert abs(info["duration"] - tl.duration) < 0.25
    assert not (out.parent / "out.ass").exists()


def test_render_video_format(tmp_path, ffmpeg):
    src = make_video(tmp_path / "src.mp4", seconds=4)
    out = tmp_path / "clips" / "v.mp4"
    renderer.render_clip(src, 0.0, 3.0, out, fmt="video", words=_words(), caption_style="clean")
    assert media_info(out)["size"] == (1920, 1080)


def test_render_uses_bundled_font(tmp_path, ffmpeg):
    from kickclipper.pipeline import subtitles

    src = make_video(tmp_path / "src.mp4", seconds=3)
    work = tmp_path / "clips"
    work.mkdir()
    subtitles.build_ass(_words(), work / "f.ass", duration=3, style="viral", hook="gancho")
    renderer._ensure_fonts(work)
    # ffmpeg verboso para ver qué fuente elige libass
    proc = subprocess.run([ffmpeg, "-v", "verbose", "-y", "-i", str(src), "-t", "1",
                           "-vf", "ass=f.ass:fontsdir=fonts", "-f", "null", "-"],
                          cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert "Anton" in proc.stderr and "Poppins" in proc.stderr


def test_runner_render_clip_end_to_end(tmp_path, ffmpeg):
    job = db.create_job("https://kick.com/canalprueba/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb",
                        {"formats": ["short"]})
    wd = runner.job_dir(job["id"])
    wd.mkdir(parents=True, exist_ok=True)
    make_video(wd / "source.mp4", seconds=20)
    words = _words(n=40, step=0.4, start=1.0)
    clip = db.create_clip(job["id"], start=0.0, end=17.0, score=8, title="Un título de prueba",
                          transcript="texto", words=words, hashtags=["a"],
                          render_options={"format": "short"},
                          meta={"hook_text": "NO TE LO VAS A CREER", "keywords": ["palabra5"],
                                "peak": 12.0})
    updated = runner.render_clip(clip["id"], {"hook_preview": True})
    assert updated["status"] == "rendered"
    info = media_info(updated["file"])
    assert info["size"] == (1080, 1920)
    ro = updated["render_options"]
    assert ro["layout_used"] in ("blur", "split", "speaker", "follow")
    assert ro["_plan_range"] == [0.0, 17.0]
    assert updated["file"].endswith(".mp4")
    publish = open(updated["file"].replace(".mp4", ".txt"), encoding="utf-8").read()
    assert "Un título de prueba" in publish and "#a" in publish


def test_runner_manual_cam_rect(tmp_path, ffmpeg):
    job = db.create_job("https://kick.com/canalprueba/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb",
                        {"formats": ["short"]})
    wd = runner.job_dir(job["id"])
    wd.mkdir(parents=True, exist_ok=True)
    make_video(wd / "source.mp4", seconds=18)
    clip = db.create_clip(job["id"], start=0.0, end=16.0, score=5, title="Cámara a mano",
                          transcript="t", words=_words(n=30, step=0.5), render_options={"format": "short"})
    updated = runner.render_clip(clip["id"], {"cam_rect": [0.7, 0.6, 0.25, 0.35], "jumpcut": False})
    assert updated["render_options"]["layout_used"] == "split"
    assert media_info(updated["file"])["size"] == (1080, 1920)


def test_video_encoder_fallback(monkeypatch, ffmpeg):
    renderer._encoder_cache = None
    # sin GPU (o con un encoder que no funciona) se usa libx264
    monkeypatch.setattr(renderer, "_encoder_works", lambda enc: False)
    assert renderer.video_encoder() == "libx264"
    renderer._encoder_cache = None
    monkeypatch.setattr(renderer, "_encoder_works", lambda enc: enc == "h264_nvenc")
    monkeypatch.setattr(renderer.platform, "system", lambda: "Windows")
    assert renderer.video_encoder() == "h264_nvenc"
    assert "-cq" in renderer.encoder_args("h264_nvenc")
    renderer._encoder_cache = None
