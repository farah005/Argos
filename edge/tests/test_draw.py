"""Tests of the debug overlay (--show / --save-annotated)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.association import Phone  # noqa: E402
from argos_edge.features import compute_features  # noqa: E402
from argos_edge.main import draw  # noqa: E402


def _kp(shoulders=True):
    kp = np.zeros((17, 3))
    kp[0] = [100, 160, 0.9]                      # nose
    if shoulders:
        kp[5] = [70, 200, 0.95]
        kp[6] = [130, 200, 0.95]
    kp[9] = [80, 230, 0.9]
    return kp


def test_features_keep_keypoints():
    f = compute_features(1, np.array([50, 120, 150, 320]), _kp(), 0.5)
    assert f.kp is not None and f.kp.shape == (17, 3)


def test_draw_marks_keypoints_and_missing_shoulders():
    frame = np.zeros((360, 640, 3), np.uint8)
    ok = compute_features(1, np.array([50, 120, 150, 320]), _kp(), 0.5)
    no_sh = compute_features(2, np.array([300, 120, 400, 320]), _kp(shoulders=False), 0.5)
    assert ok.h_pitch is not None and no_sh.h_pitch is None
    out = draw(frame.copy(), [ok, no_sh], [Phone(np.array([500, 200, 540, 260]), 0.5)], None, {},
               kp_conf=0.5, pitch_thr=0.45, fps=5.0)
    assert out.shape == frame.shape
    assert tuple(out[160, 100]) == (0, 255, 255)        # nose point drawn in yellow
    assert out[:22].any()                               # status bar
    assert (out[200, 500] == (255, 0, 255)).all()       # phone box
