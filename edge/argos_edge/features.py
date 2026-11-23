"""Feature extraction from COCO keypoints (section 2.3 of the report).

All distances are divided by the shoulder width ws so that they do not
depend on the student-camera distance.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

# COCO-17 indices (only the upper body is used)
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SH, R_SH, L_EL, R_EL, L_WR, R_WR = 5, 6, 7, 8, 9, 10


@dataclass
class PersonFeatures:
    track_id: int
    box: np.ndarray                     # xyxy in pixels
    ws: float | None = None             # shoulder width (px)
    sh_mid: np.ndarray | None = None    # shoulder midpoint
    h_pitch: float | None = None        # (y_shoulders - y_nose) / ws; small => head down
    h_yaw: float | None = None          # (x_nose - x_ears) / ear width
    posture_conf: float = 0.0           # min(conf nose, shoulders) if valid
    wrists: dict = field(default_factory=dict)    # "L"/"R" -> (x, y, conf) valid wrists
    hand_rel: dict = field(default_factory=dict)  # "L"/"R" -> (dx, dy) / ws relative to the shoulders
    nose_visible: bool = False
    in_seat: int | None = None          # seat index (if seats are configured)
    kp: np.ndarray | None = None        # raw keypoints (17, 3), for the debug overlay

    @property
    def center(self) -> np.ndarray:
        x1, y1, x2, y2 = self.box
        return np.array([(x1 + x2) / 2, (y1 + y2) / 2])

    @property
    def bottom_center(self) -> np.ndarray:
        x1, _, x2, y2 = self.box
        return np.array([(x1 + x2) / 2, y2])


def _ok(kp: np.ndarray, i: int, thr: float) -> bool:
    return kp.shape[0] > i and kp[i, 2] >= thr


def compute_features(track_id: int, box: np.ndarray, kp: np.ndarray,
                     kp_conf: float, min_shoulder_px: float = 12.0) -> PersonFeatures:
    """kp: array (17, 3) = (x, y, confidence)."""
    f = PersonFeatures(track_id=track_id, box=np.asarray(box, dtype=float), kp=np.asarray(kp, dtype=float))
    f.nose_visible = _ok(kp, NOSE, kp_conf)

    if not (_ok(kp, L_SH, kp_conf) and _ok(kp, R_SH, kp_conf)):
        return f  # without both shoulders, no normalised measure is reliable
    ls, rs = kp[L_SH, :2], kp[R_SH, :2]
    ws = float(np.linalg.norm(ls - rs))
    if ws < min_shoulder_px:
        return f
    f.ws = ws
    f.sh_mid = (ls + rs) / 2

    if f.nose_visible:
        f.h_pitch = float((f.sh_mid[1] - kp[NOSE, 1]) / ws)
        f.posture_conf = float(min(kp[NOSE, 2], kp[L_SH, 2], kp[R_SH, 2]))
        if _ok(kp, L_EAR, kp_conf) and _ok(kp, R_EAR, kp_conf):
            ear_w = float(np.linalg.norm(kp[L_EAR, :2] - kp[R_EAR, :2]))
            if ear_w > 1e-3:
                ears_mid_x = (kp[L_EAR, 0] + kp[R_EAR, 0]) / 2
                f.h_yaw = float((kp[NOSE, 0] - ears_mid_x) / ear_w)

    for side, idx in (("L", L_WR), ("R", R_WR)):
        if _ok(kp, idx, kp_conf):
            x, y, c = kp[idx]
            f.wrists[side] = (float(x), float(y), float(c))
            f.hand_rel[side] = (float((x - f.sh_mid[0]) / ws), float((y - f.sh_mid[1]) / ws))
    return f


def phone_distance(f: PersonFeatures, phone_center: np.ndarray) -> tuple[float, str | None]:
    """d_ph = min over wrists of ||wrist - phone centre|| / ws."""
    if f.ws is None or not f.wrists:
        return float("inf"), None
    best, best_side = float("inf"), None
    for side, (x, y, _) in f.wrists.items():
        d = float(np.hypot(x - phone_center[0], y - phone_center[1]) / f.ws)
        if d < best:
            best, best_side = d, side
    return best, best_side


def seat_of(point: np.ndarray, seats_px: list[np.ndarray]) -> int | None:
    for i, poly in enumerate(seats_px):
        if cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), False) >= 0:
            return i
    return None


def to_px_polygon(norm_poly, w: int, h: int) -> np.ndarray:
    return np.array([[x * w, y * h] for x, y in norm_poly], dtype=np.float32).reshape(-1, 1, 2)
