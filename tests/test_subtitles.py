import re

from kickclipper.pipeline import subtitles


def _words(text, start=0.0, step=0.4):
    out = []
    t = start
    for w in text.split():
        out.append({"w": w, "s": t, "e": t + step * 0.9})
        t += step
    return out


def _dialogues(path):
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.startswith("Dialogue:")]


def test_viral_style_uppercase_short_cards(tmp_path):
    out = tmp_path / "a.ass"
    words = _words("no puede ser lo que acaba de pasar aquí 3 veces")
    subtitles.build_ass(words, out, duration=10, style="viral", keywords=["pasar"])
    lines = _dialogues(out)
    assert lines
    text = "\n".join(lines)
    assert "NO PUEDE SER" in re.sub(r"\{[^}]*\}", "", text)
    # pop al aparecer cada tarjeta
    assert "\\t(0,100,\\fscx100\\fscy100)" in text
    # palabra clave del LLM y cifras en verde
    assert subtitles.GREEN in text
    for line in lines:
        visible = re.sub(r"\{[^}]*\}", "", line.split(",,", 1)[1])
        assert len(visible.split()) <= 3


def test_captions_stay_inside_safe_zone(tmp_path):
    out = tmp_path / "b.ass"
    subtitles.build_ass(_words("hola chat"), out, duration=3, caption_y=0.95)
    pos = re.findall(r"\\pos\((\d+),(\d+)\)", out.read_text(encoding="utf-8"))
    y = int(pos[0][1])
    assert y <= 1920 - 672  # nunca bajo la UI inferior de la app


def test_hook_text_on_top(tmp_path):
    out = tmp_path / "c.ass"
    subtitles.build_ass([], out, duration=20, hook="el peor clutch de la historia")
    hook = [l for l in _dialogues(out) if ",Hook," in l]
    assert len(hook) == 1
    assert "EL PEOR CLUTCH" in hook[0].replace("\\N", " ")
    assert "0:00:03.00" in hook[0]
    assert re.search(r"\\an8\\pos\(540,(\d+)\)", hook[0])


def test_speaker_colors(tmp_path):
    out = tmp_path / "d.ass"
    words = _words("hola qué tal", 0.0) + _words("muy bien gracias", 2.0)
    for w in words[:3]:
        w["spk"] = 0
    for w in words[3:]:
        w["spk"] = 1
    subtitles.build_ass(words, out, duration=5, style="viral")
    text = out.read_text(encoding="utf-8")
    assert subtitles.SPEAKER_COLORS[0] in text and subtitles.SPEAKER_COLORS[1] in text


def test_clean_style_for_video(tmp_path):
    out = tmp_path / "e.ass"
    words = _words("esto es una frase bastante larga para ver como se parte en dos lineas bien")
    subtitles.build_ass(words, out, duration=10, fmt="video")
    text = out.read_text(encoding="utf-8")
    assert "PlayResX: 1920" in text
    assert "\\fscx80" not in text  # sin pop en el estilo limpio


def test_escapes_ass_override_chars(tmp_path):
    out = tmp_path / "f.ass"
    subtitles.build_ass([{"w": "{\\b1}hack", "s": 0, "e": 1}], out, duration=2, style="karaoke")
    body = _dialogues(out)[0].split(",,", 1)[1]
    assert "\\b1" not in body
