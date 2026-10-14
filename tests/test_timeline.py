import numpy as np

from kickclipper.pipeline import timeline


def _words(spec):
    return [{"w": w, "s": s, "e": e} for w, s, e in spec]


def test_plain_timeline_is_identity():
    tl = timeline.build(10.0, _words([("hola", 0.5, 1.0)]))
    assert tl.is_plain(10.0)
    assert tl.duration == 10.0
    assert tl.map_time(3.0) == [3.0]


def test_jumpcut_removes_quiet_pauses_only():
    words = _words([("uno", 0.0, 1.0), ("dos", 3.0, 4.0), ("tres", 6.0, 7.0)])
    hop = 0.05
    energy = np.full(int(8 / hop), -60.0)  # silencio
    energy[: int(1 / hop)] = -20.0          # voz
    energy[int(3 / hop) : int(4 / hop)] = -20.0
    energy[int(4.2 / hop) : int(5.8 / hop)] = -15.0  # ¡acción en la segunda pausa!
    energy[int(6 / hop) : int(7 / hop)] = -20.0
    tl = timeline.build(8.0, words, energy, energy_hop=hop, jumpcut=True, min_gap=0.7, pad=0.2)
    # la primera pausa (1-3 s) se recorta, la segunda (4-6 s, con acción) no
    assert tl.map_time(2.0) == []
    assert tl.map_time(5.0)
    assert tl.duration < 8.0
    # el final silencioso (7-8 s) también se recorta
    assert tl.map_time(7.9) == []


def test_jumpcut_without_energy_uses_voice_gaps():
    words = _words([("a", 0.0, 1.0), ("b", 2.5, 3.0)])
    tl = timeline.build(3.0, words, jumpcut=True, min_gap=0.7, pad=0.15)
    assert len(tl.pieces) == 2
    assert abs(tl.duration - (3.0 - (1.5 - 0.3))) < 1e-6


def test_preview_duplicates_words_at_start():
    words = _words([("ojo", 1.0, 1.4), ("GOL", 8.0, 8.5)])
    tl = timeline.build(10.0, words, preview=(7.6, 9.0))
    assert tl.pieces[0] == (7.6, 9.0)
    out = tl.map_words(words)
    gols = [w for w in out if w["w"] == "GOL"]
    assert len(gols) == 2
    assert gols[0]["s"] < 1.0  # aparece en el cold open
    assert abs(tl.duration - 11.4) < 1e-6


def test_map_spans_splits_across_cuts():
    tl = timeline.Timeline([(0.0, 2.0), (4.0, 6.0)])
    spans = tl.map_spans([{"t0": 1.0, "t1": 5.0, "mode": "single"}])
    assert [(round(s["t0"], 3), round(s["t1"], 3)) for s in spans] == [(1.0, 2.0), (2.0, 3.0)]


def test_never_cuts_everything():
    words = _words([("a", 0.0, 0.2), ("b", 5.0, 5.2)])
    tl = timeline.build(5.2, words, jumpcut=True, min_gap=0.3, pad=0.0)
    assert tl.duration >= 0.4
