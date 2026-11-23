"""Detection, tracking and pose with Ultralytics (section 2.2).

YOLO11-Pose detects people AND their keypoints: one model is enough for
people. ByteTrack provides temporary IDs (session only, never linked to the
student's identity). YOLO11 is used only for COCO class 67 "cell phone".
"""
from __future__ import annotations

import numpy as np
from ultralytics import YOLO

from .association import Phone

COCO_PERSON, COCO_PHONE = 0, 67


class Vision:
    def __init__(self, cfg: dict):
        m = cfg["models"]
        self.imgsz = int(cfg.get("imgsz", 640))
        self.device = m.get("device", "cpu")
        self.person_conf = float(m.get("person_conf", 0.3))
        self.phone_conf = float(m.get("phone_det_conf", 0.2))
        self.phone_every = max(1, int(m.get("phone_every_n", 1)))
        self.pose = YOLO(m["pose"], task="pose")
        self.det = YOLO(m["detect"], task="detect")
        self._n = 0
        self._last_phones: list[Phone] = []

    def persons(self, frame: np.ndarray) -> list[tuple[int, np.ndarray, np.ndarray]]:
        """Returns [(track_id, box xyxy, keypoints (17,3))]."""
        r = self.pose.track(frame, persist=True, tracker="bytetrack.yaml", classes=[COCO_PERSON],
                            conf=self.person_conf, imgsz=self.imgsz, device=self.device, verbose=False)[0]
        if r.boxes is None or r.boxes.id is None or r.keypoints is None:
            return []
        ids = r.boxes.id.int().cpu().numpy()
        boxes = r.boxes.xyxy.cpu().numpy()
        kps = r.keypoints.data.cpu().numpy()
        if kps.shape[-1] == 2:   # model without per-point confidence: treat every point as visible
            kps = np.concatenate([kps, np.ones(kps.shape[:2] + (1,))], axis=-1)
        return [(int(i), b, k) for i, b, k in zip(ids, boxes, kps)]

    def phones(self, frame: np.ndarray) -> list[Phone]:
        self._n += 1
        if (self._n - 1) % self.phone_every != 0:
            return [Phone(p.box.copy(), p.conf) for p in self._last_phones]
        r = self.det.predict(frame, classes=[COCO_PHONE], conf=self.phone_conf, imgsz=self.imgsz,
                             device=self.device, verbose=False)[0]
        out = []
        if r.boxes is not None:
            for b, c in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
                out.append(Phone(b, float(c)))
        self._last_phones = out
        return out
