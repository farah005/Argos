"""Tests of rule 3 "possible physical conflict" with synthetic keypoints."""
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argos_edge.association import PhoneTracker  # noqa: E402
from argos_edge.features import compute_features  # noqa: E402
from argos_edge.main import LABELS  # noqa: E402
from argos_edge.rules import CONFLICT, RuleEngine  # noqa: E402

CFG = yaml.safe_load(open(Path(__file__).resolve().parents[2] / "config" / "edge.yaml", encoding="utf-8"))
DT = 0.2   # 5 frames/s


def kp(cx, r_wrist=None):
    """Seated student: shoulders at y=200, width 60 px (ws=60), nose above."""
    k = np.zeros((17, 3))
    k[0] = [cx, 160, 0.9]
    k[5] = [cx - 30, 200, 0.95]
    k[6] = [cx + 30, 200, 0.95]
    k[9] = [cx - 20, 230, 0.9]
    k[10] = [*(r_wrist or (cx + 20, 230)), 0.9]
    return k


def person(tid, cx, r_wrist=None):
    return compute_features(tid, np.array([cx - 50, 120, cx + 50, 320]), kp(cx, r_wrist), 0.5)


def run(n, cx2=200, fast=True, teacher_id=None):
    """Student 1's right wrist moves back and forth between their body and student 2's torso."""
    eng, trk, events = RuleEngine(CFG), PhoneTracker(), []
    for i in range(n):
        if fast:
            wrist = (cx2 - 10, 205) if i % 2 else (120, 230)        # ~74 px in 0.2 s = 6 ws/s
        else:
            wrist = (120 + i, 230)                                  # 1 px per frame: slow gesture
        persons = [person(1, 100, wrist), person(2, cx2)]
        events += eng.update(i, i * DT, persons, [], trk, teacher_id=teacher_id)
    return [e for e in events if e.category == CONFLICT]


def test_fast_strikes_between_close_students_fire_once():
    ev = run(40)
    assert len(ev) == 1                                  # merged during the cooldown
    e = ev[0]
    assert e.details["tracks"] == [1, 2]
    assert e.box == [50.0, 120.0, 250.0, 320.0]           # box enclosing both students
    assert 0 < e.confidence <= 0.9 and e.tier in ("high", "medium", "low")
    assert LABELS[CONFLICT] == "conflit possible"        # no KeyError in the main loop


def test_slow_gesture_does_not_fire():
    assert run(40, fast=False) == []


def test_far_apart_students_do_not_fire():
    # student 2 at 400 px: 5 shoulder widths, beyond conflict_dist; the wrist cannot reach them
    assert run(40, cx2=400) == []


def test_single_quick_gesture_does_not_fire():
    assert run(CFG["rules"]["conflict"]["k"]) == []   # at most k-1 fast strikes: not enough evidence


def test_teacher_is_never_part_of_a_conflict():
    assert run(40, teacher_id=1) == []
