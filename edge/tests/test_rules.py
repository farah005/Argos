"""Tests of features, phone association and rules with synthetic keypoints."""
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.association import Phone, PhoneTracker, assign_phones  # noqa: E402
from argos_edge.features import compute_features  # noqa: E402
from argos_edge.rules import PHONE, POSTURE, RuleEngine  # noqa: E402

CFG = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))


def person_kp(cx, head_down=False, wrist_up=True):
    """Seated student: shoulders at y=200, width 60 px."""
    kp = np.zeros((17, 3))
    kp[:, 2] = 0.9
    kp[5] = [cx - 30, 200, 0.95]
    kp[6] = [cx + 30, 200, 0.95]
    kp[0] = [cx, 190 if head_down else 160, 0.9]       # nose: h_pitch 0.17 (down) or 0.67
    kp[3] = [cx - 15, 165, 0.9]
    kp[4] = [cx + 15, 165, 0.9]
    wy = 230 if wrist_up else 400
    kp[9] = [cx - 20, wy, 0.9]
    kp[10] = [cx + 20, wy, 0.9]
    return kp


def box(cx):
    return np.array([cx - 50, 120, cx + 50, 320])


def test_features():
    f = compute_features(1, box(100), person_kp(100), 0.5)
    assert abs(f.ws - 60) < 1e-6
    assert abs(f.h_pitch - 40 / 60) < 1e-6
    fd = compute_features(1, box(100), person_kp(100, head_down=True), 0.5)
    assert fd.h_pitch < CFG["thresholds"]["pitch"] < f.h_pitch


def test_hungarian_one_phone_one_student():
    a = compute_features(1, box(100), person_kp(100), 0.5)
    b = compute_features(2, box(160), person_kp(160), 0.5)
    ph = Phone(np.array([115, 222, 135, 242]), 0.8)   # near student 1's right wrist
    res = assign_phones([a, b], [ph])
    assert len(res) == 1 and res[0].person.track_id == 1


def run_frames(engine, trk, n, head_down=False, phone=True, t0=0.0, idx0=0):
    events = []
    for i in range(n):
        p1 = compute_features(1, box(100), person_kp(100, head_down=head_down), 0.5)
        p2 = compute_features(2, box(300), person_kp(300), 0.5)
        phones = [Phone(np.array([115, 222, 135, 242]), 0.8)] if phone else []
        trk.update(phones)
        asg = assign_phones([p1, p2], phones)
        trk.record_owners(asg, phones)
        events += engine.update(idx0 + i, t0 + i * 0.2, [p1, p2], asg, trk)
    return events


def test_phone_rule_fires_once_then_merges():
    eng, trk = RuleEngine(CFG), PhoneTracker(history=15)
    ev = run_frames(eng, trk, 40)
    phone_ev = [e for e in ev if e.category == PHONE]
    assert len(phone_ev) == 1                         # merged during the cooldown
    assert phone_ev[0].track_id == 1
    assert abs(phone_ev[0].confidence - 0.8) < 0.01    # continuous evidence => score = confidence
    assert phone_ev[0].tier == "high"


def test_short_glance_does_not_fire():
    eng, trk = RuleEngine(CFG), PhoneTracker(history=15)
    ev = run_frames(eng, trk, CFG["rules"]["phone"]["k"] - 1)
    ev += run_frames(eng, trk, 20, phone=False, t0=10, idx0=100)
    assert not [e for e in ev if e.category == PHONE]


def test_posture_rule():
    eng, trk = RuleEngine(CFG), PhoneTracker()
    ev = run_frames(eng, trk, 60, head_down=True, phone=False)
    post = [e for e in ev if e.category == POSTURE]
    assert len(post) == 1 and post[0].track_id == 1


def test_teacher_board_lowers_k():
    eng, trk = RuleEngine(CFG), PhoneTracker()
    k = CFG["rules"]["posture"]["k"]
    k_eff = int(np.ceil(k * CFG["context"]["board_sensitivity_factor"]))
    ev = []
    for i in range(k_eff):
        p1 = compute_features(1, box(100), person_kp(100, head_down=True), 0.5)
        ev += eng.update(i, i * 0.2, [p1], [], trk, teacher_id=None, teacher_facing_board=True)
    assert any(e.category == POSTURE for e in ev)


def test_teacher_never_evaluated():
    eng, trk = RuleEngine(CFG), PhoneTracker()
    ev = []
    for i in range(60):
        p1 = compute_features(1, box(100), person_kp(100, head_down=True), 0.5)
        ev += eng.update(i, i * 0.2, [p1], [], trk, teacher_id=1)
    assert ev == []
