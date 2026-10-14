"""Análisis de la señal de audio para detectar momentos de "hype".

Calcula, por segundo de stream:
- RMS (volumen): gritos, risas, reacciones fuertes.
- Contraste local: cuánto sobresale el volumen de su entorno (los últimos
  ~minutos), porque lo que importa es el cambio, no el volumen absoluto.
- Flujo espectral (cambios bruscos del espectro): golpes, sustos, explosiones.
- Brillo (energía en 1,5-5 kHz frente a la de la voz grave): gritos y voz
  excitada son más "brillantes".
- Ritmo de risa: modulación regular de la energía a 3,5-7 Hz ("ja-ja-ja").

El audio se lee en bloques desde ffmpeg, así un VOD de varias horas no
necesita cargarse entero en memoria. Devuelve una curva de excitación 0..1
que la etapa de highlights combina con el chat, la transcripción y el LLM.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import settings
from . import signals

log = logging.getLogger(__name__)

SR = 16000
HOP = SR  # resolución: 1 segundo
_ENV_HOP = SR // 100  # envolvente a 100 Hz para el ritmo de risa
_BLOCK_SECONDS = 120


@dataclass
class AudioFeatures:
    rms_db: np.ndarray
    flux: np.ndarray
    brightness: np.ndarray
    laughter: np.ndarray
    excitement: np.ndarray

    def save(self, path: Path) -> None:
        np.savez_compressed(path, **self.__dict__)

    @classmethod
    def load(cls, path: Path) -> "AudioFeatures":
        data = np.load(path)
        return cls(**{k: data[k] for k in cls.__dataclass_fields__})


def _iter_blocks(wav: Path, start: float | None = None, duration: float | None = None):
    """Bloques float32 mono 16 kHz leídos de ffmpeg (sin cargar todo el VOD)."""
    cmd = [settings.ffmpeg_bin, "-v", "error"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-i", str(wav), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    block_bytes = SR * _BLOCK_SECONDS * 4
    try:
        while True:
            raw = proc.stdout.read(block_bytes)
            if not raw:
                break
            yield np.frombuffer(raw[: len(raw) // 4 * 4], dtype=np.float32)
    finally:
        proc.stdout.close()
        proc.wait()
        if proc.returncode not in (0, None):
            err = proc.stderr.read().decode(errors="ignore") if proc.stderr else ""
            raise RuntimeError(f"ffmpeg no pudo leer el audio: {err[-500:]}")


def load_pcm(wav: Path, start: float | None = None, duration: float | None = None) -> np.ndarray:
    """Lee (un tramo de) el audio como float32 mono 16 kHz."""
    blocks = list(_iter_blocks(wav, start, duration))
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def analyze(wav: Path) -> AudioFeatures:
    """Extrae las curvas por segundo de todo el audio, en streaming."""
    freqs = np.fft.rfftfreq(HOP, 1 / SR)
    band_hi = (freqs >= 1500) & (freqs < 5000)
    band_lo = (freqs >= 100) & (freqs < 1000)
    env_freqs = np.fft.rfftfreq(100, 1 / 100)
    band_laugh = (env_freqs >= 3.5) & (env_freqs <= 7.0)
    band_mod = env_freqs >= 1.0
    window = np.hanning(HOP).astype(np.float32)

    rms_db, flux, bright, laugh = [], [], [], []
    prev_spec: np.ndarray | None = None
    carry = np.zeros(0, dtype=np.float32)
    for block in _iter_blocks(wav):
        data = np.concatenate([carry, block])
        n_sec = len(data) // HOP
        carry = data[n_sec * HOP :]
        if n_sec == 0:
            continue
        frames = data[: n_sec * HOP].reshape(n_sec, HOP)

        rms = np.sqrt((frames.astype(np.float64) ** 2).mean(axis=1) + 1e-12)
        rms_db.append(20 * np.log10(rms + 1e-12))

        spec = np.abs(np.fft.rfft(frames * window, axis=1))
        energy = spec ** 2
        bright.append(np.log10(
            (energy[:, band_hi].sum(axis=1) + 1e-9) / (energy[:, band_lo].sum(axis=1) + 1e-9)
        ))
        norm_spec = spec / (spec.sum(axis=1, keepdims=True) + 1e-12)
        f = np.zeros(n_sec)
        if prev_spec is not None:
            f[0] = np.abs(norm_spec[0] - prev_spec).sum()
        if n_sec > 1:
            f[1:] = np.abs(np.diff(norm_spec, axis=0)).sum(axis=1)
        flux.append(f)
        prev_spec = norm_spec[-1]

        # Envolvente a 100 Hz y su espectro de modulación (ritmo de 3,5-7 Hz)
        env = np.sqrt((frames.reshape(n_sec, 100, _ENV_HOP) ** 2).mean(axis=2) + 1e-12)
        env = env - env.mean(axis=1, keepdims=True)
        mod = np.abs(np.fft.rfft(env * np.hanning(100), axis=1)) ** 2
        laugh.append(mod[:, band_laugh].sum(axis=1) / (mod[:, band_mod].sum(axis=1) + 1e-12))

    if not rms_db:
        empty = np.zeros(1)
        return AudioFeatures(empty, empty, empty, empty, empty)

    rms_db_a = np.concatenate(rms_db)
    flux_a = np.concatenate(flux)
    bright_a = np.concatenate(bright)
    laugh_a = np.concatenate(laugh)
    return AudioFeatures(
        rms_db=rms_db_a, flux=flux_a, brightness=bright_a, laughter=laugh_a,
        excitement=combine(rms_db_a, flux_a, bright_a, laugh_a),
    )


def combine(rms_db: np.ndarray, flux: np.ndarray, bright: np.ndarray,
            laugh: np.ndarray) -> np.ndarray:
    """Curva de excitación 0..1 a partir de las características por segundo."""
    loud = signals.robust_norm(rms_db)
    # Contraste frente al último minuto y frente a los últimos ~5 min
    local = 0.5 * signals.contrast(rms_db, 61) + 0.5 * signals.contrast(rms_db, 301)
    # El brillo y el ritmo solo cuentan cuando hay sonido de verdad
    voiced = loud > 0.25
    score = (
        0.34 * loud
        + 0.26 * local
        + 0.18 * signals.robust_norm(flux)
        + 0.12 * signals.robust_norm(bright) * voiced
        + 0.10 * signals.robust_norm(laugh) * voiced
    )
    # Suavizado ±2 s: evita falsos picos de un solo segundo
    return np.clip(signals.smooth(score, 5), 0.0, 1.0)


def excitement_curve(wav: Path) -> np.ndarray:
    """Compatibilidad: solo la curva de excitación combinada."""
    return analyze(wav).excitement


def fine_energy(wav: Path, start: float, end: float, hop: float = 0.05) -> np.ndarray:
    """Energía en dB con resolución fina (por defecto 50 ms) de un tramo.

    Se usa para decidir qué pausas se pueden recortar sin perder acción.
    """
    pcm = load_pcm(wav, start, max(end - start, 0.0))
    step = max(1, int(SR * hop))
    n = len(pcm) // step
    if n == 0:
        return np.zeros(0)
    frames = pcm[: n * step].reshape(n, step).astype(np.float64)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1) + 1e-12) + 1e-12)
