import subprocess

import numpy as np

from kickclipper.pipeline import audio_analysis, signals


def test_contrast_detects_local_spike():
    x = np.concatenate([np.full(100, 1.0), [5.0], np.full(100, 1.0)])
    c = signals.contrast(x, 31)
    assert c.argmax() == 100
    assert c[100] > 0.9 and c[:90].max() < 0.1


def test_find_peaks_min_distance():
    x = np.zeros(100)
    x[[10, 12, 50, 90]] = [1.0, 0.9, 0.8, 0.7]
    assert signals.find_peaks(x, min_distance=5) == [10, 50, 90]


def test_rolling_median_long_window_subsampled():
    x = np.arange(10_000, dtype=float)
    med = signals.rolling_median(x, 301)
    assert abs(med[5000] - 5000) < 5


def test_analyze_finds_loud_bursts(tmp_path, ffmpeg):
    sr = 16000
    rng = np.random.default_rng(0)
    pcm = rng.normal(0, 0.01, sr * 120).astype(np.float32)  # 2 min de murmullo
    t = np.arange(sr * 3) / sr
    burst = (0.6 * np.sin(2 * np.pi * 900 * t) * (1 + np.sin(2 * np.pi * 5 * t))).astype(np.float32)
    pcm[sr * 60 : sr * 63] += burst  # "grito" en el segundo 60
    raw = tmp_path / "a.f32"
    raw.write_bytes(pcm.tobytes())
    wav = tmp_path / "a.wav"
    subprocess.run([ffmpeg, "-y", "-v", "error", "-f", "f32le", "-ar", str(sr), "-ac", "1",
                    "-i", str(raw), str(wav)], check=True)
    feats = audio_analysis.analyze(wav)
    assert len(feats.excitement) == 120
    assert 58 <= int(np.argmax(feats.excitement)) <= 64
    assert feats.excitement[60] > 3 * np.median(feats.excitement)
    # caché en disco
    feats.save(tmp_path / "f.npz")
    again = audio_analysis.AudioFeatures.load(tmp_path / "f.npz")
    assert np.allclose(again.excitement, feats.excitement)
    fine = audio_analysis.fine_energy(wav, 59.0, 64.0)
    assert len(fine) == 100 and fine.max() > fine.min() + 20
