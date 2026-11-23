"""Real-time clips: at ~1 frame/s (Pi 4 + phone camera), a clip must cover pre_s before
and post_s after the event and last the real elapsed time."""
import copy
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.clips import ClipRecorder, clip_fps  # noqa: E402
from argos_edge.main import load_config  # noqa: E402

CFG = load_config(str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))


def _rec(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["clips"]["dir"] = str(tmp_path)
    cfg["clips"]["transcode_h264"] = False
    return ClipRecorder(cfg, fps=float(cfg["sample_fps"])), cfg["clips"]


def test_clip_covers_real_time_at_low_fps(tmp_path):
    rec, c = _rec(tmp_path)
    frame = np.zeros((120, 160, 3), np.uint8)
    out = []
    t_event = 20.0
    for i in range(40):                        # 1 frame/s for 40 s
        t = float(i)
        rec.push(t, frame, {1: [10, 10, 50, 50]})
        if t == t_event:
            rec.start(1, t, out.append)
    assert len(out) == 1 and out[0] is not None
    cap = cv2.VideoCapture(str(out[0]))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    expected = int(c["pre_s"]) + int(c["post_s"]) + 1    # from t-pre_s to t+post_s inclusive
    assert n == expected                                   # not 20 s before (buffer size)
    assert abs(n / fps - expected) < 1.5                   # real duration, not played 5x too fast


def test_clip_fps():
    assert clip_fps([(0.0,), (1.0,), (2.0,)], 5) == 1.0
    assert clip_fps([(0.0,)], 5) == 5
    assert clip_fps([(0.0,), (0.0,)], 5) == 5
    assert clip_fps([(0.0,), (0.01,)], 5) == 30.0
