"""Ring buffer of frames and writing of short clips (section 2.2).

A clip covers pre_s seconds before and post_s seconds after the event;
the student concerned is framed so the teacher spots them immediately.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

log = logging.getLogger("argos.clips")


@dataclass
class _Pending:
    track_id: int
    end_t: float
    frames: list = field(default_factory=list)
    on_done: Callable[[Path | None], None] | None = None


def purge_old_clips(directory: Path, max_age_h: float, now: float | None = None) -> int:
    """Deletes clips left on the device for more than max_age_h hours (failed upload, hard stop).
    Normally a clip is erased as soon as it reaches the backend."""
    now = time.time() if now is None else now
    n = 0
    for f in directory.glob("*"):
        if f.is_file() and now - f.stat().st_mtime > max_age_h * 3600:
            f.unlink(missing_ok=True)
            n += 1
    if n:
        log.info("%d clip(s) de plus de %.0f h supprimé(s) du boîtier", n, max_age_h)
    return n


def clip_fps(frames: list, default: float) -> float:
    """Playback rate = real frame rate, so the clip lasts the real elapsed time."""
    if len(frames) < 2:
        return default
    span = frames[-1][0] - frames[0][0]
    return default if span <= 0 else min(max((len(frames) - 1) / span, 0.5), 30.0)


class ClipRecorder:
    def __init__(self, cfg: dict, fps: float):
        c = cfg["clips"]
        self.dir = Path(c["dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_age_h = float(c["max_age_h"])
        self.purge_every_s = float(c["purge_every_s"])
        purge_old_clips(self.dir, self.max_age_h)
        self._last_purge = time.monotonic()
        self.pre_s, self.post_s = float(c["pre_s"]), float(c["post_s"])
        self.width = int(c["width"])
        self.fps = fps
        self.h264 = bool(c.get("transcode_h264", True)) and shutil.which("ffmpeg") is not None
        if c.get("transcode_h264", True) and not self.h264:
            log.warning("ffmpeg introuvable : les clips resteront en mp4v, illisibles dans le navigateur "
                        "(installer ffmpeg et rouvrir le terminal)")
        self.buf: deque = deque(maxlen=max(2, int(self.pre_s * fps) + 1))
        self.pending: list[_Pending] = []

    def _small(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        if w <= self.width:
            return frame.copy()
        s = self.width / w
        return cv2.resize(frame, (self.width, int(h * s)))

    def push(self, t: float, frame: np.ndarray, boxes: dict[int, list[float]]) -> None:
        h, w = frame.shape[:2]
        small = self._small(frame)
        scale = small.shape[1] / w
        item = (t, small, {tid: [v * scale for v in b] for tid, b in boxes.items()})
        self.buf.append(item)
        for p in self.pending:
            p.frames.append(item)
        if time.monotonic() - self._last_purge > self.purge_every_s:
            self._last_purge = time.monotonic()
            purge_old_clips(self.dir, self.max_age_h)
        done = [p for p in self.pending if t >= p.end_t]
        self.pending = [p for p in self.pending if t < p.end_t]
        for p in done:
            self._finish(p)

    def start(self, track_id: int, t: float, on_done: Callable[[Path | None], None]) -> None:
        # select by real time: the effective rate can be far below sample_fps
        # (Pi 4, phone camera ~1 frame/s), the buffer would then hold much more than pre_s
        before = [f for f in self.buf if f[0] >= t - self.pre_s]
        self.pending.append(_Pending(track_id, t + self.post_s, before, on_done))

    def flush(self) -> None:
        for p in self.pending:
            self._finish(p)
        self.pending = []

    def _finish(self, p: _Pending) -> None:
        path = None
        try:
            path = self._write(p)
        except Exception:  # a failed clip must never stop monitoring
            log.exception("échec d'écriture du clip")
        if p.on_done:
            p.on_done(path)

    def _write(self, p: _Pending) -> Path | None:
        if not p.frames:
            return None
        h, w = p.frames[0][1].shape[:2]
        raw = self.dir / f"{uuid.uuid4().hex}_raw.mp4"
        vw = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), clip_fps(p.frames, self.fps), (w, h))
        for _, img, boxes in p.frames:
            img = img.copy()
            b = boxes.get(p.track_id)
            if b is not None:
                x1, y1, x2, y2 = map(int, b)
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 165, 255), 2)
            vw.write(img)
        vw.release()
        if not self.h264:
            return raw
        out = raw.with_name(raw.name.replace("_raw", ""))
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(raw), "-c:v", "libx264",
                            "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                            str(out)], capture_output=True)
        if r.returncode == 0:
            raw.unlink(missing_ok=True)
            return out
        log.warning("ffmpeg a échoué, clip gardé en mp4v : %s", r.stderr.decode()[-200:])
        return raw
