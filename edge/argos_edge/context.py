"""Teacher context (section 2.5): who is the teacher, and are they facing the board?

The teacher's orientation is used ONLY to adjust sensitivity, never as evidence
of a behaviour, and monitoring is never switched off.
"""
from __future__ import annotations

import cv2
import numpy as np

from .features import PersonFeatures, to_px_polygon


class TeacherContext:
    def __init__(self, cfg: dict):
        c = cfg.get("context", {})
        self.enabled = bool(c.get("enabled")) and len(c.get("teacher_zone") or []) >= 3
        self.zone_norm = c.get("teacher_zone") or []
        self.camera_position = c.get("camera_position", "front")
        self._zone_px = None
        self._size = None

    def _zone(self, w: int, h: int):
        if self._size != (w, h):
            self._zone_px = to_px_polygon(self.zone_norm, w, h)
            self._size = (w, h)
        return self._zone_px

    def find(self, persons: list[PersonFeatures], w: int, h: int) -> tuple[int | None, bool]:
        """Returns (teacher track_id, teacher facing the board)."""
        if not self.enabled:
            return None, False
        zone = self._zone(w, h)
        teacher = None
        for p in persons:
            pt = p.bottom_center
            if cv2.pointPolygonTest(zone, (float(pt[0]), float(pt[1])), False) >= 0:
                if teacher is None or (p.box[3] - p.box[1]) > (teacher.box[3] - teacher.box[1]):
                    teacher = p
        if teacher is None:
            return None, False
        shoulders_seen = teacher.ws is not None
        if self.camera_position == "back":
            # camera at the back: facing the board = back to the camera (shoulders visible, nose not)
            facing = shoulders_seen and not teacher.nose_visible
        else:
            # camera above the board: facing the board = facing the camera
            facing = shoulders_seen and teacher.nose_visible and (
                teacher.h_yaw is None or abs(teacher.h_yaw) < 0.35)
        return teacher.track_id, facing
