"""Video sources: Raspberry Pi camera module (picamera2), USB camera, file, RTSP,
or the phone camera (test mode, frames relayed by the backend).

    source: picamera | 0 | video.mp4 | rtsp://... | phone | auto
    auto = Pi camera module if detected, otherwise the phone camera.

Samples about sample_fps frames per second.
"""
from __future__ import annotations

import logging
import time
from typing import Iterator

import cv2
import numpy as np
import requests

log = logging.getLogger("argos.camera")


def picamera_available() -> bool:
    try:
        from picamera2 import Picamera2
        return bool(Picamera2.global_camera_info())
    except Exception:   # picamera2 missing (PC) or libcamera error
        return False


class PhoneRelay:
    """Fetches from the backend the latest frame sent by the app (hidden "Camera test"
    screen). Nothing is stored: the frame is decoded and analysed in memory."""

    WAIT_LOG_S = 5.0   # "waiting" log message only after 5 s without a frame (logging, not a threshold)

    def __init__(self, url: str, device_key: str, poll_s: float, timeout_s: float, session=None):
        self.url = url.rstrip("/") + "/api/device/frame"
        self.headers = {"X-Device-Key": device_key}
        self.poll_s, self.timeout_s = poll_s, timeout_s
        self.s = session or requests.Session()
        self.seq = 0
        self._last_ok = time.monotonic()
        self._waiting_logged = False

    def read(self) -> tuple[bool, np.ndarray | None]:
        try:
            r = self.s.get(self.url, params={"after": self.seq}, headers=self.headers, timeout=self.timeout_s)
        except requests.RequestException as e:
            log.warning("backend injoignable pour la caméra du téléphone : %s", e)
            time.sleep(1.0)
            return False, None
        if r.status_code != 200:
            if r.status_code == 401:
                log.error("clé de boîtier refusée par le backend (classroom_device_key)")
            elif not self._waiting_logged and time.monotonic() - self._last_ok > self.WAIT_LOG_S:
                log.info("en attente des images du téléphone (Argos > 5 touches sur le titre > Caméra test)")
                self._waiting_logged = True
            time.sleep(self.poll_s)
            return False, None
        frame = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            time.sleep(self.poll_s)
            return False, None
        self.seq = int(r.headers.get("X-Frame-Seq", self.seq + 1))
        self._last_ok = time.monotonic()
        if self._waiting_logged:
            log.info("images du téléphone reçues")
        self._waiting_logged = False
        return True, frame


class FrameSource:
    def __init__(self, source, sample_fps: float, cfg: dict | None = None):
        self.sample_fps = sample_fps
        self.is_file = False
        self._picam = None
        self._cap = None
        self._relay = None
        if source == "auto":
            source = "picamera" if picamera_available() else "phone"
            log.info("source auto : %s", "caméra du Pi" if source == "picamera" else "caméra du téléphone")
        self.source = source
        if source == "picamera":
            from picamera2 import Picamera2  # shipped with Raspberry Pi OS (sudo apt install python3-picamera2)
            self._picam = Picamera2()
            self._picam.configure(self._picam.create_video_configuration(
                main={"size": (1280, 720), "format": "RGB888"}))
            self._picam.start()
        elif source == "phone":
            if cfg is None:
                raise ValueError("source phone : la configuration (backend, clé, phone_camera) est requise")
            pc = cfg["phone_camera"]
            self._relay = PhoneRelay(cfg["backend"]["url"], cfg["classroom_device_key"],
                                     float(pc["poll_s"]), float(pc["timeout_s"]))
        else:
            src = int(source) if isinstance(source, int) or str(source).isdigit() else str(source)
            self._cap = cv2.VideoCapture(src)
            if not self._cap.isOpened():
                raise RuntimeError(f"Impossible d'ouvrir la source vidéo : {source}")
            self.is_file = isinstance(src, str) and not src.startswith(("rtsp://", "http://", "https://"))
            self.src_fps = self._cap.get(cv2.CAP_PROP_FPS) or 25.0

    def read(self) -> tuple[bool, np.ndarray | None]:
        """One read attempt, without waiting for the sampling rate."""
        if self._picam is not None:
            return True, self._picam.capture_array()
        if self._relay is not None:
            return self._relay.read()
        return self._cap.read()

    def frames(self) -> Iterator[tuple[float, np.ndarray]]:
        """Yields (t in seconds since start, BGR frame)."""
        if self.is_file:
            step = max(1, round(self.src_fps / self.sample_fps))
            i = 0
            while True:
                ok, frame = self._cap.read()
                if not ok:
                    return
                if i % step == 0:
                    yield i / self.src_fps, frame
                i += 1
        else:
            yield from self._live(self.read)

    def _live(self, read) -> Iterator[tuple[float, np.ndarray]]:
        t0 = time.monotonic()
        period = 1.0 / self.sample_fps
        next_t = t0
        while True:
            ok, frame = read()
            if not ok:
                time.sleep(0.05)
                continue
            now = time.monotonic()
            if now < next_t:
                continue   # drain the camera buffer to keep the most recent frame
            next_t = max(next_t + period, now)
            yield now - t0, frame

    def close(self) -> None:
        if self._picam is not None:
            self._picam.stop()
        if self._cap is not None:
            self._cap.release()
