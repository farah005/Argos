"""Argos main loop on the Raspberry Pi.

    python -m argos_edge.main --config ../config/edge.yaml
    python -m argos_edge.main --config ../config/edge.yaml --source video.mp4 --dry-run --save-annotated out.mp4
"""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import yaml

from .association import PhoneTracker, assign_phones
from .camera import FrameSource
from .client import BackendClient
from .clips import ClipRecorder
from .context import TeacherContext
from .features import compute_features, seat_of, to_px_polygon
from .rules import RuleEngine
from .status import live_status

log = logging.getLogger("argos")

LABELS = {"possible_phone_use": "telephone possible", "persistent_head_down": "tete baissee",
          "possible_conflict": "conflit possible"}


def route_event(tier: str, clip_tiers: set[str]) -> str | None:
    """Returns "clip" (sent with a clip), "send" (sent without one) or None (low tier, nothing sent)."""
    if tier not in ("high", "medium"):
        return None
    return "clip" if tier in clip_tiers else "send"


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # The device key is a secret: the repository holds a placeholder, the real key comes from
    # ARGOS_DEVICE_KEY (PC) or is written into the Pi's copy of edge.yaml by install_pi.sh.
    if os.environ.get("ARGOS_DEVICE_KEY"):
        cfg["classroom_device_key"] = os.environ["ARGOS_DEVICE_KEY"]
    return cfg


KP_DRAW = (0, 5, 6, 9, 10)   # nose, shoulders, wrists: the only points used by the rules


def draw(frame, persons, phones, teacher_id, active: dict[int, str],
         kp_conf: float = 0.5, pitch_thr: float | None = None, fps: float | None = None):
    for p in persons:
        color = (255, 200, 0) if p.track_id == teacher_id else (0, 200, 0)
        if p.track_id in active:
            color = (0, 0, 255)
        x1, y1, x2, y2 = map(int, p.box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        if p.kp is not None:
            for i in KP_DRAW:
                if i < len(p.kp) and p.kp[i, 2] >= kp_conf:
                    cv2.circle(frame, (int(p.kp[i, 0]), int(p.kp[i, 1])), 4, (0, 255, 255), -1)
        txt = f"#{p.track_id}"
        if p.h_pitch is not None:
            txt += f" p={p.h_pitch:.2f}"
            if pitch_thr is not None and p.h_pitch < pitch_thr:
                txt += " BAS"
        elif p.ws is None:
            txt += " epaules ?"      # shoulders out of frame or uncertain: h_pitch cannot be computed
        else:
            txt += " nez ?"
        if p.track_id in active:
            txt += f" {active[p.track_id]}"
        cv2.putText(frame, txt, (x1 + 2, max(14, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
    for ph in phones:
        x1, y1, x2, y2 = map(int, ph.box)
        cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 255), 2)
        cv2.putText(frame, f"tel {ph.conf:.2f}", (x1, max(14, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 0, 255), 2)
    status = f"personnes: {len(persons)}  telephones: {len(phones)}"
    if fps is not None:
        status += f"  {fps:.1f} i/s"
    cv2.rectangle(frame, (0, 0), (frame.shape[1], 22), (0, 0, 0), -1)
    cv2.putText(frame, status, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return frame


def run(cfg: dict, args) -> None:
    from .vision import Vision   # late import: loads the models

    source = args.source if args.source is not None else cfg["source"]
    fps = float(cfg["sample_fps"])
    cam = FrameSource(source, fps, cfg)
    vision = Vision(cfg)
    rules = RuleEngine(cfg)
    phones_trk = PhoneTracker(history=cfg["rules"]["phone"]["window"])
    ctx = TeacherContext(cfg)
    clips = ClipRecorder(cfg, fps)
    client = BackendClient(cfg["backend"]["url"], cfg["classroom_device_key"],
                           cfg["backend"].get("timeout_s", 10), dry_run=args.dry_run)
    th = cfg["thresholds"]
    clip_tiers = set(cfg["confidence"]["clip_tiers"])
    seats_px = None
    writer = None
    active: dict[int, tuple[str, float]] = {}
    wall0 = datetime.now(timezone.utc)
    stats = {"frames": 0, "t0": time.monotonic(), "events": 0}

    def iso(t: float) -> str:
        return (wall0 + timedelta(seconds=t)).isoformat()

    try:
        for idx, (t, frame) in enumerate(cam.frames()):
            t_proc = time.monotonic()
            h, w = frame.shape[:2]
            if seats_px is None:
                seats_px = [to_px_polygon(s, w, h) for s in cfg.get("seats") or []]

            raw = vision.persons(frame)
            persons = [compute_features(tid, b, k, th["kp_conf"], th["min_shoulder_px"]) for tid, b, k in raw]
            for p in persons:
                if seats_px:
                    p.in_seat = seat_of(p.bottom_center, seats_px)
            phones = vision.phones(frame)
            phones_trk.update(phones)
            teacher_id, facing = ctx.find(persons, w, h)
            students = [p for p in persons if p.track_id != teacher_id]
            assigns = assign_phones(students, phones)
            phones_trk.record_owners(assigns, phones)

            events = rules.update(idx, t, persons, assigns, phones_trk, teacher_id, facing)
            # live feedback for the "Camera test" screen (always sent: this tells the app whether
            # the Pi camera works, in which case it stops sending the phone camera)
            capacity = 1.0 / max(time.monotonic() - t_proc, 1e-6)   # frames/s the device can process
            alerted = {tid for tid, (_, t_ev) in active.items() if t - t_ev < cfg["phone_camera"]["alert_box_s"]}
            client.status(live_status(persons, phones, assigns, teacher_id, rules.progress(), th["pitch"],
                                      capacity, (w, h), cam.source, alerted))
            clips.push(t, frame, {p.track_id: list(p.box) for p in persons})

            for ev in events:
                stats["events"] += 1
                active[ev.track_id] = (LABELS[ev.category], t)
                payload = {
                    "category": ev.category, "confidence": ev.confidence, "tier": ev.tier,
                    "track_id": ev.track_id, "started_at": iso(ev.started_at), "detected_at": iso(ev.fired_at),
                    "box": ev.box, "teacher_facing_board": facing, "details": ev.details,
                }
                log.info("événement %s piste #%d score=%.2f niveau=%s", ev.category, ev.track_id,
                         ev.confidence, ev.tier)
                route = route_event(ev.tier, clip_tiers)
                if route == "clip":
                    # send with the clip as soon as it is written (pre_s before, post_s after)
                    clips.start(ev.track_id, t, lambda path, pl=payload: client.send(pl, path))
                elif route == "send":
                    client.send(payload)           # without a clip
                # low tier: nothing is sent (only counted)

            if args.save_annotated or args.show:
                active = {k: v for k, v in active.items() if t - v[1] < 5}
                el = time.monotonic() - stats["t0"]
                vis = draw(frame.copy(), persons, phones, teacher_id, {k: v[0] for k, v in active.items()},
                           th["kp_conf"], th["pitch"], (stats["frames"] + 1) / max(el, 1e-6))
                if args.save_annotated:
                    if writer is None:
                        writer = cv2.VideoWriter(args.save_annotated, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
                    writer.write(vis)
                if args.show:
                    cv2.imshow("Argos", vis)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break

            stats["frames"] += 1
            if stats["frames"] % 50 == 0:
                el = time.monotonic() - stats["t0"]
                log.info("%d images, %.1f i/s traitées, %d personnes, %d événements",
                         stats["frames"], stats["frames"] / el, len(persons), stats["events"])
            if args.max_frames and stats["frames"] >= args.max_frames:
                break
    finally:
        clips.flush()
        cam.close()
        if writer:
            writer.release()
        client.close()
        el = time.monotonic() - stats["t0"]
        log.info("fin : %d images en %.1fs (%.1f i/s), %d événements", stats["frames"], el,
                 stats["frames"] / max(el, 1e-6), stats["events"])


def main() -> None:
    ap = argparse.ArgumentParser(description="Argos - boîtier de surveillance de classe")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))
    ap.add_argument("--source", help="remplace la source de la config (0, picamera, phone, auto, fichier, rtsp)")
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien au backend, affiche les événements")
    ap.add_argument("--show", action="store_true", help="affiche la vidéo annotée (écran requis)")
    ap.add_argument("--save-annotated", help="enregistre une vidéo annotée pour le débogage")
    ap.add_argument("--max-frames", type=int, default=0)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(load_config(args.config), args)


if __name__ == "__main__":
    main()
