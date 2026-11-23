"""Summary of "what the device sees" sent to the Camera test screen."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.association import Assignment, Phone  # noqa: E402
from argos_edge.features import compute_features  # noqa: E402
from argos_edge.main import load_config  # noqa: E402
from argos_edge.rules import RuleEngine  # noqa: E402
from argos_edge.status import live_status  # noqa: E402

CFG = load_config(str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))
PITCH = CFG["thresholds"]["pitch"]


def _person(tid, nose_y):
    kp = np.zeros((17, 3))
    kp[0] = [100, nose_y, 0.9]           # nose
    kp[5] = [70, 200, 0.95]              # shoulders: width 60 px
    kp[6] = [130, 200, 0.95]
    kp[9] = [80, 230, 0.9]
    return compute_features(tid, np.array([50, 120, 150, 320]), kp, CFG["thresholds"]["kp_conf"])


def test_status_reports_head_phone_and_boxes_without_images():
    up = _person(1, 160)                 # nose 40 px above the shoulders: h_pitch 0.67
    down = _person(2, 195)               # nose almost at shoulder height: h_pitch 0.08
    calm = _person(3, 160)
    teacher = _person(9, 160)
    phone = Phone(np.array([75, 220, 95, 245]), 0.8)
    s = live_status([up, down, calm, teacher], [phone], [Assignment(phone, up, 0.3, "left")], teacher_id=9,
                    progress={1: {"phone": (4, 10), "posture": (0, 40)}}, pitch_thr=PITCH, fps=0.83,
                    size=(200, 400), source="phone")
    assert s["source"] == "phone" and s["persons"] == 3 and s["phones"] == 1 and s["teacher"] is True
    assert s["phone_boxes"][0] == pytest.approx([0.375, 0.55, 0.475, 0.6125], abs=1e-3)
    p1, p2, p3 = s["people"]
    assert p1 == {"id": 1, "box": [0.25, 0.3, 0.75, 0.8], "head_down": False, "head_visible": True,
                  "phone_near": True, "alert": True, "phone_progress": [4, 10], "head_down_progress": [0, 40]}
    assert p2["head_down"] is True and p2["alert"] is True      # red box
    assert p3["alert"] is False                                  # green box
    assert all(set(p) == set(p1) for p in s["people"])           # no keypoints, no image


def test_recent_alert_keeps_box_red():
    calm = _person(4, 160)
    s = live_status([calm], [], [], None, {}, PITCH, 1.0, (200, 400), "phone", alerted={4})
    assert s["people"][0]["alert"] is True


def test_boxes_clamped_to_image():
    p = _person(5, 160)
    p.box = np.array([-10, -5, 250, 500])
    s = live_status([p], [], [], None, {}, PITCH, 1.0, (200, 400), "picamera")
    assert s["people"][0]["box"] == [0.0, 0.0, 1.0, 1.0] and s["source"] == "picamera"


def test_rule_progress_counts_evidence():
    eng = RuleEngine(CFG)
    down = _person(3, 195)
    for i in range(5):
        eng.update(i, i * 0.2, [down], [], tracker=None)
    pr = eng.progress()[3]
    assert pr["posture"] == (5, CFG["rules"]["posture"]["k"])
    assert pr["phone"][1] == CFG["rules"]["phone"]["k"]


def test_clip_for_every_sent_detection():
    from argos_edge.main import route_event
    tiers = set(CFG["confidence"]["clip_tiers"])
    assert route_event("high", tiers) == "clip"
    assert route_event("medium", tiers) == "clip"      # decision of 23 Nov: clip for the medium tier too
    assert route_event("low", tiers) is None
    assert route_event("medium", {"high"}) == "send"   # rule of the original report


def test_device_key_from_environment(monkeypatch):
    path = str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml")
    monkeypatch.delenv("ARGOS_DEVICE_KEY", raising=False)
    assert load_config(path)["classroom_device_key"] == "CHANGE_ME"   # no secret in the repository
    monkeypatch.setenv("ARGOS_DEVICE_KEY", "real-key")
    assert load_config(path)["classroom_device_key"] == "real-key"
