from datetime import datetime, timedelta, timezone

import numpy as np

from kickclipper.pipeline import chat

URL = "https://kick.com/canal/videos/5c697a87-afce-4256-b01f-3c8fe71ef5cb"
START = datetime(2026, 1, 1, 20, 0, 0, tzinfo=timezone.utc)


class _Resp:
    def __init__(self, data, status=200):
        self._data, self.status_code = data, status

    def json(self):
        return self._data


class _FakeSession:
    """Chat sintético: 1 msg/s de base y 10 msg/s entre los segundos 300 y 320."""

    def __init__(self):
        self.calls = 0

    def get(self, url, params=None, headers=None):
        self.calls += 1
        if "/v1/video/" in url:
            return _Resp({"livestream": {"channel": {"id": 42},
                                         "start_time": "2026-01-01 20:00:00", "duration": 900000}})
        at = datetime.fromisoformat(params["start_time"].replace("Z", "+00:00"))
        t = (at - START).total_seconds()
        msgs = []
        for k in range(25):
            rate = 10 if 300 <= t + k * 0.1 < 320 else 1
            ts = at + timedelta(seconds=k / rate)
            text = "KEKW jajaja" if rate == 10 else "hola"
            msgs.append({"created_at": ts.isoformat().replace("+00:00", "Z"), "content": text})
        return _Resp({"data": {"messages": msgs}})


def test_activity_and_spike(monkeypatch):
    fake = _FakeSession()
    monkeypatch.setattr(chat, "_session", lambda: fake)
    monkeypatch.setattr(chat.settings, "chat_request_delay", 0.0)
    data = chat.activity(URL, 900.0)
    assert data is not None
    rate = data["rate"]
    assert rate[310] > 5 * np.median(rate)
    spike = chat.spike_curve(data, 900, lag=6)
    assert spike.argmax() in range(290, 320)
    assert spike[310 - 6] > 0.5 and np.median(spike) < 0.2
    assert fake.calls <= chat.settings.chat_max_requests + 1


def test_activity_gives_up_on_errors(monkeypatch):
    class Broken:
        def get(self, *a, **k):
            return _Resp({}, status=403)
    monkeypatch.setattr(chat, "_session", lambda: Broken())
    assert chat.activity(URL, 600.0) is None


def test_vod_uuid():
    assert chat.vod_uuid(URL) == "5c697a87-afce-4256-b01f-3c8fe71ef5cb"
    assert chat.vod_uuid("https://kick.com/canal") is None
