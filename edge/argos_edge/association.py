"""Phone -> student association (section 2.4).

- Hungarian algorithm: a phone cannot be assigned to two students.
- Simple phone tracking (IoU) to check that the phone-student pair stays
  stable over time; if it alternates between two students => ambiguous.
"""
from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from .features import PersonFeatures, phone_distance

BIG = 1e6


@dataclass
class Phone:
    box: np.ndarray
    conf: float
    phone_id: int = -1

    @property
    def center(self) -> np.ndarray:
        x1, y1, x2, y2 = self.box
        return np.array([(x1 + x2) / 2, (y1 + y2) / 2])


@dataclass
class Assignment:
    phone: Phone
    person: PersonFeatures
    d_ph: float
    wrist_side: str | None


def _expanded(box: np.ndarray, margin: float = 0.25) -> np.ndarray:
    x1, y1, x2, y2 = box
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    return np.array([x1 - mx, y1 - my, x2 + mx, y2 + my])


def _inside(pt: np.ndarray, box: np.ndarray) -> bool:
    return box[0] <= pt[0] <= box[2] and box[1] <= pt[1] <= box[3]


def assign_phones(persons: list[PersonFeatures], phones: list[Phone]) -> list[Assignment]:
    if not persons or not phones:
        return []
    cost = np.full((len(phones), len(persons)), BIG)
    sides: dict[tuple[int, int], str | None] = {}
    for i, ph in enumerate(phones):
        c = ph.center
        for j, p in enumerate(persons):
            if not _inside(c, _expanded(p.box)):
                continue  # phone outside the person's enlarged box
            d, side = phone_distance(p, c)
            if np.isfinite(d):
                cost[i, j] = d
                sides[(i, j)] = side
    rows, cols = linear_sum_assignment(cost)
    out = []
    for i, j in zip(rows, cols):
        if cost[i, j] < BIG:
            out.append(Assignment(phones[i], persons[j], float(cost[i, j]), sides[(i, j)]))
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


class PhoneTracker:
    """Gives each phone a stable ID and remembers who it is assigned to."""

    def __init__(self, history: int = 15, max_missing: int = 5, iou_thr: float = 0.2):
        self.history = history
        self.max_missing = max_missing
        self.iou_thr = iou_thr
        self._next_id = 0
        self._tracks: dict[int, dict] = {}   # id -> {box, missing, owners: deque}

    def update(self, phones: list[Phone]) -> None:
        unmatched = set(self._tracks)
        for ph in sorted(phones, key=lambda p: -p.conf):
            best, best_iou = None, self.iou_thr
            for tid in unmatched:
                iou = _iou(ph.box, self._tracks[tid]["box"])
                if iou >= best_iou:
                    best, best_iou = tid, iou
            if best is None:
                best = self._next_id
                self._next_id += 1
                self._tracks[best] = {"owners": deque(maxlen=self.history)}
            else:
                unmatched.discard(best)
            self._tracks[best].update(box=ph.box, missing=0)
            ph.phone_id = best
        for tid in list(unmatched):
            self._tracks[tid]["missing"] += 1
            if self._tracks[tid]["missing"] > self.max_missing:
                del self._tracks[tid]

    def record_owners(self, assignments: list[Assignment], phones: list[Phone]) -> None:
        owner_of = {a.phone.phone_id: a.person.track_id for a in assignments}
        for ph in phones:
            if ph.phone_id in self._tracks:
                self._tracks[ph.phone_id]["owners"].append(owner_of.get(ph.phone_id))

    def ownership(self, phone_id: int, track_id: int) -> float:
        """Share of recent observations in which this phone was assigned to this student."""
        t = self._tracks.get(phone_id)
        if not t or not t["owners"]:
            return 0.0
        owners = [o for o in t["owners"] if o is not None]
        if not owners:
            return 0.0
        return Counter(owners)[track_id] / len(owners)
