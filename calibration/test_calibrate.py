"""Tests of the calibration threshold functions (no model, no dataset)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from calibrate_scb import best_threshold, max_threshold_for_fpr, rate_below, report  # noqa: E402


def test_rate_below():
    assert rate_below(np.array([0.1, 0.2, 0.5, 0.6]), 0.45) == 0.5
    assert np.isnan(rate_below(np.array([]), 0.45))


def test_max_threshold_for_fpr_keeps_negatives_above():
    neg = np.linspace(0.30, 0.79, 50)           # reading / writing
    t = max_threshold_for_fpr(neg, 0.10)
    assert rate_below(neg, t) <= 0.10
    assert max_threshold_for_fpr(np.array([]), 0.1) is None


def test_youden_separates_classes():
    pos, neg = np.array([0.1, 0.15, 0.2]), np.array([0.5, 0.6, 0.7])
    t, tpr, fpr, _ = best_threshold(pos, neg, lower_is_positive=True)
    assert 0.2 < t <= 0.5 and tpr == 1.0 and fpr == 0.0


def test_report_without_positive_class(tmp_path):
    rows = [{"image": "a.jpg", "class": c, "matched": 1, "h_pitch": hp, "h_yaw": 0.1, "posture_conf": 0.9,
             "shoulder_px": 40, "phone_conf": None, "d_phone": None, "hand_rel_y": None}
            for c, hp in [("read", 0.3), ("read", 0.5), ("write", 0.35), ("lookup", 0.7)]]
    report(rows, set(), {"read", "write"}, set(), tmp_path, current_pitch=0.45, max_fpr=0.34)
    txt = (tmp_path / "rapport.md").read_text(encoding="utf-8")
    assert "fausses alarmes" in txt and "67%" in txt        # 2 of 3 reading/writing values below 0.45
    assert (tmp_path / "h_pitch.png").exists()
