"""Camera and detection test on the device, before starting the service.

    python -m argos_edge.camtest                      # camera from the config (picamera on the Pi)
    python -m argos_edge.camtest --source 0           # USB webcam
    python -m argos_edge.camtest --source phone       # phone camera (hidden "Camera test" screen)
    python -m argos_edge.camtest --image photo.jpg    # no camera: only checks detection

Stop the service first (it holds the camera): sudo systemctl stop argos-edge
Writes camtest_brut.jpg (raw camera frame) and camtest_detection.jpg (annotated).
Exit codes: 0 OK, 2 no camera, 3 unusable frames, 4 detection error.
"""
from __future__ import annotations

import argparse
import glob
import sys
import time
from pathlib import Path

import cv2
import numpy as np

from .features import compute_features
from .main import draw, load_config

def frame_quality(frame: np.ndarray | None, dark_mean: float, flat_std: float) -> tuple[bool, str]:
    """(usable, explanation) for a BGR frame. Thresholds: camtest section of config/edge.yaml."""
    if frame is None or frame.size == 0:
        return False, "aucune image reçue"
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    mean, std = float(gray.mean()), float(gray.std())
    if mean < dark_mean:
        return False, f"image noire (luminosité {mean:.0f}/255) : cache sur l'objectif ou pièce sombre ?"
    if std < flat_std:
        return False, f"image uniforme (contraste {std:.1f}) : capteur défectueux ou objectif masqué ?"
    return True, f"luminosité {mean:.0f}/255, contraste {std:.0f}"


def frozen(frames: list[np.ndarray]) -> bool:
    """True if all frames are identical (frozen stream)."""
    return len(frames) >= 2 and all(np.array_equal(frames[0], f) for f in frames[1:])


def list_cameras() -> list[str]:
    found = []
    try:
        from picamera2 import Picamera2
        for i, c in enumerate(Picamera2.global_camera_info()):
            found.append(f"picamera {i} : {c.get('Model', '?')}")
    except ImportError:
        pass
    except Exception as e:   # libcamera present but failing
        found.append(f"picamera : erreur {e}")
    # USB cameras: /dev/video0..9 (on the Pi, /dev/video10+ are internal codecs)
    for dev in sorted(glob.glob("/dev/video[0-9]")):
        cap = cv2.VideoCapture(dev)
        if cap.isOpened() and cap.read()[0]:
            found.append(f"USB {dev[-1]} : {dev}")
        cap.release()
    return found


def grab(source, n: int, cfg: dict, wait_s: float) -> list[np.ndarray]:
    """Up to n frames, waiting at most wait_s seconds (the phone camera may be absent)."""
    from .camera import FrameSource
    cam = FrameSource(source, sample_fps=30, cfg=cfg)
    out = []
    end = time.monotonic() + wait_s
    try:
        while len(out) < n and time.monotonic() < end:
            ok, frame = cam.read()
            if ok and frame is not None:
                out.append(frame.copy())
            else:
                time.sleep(0.05)
    finally:
        cam.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Test caméra + détection Argos")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[2] / "config" / "edge.yaml"))
    ap.add_argument("--source", help="picamera, 0 (USB), fichier vidéo… (défaut : celle de la config)")
    ap.add_argument("--image", help="tester la détection sur une image au lieu de la caméra")
    ap.add_argument("--frames", type=int, default=5, help="images analysées pour la mesure de vitesse")
    ap.add_argument("--wait", type=float, default=30, help="attente maximale des images (s)")
    ap.add_argument("--out", default=".", help="dossier des images produites")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    if args.image:
        img = cv2.imread(args.image)
        if img is None:
            print(f"❌ image illisible : {args.image}")
            return 3
        frames = [img] * args.frames
        print(f"Mode image : {args.image} ({img.shape[1]}x{img.shape[0]})")
    else:
        source = args.source if args.source is not None else cfg["source"]
        print("1) Caméras détectées :")
        cams = list_cameras()
        for c in cams:
            print("   -", c)
        if source in ("phone", "auto"):
            from .camera import picamera_available
            if source == "phone" or not picamera_available():
                source = "phone"
                print("   → caméra du téléphone : ouvrir Argos, toucher 5 fois le titre, « Caméra test »")
                cams = cams or ["phone"]
        if not cams:
            print("   ❌ aucune caméra. Module : nappe dans le connecteur CAMERA, contacts vers le HDMI,"
                  " Pi éteint pendant le branchement. USB : brancher puis --source 0.")
            return 2
        print(f"2) Capture de {args.frames} images (source {source}) :")
        try:
            frames = grab(source, max(2, args.frames), cfg, args.wait)
        except Exception as e:
            print(f"   ❌ ouverture impossible : {e}")
            print("   (si le service tourne, il occupe la caméra : sudo systemctl stop argos-edge)")
            return 2
        if len(frames) < 2:
            print(f"   ❌ moins de 2 images reçues en {args.wait:.0f} s")
            return 2
        ct = cfg["camtest"]
        ok, why = frame_quality(frames[-1], ct["dark_mean"], ct["flat_std"])
        print(f"   {'✅' if ok else '❌'} {frames[-1].shape[1]}x{frames[-1].shape[0]}, {why}")
        if frozen(frames):
            print("   ❌ toutes les images sont identiques : flux figé")
            return 3
        cv2.imwrite(str(out / "camtest_brut.jpg"), frames[-1])
        if not ok:
            return 3

    print("3) Détection Argos :")
    try:
        from .vision import Vision
        t = time.monotonic()
        vision = Vision(cfg)
        print(f"   modèles chargés en {time.monotonic() - t:.1f} s ({cfg['models']['pose']}, "
              f"{cfg['models']['detect']}, imgsz {vision.imgsz})")
        th = cfg["thresholds"]
        times = []
        for i, frame in enumerate(frames):
            t = time.monotonic()
            raw = vision.persons(frame)
            phones = vision.phones(frame)
            times.append(time.monotonic() - t)
        persons = [compute_features(tid, b, k, th["kp_conf"], th["min_shoulder_px"]) for tid, b, k in raw]
    except Exception as e:
        print(f"   ❌ {type(e).__name__}: {e}")
        return 4
    per = float(np.median(times[1:] if len(times) > 1 else times))   # the first frame includes warm-up
    print(f"   personnes : {len(persons)}, téléphones : {len(phones)}")
    for p in persons:
        pitch = f"h_pitch={p.h_pitch:.2f}" if p.h_pitch is not None else "h_pitch non calculable"
        low = " (sous le seuil : tête baissée)" if p.h_pitch is not None and p.h_pitch < th["pitch"] else ""
        print(f"   - personne #{p.track_id} : {pitch}{low}")
    for ph in phones:
        print(f"   - téléphone, confiance {ph.conf:.2f}")
    print(f"   vitesse : {per * 1000:.0f} ms par image → {1 / per:.1f} i/s possibles "
          f"(la config en demande {cfg['sample_fps']})")
    annotated = draw(frames[-1].copy(), persons, phones, None, {}, th["kp_conf"], th["pitch"], 1 / per)
    cv2.imwrite(str(out / "camtest_detection.jpg"), annotated)
    print(f"✅ terminé. Images : {out / 'camtest_detection.jpg'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
