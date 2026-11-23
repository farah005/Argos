"""Temporal k-of-W rules, confidence score and tiers (sections 2.4 and 2.5).

A rule fires only if its condition holds in at least k of the last W analysed
frames. More than g consecutive misses (or a lost track) closes the window.
Later detections of the same category for the same student during the
cooldown are merged.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from .association import Assignment, PhoneTracker
from .features import PersonFeatures

PHONE = "possible_phone_use"
POSTURE = "persistent_head_down"
CONFLICT = "possible_conflict"


@dataclass
class CandidateEvent:
    track_id: int
    category: str
    confidence: float
    tier: str
    started_at: float          # seconds (pipeline clock)
    fired_at: float
    frame_idx: int
    box: list[float]
    details: dict = field(default_factory=dict)


@dataclass
class _Window:
    W: int
    k: int
    g: int
    hist: deque = None          # items: None (false) or (t, conf)
    miss: int = 0

    def __post_init__(self):
        self.hist = deque(maxlen=self.W)

    def push(self, t: float, ok: bool, conf: float = 0.0) -> None:
        if ok:
            self.miss = 0
            self.hist.append((t, conf))
        else:
            self.miss += 1
            if self.miss > self.g:
                self.hist.clear()   # too many consecutive misses: window closed
            else:
                self.hist.append(None)

    def hits(self) -> list[tuple[float, float]]:
        return [h for h in self.hist if h is not None]


@dataclass
class _TrackState:
    phone: _Window
    posture: _Window
    last_seen: int = 0
    last_fire: dict = field(default_factory=dict)       # category -> t of the last firing
    head_down_support: deque = field(default_factory=lambda: deque(maxlen=15))
    prev_wrists: dict = field(default_factory=dict)     # side -> (x, y) in the previous frame
    prev_t: float | None = None


@dataclass
class _PairState:
    """Rule 3 (possible conflict): evaluated per pair of students, not per student."""
    win: _Window
    last_seen: int = 0
    last_fire: float | None = None

def tier_of(conf: float, high: float, medium: float) -> str:
    if conf >= high:
        return "high"
    if conf >= medium:
        return "medium"
    return "low"


class RuleEngine:
    def __init__(self, cfg: dict):
        self.th = cfg["thresholds"]
        self.r = cfg["rules"]
        self.conf_cfg = cfg["confidence"]
        self.board_factor = float(cfg.get("context", {}).get("board_sensitivity_factor", 0.8))
        self.tracks: dict[int, _TrackState] = {}
        self.pairs: dict[tuple[int, int], _PairState] = {}

    def _state(self, tid: int) -> _TrackState:
        if tid not in self.tracks:
            p, q = self.r["phone"], self.r["posture"]
            self.tracks[tid] = _TrackState(_Window(p["window"], p["k"], p["gap"]),
                                           _Window(q["window"], q["k"], q["gap"]))
        return self.tracks[tid]

    def progress(self) -> dict[int, dict[str, tuple[int, int]]]:
        """Rule progress per student: {track: {"phone": (evidence, k), "posture": (evidence, k)}}.
        For the test display only; does not change when rules fire."""
        return {tid: {"phone": (len(st.phone.hits()), st.phone.k),
                      "posture": (len(st.posture.hits()), st.posture.k)}
                for tid, st in self.tracks.items()}

    def _phone_evidence(self, a: Assignment, tracker: PhoneTracker) -> bool:
        th = self.th
        if a.phone.conf < th["phone_conf"] or a.d_ph >= th["d_phone"]:
            return False
        rel = a.person.hand_rel.get(a.wrist_side) if a.wrist_side else None
        if rel is None or rel[1] > th["hand_rel_y"]:
            return False   # wrist too low: not a raised or forward hand
        return tracker.ownership(a.phone.phone_id, a.person.track_id) >= th["phone_owner_consistency"]

    def update(self, frame_idx: int, t: float, persons: list[PersonFeatures],
               assignments: list[Assignment], tracker: PhoneTracker,
               teacher_id: int | None = None, teacher_facing_board: bool = False) -> list[CandidateEvent]:
        factor = 1.0
        if teacher_facing_board:
            factor = self.board_factor
        by_track = {a.person.track_id: a for a in assignments}
        events: list[CandidateEvent] = []
        students: list[tuple[PersonFeatures, dict]] = []

        for p in persons:
            if p.track_id == teacher_id:
                continue   # the teacher is never evaluated
            st = self._state(p.track_id)
            st.last_seen = frame_idx
            students.append((p, self._wrist_speeds(st, p, t)))

            head_down = p.h_pitch is not None and p.h_pitch < self.th["pitch"]
            st.head_down_support.append(head_down)

            # Rule 1: possible phone use
            a = by_track.get(p.track_id)
            ok = a is not None and self._phone_evidence(a, tracker)
            st.phone.push(t, ok, a.phone.conf if ok else 0.0)

            # Rule 2: persistent head down (keypoints required above τkp)
            st.posture.push(t, head_down, p.posture_conf if head_down else 0.0)

            for cat, win in ((PHONE, st.phone), (POSTURE, st.posture)):
                ev = self._check(cat, win, st, p, frame_idx, t, factor)
                if ev:
                    if cat == PHONE:
                        hd = list(st.head_down_support)
                        ev.details["head_down_support"] = round(sum(hd) / len(hd), 2) if hd else 0.0
                    events.append(ev)

        if "conflict" in self.r:
            events += self._update_pairs(frame_idx, t, students, factor)

        # Track lost for too long: windows closed, no event
        lost = self.r.get("track_lost_frames", 10)
        for tid in [tid for tid, s in self.tracks.items() if frame_idx - s.last_seen > lost]:
            del self.tracks[tid]
        for key in [k for k, s in self.pairs.items() if frame_idx - s.last_seen > lost]:
            del self.pairs[key]
        return events

    # ------------------------------------------------------------ rule 3: possible conflict
    @staticmethod
    def _wrist_speeds(st: _TrackState, p: PersonFeatures, t: float) -> dict[str, float]:
        """Speed of each wrist, in shoulder widths per second (independent of distance)."""
        speeds = {}
        if p.ws and st.prev_t is not None and 0 < t - st.prev_t <= 1.0:
            dt = t - st.prev_t
            for side, (x, y, _) in p.wrists.items():
                if side in st.prev_wrists:
                    px, py = st.prev_wrists[side]
                    speeds[side] = math.hypot(x - px, y - py) / p.ws / dt
        st.prev_wrists = {s: (x, y) for s, (x, y, _) in p.wrists.items()}
        st.prev_t = t
        return speeds

    def _reaching(self, a: PersonFeatures, speeds: dict, b: PersonFeatures) -> float | None:
        """Confidence of a's wrist arriving fast near b's torso, otherwise None."""
        th = self.th
        for side, v in speeds.items():
            if v < th["conflict_wrist_speed"] or side not in a.wrists:
                continue
            x, y, c = a.wrists[side]
            if math.hypot(x - b.sh_mid[0], y - b.sh_mid[1]) / b.ws <= th["conflict_reach"]:
                return c
        return None

    def _conflict_evidence(self, a, sa, b, sb) -> float | None:
        if a.ws is None or b.ws is None:
            return None
        ws_mean = (a.ws + b.ws) / 2
        if math.hypot(*(a.sh_mid - b.sh_mid)) / ws_mean > self.th["conflict_dist"]:
            return None
        ca, cb = self._reaching(a, sa, b), self._reaching(b, sb, a)
        return max((c for c in (ca, cb) if c is not None), default=None)

    def _update_pairs(self, frame_idx: int, t: float, students: list, factor: float) -> list[CandidateEvent]:
        q = self.r["conflict"]
        out = []
        for i in range(len(students)):
            for j in range(i + 1, len(students)):
                (a, sa), (b, sb) = sorted((students[i], students[j]), key=lambda s: s[0].track_id)
                key = (a.track_id, b.track_id)
                conf = self._conflict_evidence(a, sa, b, sb)
                ps = self.pairs.get(key)
                if ps is None:
                    if conf is None:
                        continue   # a pair's state is created only at its first evidence
                    ps = self.pairs[key] = _PairState(_Window(q["window"], q["k"], q["gap"]))
                ps.last_seen = frame_idx
                ps.win.push(t, conf is not None, conf or 0.0)
                hits = ps.win.hits()
                k_eff = max(1, math.ceil(ps.win.k * factor))
                if len(hits) < k_eff:
                    continue
                last, ps.last_fire = ps.last_fire, t
                if last is not None and t - last < self.r["cooldown_s"]:
                    continue
                mean_conf = sum(c for _, c in hits) / len(hits)
                score = mean_conf * (len(hits) / len(ps.win.hist))
                box = [round(float(v), 1) for v in (min(a.box[0], b.box[0]), min(a.box[1], b.box[1]),
                                                     max(a.box[2], b.box[2]), max(a.box[3], b.box[3]))]
                out.append(CandidateEvent(
                    track_id=a.track_id, category=CONFLICT, confidence=round(score, 3),
                    tier=tier_of(score, self.conf_cfg["high"], self.conf_cfg["medium"]),
                    started_at=hits[0][0], fired_at=t, frame_idx=frame_idx, box=box,
                    details={"tracks": [a.track_id, b.track_id], "hits": len(hits), "window": ps.win.W,
                             "k_effective": k_eff, "mean_detection_conf": round(mean_conf, 3)},
                ))
        return out

    def _check(self, cat: str, win: _Window, st: _TrackState, p: PersonFeatures,
               frame_idx: int, t: float, factor: float) -> CandidateEvent | None:
        hits = win.hits()
        k_eff = max(1, math.ceil(win.k * factor))
        if len(hits) < k_eff:
            return None
        last = st.last_fire.get(cat)
        st.last_fire[cat] = t
        if last is not None and t - last < self.r["cooldown_s"]:
            return None   # same behaviour continuing: merged into the current event
        mean_conf = sum(c for _, c in hits) / len(hits)
        # share of observed frames in the window where the condition held
        # (dividing by W would cap the score at k/W on first firing)
        score = mean_conf * (len(hits) / len(win.hist))
        return CandidateEvent(
            track_id=p.track_id, category=cat, confidence=round(score, 3),
            tier=tier_of(score, self.conf_cfg["high"], self.conf_cfg["medium"]),
            started_at=hits[0][0], fired_at=t, frame_idx=frame_idx,
            box=[round(float(v), 1) for v in p.box],
            details={"hits": len(hits), "window": win.W, "k_effective": k_eff,
                     "mean_detection_conf": round(mean_conf, 3)},
        )
