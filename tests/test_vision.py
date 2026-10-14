import urllib.request
from pathlib import Path

import numpy as np
import pytest

from kickclipper.pipeline import vision

from .conftest import make_video


def _track(tid, box, n=60, act=None, fps=6.0):
    t = [round(i / fps, 3) for i in range(n)]
    return {"id": tid, "t": t, "box": [list(box)] * n,
            "act": list(act) if act is not None else [0.1] * n}


def _analysis(tracks, n=60, fps=6.0):
    return {"src": [1920, 1080], "fps": fps, "frames": n, "duration": n / fps, "tracks": tracks}


def test_plan_split_for_small_corner_facecam():
    a = _analysis([_track(0, [0.82, 0.70, 0.06, 0.11])])
    p = vision.plan(a)
    assert p["layout"] == "split"
    x, y, w, h = p["cam"]
    assert x <= 0.82 and x + w >= 0.88 and 0 <= y and y + h <= 1.0001
    # el recorte de la cámara respeta la relación de aspecto del panel
    cam_px = (w * 1920) / (h * 1080)
    assert abs(cam_px - 1080 / round(1920 * vision.CAM_FRACTION)) < 0.05
    assert p["game"][3] == 1.0


def test_plan_blur_without_faces():
    assert vision.plan(_analysis([]))["layout"] == "blur"
    assert vision.plan(None)["layout"] == "blur"


def test_plan_follow_single_big_face():
    p = vision.plan(_analysis([_track(0, [0.40, 0.20, 0.15, 0.27])]))
    assert p["layout"] == "follow"
    x, y, w, h = p["rect"]
    assert abs((w * 1920) / (h * 1080) - 9 / 16) < 0.01


def test_plan_speaker_switches_on_mouth_activity():
    fps, n = 6.0, 120  # 20 s
    act_a = np.full(n, 0.05)
    act_b = np.full(n, 0.05)
    rng = np.random.default_rng(1)
    act_a[:60] = 0.5 + 0.2 * rng.random(60)   # A habla los primeros 10 s
    act_b[60:] = 0.5 + 0.2 * rng.random(60)   # B los últimos 10 s
    a = _analysis([_track(0, [0.15, 0.25, 0.12, 0.22], n, act_a),
                   _track(1, [0.70, 0.25, 0.12, 0.22], n, act_b)], n, fps)
    words = [{"w": "x", "s": t, "e": t + 0.3} for t in np.arange(0, 20, 0.4)]
    p = vision.plan(a, words, 20.0)
    assert p["layout"] == "speaker"
    singles = [s for s in p["shots"] if s["mode"] == "single"]
    assert [s["spk"] for s in singles] == [0, 1]
    switch = singles[1]["t0"]
    assert 9.5 <= switch <= 11.5
    assert all(s["t1"] - s["t0"] >= vision.MIN_SHOT - 1e-6 for s in p["shots"])
    # mismo tamaño de recorte en todos los planos
    assert len({tuple(s["rect"][2:]) for s in singles}) == 1
    assert vision.speaker_at(p, 2.0) == 0 and vision.speaker_at(p, 18.0) == 1


def test_plan_speaker_crosstalk_stack():
    fps, n = 6.0, 120
    act = np.full(n, 0.05)
    act[30:90] = 0.6
    a = _analysis([_track(0, [0.15, 0.25, 0.12, 0.22], n, act + 0.01),
                   _track(1, [0.70, 0.25, 0.12, 0.22], n, act)], n, fps)
    words = [{"w": "x", "s": t, "e": t + 0.3} for t in np.arange(0, 20, 0.4)]
    p = vision.plan(a, words, 20.0)
    assert any(s["mode"] == "stack" for s in p["shots"])


@pytest.fixture(scope="module")
def face_image(tmp_path_factory):
    path = tmp_path_factory.mktemp("img") / "portrait.jpg"
    try:
        urllib.request.urlretrieve("https://storage.googleapis.com/mediapipe-assets/portrait.jpg", path)
    except Exception:
        pytest.skip("sin red para descargar la imagen de prueba")
    return path


def test_real_facecam_detection(tmp_path, ffmpeg, face_image):
    if not vision.available() or vision._model_path() is None:
        pytest.skip("OpenCV o el modelo YuNet no disponibles")
    video = make_video(tmp_path / "cam.mp4", seconds=3, size="1920x1080",
                       overlay=face_image, overlay_w=300)
    analysis = vision.analyze(video, 0.0, 3.0)
    assert analysis and analysis["tracks"]
    p = vision.plan(analysis)
    assert p["layout"] == "split"
    x, y, w, h = p["cam"]
    assert x + w > 0.8 and y + h > 0.7  # esquina inferior derecha


def test_split_from_manual_rect_keeps_panel_aspect():
    p = vision.split_from_rect([0.05, 0.6, 0.2, 0.3], 1920, 1080)
    assert p["layout"] == "split" and p["manual"]
    x, y, w, h = p["cam"]
    panel = 1080 / round(1920 * vision.CAM_FRACTION)
    assert abs((w * 1920) / (h * 1080) - panel) < 0.02
    # contiene lo que marcó el usuario
    assert x <= 0.05 + 1e-6 and x + w >= 0.25 - 1e-6 and 0 <= y and y + h <= 1.0001
