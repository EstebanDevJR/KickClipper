from kickclipper import hardware


def test_recommend_by_memory():
    assert hardware.recommend(32, (8.0, "AMD"), False)[0] == "qwen3:8b"
    assert hardware.recommend(64, (24.0, "NVIDIA"), False)[0] == "qwen3:14b"
    assert hardware.recommend(16, (4.0, "NVIDIA"), False)[0] == "qwen3:4b"
    # Apple Silicon: memoria unificada
    assert hardware.recommend(16, None, True)[0] == "qwen3:8b"
    assert hardware.recommend(36, None, True)[0] == "qwen3:14b"
    # solo CPU: el pequeño, aunque sobre RAM
    model, reason = hardware.recommend(64, None, False)
    assert model == "qwen3:4b" and "CPU" in reason


def test_summary_never_fails():
    s = hardware.summary()
    assert s["recommended_model"].startswith("qwen3:")
