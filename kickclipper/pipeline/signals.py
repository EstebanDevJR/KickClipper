"""Utilidades numéricas para curvas por segundo (audio, chat, excitación).

Todas trabajan sobre arrays 1-D donde el índice es el segundo del VOD.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def robust_norm(x: np.ndarray, lo_pct: float = 10, hi_pct: float = 98) -> np.ndarray:
    """Normaliza a 0..1 con percentiles (robusto frente a outliers)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    lo, hi = np.percentile(x, lo_pct), np.percentile(x, hi_pct)
    if hi - lo < 1e-9:
        return np.zeros_like(x)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Mediana centrada de ancho `win` (bordes replicados)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0 or win <= 1:
        return x.copy()
    win = min(win, x.size)
    if win % 2 == 0:
        win -= 1
    if win <= 1:
        return x.copy()
    pad = win // 2
    padded = np.pad(x, pad, mode="edge")
    # Submuestreo de la ventana para VODs largos: la mediana de 1 de cada k
    # muestras es indistinguible y evita matrices enormes.
    k = max(1, win // 61)
    view = sliding_window_view(padded, win)[:, ::k]
    return np.median(view, axis=1)


def smooth(x: np.ndarray, width: int) -> np.ndarray:
    """Suavizado con ventana de Hann normalizada."""
    x = np.asarray(x, dtype=np.float64)
    if width <= 1 or x.size < 3:
        return x.copy()
    kernel = np.hanning(width + 2)[1:-1]
    kernel /= kernel.sum()
    return np.convolve(np.pad(x, width, mode="edge"), kernel, mode="same")[width:-width]


def contrast(x: np.ndarray, win: int = 61) -> np.ndarray:
    """Cuánto sobresale cada punto de su entorno (línea base local), 0..1.

    Un grito en un stream tranquilo puntúa alto aunque haya partes más ruidosas
    en otro momento del VOD: así se compara cada momento con su contexto.
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return x
    base = rolling_median(x, win)
    dev = np.abs(x - base)
    spread = rolling_median(dev, win) * 1.4826 + 1e-6  # MAD → desviación típica
    z = (x - base) / spread
    return np.clip(z / 4.0, 0.0, 1.0)


def find_peaks(x: np.ndarray, min_distance: int, threshold: float | None = None,
               limit: int | None = None) -> list[int]:
    """Máximos ordenados por valor, separados al menos `min_distance`.

    Solo cuentan los puntos estrictamente por encima de `threshold` (por
    defecto, el mínimo de la curva: una curva plana no tiene picos).
    """
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return []
    if threshold is None:
        threshold = float(x.min())
    order = np.argsort(x, kind="stable")[::-1]
    taken = np.zeros(x.size, dtype=bool)
    peaks: list[int] = []
    for i in order:
        if x[i] <= threshold:
            break
        if taken[i]:
            continue
        peaks.append(int(i))
        taken[max(0, i - min_distance) : i + min_distance + 1] = True
        if limit and len(peaks) >= limit:
            break
    return peaks


def window_stats(curve: np.ndarray, start: float, end: float) -> tuple[float, float]:
    """(media, máximo) de una curva por segundo dentro de [start, end)."""
    if curve is None or len(curve) == 0:
        return 0.0, 0.0
    i0 = int(max(0, min(start, len(curve) - 1)))
    i1 = int(max(i0 + 1, min(np.ceil(end), len(curve))))
    seg = curve[i0:i1]
    if len(seg) == 0:
        return 0.0, 0.0
    return float(seg.mean()), float(seg.max())
