"""Utilidades de canal: URLs de Kick, formatos de clip y redes sociales.

KickClipper funciona con cualquier canal de kick.com. Úsalo únicamente con
contenido sobre el que tengas derechos o permiso del creador.
"""
from __future__ import annotations

import re

from .config import settings

FORMATS = ("short", "video")
FORMAT_LABEL = {"short": "shorts", "video": "videos"}


def channel_from_url(url: str) -> str | None:
    m = re.search(r"kick\.com/([A-Za-z0-9_.-]+)", url)
    return m.group(1).lower() if m else None


def validate_url(url: str) -> str:
    """Devuelve el slug del canal; lanza ValueError si no es una URL de Kick."""
    channel = channel_from_url(url)
    if channel is None:
        raise ValueError(
            "La URL debe ser de kick.com (https://kick.com/<canal>/videos/…)"
        )
    return channel


def validate_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise ValueError(f"Formato desconocido: {fmt} (usa: {', '.join(FORMATS)})")


def validate_formats(formats: list[str] | None) -> list[str]:
    """Normaliza la lista de formatos pedidos para un job (únicos, en orden)."""
    fmts = list(dict.fromkeys(f for f in (formats or []) if f))
    for f in fmts:
        validate_format(f)
    return fmts or ["short"]


def socials(channel: str) -> dict[str, str]:
    """Redes configuradas del canal; si no hay ninguna, su página de Kick."""
    links = {
        red: url
        for red, url in settings.streamer_socials.get(channel.lower(), {}).items()
        if url
    }
    return links or {"Kick": f"https://kick.com/{channel.lower()}"}
