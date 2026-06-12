"""Detección de momentos clipeables.

Estrategia en dos fases (optimizada para no saturar el LLM local):

1. HEURÍSTICA (barata, sobre todo el VOD):
   - Ventanas candidatas alineadas a frases de la transcripción.
   - Puntuación = excitación de audio + densidad de habla + señales léxicas
     (risas, exclamaciones, palabras de reacción).

2. LLM (cara, solo sobre las mejores N ventanas):
   - Ollama puntúa cada candidata 0-10 según potencial viral, y genera
     título corto + hashtags + razón.

La puntuación final combina ambas y se eliminan solapamientos.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

import httpx
import numpy as np

from ..config import settings
from .transcriber import Segment

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

# Señales léxicas de momento fuerte (es/en, streamers suelen mezclar)
_REACTION_RE = re.compile(
    r"(jaja|jeje|xd+|lmao|lol|no me jodas|no puede ser|qué (es esto|pasó|haces)|"
    r"increíble|insano|brutal|epico|épico|hostia|wtf|oh my god|omg|dios mío|"
    r"clip|clipealo|clipéalo|me muero|literal|na+h?\b|bro+\b|loco\b|"
    r"gan[éeoa]|perd[íio]|mat[éeoa]|ace\b|victoria|derrota)",
    re.IGNORECASE,
)
_EXCLAIM_RE = re.compile(r"[!?¡¿]")


def detect(
    segments: list[Segment],
    excitement: np.ndarray,
    duration: float,
    on_progress: ProgressFn | None = None,
    formats: tuple[str, ...] | list[str] = ("short",),
) -> list[dict]:
    """Devuelve clips candidatos (con su formato) ordenados por puntuación final.

    Cada formato tiene su propio rango de duración: shorts de hasta 1 minuto
    y videos de 5 a 10 minutos, así que se generan ventanas por separado.
    """
    shortlist: list[dict] = []
    for fmt in formats:
        windows = _build_windows(segments, fmt)
        if not windows:
            continue
        for w in windows:
            w["heuristic"] = _heuristic_score(w, excitement)
        windows.sort(key=lambda w: w["heuristic"], reverse=True)
        cap = (
            settings.llm_max_candidates if fmt == "short"
            else max(4, settings.llm_max_candidates // 3)
        )
        shortlist.extend(_dedupe(windows, overlap=0.5)[:cap])
    if not shortlist:
        return []

    scored = _llm_score(shortlist, on_progress)

    final: list[dict] = []
    for fmt in formats:
        group = [w for w in scored if w["format"] == fmt]
        if not group:
            continue
        h = np.array([w["heuristic"] for w in group])
        h_norm = (h - h.min()) / (h.max() - h.min() + 1e-9)
        for w, hn in zip(group, h_norm):
            w["final"] = 0.45 * float(hn) * 10 + 0.55 * w["llm_score"]
        group.sort(key=lambda w: w["final"], reverse=True)
        limit = settings.max_clips_per_job if fmt == "short" else settings.max_videos_per_job
        final.extend(_dedupe(group, overlap=0.3, key="final")[:limit])

    final.sort(key=lambda w: w["final"], reverse=True)
    return final


# ---------- Fase 1: ventanas y heurística ----------

def _build_windows(segments: list[Segment], fmt: str) -> list[dict]:
    """Ventanas deslizantes alineadas a límites de frase.

    Para videos (5-10 min) el muestreo es más grueso: sin un paso mínimo
    entre inicios/finales, un VOD de horas generaría cientos de miles de
    ventanas candidatas.
    """
    min_s, max_s, target = settings.clip_limits(fmt)
    step = 0.0 if fmt == "short" else 45.0
    stop = target * 1.3 if fmt == "short" else max_s
    windows: list[dict] = []

    last_start = -1e9
    for i, seg in enumerate(segments):
        start = seg["start"]
        if start - last_start < step:
            continue
        last_start = start
        j = i
        while j < len(segments) and segments[j]["end"] - start < max_s:
            j += 1
        # candidatas terminando en cada frase dentro del rango permitido
        last_len = 0.0
        for k in range(i, j):
            end = segments[k]["end"]
            length = end - start
            if length < min_s or (last_len and length - last_len < step):
                continue
            windows.append({
                "start": start,
                "end": end,
                "segments": segments[i : k + 1],
                "format": fmt,
                # preferencia suave por la duración objetivo
                "len_bonus": 1.0 - abs(length - target) / max_s,
            })
            last_len = length
            if length >= stop:
                break
    return windows


def _heuristic_score(w: dict, excitement: np.ndarray) -> float:
    i0, i1 = int(w["start"]), max(int(w["end"]), int(w["start"]) + 1)
    exc = excitement[min(i0, len(excitement) - 1) : min(i1, len(excitement))]
    audio = float(exc.mean() * 0.5 + exc.max() * 0.5) if len(exc) else 0.0

    text = " ".join(s["text"] for s in w["segments"])
    dur = w["end"] - w["start"]
    n_words = sum(len(s["words"]) for s in w["segments"])
    speech_density = min(n_words / max(dur, 1) / 3.5, 1.0)  # ~3.5 palabras/s = máximo
    reactions = min(len(_REACTION_RE.findall(text)) / 3.0, 1.0)
    exclaims = min(len(_EXCLAIM_RE.findall(text)) / 6.0, 1.0)

    return (
        0.40 * audio
        + 0.20 * speech_density
        + 0.20 * reactions
        + 0.10 * exclaims
        + 0.10 * max(w["len_bonus"], 0)
    )


def _dedupe(windows: list[dict], overlap: float, key: str = "heuristic") -> list[dict]:
    """Supresión de no-máximos: descarta ventanas muy solapadas con otra mejor."""
    kept: list[dict] = []
    for w in windows:
        ok = True
        for k in kept:
            inter = min(w["end"], k["end"]) - max(w["start"], k["start"])
            shorter = min(w["end"] - w["start"], k["end"] - k["start"])
            if inter > 0 and inter / shorter > overlap:
                ok = False
                break
        if ok:
            kept.append(w)
    return kept


# ---------- Fase 2: LLM ----------

_PROMPT = """Eres un editor experto de clips virales para TikTok/Shorts/Reels de streamers.

Te paso la transcripción de un fragmento de stream. Evalúa su potencial como clip viral.

Criterios: humor, drama, reacciones fuertes, frases citables, historias completas con remate, momentos de juego épicos, opiniones polémicas. Penaliza: relleno, conversación sin contexto, lectura de chat aburrida, frases cortadas.

El título, la descripción y los hashtags deben ir en el MISMO idioma de la transcripción.

Transcripción del fragmento:
\"\"\"{text}\"\"\"

Responde SOLO con JSON válido, sin markdown:
{{"score": <0-10>, "title": "<título gancho en el idioma de la transcripción, máx 60 caracteres, sin comillas internas>", "description": "<descripción para la publicación, 1-2 frases con gancho, sin hashtags>", "reason": "<por qué sí o no funciona, 1 frase>", "hashtags": ["<3-5 hashtags sin #>"]}}"""

_PROMPT_VIDEO = """Eres un editor experto de videos de YouTube (5-10 minutos) de streamers.

Te paso la transcripción de un fragmento largo de stream. Evalúa su potencial como video completo de YouTube: debe sostener la atención varios minutos.

Criterios: historias o situaciones con desarrollo y remate, drama continuado, partidas épicas completas, debates u opiniones con sustancia, humor sostenido. Penaliza: relleno, silencios, temas inconexos, lectura de chat aburrida, fragmentos sin principio ni final claros.

El título, la descripción y los hashtags deben ir en el MISMO idioma de la transcripción.

Transcripción del fragmento:
\"\"\"{text}\"\"\"

Responde SOLO con JSON válido, sin markdown:
{{"score": <0-10>, "title": "<título gancho en el idioma de la transcripción, máx 60 caracteres, sin comillas internas>", "description": "<descripción para YouTube, 2-3 frases que vendan el video sin spoilear el final, sin hashtags>", "reason": "<por qué sí o no funciona, 1 frase>", "hashtags": ["<3-5 hashtags sin #>"]}}"""


def _llm_score(windows: list[dict], on_progress: ProgressFn | None) -> list[dict]:
    total = len(windows)
    with httpx.Client(timeout=settings.ollama_timeout) as client:
        for n, w in enumerate(windows):
            is_video = w.get("format") == "video"
            text = " ".join(s["text"] for s in w["segments"])[: 6000 if is_video else 4000]
            prompt = _PROMPT_VIDEO if is_video else _PROMPT
            data = _ask_ollama(client, prompt.format(text=text))
            w["llm_score"] = float(np.clip(data.get("score", 0), 0, 10))
            w["title"] = str(data.get("title", ""))[:80]
            w["description"] = str(data.get("description", ""))[:600]
            w["reason"] = str(data.get("reason", ""))[:300]
            w["hashtags"] = [str(h).lstrip("#") for h in data.get("hashtags", [])][:5]
            if on_progress:
                on_progress((n + 1) / total, f"IA evaluando momento {n + 1}/{total}")
    return windows


def _ask_ollama(client: httpx.Client, prompt: str) -> dict:
    try:
        resp = client.post(
            f"{settings.ollama_url}/api/generate",
            json={
                "model": settings.ollama_model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "options": {"temperature": 0.3, "num_predict": 300},
                "think": False,
            },
        )
        resp.raise_for_status()
        return json.loads(resp.json().get("response", "{}"))
    except Exception:
        log.exception("Fallo consultando Ollama; se usa solo heurística")
        return {}
