"""Análisis de la señal de audio para detectar momentos de "hype".

Calcula, por segundo de stream:
- RMS (volumen): gritos, risas, reacciones fuertes.
- Flujo espectral (cambios bruscos del espectro): risas, golpes, sustos.
- Densidad de habla (se rellena luego con la transcripción).

Devuelve una curva de excitación normalizada 0..1 que la etapa de
highlights combina con la puntuación del LLM.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from ..config import settings

SR = 16000
HOP = SR  # resolución: 1 segundo


def load_pcm(wav: Path) -> np.ndarray:
    """Lee el WAV como float32 mono 16k usando ffmpeg (evita deps extra)."""
    cmd = [
        settings.ffmpeg_bin, "-v", "error", "-i", str(wav),
        "-f", "f32le", "-ac", "1", "-ar", str(SR), "-",
    ]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def excitement_curve(wav: Path) -> np.ndarray:
    """Curva 0..1 por segundo combinando energía y flujo espectral."""
    pcm = load_pcm(wav)
    n_sec = max(1, len(pcm) // HOP)
    pcm = pcm[: n_sec * HOP].reshape(n_sec, HOP)

    # RMS por segundo (en dB para comprimir el rango)
    rms = np.sqrt((pcm ** 2).mean(axis=1) + 1e-12)
    rms_db = 20 * np.log10(rms + 1e-12)

    # Flujo espectral: novedad del espectro entre segundos consecutivos
    spec = np.abs(np.fft.rfft(pcm * np.hanning(HOP), axis=1))
    spec /= spec.sum(axis=1, keepdims=True) + 1e-12
    flux = np.zeros(n_sec)
    flux[1:] = np.abs(np.diff(spec, axis=0)).sum(axis=1)

    score = 0.7 * _robust_norm(rms_db) + 0.3 * _robust_norm(flux)
    # Suavizado: una ventana de ±3s evita falsos picos de un solo frame
    kernel = np.hanning(7)
    kernel /= kernel.sum()
    return np.convolve(score, kernel, mode="same")


def _robust_norm(x: np.ndarray) -> np.ndarray:
    """Normaliza a 0..1 con percentiles (robusto frente a outliers)."""
    lo, hi = np.percentile(x, 10), np.percentile(x, 98)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)
