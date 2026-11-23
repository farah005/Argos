"""Sends events to the backend from a separate thread, with retries.

If the backend is unreachable, events stay queued and are sent again.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from pathlib import Path

import requests

log = logging.getLogger("argos.client")


class BackendClient:
    def __init__(self, url: str, device_key: str, timeout: float = 10, dry_run: bool = False):
        self.url = url.rstrip("/")
        self.key = device_key
        self.timeout = timeout
        self.dry_run = dry_run
        self.q: queue.Queue = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        # "what the device sees" summary: only the latest one matters, never queued
        self._status: dict | None = None
        self._status_ready = threading.Event()
        self._ts = threading.Thread(target=self._status_run, daemon=True)
        self._ts.start()

    def status(self, summary: dict) -> None:
        self._status = summary
        self._status_ready.set()

    def _status_run(self) -> None:
        while not self._stop.is_set():
            if not self._status_ready.wait(timeout=0.5):
                continue
            self._status_ready.clear()
            summary, self._status = self._status, None
            if summary is None or self.dry_run:
                continue
            try:
                requests.post(f"{self.url}/api/device/status", json=summary,
                              headers={"X-Device-Key": self.key}, timeout=2)
            except requests.RequestException as e:
                log.debug("résumé non envoyé : %s", e)

    def send(self, event: dict, clip: Path | None = None) -> None:
        try:
            self.q.put_nowait((event, clip, 0))
        except queue.Full:
            log.error("file d'envoi pleine, événement perdu")

    def _post(self, event: dict, clip: Path | None) -> None:
        files = {"meta": (None, json.dumps(event), "application/json")}
        fh = None
        if clip and clip.exists():
            fh = open(clip, "rb")
            files["clip"] = (clip.name, fh, "video/mp4")
        try:
            r = requests.post(f"{self.url}/api/device/events", files=files,
                              headers={"X-Device-Key": self.key}, timeout=self.timeout)
            r.raise_for_status()
        finally:
            if fh:
                fh.close()
        if clip and clip.exists():
            clip.unlink()   # the clip does not stay on the device once transmitted

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event, clip, tries = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.dry_run:
                log.info("[dry-run] événement : %s (clip=%s)", event, clip)
                continue
            try:
                self._post(event, clip)
                log.info("événement envoyé : %s %.2f", event["category"], event["confidence"])
            except Exception as e:
                wait = min(60, 2 ** min(tries, 6))
                log.warning("envoi échoué (%s), nouvel essai dans %ss", e, wait)
                time.sleep(wait)
                self.q.put((event, clip, tries + 1))

    def close(self, wait_s: float = 10) -> None:
        end = time.time() + wait_s
        while not self.q.empty() and time.time() < end:
            time.sleep(0.2)
        self._stop.set()
