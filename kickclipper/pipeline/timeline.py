"""Línea de tiempo de salida de un clip.

Un clip ya no es solo [inicio, fin] del VOD: puede llevar jump cuts (pausas
eliminadas) y un "cold open" (un adelanto del clímax al principio). La línea
de tiempo es la lista ordenada de tramos del original (en segundos relativos
al inicio del clip) que forman el vídeo final; todo lo que se dibuja encima
(subtítulos, encuadres por hablante) se traduce con ella a tiempo de salida.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

_MIN_PIECE = 0.12  # tramos más cortos que esto no merecen un corte


@dataclass
class Timeline:
    pieces: list[tuple[float, float]] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return sum(b - a for a, b in self.pieces)

    def offsets(self) -> list[float]:
        """Instante de salida en el que empieza cada tramo."""
        out, acc = [], 0.0
        for a, b in self.pieces:
            out.append(acc)
            acc += b - a
        return out

    def is_plain(self, clip_len: float) -> bool:
        return (len(self.pieces) == 1 and abs(self.pieces[0][0]) < 1e-3
                and abs(self.pieces[0][1] - clip_len) < 1e-3)

    def map_interval(self, a: float, b: float) -> list[tuple[float, float]]:
        """Intervalos de salida donde aparece el intervalo fuente [a, b]."""
        res = []
        for (p0, p1), off in zip(self.pieces, self.offsets()):
            lo, hi = max(a, p0), min(b, p1)
            if hi > lo:
                res.append((off + lo - p0, off + hi - p0))
        return res

    def map_time(self, t: float) -> list[float]:
        return [off + t - p0 for (p0, p1), off in zip(self.pieces, self.offsets())
                if p0 <= t < p1]

    def map_words(self, words: list[dict]) -> list[dict]:
        """Palabras {w,s,e} en tiempo fuente → tiempo de salida.

        Una palabra puede salir dos veces (en el cold open y en su sitio) y
        desaparece si cae casi entera en una pausa recortada.
        """
        out = []
        for (p0, p1), off in zip(self.pieces, self.offsets()):
            for w in words:
                lo, hi = max(w["s"], p0), min(w["e"], p1)
                dur = max(w["e"] - w["s"], 1e-3)
                if hi - lo < 0.5 * dur and not (p0 <= w["s"] < p1):
                    continue
                if hi <= lo:
                    continue
                out.append({**w, "s": off + lo - p0, "e": off + hi - p0})
        out.sort(key=lambda x: x["s"])
        return out

    def map_spans(self, spans: list[dict]) -> list[dict]:
        """Tramos {t0, t1, ...} (p.ej. planos por hablante) → tiempo de salida."""
        out = []
        for sp in spans:
            for o0, o1 in self.map_interval(sp["t0"], sp["t1"]):
                out.append({**sp, "t0": o0, "t1": o1})
        out.sort(key=lambda x: x["t0"])
        return out


def build(
    clip_len: float,
    words: list[dict] | None = None,
    energy: np.ndarray | None = None,
    energy_hop: float = 0.05,
    jumpcut: bool = False,
    min_gap: float = 0.7,
    pad: float = 0.18,
    preview: tuple[float, float] | None = None,
) -> Timeline:
    """Construye la línea de tiempo de un clip.

    words: palabras con tiempos relativos al inicio del clip.
    energy: energía en dB del clip con resolución `energy_hop` (opcional). Si
        se da, una pausa de voz solo se recorta si el audio también está en
        calma: en gameplay un silencio del streamer puede ser pura tensión.
    preview: tramo (relativo) que se repite al principio como cold open.
    """
    keep = [(0.0, clip_len)]
    if jumpcut and words:
        gaps = _quiet_gaps(clip_len, words, energy, energy_hop, min_gap)
        for g0, g1 in gaps:
            # al principio y al final del clip el silencio se quita entero
            a = 0.0 if g0 <= 1e-6 else g0 + pad
            b = clip_len if g1 >= clip_len - 1e-6 else g1 - pad
            keep = _subtract(keep, a, b)
        keep = _merge_small(keep)
        if sum(b - a for a, b in keep) < 0.35 * clip_len:
            keep = [(0.0, clip_len)]  # recortar tanto indica palabras mal alineadas
    pieces = list(keep)
    if preview:
        p0, p1 = max(0.0, preview[0]), min(clip_len, preview[1])
        if p1 - p0 >= 0.5:
            pieces.insert(0, (p0, p1))
    return Timeline(pieces)


def _quiet_gaps(clip_len: float, words: list[dict], energy: np.ndarray | None,
                hop: float, min_gap: float) -> list[tuple[float, float]]:
    ws = sorted(words, key=lambda w: w["s"])
    candidates = []
    if ws and ws[0]["s"] > min_gap:
        candidates.append((0.0, ws[0]["s"]))
    for prev, nxt in zip(ws, ws[1:]):
        if nxt["s"] - prev["e"] > min_gap:
            candidates.append((prev["e"], nxt["s"]))
    if ws and clip_len - ws[-1]["e"] > min_gap:
        candidates.append((ws[-1]["e"], clip_len))
    if energy is None or len(energy) == 0:
        return candidates

    # Nivel de referencia: la energía mientras se habla
    speech = np.zeros(len(energy), dtype=bool)
    for w in ws:
        speech[int(w["s"] / hop) : int(np.ceil(w["e"] / hop))] = True
    ref = float(np.median(energy[speech])) if speech.any() else float(np.median(energy))
    quiet = []
    for g0, g1 in candidates:
        seg = energy[int(g0 / hop) : int(np.ceil(g1 / hop))]
        # Acción/risas/gritos en la pausa: no se toca
        if len(seg) and (seg > ref - 6.0).mean() > 0.25:
            continue
        quiet.append((g0, g1))
    return quiet


def _subtract(keep: list[tuple[float, float]], a: float, b: float) -> list[tuple[float, float]]:
    if b - a <= 0:
        return keep
    out = []
    for k0, k1 in keep:
        if b <= k0 or a >= k1:
            out.append((k0, k1))
            continue
        if a > k0:
            out.append((k0, a))
        if b < k1:
            out.append((b, k1))
    return out


def _merge_small(keep: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return [(a, b) for a, b in keep if b - a >= _MIN_PIECE]
