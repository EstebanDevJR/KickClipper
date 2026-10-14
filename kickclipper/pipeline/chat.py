"""Actividad del chat del VOD como señal de highlights.

El chat reacciona en 1-2 s a todo lo que merece la pena clipear, así que su
velocidad (mensajes/s) frente a una línea base móvil es de las mejores
señales para encontrar momentos. Además se cuentan los mensajes de risa/hype
(KEKW, jajaja, W, POG…).

Kick sirve la repetición del chat de un VOD en
``/api/v2/channels/<id>/messages?start_time=<ISO>``. No sabemos cuántos
mensajes devuelve cada petición, así que se estima la velocidad a partir de la
separación temporal de los mensajes recibidos (n / intervalo) y se interpola
entre muestras. Solo se pide el chat del VOD que se procesa, con un tope de
peticiones y una pausa entre ellas; si Kick responde con errores se abandona
sin afectar al resto del pipeline.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np

from ..config import settings
from . import signals

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]

_API = "https://kick.com/api"
_UUID_RE = re.compile(r"/videos/([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})", re.I)
# Emotes de Kick llegan como [emote:123:KEKW]; se miran nombre y texto libre
_HYPE_RE = re.compile(
    r"(kekw|omegalul|lulw?|icant|lmao|jaja|jeje|\bxd+\b|😂|🤣|💀|pog|\bw+\b|\bl+\b|🔥|"
    r"clip|no way|no puede ser|wtf|omg|letsgo|let'?s go|\bgg\b)",
    re.IGNORECASE,
)


def vod_uuid(url: str) -> str | None:
    m = _UUID_RE.search(url)
    return m.group(1) if m else None


def _session():
    # curl_cffi imita la huella TLS de un navegador; es lo mismo que usa yt-dlp
    # para leer la API de Kick al descargar el VOD.
    from curl_cffi import requests as creq

    return creq.Session(impersonate="chrome", timeout=10)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fetch_vod_info(session, uuid: str) -> dict | None:
    """Canal y hora de inicio del VOD (necesarios para pedir su chat)."""
    resp = session.get(f"{_API}/v1/video/{uuid}", headers={"Accept": "application/json"})
    if resp.status_code != 200:
        log.info("Kick devolvió %s al pedir el VOD %s", resp.status_code, uuid)
        return None
    data = resp.json()
    live = data.get("livestream") or {}
    channel_id = (live.get("channel") or {}).get("id") or live.get("channel_id")
    start = _parse_time(live.get("start_time")) or _parse_time(data.get("created_at"))
    if not channel_id or start is None:
        return None
    return {"channel_id": channel_id, "start": start,
            "duration": float(live.get("duration") or 0) / 1000.0}


def activity(
    url: str,
    duration: float,
    offset: float = 0.0,
    on_progress: ProgressFn | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict | None:
    """Curvas por segundo del chat del VOD: velocidad y proporción de hype.

    offset: segundo del VOD en el que empieza el tramo descargado (si el job
    procesa solo un rango). Devuelve None si no se pudo leer el chat.
    """
    uuid = vod_uuid(url)
    if not uuid or duration <= 0:
        return None
    try:
        session = _session()
        info = fetch_vod_info(session, uuid)
    except Exception as exc:  # red, Cloudflare, cambios de la API…
        log.info("Chat no disponible (%s)", exc)
        return None
    if info is None:
        return None

    n = int(np.ceil(duration))
    budget = max(10, settings.chat_max_requests)
    step = max(5.0, duration / budget)
    times = np.arange(0.0, duration, step)
    centers: list[float] = []
    rates: list[float] = []
    hype: list[float] = []
    fails = 0
    for k, t in enumerate(times):
        if cancelled and cancelled():
            return None
        at = info["start"] + timedelta(seconds=offset + float(t))
        iso = at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        try:
            resp = session.get(
                f"{_API}/v2/channels/{info['channel_id']}/messages",
                params={"start_time": iso}, headers={"Accept": "application/json"},
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            msgs = ((resp.json() or {}).get("data") or {}).get("messages") or []
            fails = 0
        except Exception as exc:
            fails += 1
            log.debug("Chat en %s falló: %s", iso, exc)
            if fails >= 3:
                log.info("Se abandona la lectura del chat tras %d fallos seguidos", fails)
                return None if not rates else _build(n, centers, rates, hype)
            continue

        stamps = []
        n_hype = 0
        for m in msgs:
            dt = _parse_time(m.get("created_at"))
            if dt is None:
                continue
            stamps.append((dt - info["start"]).total_seconds() - offset)
            if _HYPE_RE.search(str(m.get("content", ""))):
                n_hype += 1
        if len(stamps) >= 2:
            span = max(max(stamps) - min(stamps), 1.0)
            centers.append(float(np.mean(stamps)))
            rates.append(len(stamps) / span)
            hype.append(n_hype / len(stamps))
        else:
            # Ventana vacía o casi: chat parado alrededor de t
            centers.append(float(t))
            rates.append(len(stamps) / step)
            hype.append(0.0)
        if on_progress and k % 5 == 0:
            on_progress((k + 1) / len(times), f"Leyendo chat del VOD {k + 1}/{len(times)}")
        time.sleep(settings.chat_request_delay)

    if len(rates) < 3:
        return None
    return _build(n, centers, rates, hype)


def _build(n: int, centers: list[float], rates: list[float], hype: list[float]) -> dict:
    order = np.argsort(centers)
    c = np.asarray(centers)[order]
    grid = np.arange(n, dtype=np.float64)
    rate = np.interp(grid, c, np.asarray(rates)[order])
    hype_ratio = np.interp(grid, c, np.asarray(hype)[order])
    return {"rate": rate, "hype": hype_ratio}


def spike_curve(chat: dict | None, length: int, lag: float | None = None) -> np.ndarray | None:
    """Curva 0..1 de "el chat explotó", alineada con el momento que la causó.

    Se compara la velocidad con su línea base móvil (~5 min) y se adelanta
    `lag` segundos, porque el chat reacciona después de lo que pasa.
    """
    if not chat:
        return None
    lag = settings.chat_lag_seconds if lag is None else lag
    rate = np.log1p(np.asarray(chat["rate"], dtype=np.float64))
    spike = 0.75 * signals.contrast(rate, 301) + 0.25 * signals.robust_norm(chat["hype"])
    shift = int(round(lag))
    if shift > 0:
        spike = np.concatenate([spike[shift:], np.full(shift, spike[-1] if len(spike) else 0)])
    spike = signals.smooth(spike, 5)
    out = np.zeros(length)
    m = min(length, len(spike))
    out[:m] = spike[:m]
    return np.clip(out, 0.0, 1.0)
