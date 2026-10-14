"""Transcripción local con timestamps por palabra.

Dos motores:
- "mlx": mlx-whisper, usa la GPU de Apple Silicon (muy rápido en M1+).
- "faster-whisper": CTranslate2 en CPU, portable a Linux/Windows.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import Callable

from ..config import settings

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

# Cada palabra: {"w": texto, "s": inicio, "e": fin}
Word = dict
# Cada segmento: {"text", "start", "end", "words": [Word]}
Segment = dict

_MLX_REPOS = {
    "large-v3-turbo": "mlx-community/whisper-large-v3-turbo",
    "large-v3": "mlx-community/whisper-large-v3-mlx",
    "medium": "mlx-community/whisper-medium-mlx",
    "small": "mlx-community/whisper-small-mlx",
    "base": "mlx-community/whisper-base-mlx",
    "tiny": "mlx-community/whisper-tiny",
}


# Frases que Whisper "alucina" sobre música o silencio (vienen de los
# subtítulos con los que se entrenó): no son habla real del streamer.
_HALLUCINATIONS = {
    "subtitulos realizados por la comunidad de amaraorg",
    "subtitulos por la comunidad de amaraorg",
    "subtitulado por la comunidad de amaraorg",
    "gracias por ver el video", "gracias por ver", "suscribete",
    "thanks for watching", "thank you for watching", "thank you",
    "subtitles by the amaraorg community", "musica", "music", "applause",
    "please subscribe", "like and subscribe",
}


def transcribe(audio: Path, on_progress: ProgressFn | None = None) -> list[Segment]:
    engine = settings.whisper_engine
    if engine == "mlx":
        try:
            return clean_segments(_transcribe_mlx(audio, on_progress))
        except Exception:
            log.exception("mlx-whisper falló; usando faster-whisper")
    return clean_segments(_transcribe_faster(audio, on_progress))


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", "", text).strip()


def clean_segments(segments: list[Segment]) -> list[Segment]:
    """Quita alucinaciones conocidas y repeticiones en bucle de Whisper."""
    out: list[Segment] = []
    repeat = 0
    for seg in segments:
        norm = _norm(seg["text"])
        if not norm or norm in _HALLUCINATIONS:
            continue
        if out and _norm(out[-1]["text"]) == norm:
            repeat += 1
            if repeat >= 2:  # la misma frase 3+ veces seguidas: bucle del modelo
                continue
        else:
            repeat = 0
        out.append(seg)
    return out


def _transcribe_mlx(audio: Path, on_progress: ProgressFn | None) -> list[Segment]:
    import mlx_whisper

    repo = _MLX_REPOS.get(settings.whisper_model, settings.whisper_model)
    if on_progress:
        on_progress(0.05, f"Transcribiendo (MLX {settings.whisper_model})")
    kwargs = dict(
        path_or_hf_repo=repo,
        word_timestamps=True,
        language=settings.whisper_language,
        condition_on_previous_text=False,
        verbose=None,
    )
    try:
        # Salta silencios largos donde Whisper suele inventar texto
        result = mlx_whisper.transcribe(str(audio), hallucination_silence_threshold=2.0, **kwargs)
    except TypeError:
        result = mlx_whisper.transcribe(str(audio), **kwargs)
    segments = []
    for seg in result.get("segments", []):
        words = [
            {"w": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"])}
            for w in seg.get("words", [])
            if w.get("word", "").strip()
        ]
        if not words:
            continue
        segments.append({
            "text": seg["text"].strip(),
            "start": float(seg["start"]),
            "end": float(seg["end"]),
            "words": words,
        })
    if on_progress:
        on_progress(1.0, "Transcripción completa")
    return segments


def _transcribe_faster(audio: Path, on_progress: ProgressFn | None) -> list[Segment]:
    from faster_whisper import WhisperModel

    if on_progress:
        on_progress(0.05, f"Transcribiendo (faster-whisper {settings.faster_whisper_model})")
    try:
        # GPU (CUDA) si la hay; si no, CPU con el tipo de cálculo más rápido
        model = WhisperModel(settings.faster_whisper_model, device="auto", compute_type="auto")
    except Exception:
        model = WhisperModel(settings.faster_whisper_model, device="cpu", compute_type="int8")
    seg_iter, info = model.transcribe(
        str(audio),
        word_timestamps=True,
        language=settings.whisper_language,
        vad_filter=True,
        condition_on_previous_text=False,
    )
    total = info.duration or 1.0
    segments: list[Segment] = []
    for seg in seg_iter:
        words = [
            {"w": w.word.strip(), "s": float(w.start), "e": float(w.end)}
            for w in (seg.words or [])
            if w.word.strip()
        ]
        if not words:
            continue
        segments.append({
            "text": seg.text.strip(),
            "start": float(seg.start),
            "end": float(seg.end),
            "words": words,
        })
        if on_progress:
            on_progress(min(seg.end / total, 0.999), "Transcribiendo")
    if on_progress:
        on_progress(1.0, "Transcripción completa")
    return segments
