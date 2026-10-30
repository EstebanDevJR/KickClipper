import numpy as np

from kickclipper.pipeline import highlights


def _segments(n=200, length=3.0, special=None):
    """Segmentos de 3 s de relleno; `special` = {índice: texto}."""
    segs = []
    for i in range(n):
        s = i * length
        text = (special or {}).get(i, "pues nada aquí estamos jugando un rato tranquilo")
        words = []
        toks = text.split()
        for j, tok in enumerate(toks):
            ws = s + j * (length / len(toks))
            words.append({"w": tok, "s": ws, "e": ws + length / len(toks) * 0.9})
        segs.append({"text": text, "start": s, "end": s + length, "words": words})
    return segs


def test_detect_without_llm_finds_peak_moment():
    segs = _segments(special={100: "¡NO PUEDE SER! jajaja qué locura", 101: "¡clipéalo chat!"})
    exc = np.full(600, 0.1)
    exc[300:306] = 1.0  # pico de audio en t=300 s (segmento 100)
    clips = highlights.detect(segs, exc, 600.0, formats=("short",))
    assert clips
    best = clips[0]
    # empieza como muy tarde en la frase de la reacción (gancho) y la incluye
    assert best["start"] <= 300 < best["end"]
    assert best["format"] == "short"
    assert 15 <= best["end"] - best["start"] <= 60
    # metadatos heurísticos cuando no hay LLM
    assert best["title"] and best["hook_text"]
    assert best["llm_score"] == 0.0
    assert "audio" in best["signals"]


def test_chat_spike_counts():
    segs = _segments()
    exc = np.full(600, 0.2)
    chat = np.zeros(600)
    chat[450:456] = 1.0  # el chat explotó en t=450 s (ya compensado el retraso)
    clips = highlights.detect(segs, exc, 600.0, formats=("short",), chat=chat)
    assert clips[0]["start"] < 452 < clips[0]["end"]
    assert clips[0]["signals"]["chat"] > 0.5


def test_tighten_start_drops_filler():
    segs = _segments(special={50: "bueno chat a ver", 51: "eh pues", 52: "¡NO PUEDE SER!"})
    ctx = highlights._Context(segs, np.full(600, 0.1), None, 600)
    w = highlights._window(segs, 50, 60, "short")
    highlights._tighten_start(w, ctx, 15.0)
    assert w["i0"] == 52


def test_llm_lines_refine_bounds():
    segs = _segments()
    ctx = highlights._Context(segs, np.full(600, 0.1), None, 600)
    w = highlights._window(segs, 40, 50, "short")
    lines, index = highlights._numbered_lines(w, ctx)
    assert lines.splitlines()[0].startswith("1. [ANTES]")
    w["_line_index"] = index
    data = {"score": 8, "hook": 9, "standalone": 7, "payoff": 8, "title": "x", "hook_text": "a b c",
            "description": "d", "hashtags": ["#uno", "dos"], "keywords": ["k"], "category": "humor",
            "reason": "r", "start_line": 1, "end_line": 9}
    highlights._apply_llm(w, data, ctx, "canal")
    assert w["llm_ok"] and w["hashtags"] == ["uno", "dos"]
    highlights._refine(w, ctx)
    assert w["i0"] == 38  # empezó en una línea [ANTES] para dar contexto
    assert w["i1"] == 46


def test_video_windows_long():
    segs = _segments(n=400)
    exc = np.full(1200, 0.2)
    clips = highlights.detect(segs, exc, 1200.0, formats=("video",))
    assert clips and all(300 <= c["end"] - c["start"] <= 600 for c in clips)


def test_pick_model_uses_any_installed_model(monkeypatch):
    monkeypatch.setattr(highlights.settings, "ollama_model", "qwen3:8b")
    installed = ["llama3.2:latest", "gemma3:4b", "qwen3:8b"]
    assert highlights.pick_model("gemma3:4b", installed) == "gemma3:4b"
    assert highlights.pick_model("llama3.2", installed) == "llama3.2:latest"
    # el pedido no está instalado: el de la configuración, y si no, el primero
    assert highlights.pick_model("mistral:7b", installed) == "qwen3:8b"
    assert highlights.pick_model(None, ["phi4:14b"]) == "phi4:14b"
    assert highlights.pick_model("gemma3:4b", []) == ""


def test_embedding_models_are_not_offered():
    assert highlights._is_text_model({"name": "qwen3:8b", "details": {"family": "qwen3"}})
    assert not highlights._is_text_model({"name": "nomic-embed-text:latest"})
    assert not highlights._is_text_model({"name": "mxbai:latest", "details": {"families": ["bert"]}})


def test_strip_think_keeps_only_the_json():
    raw = '<think>pienso…{"x": 1}</think>\nAquí va: {"score": 7}'
    assert highlights._strip_think(raw) == '{"score": 7}'
    assert highlights._strip_think("sin json") == "{}"


def test_detect_without_llm_when_disabled(monkeypatch):
    called = []
    monkeypatch.setattr(highlights._Ollama, "available", lambda self: called.append(1) or True)
    segs = _segments(special={100: "¡NO PUEDE SER! jajaja qué locura"})
    exc = np.full(600, 0.1)
    exc[300:306] = 1.0
    clips = highlights.detect(segs, exc, 600.0, formats=("short",), llm_model=highlights.NO_LLM)
    assert clips and not called
