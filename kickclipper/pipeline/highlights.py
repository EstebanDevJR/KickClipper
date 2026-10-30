"""Detección de momentos clipeables.

Estrategia (ver docs/GUIA_CLIPS_VIRALES.md §2-3):

1. CURVA DE MOMENTOS: excitación del audio (energía frente a su línea base,
   gritos, risas…) + picos de velocidad del chat (si se pudo leer).

2. CANDIDATAS:
   - Ventanas ancladas a picos, con la estructura que usan los clippers:
     preparación → pico → reacción, alineadas a frases.
   - Ventanas deslizantes alineadas a frases, para cubrir momentos de pura
     conversación (historias, opiniones) que no gritan.

3. HEURÍSTICA (barata, sobre todas): audio, chat, fuerza de los primeros
   segundos, densidad de habla, palabras de reacción, final en el remate,
   duración; penaliza silencios ("aire muerto").

4. LLM (Ollama, solo sobre las mejores N): puntúa potencial viral, gancho,
   si se entiende solo y el remate; elige en qué frase empezar y terminar,
   y escribe título, texto gancho para la pantalla, descripción, hashtags y
   palabras clave para los subtítulos.

5. AJUSTE FINAL: recorte de intros sin gancho y de colas largas, y
   supresión de solapamientos.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable

import httpx
import numpy as np

from ..config import settings
from . import signals
from .transcriber import Segment

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

# Señales léxicas de momento fuerte (es/en/pt; los streamers mezclan)
_REACTION_RE = re.compile(
    r"(jaja|jeje|kkkk|xd+|lmao|lol|no me jodas|no puede ser|qué (es esto|pasó|haces)|"
    r"increíble|insano|brutal|[eé]pico|hostia|wtf|oh my god|omg|dios mío|what the|"
    r"clip|clipealo|clipéalo|me muero|literal|na+h?\b|bro+\b|loco\b|"
    r"gan[éeoa]|perd[íio]|mat[éeoa]|ace\b|clutch|victoria|derrota|"
    r"no no no|vamos+|let'?s go|holy|nooo+|s[ií]{2,}|qu[eé]{2,}|madre m[ií]a|"
    r"imposible|locura|est[aá]s loco|meu deus|caralho|nossa)",
    re.IGNORECASE,
)
_EXCLAIM_RE = re.compile(r"[!?¡¿]")
_FILLER_RE = re.compile(
    r"^\W*(bueno|eh+|em+|este|o sea|a ver|ok(ay)?|vale|vamos a ver|chat|hola|"
    r"buenas|so\b|um+|uh+|well|like|alright|anyway|y nada|pues)\b",
    re.IGNORECASE,
)
_CATEGORIES = ["humor", "reaccion", "epico", "fail", "drama", "historia", "opinion",
               "rage", "wholesome", "otro"]


def detect(
    segments: list[Segment],
    excitement: np.ndarray,
    duration: float,
    on_progress: ProgressFn | None = None,
    formats: tuple[str, ...] | list[str] = ("short",),
    chat: np.ndarray | None = None,
    channel: str = "",
    llm_model: str | None = None,
) -> list[dict]:
    """Devuelve clips candidatos (con su formato) ordenados por puntuación final.

    Cada formato tiene su propio rango de duración: shorts de hasta 1 minuto
    (configurable) y videos de 5 a 10 minutos, así que se generan por separado.
    chat: curva 0..1 de picos del chat (chat.spike_curve) o None.
    llm_model: modelo de Ollama a usar (cualquiera instalado); None = el de la
    configuración, "none" = sin LLM, solo señales automáticas.
    """
    if not segments:
        return []
    ctx = _Context(segments, excitement, chat, duration)
    shortlist: list[dict] = []
    for fmt in formats:
        windows = _build_windows(segments, fmt)
        if fmt == "short":
            windows += _peak_windows(segments, ctx, fmt)
        if not windows:
            continue
        for w in windows:
            _heuristic(w, ctx)
        windows.sort(key=lambda w: w["heuristic"], reverse=True)
        cap = (settings.llm_max_candidates if fmt == "short"
               else max(4, settings.llm_max_candidates // 3))
        shortlist.extend(_dedupe(windows, overlap=0.5)[:cap])
    if not shortlist:
        return []

    llm = _Ollama(llm_model)
    if llm_model != NO_LLM and llm.available():
        log.info("Puntuando con el modelo de Ollama %s", llm.model)
        _llm_score(shortlist, ctx, llm, channel, on_progress)
    else:
        if llm_model != NO_LLM:
            log.warning("Ollama no disponible: se usan solo señales automáticas")
        for w in shortlist:
            _heuristic_metadata(w, ctx, channel)

    final: list[dict] = []
    for fmt in formats:
        group = [w for w in shortlist if w["format"] == fmt]
        if not group:
            continue
        for w in group:
            _refine(w, ctx)
            _heuristic(w, ctx)  # con los límites ya ajustados
        h = np.array([w["heuristic"] for w in group])
        h_norm = (h - h.min()) / (h.max() - h.min() + 1e-9)
        llm_vals = [w["llm_score"] for w in group if w.get("llm_ok")]
        for w, hn in zip(group, h_norm):
            if w.get("llm_ok"):
                w["final"] = 0.4 * float(hn) * 10 + 0.6 * w["llm_score"]
            elif llm_vals:
                # el LLM falló solo en esta: se usa su media para no descolocarla
                w["final"] = 0.4 * float(hn) * 10 + 0.6 * float(np.mean(llm_vals))
                w["llm_score"] = 0.0
            else:
                w["final"] = float(hn) * 10
                w["llm_score"] = 0.0
        group.sort(key=lambda w: w["final"], reverse=True)
        limit = settings.max_clips_per_job if fmt == "short" else settings.max_videos_per_job
        final.extend(_dedupe(group, overlap=0.3, key="final")[:limit])

    final.sort(key=lambda w: w["final"], reverse=True)
    return final


# ---------- contexto (curvas precalculadas) ----------

class _Context:
    def __init__(self, segments: list[Segment], excitement: np.ndarray,
                 chat: np.ndarray | None, duration: float):
        n = int(np.ceil(max(duration, segments[-1]["end"]))) + 1
        self.n = n
        self.segments = segments
        self.exc = _fit(excitement, n)
        self.chat = _fit(chat, n) if chat is not None and len(chat) else None
        self.moment = (0.65 * self.exc + 0.35 * self.chat) if self.chat is not None else self.exc
        self.words = np.zeros(n)
        for s in segments:
            for w in s["words"]:
                i = int(w["s"])
                if 0 <= i < n:
                    self.words[i] += 1
        self.seg_starts = np.array([s["start"] for s in segments])
        self.seg_ends = np.array([s["end"] for s in segments])
        smooth = signals.smooth(self.moment, 3)
        # Un pico debe sobresalir: por encima del percentil 75 y de la mediana + 0,1
        thr = max(float(np.percentile(smooth, 75)), float(np.median(smooth)) + 0.1) if n > 10 else 0.0
        self.peaks = sorted(signals.find_peaks(
            smooth, min_distance=20, threshold=thr,
            limit=max(8, settings.max_clips_per_job * 4),
        ))


def _fit(arr: np.ndarray | None, n: int) -> np.ndarray:
    out = np.zeros(n)
    if arr is None:
        return out
    m = min(n, len(arr))
    out[:m] = arr[:m]
    return out


# ---------- fase 1: candidatas ----------

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
        last_len = 0.0
        for k in range(i, len(segments)):
            end = segments[k]["end"]
            length = end - start
            if length > max_s:
                break
            if length < min_s or (last_len and length - last_len < step):
                continue
            windows.append(_window(segments, i, k, fmt))
            last_len = length
            if length >= stop:
                break
    return windows


def _peak_windows(segments: list[Segment], ctx: _Context, fmt: str) -> list[dict]:
    """Ventanas preparación → pico → reacción alrededor de cada pico."""
    min_s, max_s, _ = settings.clip_limits(fmt)
    windows = []
    for p in ctx.peaks:
        starts = np.where((ctx.seg_starts >= p - (max_s - 6)) & (ctx.seg_starts <= p - 1.5))[0]
        ends = np.where((ctx.seg_ends >= p + 2) & (ctx.seg_ends <= p + 15))[0]
        if not len(starts) or not len(ends):
            continue
        pick_s = {int(starts[np.argmin(np.abs(ctx.seg_starts[starts] - (p - d)))])
                  for d in (4, 9, 16, 26, 40)}
        pick_e = {int(ends[np.argmin(np.abs(ctx.seg_ends[ends] - (p + d)))]) for d in (3, 6, 11)}
        for i in sorted(pick_s):
            for k in sorted(pick_e):
                length = segments[k]["end"] - segments[i]["start"]
                if k >= i and min_s <= length <= max_s:
                    w = _window(segments, i, k, fmt)
                    w["peak"] = float(p)
                    windows.append(w)
    return windows


def _window(segments: list[Segment], i: int, k: int, fmt: str) -> dict:
    return {
        "start": segments[i]["start"], "end": segments[k]["end"],
        "i0": i, "i1": k, "segments": segments[i : k + 1], "format": fmt,
    }


# ---------- fase 2: heurística ----------

def _heuristic(w: dict, ctx: _Context) -> None:
    fmt = w["format"]
    min_s, max_s, target = settings.clip_limits(fmt)
    s, e = w["start"], w["end"]
    dur = max(e - s, 1.0)
    i0, i1 = int(s), max(int(np.ceil(e)), int(s) + 1)
    exc = ctx.exc[i0:i1]
    a_mean, a_max = (float(exc.mean()), float(exc.max())) if len(exc) else (0.0, 0.0)
    audio = 0.5 * a_mean + 0.5 * a_max if fmt == "short" else 0.75 * a_mean + 0.25 * a_max

    chat = None
    if ctx.chat is not None:
        c = ctx.chat[i0:i1]  # la curva ya viene adelantada (retraso del chat)
        chat = float(c.max()) if len(c) else 0.0
        if fmt == "video":
            chat = 0.6 * float(c.mean()) + 0.4 * chat

    segs = w["segments"]
    text = " ".join(x["text"] for x in segs)
    first = segs[0]["text"] if segs else ""
    hook_audio = float(ctx.exc[i0 : i0 + 3].mean()) if i0 < ctx.n else 0.0
    hook_lex = 1.0 if (_REACTION_RE.search(first) or _EXCLAIM_RE.search(first)) else 0.0
    filler = 1.0 if _FILLER_RE.match(first) else 0.0
    hook = max(0.0, 0.55 * hook_audio + 0.45 * hook_lex - 0.3 * filler)

    words = ctx.words[i0:i1]
    density = min(float(words.sum()) / dur / 3.0, 1.0)
    reactions = min(len(_REACTION_RE.findall(text)) / 3.0, 1.0)
    exclaims = min(len(_EXCLAIM_RE.findall(text)) / 5.0, 1.0)
    dead = float(((words == 0) & (exc < 0.3)).mean()) if len(exc) else 0.0
    tail = ctx.exc[max(i1 - 4, i0) : i1]
    ending = min(float(tail.max()) / (a_max + 1e-6), 1.0) if len(tail) else 0.0
    len_bonus = max(1.0 - abs(dur - target) / max_s, 0.0)
    peaks_in = [p for p in ctx.peaks if s + 0.15 * dur <= p <= e - 0.1 * dur]
    if fmt == "short":
        peak = 1.0 if peaks_in else 0.0
    else:
        peak = min(len(peaks_in) / max(dur / 90.0, 1.0), 1.0)

    if fmt == "short":
        weights = {"audio": .22, "chat": .16, "hook": .12, "density": .10, "reactions": .12,
                   "exclaims": .05, "ending": .08, "len": .07, "peak": .08}
        dead_w = 0.20
    else:
        weights = {"audio": .22, "chat": .15, "hook": .05, "density": .20, "reactions": .10,
                   "exclaims": .05, "ending": .05, "len": .10, "peak": .08}
        dead_w = 0.25
    vals = {"audio": audio, "chat": chat, "hook": hook, "density": density,
            "reactions": reactions, "exclaims": exclaims, "ending": ending,
            "len": len_bonus, "peak": peak}
    if chat is None:  # sin chat: su peso pasa al audio
        weights["audio"] += weights.pop("chat")
        vals.pop("chat")
    score = sum(weights[k] * vals[k] for k in weights) - dead_w * dead
    w["heuristic"] = float(score)
    w["signals"] = {k: round(float(v), 3) for k, v in vals.items()} | {"dead_air": round(dead, 3)}


def _dedupe(windows: list[dict], overlap: float, key: str = "heuristic") -> list[dict]:
    """Supresión de no-máximos: descarta ventanas muy solapadas con otra mejor."""
    kept: list[dict] = []
    for w in windows:
        ok = True
        for k in kept:
            if k["format"] != w["format"]:
                continue
            inter = min(w["end"], k["end"]) - max(w["start"], k["start"])
            shorter = min(w["end"] - w["start"], k["end"] - k["start"])
            if inter > 0 and inter / shorter > overlap:
                ok = False
                break
        if ok:
            kept.append(w)
    return kept


# ---------- fase 3: LLM ----------

_GUIDE = """Sabes cómo funcionan los clips de streamers en TikTok, YouTube Shorts y Reels:
- Los primeros 3 segundos deciden si la gente se queda: el clip debe abrir con la frase o reacción más fuerte, o con una tensión clara. Nada de saludos ni relleno al inicio.
- Debe entenderse sin haber visto el stream: contexto mínimo y una sola idea.
- Necesita un remate (la jugada, la frase, el grito, la reacción) y terminar justo después.
- Funcionan: humor, reacciones exageradas, frases citables, drama, momentos épicos o vergonzosos de juego, historias cortas con giro, opiniones polémicas.
- No funcionan: relleno, leer el chat sin gracia, conversaciones a medias, chistes internos sin contexto."""

_PROMPT_SHORT = """Eres editor experto de clips virales de streamers.
{guide}

Canal: {channel}
Señales automáticas del fragmento: {signals}

Transcripción con número de línea y segundo relativo. Las líneas [ANTES] y [DESPUÉS] están fuera del fragmento propuesto; úsalas solo si hacen falta para el contexto o el remate:
{lines}

Elige en qué línea debe EMPEZAR el clip (start_line) para tener el mejor gancho sin perder el contexto imprescindible, y en cuál debe TERMINAR (end_line) justo tras el remate o la reacción. La duración resultante debe quedar entre {min_s:.0f} y {max_s:.0f} segundos.

Título, texto gancho, descripción y hashtags en el MISMO idioma de la transcripción.

Responde SOLO con JSON:
{{"score": <0-10 potencial viral>, "hook": <0-10 fuerza de los 3 primeros segundos>, "standalone": <0-10 se entiende sin contexto>, "payoff": <0-10 calidad del remate>, "start_line": <int>, "end_line": <int>, "title": "<gancho, máx 60 caracteres>", "hook_text": "<3 a 6 palabras para la pantalla al inicio, distinto del título>", "description": "<1-2 frases con gancho, sin hashtags>", "hashtags": ["<3-5 sin #>"], "keywords": ["<hasta 5 palabras textuales de la transcripción para resaltar>"], "category": "<{categories}>", "reason": "<1 frase>"}}"""

_PROMPT_VIDEO = """Eres editor experto de videos de YouTube (5-10 minutos) hechos con streams.
Evalúa si este fragmento largo sostiene la atención varios minutos: historias o situaciones con desarrollo y remate, drama continuado, partidas épicas completas, debates con sustancia, humor sostenido. Penaliza relleno, silencios, temas inconexos y fragmentos sin principio ni final claros.

Canal: {channel}
Señales automáticas: {signals}

Transcripción:
\"\"\"{text}\"\"\"

Título, descripción y hashtags en el MISMO idioma de la transcripción.

Responde SOLO con JSON:
{{"score": <0-10>, "hook": <0-10 fuerza del primer minuto>, "standalone": <0-10>, "payoff": <0-10>, "title": "<gancho, máx 60 caracteres>", "hook_text": "<3 a 6 palabras>", "description": "<2-3 frases que vendan el video sin destripar el final, sin hashtags>", "hashtags": ["<3-5 sin #>"], "keywords": ["<hasta 5 palabras>"], "category": "<{categories}>", "reason": "<1 frase>"}}"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "number"}, "hook": {"type": "number"},
        "standalone": {"type": "number"}, "payoff": {"type": "number"},
        "start_line": {"type": "integer"}, "end_line": {"type": "integer"},
        "title": {"type": "string"}, "hook_text": {"type": "string"},
        "description": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string"}, "reason": {"type": "string"},
    },
    "required": ["score", "hook", "standalone", "payoff", "title", "hook_text",
                 "description", "hashtags", "reason"],
}


NO_LLM = "none"  # llm_model para desactivar el LLM en un trabajo


def _is_text_model(m: dict) -> bool:
    """Descarta los modelos de embeddings (no generan texto)."""
    details = m.get("details") or {}
    families = [details.get("family") or "", *(details.get("families") or [])]
    return "embed" not in m.get("name", "").lower() and not any("bert" in f.lower() for f in families)


def ollama_models(timeout: float = 3) -> list[str] | None:
    """Modelos de texto instalados en Ollama, o None si el servidor no responde."""
    try:
        r = httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", []) if m.get("name") and _is_text_model(m)]
    except Exception:
        return None


def pick_model(requested: str | None, installed: list[str]) -> str:
    """El modelo pedido si está instalado; si no, el de la configuración o el primero."""
    for want in (requested, settings.ollama_model):
        if not want or want == NO_LLM:
            continue
        if want in installed:
            return want
        if ":" not in want and f"{want}:latest" in installed:
            return f"{want}:latest"
    return installed[0] if installed else ""


class _Ollama:
    """Cliente mínimo con corte rápido: tras 2 fallos seguidos deja de intentarlo."""

    def __init__(self, model: str | None = None) -> None:
        self.fails = 0
        self.requested = model
        self.model = ""
        self.think = True  # manda think=False; se quita si el modelo no lo admite

    def available(self) -> bool:
        installed = ollama_models()
        if not installed:
            if installed is not None:
                log.warning("Ollama no tiene modelos de texto instalados (ollama pull <modelo>)")
            return False
        self.model = pick_model(self.requested, installed)
        if self.requested and self.requested != self.model:
            log.warning("El modelo %s no está instalado; se usa %s", self.requested, self.model)
        return True

    def warm_up(self, client: httpx.Client) -> None:
        """Carga el modelo en memoria (prompt vacío) antes de la primera consulta."""
        try:
            client.post(f"{settings.ollama_url}/api/generate",
                        json={"model": self.model, "keep_alive": "15m"}).raise_for_status()
        except Exception:
            log.warning("Ollama no pudo cargar %s", self.model)

    def ask(self, client: httpx.Client, prompt: str, num_ctx: int = 4096) -> dict:
        if self.fails >= 2:
            return {}
        body = {
            "model": self.model or settings.ollama_model,
            "prompt": prompt,
            "stream": False,
            "format": _SCHEMA,
            "options": {"temperature": 0.3, "num_predict": 450, "num_ctx": num_ctx},
            "keep_alive": "15m",
        }
        try:
            resp = client.post(f"{settings.ollama_url}/api/generate",
                               json={**body, "think": False} if self.think else body)
            if resp.status_code == 400 and self.think and "think" in resp.text.lower():
                # modelos sin modo de razonamiento rechazan el campo "think"
                self.think = False
                resp = client.post(f"{settings.ollama_url}/api/generate", json=body)
            resp.raise_for_status()
            data = json.loads(_strip_think(resp.json().get("response", "{}")))
            self.fails = 0
            return data if isinstance(data, dict) else {}
        except Exception:
            self.fails += 1
            log.exception("Fallo consultando Ollama; se usa solo heurística")
            return {}


def _strip_think(text: str) -> str:
    """Quita un bloque <think>…</think> que algunos modelos escriben igualmente."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else "{}"


def _llm_score(windows: list[dict], ctx: _Context, llm: _Ollama, channel: str,
               on_progress: ProgressFn | None) -> None:
    total = len(windows)
    with httpx.Client(timeout=settings.ollama_timeout) as client:
        if on_progress:
            on_progress(0.0, f"Cargando {llm.model} en Ollama (la primera vez puede tardar)")
        llm.warm_up(client)
        for n, w in enumerate(windows):
            is_video = w["format"] == "video"
            sig = _signals_text(w, ctx)
            if is_video:
                text = " ".join(s["text"] for s in w["segments"])[:7000]
                prompt = _PROMPT_VIDEO.format(text=text, signals=sig, channel=channel or "?",
                                              categories="|".join(_CATEGORIES))
                data = llm.ask(client, prompt, num_ctx=8192)
            else:
                lines, index = _numbered_lines(w, ctx)
                min_s, max_s, _ = settings.clip_limits("short")
                prompt = _PROMPT_SHORT.format(
                    guide=_GUIDE, lines=lines, signals=sig, channel=channel or "?",
                    min_s=min_s, max_s=max_s, categories="|".join(_CATEGORIES))
                data = llm.ask(client, prompt)
                w["_line_index"] = index
            _apply_llm(w, data, ctx, channel)
            if w.get("llm_ok"):
                w["llm_scores"]["model"] = llm.model
            if on_progress:
                on_progress((n + 1) / total, f"IA ({llm.model}) evaluando momento {n + 1}/{total}")


def _apply_llm(w: dict, data: dict, ctx: _Context, channel: str) -> None:
    if not data:
        _heuristic_metadata(w, ctx, channel)
        return

    def num(key: str) -> float:
        try:
            return float(np.clip(float(data.get(key, 0)), 0, 10))
        except (TypeError, ValueError):
            return 0.0

    score, hook, standalone, payoff = num("score"), num("hook"), num("standalone"), num("payoff")
    w["llm_ok"] = True
    w["llm_scores"] = {"score": score, "hook": hook, "standalone": standalone, "payoff": payoff}
    w["llm_score"] = 0.45 * score + 0.20 * hook + 0.15 * standalone + 0.20 * payoff
    w["title"] = _clean_text(data.get("title"), 80)
    w["hook_text"] = " ".join(_clean_text(data.get("hook_text"), 60).split()[:7])
    w["description"] = _clean_text(data.get("description"), 600)
    w["reason"] = _clean_text(data.get("reason"), 300)
    w["hashtags"] = [re.sub(r"\s+", "", str(h)).lstrip("#") for h in data.get("hashtags") or []
                     if str(h).strip()][:5]
    w["keywords"] = [str(k).strip() for k in data.get("keywords") or [] if str(k).strip()][:5]
    cat = str(data.get("category", "")).strip().lower()
    w["category"] = cat if cat in _CATEGORIES else "otro"
    if "start_line" in data and "end_line" in data and w.get("_line_index"):
        w["_lines"] = (data.get("start_line"), data.get("end_line"))


def _numbered_lines(w: dict, ctx: _Context) -> tuple[str, list[int]]:
    """Líneas numeradas (2 antes + fragmento + 2 después) y su índice de segmento."""
    segs = ctx.segments
    lo, hi = max(0, w["i0"] - 2), min(len(segs) - 1, w["i1"] + 2)
    out, index = [], []
    for n, i in enumerate(range(lo, hi + 1), start=1):
        tag = "[ANTES] " if i < w["i0"] else "[DESPUÉS] " if i > w["i1"] else ""
        rel = segs[i]["start"] - w["start"]
        out.append(f"{n}. {tag}(+{rel:.0f}s) {segs[i]['text']}")
        index.append(i)
    return "\n".join(out), index


def _signals_text(w: dict, ctx: _Context) -> str:
    sig = w.get("signals", {})
    parts = [f"audio {sig.get('audio', 0):.2f}"]
    if "chat" in sig:
        parts.append(f"chat {sig['chat']:.2f}")
    if w.get("peak") is not None:
        parts.append(f"pico de emoción en +{w['peak'] - w['start']:.0f}s")
    parts.append(f"silencios {sig.get('dead_air', 0):.0%}")
    return ", ".join(parts) + " (0 = nada, 1 = máximo)"


def _heuristic_metadata(w: dict, ctx: _Context, channel: str) -> None:
    """Título y texto gancho sin LLM: la frase más fuerte del clip."""
    best, best_score = None, -1.0
    for s in w["segments"]:
        sc = (len(_REACTION_RE.findall(s["text"])) + len(_EXCLAIM_RE.findall(s["text"])) * 0.5
              + float(ctx.exc[min(int(s["start"]), ctx.n - 1)]))
        if sc > best_score and len(s["text"]) > 6:
            best, best_score = s, sc
    text = (best or w["segments"][0])["text"].strip() if w["segments"] else ""
    w.setdefault("title", text[:60])
    w.setdefault("hook_text", " ".join(text.split()[:5]))
    w.setdefault("description", "")
    w.setdefault("reason", "Elegido por señales automáticas (audio, chat y transcripción)")
    tags = [channel] if channel else []
    w.setdefault("hashtags", tags + ["kick", "stream", "clips"])
    w.setdefault("keywords", [])
    w.setdefault("category", "otro")
    w["llm_ok"] = False


def _clean_text(value, limit: int) -> str:
    return " ".join(str(value or "").replace('"', "").split())[:limit]


# ---------- fase 4: ajuste fino ----------

def _refine(w: dict, ctx: _Context) -> None:
    """Aplica las líneas elegidas por el LLM, o recorta intro y cola."""
    min_s, max_s, _ = settings.clip_limits(w["format"])
    segs = ctx.segments
    moved = False
    if w.get("_lines") and w.get("_line_index"):
        index = w["_line_index"]
        try:
            a, b = (int(x) for x in w["_lines"])
        except (TypeError, ValueError):
            a, b = 0, 0
        if 1 <= a <= b <= len(index):
            i, k = index[a - 1], index[b - 1]
            length = segs[k]["end"] - segs[i]["start"]
            if min_s - 0.5 <= length <= max_s + 0.5:
                _set_bounds(w, segs, i, k)
                moved = True
    if w["format"] != "short":
        return
    if not moved:
        _tighten_start(w, ctx, min_s)
    _trim_tail(w, ctx, min_s)


def _set_bounds(w: dict, segs: list[Segment], i: int, k: int) -> None:
    w.update(start=segs[i]["start"], end=segs[k]["end"], i0=i, i1=k, segments=segs[i : k + 1])


def _tighten_start(w: dict, ctx: _Context, min_s: float) -> None:
    """Quita frases iniciales de relleno o sin energía (máx. 2 y 8 s)."""
    segs = ctx.segments
    i, k = w["i0"], w["i1"]
    dropped = 0
    while dropped < 2 and i < k:
        seg = segs[i]
        nxt = segs[i + 1]
        if w["end"] - nxt["start"] < min_s or nxt["start"] - w["start"] > 8.0:
            break
        if w.get("peak") is not None and nxt["start"] > w["peak"] - 2:
            break
        energy = float(ctx.exc[int(seg["start"]) : max(int(seg["end"]), int(seg["start"]) + 1)].mean())
        weak = (_FILLER_RE.match(seg["text"]) is not None
                or (energy < 0.35 and not _REACTION_RE.search(seg["text"])
                    and not _EXCLAIM_RE.search(seg["text"])))
        if not weak:
            break
        i += 1
        dropped += 1
    if i != w["i0"]:
        _set_bounds(w, segs, i, k)


def _trim_tail(w: dict, ctx: _Context, min_s: float) -> None:
    """Si el clip sigue mucho tras su pico, corta la cola floja."""
    peak = w.get("peak")
    if peak is None:
        return
    segs = ctx.segments
    i, k = w["i0"], w["i1"]
    while k > i and w["end"] - peak > 12:
        prev_end = segs[k - 1]["end"]
        if prev_end - w["start"] < min_s or prev_end < peak + 3:
            break
        last = segs[k]
        energy = float(ctx.exc[int(last["start"]) : max(int(last["end"]), int(last["start"]) + 1)].mean())
        if energy > 0.5 or _REACTION_RE.search(last["text"]):
            break
        k -= 1
        _set_bounds(w, segs, i, k)
