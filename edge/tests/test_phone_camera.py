"""Phone camera (relayed by the backend) and deletion of clips older than 24 h."""
import os
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge import camera  # noqa: E402
from argos_edge.camera import FrameSource, PhoneRelay  # noqa: E402
from argos_edge.clips import purge_old_clips  # noqa: E402
from argos_edge.main import load_config  # noqa: E402

CFG = load_config(str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))


class _Resp:
    def __init__(self, status, content=b"", headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {}), dict(headers or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _jpeg():
    img = np.full((48, 64, 3), 90, np.uint8)
    cv2.rectangle(img, (10, 10), (40, 30), (0, 200, 0), -1)
    return cv2.imencode(".jpg", img)[1].tobytes()


def _relay(responses):
    s = _Session(responses)
    return PhoneRelay("http://pi:8000/", "cle", poll_s=0, timeout_s=1, session=s), s


def test_relay_decodes_frame_and_tracks_sequence():
    r, s = _relay([_Resp(200, _jpeg(), {"X-Frame-Seq": "7"}), _Resp(204)])
    ok, frame = r.read()
    assert ok and frame.shape == (48, 64, 3)
    assert r.seq == 7
    ok, frame = r.read()                       # nothing new
    assert not ok and frame is None
    url, params, headers = s.calls[1]
    assert url == "http://pi:8000/api/device/frame"
    assert params == {"after": 7} and headers == {"X-Device-Key": "cle"}


def test_relay_survives_backend_down_and_bad_image(monkeypatch):
    monkeypatch.setattr(camera.time, "sleep", lambda s: None)
    r, _ = _relay([requests.ConnectionError("down"), _Resp(200, b"\xff\xd8pas un jpeg")])
    assert r.read() == (False, None)
    assert r.read() == (False, None)
    assert r.seq == 0


def test_auto_source_falls_back_to_phone(monkeypatch):
    monkeypatch.setattr(camera, "picamera_available", lambda: False)
    src = FrameSource("auto", 5, CFG)
    assert src.source == "phone" and src._relay is not None
    src.close()


def test_purge_old_clips(tmp_path):
    old, recent = tmp_path / "vieux.mp4", tmp_path / "recent.mp4"
    old.write_bytes(b"x")
    recent.write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 25 * 3600, now - 25 * 3600))
    os.utime(recent, (now - 23 * 3600, now - 23 * 3600))
    assert purge_old_clips(tmp_path, CFG["clips"]["max_age_h"], now) == 1
    assert not old.exists() and recent.exists()
