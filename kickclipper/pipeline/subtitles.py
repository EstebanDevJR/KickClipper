"""Subtítulos ASS para shorts y videos.

Estilos (ver docs/GUIA_CLIPS_VIRALES.md §4):
- "viral": 1-3 palabras en MAYÚSCULAS, tipografía gruesa con contorno,
  aparición con "pop", palabra activa resaltada y palabras clave en verde.
  Con varios hablantes, cada uno resalta en su color.
- "karaoke": tarjetas de hasta 4 palabras con la palabra activa en ámbar.
- "clean": frases de 1-2 líneas, sin animación (vídeos 16:9 o accesibilidad).
- "none": sin subtítulos (solo el texto gancho, si lo hay).

Además dibuja el texto gancho (3-6 palabras) arriba, dentro de la zona segura,
durante los primeros segundos.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from ..config import settings

STYLES = ("viral", "karaoke", "clean", "none")

WHITE = "&H00FFFFFF&"
YELLOW = "&H004DE1FF&"   # #FFE14D
AMBER = "&H004AD2FF&"    # #FFD24A
GREEN = "&H0018FC53&"    # #53FC18 (verde Kick)
SPEAKER_COLORS = [YELLOW, "&H00FFE34D&", "&H00D66BFF&", "&H00439FFF&"]  # amarillo, cian, rosa, naranja

# Zona segura 9:16 (cifras de Google para Shorts, sobre 1080×1920):
# arriba 288 px, abajo 672 px, izquierda 48 px, derecha 192 px.
SAFE_TOP = 288 / 1920
SAFE_BOTTOM = 672 / 1920

# Exclamaciones que siempre merecen color (además de cifras y palabras del LLM)
_STRONG_RE = re.compile(
    r"^(noo+|s[ií]{2,}|wtf|omg|bro{2,}|dios|hostia|joder|brutal|insano|incre[ií]ble|"
    r"ja(ja)+|je(je)+|gg|ace|clutch|imposible|locura|literal(ly)?)$",
    re.IGNORECASE,
)

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: {wrap}
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{sub_font},{sub_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,{sub_bold},0,0,0,100,100,{spacing},0,1,{outline},{shadow},{align},{margin_l},{margin_r},{margin_v},1
Style: Hook,{hook_font},{hook_size},&H00101010,&H00101010,&H00FFFFFF,&H64000000,0,0,0,0,100,100,0,0,3,{hook_pad},0,8,90,90,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def default_style(fmt: str) -> str:
    return "clean" if fmt == "video" else settings.caption_style


def build_ass(
    words: list[dict],
    out: Path,
    *,
    duration: float,
    fmt: str = "short",
    style: str | None = None,
    hook: str = "",
    hook_seconds: float = 3.0,
    keywords: list[str] | tuple[str, ...] = (),
    caption_y: float | None = None,
    hook_y: float | None = None,
) -> Path:
    """Escribe el .ass del clip.

    words: [{"w","s","e", "spk"?}] en tiempo de SALIDA (ya pasado por la
        línea de tiempo del clip). "spk" es el índice de hablante, si se sabe.
    caption_y: altura (0..1) del centro de los subtítulos; por defecto, el
        centro-bajo dentro de la zona segura.
    hook_y: altura (0..1) del borde superior del texto gancho; por defecto,
        arriba del todo dentro de la zona segura.
    """
    style = style or default_style(fmt)
    if style not in STYLES:
        style = "viral"
    if fmt == "video":
        w, h = settings.video_width, settings.video_height
    else:
        w, h = settings.output_width, settings.output_height

    viral = style == "viral"
    sub_font, hook_font = _fonts(style)
    if fmt == "video":
        sub_size = int(h * (0.075 if viral else 0.052))
        y = caption_y if caption_y is not None else 0.80
    else:
        sub_size = int(h * (0.058 if viral else 0.042 if style == "karaoke" else 0.036))
        y = caption_y if caption_y is not None else 0.62
    # nunca dentro de las franjas que tapa la UI de la app
    if fmt != "video":
        y = min(max(y, SAFE_TOP + 0.06), 1 - SAFE_BOTTOM - 0.02)

    lines = [_HEADER.format(
        w=w, h=h, wrap=0 if style == "clean" else 2,
        sub_font=sub_font, sub_size=sub_size, sub_bold=0 if viral else -1,
        spacing=1 if viral else 0,
        outline=max(4, int(sub_size * (0.085 if viral else 0.07))),
        shadow=max(2, int(sub_size * 0.04)),
        align=5, margin_l=60, margin_r=60, margin_v=0,
        hook_font=hook_font, hook_size=int((h if fmt != "video" else h * 1.4) * 0.033),
        hook_pad=int(h * 0.011),
    )]

    if hook:
        lines.append(_hook_event(hook, min(hook_seconds, duration), w, h, fmt, hook_y))

    if style != "none" and words:
        cx, cy = int(w / 2), int(h * y)
        kw = {_norm(k) for k in keywords if k}
        n_speakers = len({x.get("spk") for x in words if x.get("spk") is not None})
        if style == "viral":
            lines += _viral_events(words, cx, cy, kw, n_speakers)
        elif style == "karaoke":
            lines += _karaoke_events(words, cx, cy, n_speakers)
        else:
            lines += _clean_events(words, cx, cy)

    out.write_text("".join(lines), encoding="utf-8")
    return out


# ---------- estilos ----------

def _viral_events(words, cx, cy, keywords, n_speakers) -> list[str]:
    events = []
    for card in _cards(words, max_words=3, max_chars=16, max_secs=1.6, gap=0.45):
        c_start, c_end = card[0]["s"], card[-1]["e"]
        for i, word in enumerate(card):
            t0 = word["s"] if i else c_start
            t1 = card[i + 1]["s"] if i + 1 < len(card) else c_end
            if t1 - t0 < 0.01:
                continue
            parts = []
            for j, other in enumerate(card):
                txt = _esc(_clean_word(other["w"]).upper())
                if not txt:
                    continue
                is_kw = _is_keyword(other["w"], keywords)
                if j == i:
                    color = _speaker_color(other, n_speakers)
                    scale = "" if i == 0 else "\\fscx110\\fscy110"
                    parts.append(f"{{\\c{GREEN if is_kw else color}{scale}}}{txt}{{\\r}}")
                elif is_kw:
                    parts.append(f"{{\\c{GREEN}}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            if not parts:
                continue
            # "pop" al aparecer la tarjeta: 80 % → 100 % en 100 ms
            pop = "\\fscx80\\fscy80\\t(0,100,\\fscx100\\fscy100)" if i == 0 else ""
            events.append(
                f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Sub,,0,0,0,,"
                f"{{\\an5\\pos({cx},{cy}){pop}}}{' '.join(parts)}\n"
            )
    return events


def _karaoke_events(words, cx, cy, n_speakers) -> list[str]:
    events = []
    for card in _cards(words, max_words=4, max_chars=28, max_secs=2.2, gap=0.8):
        c_start, c_end = card[0]["s"], card[-1]["e"]
        for i, word in enumerate(card):
            t0 = word["s"] if i else c_start
            t1 = card[i + 1]["s"] if i + 1 < len(card) else c_end
            if t1 - t0 < 0.01:
                continue
            parts = []
            for j, other in enumerate(card):
                txt = _esc(other["w"])
                if j == i:
                    color = _speaker_color(other, n_speakers, default=AMBER)
                    parts.append(f"{{\\c{color}\\fscx108\\fscy108}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            events.append(
                f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Sub,,0,0,0,,"
                f"{{\\an5\\pos({cx},{cy})}}{' '.join(parts)}\n"
            )
    return events


def _clean_events(words, cx, cy) -> list[str]:
    events = []
    for card in _cards(words, max_words=12, max_chars=60, max_secs=4.5, gap=0.9,
                       break_on_punct=True):
        text = " ".join(_esc(x["w"]) for x in card)
        text = _two_lines(text, 42)
        events.append(
            f"Dialogue: 0,{_ts(card[0]['s'])},{_ts(card[-1]['e'] + 0.15)},Sub,,0,0,0,,"
            f"{{\\an5\\pos({cx},{cy})}}{text}\n"
        )
    return events


def _hook_event(hook: str, end_t: float, w: int, h: int, fmt: str,
                hook_y: float | None = None) -> str:
    text = _esc(" ".join(hook.replace("\n", " ").split())).upper()
    text = _two_lines(text, 20 if fmt != "video" else 34)
    if hook_y is not None:
        y = int(h * hook_y)
    else:
        y = int(h * (SAFE_TOP + 0.01)) if fmt != "video" else int(h * 0.07)
    anim = "\\fad(120,220)\\fscx88\\fscy88\\t(0,140,\\fscx100\\fscy100)"
    return (f"Dialogue: 2,{_ts(0)},{_ts(max(end_t, 0.5))},Hook,,0,0,0,,"
            f"{{\\an8\\pos({w // 2},{y}){anim}}}{text}\n")


# ---------- agrupación ----------

def _cards(words: list[dict], *, max_words: int, max_chars: int, max_secs: float,
           gap: float, break_on_punct: bool = True) -> list[list[dict]]:
    """Agrupa palabras en tarjetas, cortando en pausas, puntuación y longitud."""
    cards: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if not str(w.get("w", "")).strip():
            continue
        if cur:
            chars = sum(len(x["w"]) for x in cur) + len(w["w"])
            ended = break_on_punct and re.search(r"[.?!…]$", cur[-1]["w"])
            if (len(cur) >= max_words or chars > max_chars
                    or w["e"] - cur[0]["s"] > max_secs
                    or w["s"] - cur[-1]["e"] > gap
                    or ended
                    or w.get("spk") != cur[-1].get("spk")):
                cards.append(cur)
                cur = []
        cur.append(w)
    if cur:
        cards.append(cur)
    return cards


def _two_lines(text: str, max_line: int) -> str:
    if len(text) <= max_line:
        return text
    mid = len(text) // 2
    spaces = [i for i, c in enumerate(text) if c == " "]
    if not spaces:
        return text
    cut = min(spaces, key=lambda i: abs(i - mid))
    return text[:cut] + "\\N" + text[cut + 1 :]


# ---------- utilidades ----------

def _fonts(style: str) -> tuple[str, str]:
    if settings.subtitle_font:
        return settings.subtitle_font, settings.subtitle_font
    return ("Anton" if style == "viral" else "Poppins ExtraBold"), "Poppins ExtraBold"


def _speaker_color(word: dict, n_speakers: int, default: str = YELLOW) -> str:
    spk = word.get("spk")
    if n_speakers >= 2 and spk is not None:
        return SPEAKER_COLORS[int(spk) % len(SPEAKER_COLORS)]
    return default


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text)


def _is_keyword(word: str, keywords: set[str]) -> bool:
    n = _norm(word)
    if not n:
        return False
    if n in keywords or any(ch.isdigit() for ch in n):
        return True
    return bool(_STRONG_RE.match(_clean_word(word)))


def _clean_word(text: str) -> str:
    """Quita la puntuación que sobra en subtítulos grandes (deja ¿?¡!)."""
    return re.sub(r"[.,;:\"“”«»]+", "", str(text)).strip()


def _esc(text: str) -> str:
    return (str(text).replace("\\", "/").replace("{", "(").replace("}", ")")
            .replace("\n", " "))


def _ts(seconds: float) -> str:
    seconds = max(seconds, 0)
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"
