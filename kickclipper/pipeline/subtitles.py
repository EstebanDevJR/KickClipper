"""Genera subtítulos ASS estilo "short viral": grupos de pocas palabras,
grandes, centrados, con la palabra activa resaltada en color.

También dibuja el título del clip como rótulo superior durante los
primeros segundos.
"""
from __future__ import annotations

from pathlib import Path

from ..config import settings

MAX_WORDS_PER_CARD = 4
MAX_CARD_SECONDS = 2.2
HIGHLIGHT = "&H004AD2FF&"  # ámbar #FFD24A (ASS usa orden BGR)

_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{sub_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,2,2,60,60,{sub_margin},1
Style: Title,{font},{title_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,16,0,8,40,40,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(words: list[dict], clip_start: float, clip_end: float,
              title: str, out: Path, fmt: str = "short") -> Path:
    """words: [{"w","s","e"}] con tiempos absolutos del VOD; se re-basan al clip.

    fmt: "short" (9:16, subtítulos grandes al centro-bajo) o
         "video" (16:9, subtítulos clásicos abajo).
    """
    if fmt == "video":
        w, h = settings.video_width, settings.video_height
        sub_size, title_size, sub_margin = int(h * 0.060), int(h * 0.042), int(h * 0.06)
    else:
        w, h = settings.output_width, settings.output_height
        sub_size, title_size, sub_margin = int(h * 0.042), int(h * 0.030), int(h * 0.30)
    lines = [_HEADER.format(
        w=w, h=h, font=settings.subtitle_font,
        sub_size=sub_size, title_size=title_size, sub_margin=sub_margin,
    )]

    if title:
        safe = title.replace("{", "(").replace("}", ")").replace("\n", " ")
        end_t = min(4.5, clip_end - clip_start)
        lines.append(
            f"Dialogue: 1,{_ts(0)},{_ts(end_t)},Title,,0,0,0,,"
            f"{{\\fad(150,300)}}{safe}\n"
        )

    rel = [
        {"w": x["w"], "s": max(x["s"] - clip_start, 0), "e": min(x["e"] - clip_start, clip_end - clip_start)}
        for x in words
        if x["e"] > clip_start and x["s"] < clip_end
    ]

    for card in _cards(rel):
        c_start, c_end = card[0]["s"], card[-1]["e"]
        # Un evento por palabra activa: resalta la palabra en curso
        for i, word in enumerate(card):
            t0 = word["s"] if i else c_start
            t1 = card[i + 1]["s"] if i + 1 < len(card) else c_end
            if t1 <= t0:
                continue
            parts = []
            for j, other in enumerate(card):
                txt = _esc(other["w"])
                if j == i:
                    parts.append(f"{{\\c{HIGHLIGHT}\\fscx108\\fscy108}}{txt}{{\\r}}")
                else:
                    parts.append(txt)
            lines.append(
                f"Dialogue: 0,{_ts(t0)},{_ts(t1)},Sub,,0,0,0,,{' '.join(parts)}\n"
            )

    out.write_text("".join(lines), encoding="utf-8")
    return out


def _cards(words: list[dict]) -> list[list[dict]]:
    """Agrupa palabras en tarjetas cortas, cortando en pausas largas."""
    cards: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur and (
            len(cur) >= MAX_WORDS_PER_CARD
            or w["e"] - cur[0]["s"] > MAX_CARD_SECONDS
            or w["s"] - cur[-1]["e"] > 0.8  # pausa: nueva tarjeta
        ):
            cards.append(cur)
            cur = []
        cur.append(w)
    if cur:
        cards.append(cur)
    return cards


def _esc(text: str) -> str:
    return text.replace("{", "(").replace("}", ")")


def _ts(seconds: float) -> str:
    seconds = max(seconds, 0)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"
