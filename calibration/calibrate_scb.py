"""Calibration of the first thresholds on SCB-Dataset3 (or any image set in YOLO format).

For each annotated box (e.g. "bow head", "using phone", "reading", "writing"),
the matching person detected by YOLO11-Pose is found, h_pitch, the phone-wrist
distance d_ph and the hand positions are computed, then the class distributions
are compared and τpitch and τd are proposed.

Expected format (YOLO):
    dataset/images/xxx.jpg
    dataset/labels/xxx.txt        # normalised "class cx cy w h"
    dataset/data.yaml             # names: [...]   (or --names "a,b,c")

Example:
    python calibrate_scb.py --root SCB-Dataset3/train --data SCB-Dataset3/data.yaml \
        --head-pos "bow head" --head-neg "reading,writing,hand-raising" \
        --phone-pos "using phone" --max-images 800 --out resultats
Run first without --head-pos to print the exact class names.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "edge"))
from argos_edge.association import Phone, assign_phones  # noqa: E402
from argos_edge.features import compute_features  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp"}


def load_names(args) -> list[str]:
    if args.names:
        return [n.strip() for n in args.names.split(",")]
    with open(args.data, encoding="utf-8") as f:
        names = yaml.safe_load(f)["names"]
    return [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)


def find_pairs(root: Path):
    imgs = root / "images" if (root / "images").is_dir() else root
    labels = root / "labels" if (root / "labels").is_dir() else root
    for p in sorted(imgs.rglob("*")):
        if p.suffix.lower() in IMG_EXT:
            lab = labels / p.relative_to(imgs).with_suffix(".txt")
            if lab.exists():
                yield p, lab


def iou(a, b) -> float:
    x1, y1, x2, y2 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def ioa(gt, det) -> float:
    """Share of the annotated box covered by the detection."""
    x1, y1, x2, y2 = max(gt[0], det[0]), max(gt[1], det[1]), min(gt[2], det[2]), min(gt[3], det[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    a = (gt[2] - gt[0]) * (gt[3] - gt[1])
    return inter / a if a > 0 else 0.0


def rate_below(vals: np.ndarray, t: float) -> float:
    """Share of values strictly below the threshold (h_pitch < τ => head down)."""
    return float(np.mean(vals < t)) if len(vals) else float("nan")


def max_threshold_for_fpr(neg: np.ndarray, fpr: float) -> float | None:
    """Largest τ such that at most `fpr` of the negatives (reading, writing...) fall below τ.
    Useful when the dataset has no "head down" class (e.g. SCBehavior)."""
    if not len(neg):
        return None
    return float(np.quantile(neg, fpr, method="lower"))


def best_threshold(pos: np.ndarray, neg: np.ndarray, lower_is_positive: bool):
    """Threshold that maximises Youden's J (TPR - FPR)."""
    if len(pos) == 0 or len(neg) == 0:
        return None, None, None, None
    cands = np.unique(np.concatenate([pos, neg]))
    best = (-1, None, None, None)
    for t in cands:
        tpr = np.mean(pos < t) if lower_is_positive else np.mean(pos > t)
        fpr = np.mean(neg < t) if lower_is_positive else np.mean(neg > t)
        if tpr - fpr > best[0]:
            best = (tpr - fpr, float(t), float(tpr), float(fpr))
    return best[1], best[2], best[3], best[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--data", help="data.yaml contenant names")
    ap.add_argument("--names", help="noms des classes séparés par des virgules (si pas de data.yaml)")
    ap.add_argument("--head-pos", default="", help="classes tête baissée (positives)")
    ap.add_argument("--head-neg", default="", help="classes à ne pas confondre (lecture, écriture…)")
    ap.add_argument("--phone-pos", default="", help="classes téléphone")
    ap.add_argument("--pose", default="yolo11n-pose.pt")
    ap.add_argument("--detect", default="yolo11n.pt")
    ap.add_argument("--imgsz", type=int, default=960, help="élèves petits : 960 aide pour SCB")
    ap.add_argument("--kp-conf", type=float, default=0.5)
    ap.add_argument("--min-match", type=float, default=0.5, help="IoA minimale annotation/détection")
    ap.add_argument("--max-images", type=int, default=0)
    ap.add_argument("--out", default="calibration_out")
    ap.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "config" / "edge.yaml"),
                    help="pour lire le τpitch actuel")
    ap.add_argument("--max-fpr", type=float, default=0.10,
                    help="sans classe positive : part maximale de lecture/écriture sous τpitch")
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        current_pitch = float(yaml.safe_load(f)["thresholds"]["pitch"])

    names = load_names(args)
    print("Classes :", {i: n for i, n in enumerate(names)})
    split = lambda s: {x.strip() for x in s.split(",") if x.strip()}  # noqa: E731
    head_pos, head_neg, phone_pos = split(args.head_pos), split(args.head_neg), split(args.phone_pos)
    unknown = (head_pos | head_neg | phone_pos) - set(names)
    if unknown:
        sys.exit(f"Classes inconnues : {unknown}. Utilisez exactement les noms affichés ci-dessus.")

    from ultralytics import YOLO
    pose, det = YOLO(args.pose), YOLO(args.detect)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    pairs = list(find_pairs(Path(args.root)))
    if args.max_images:
        pairs = pairs[: args.max_images]
    print(f"{len(pairs)} images")

    for n, (img_p, lab_p) in enumerate(pairs, 1):
        img = cv2.imread(str(img_p))
        if img is None:
            continue
        h, w = img.shape[:2]
        r = pose.predict(img, classes=[0], conf=0.25, imgsz=args.imgsz, verbose=False)[0]
        persons = []
        if r.boxes is not None and r.keypoints is not None:
            for i, (b, k) in enumerate(zip(r.boxes.xyxy.cpu().numpy(), r.keypoints.data.cpu().numpy())):
                persons.append(compute_features(i, b, k, args.kp_conf))
        rp = det.predict(img, classes=[67], conf=0.15, imgsz=args.imgsz, verbose=False)[0]
        phones = [Phone(b, float(c)) for b, c in zip(rp.boxes.xyxy.cpu().numpy(), rp.boxes.conf.cpu().numpy())]
        asg = {a.person.track_id: a for a in assign_phones(persons, phones)}

        for line in lab_p.read_text().split("\n"):
            parts = line.split()
            if len(parts) < 5:
                continue
            c = int(float(parts[0]))
            cx, cy, bw, bh = map(float, parts[1:5])
            gt = [(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h]
            match = max(persons, key=lambda p: (ioa(gt, p.box), iou(gt, p.box)), default=None)
            matched = match is not None and ioa(gt, match.box) >= args.min_match
            a = asg.get(match.track_id) if matched else None
            hand_y = min((v[1] for v in match.hand_rel.values()), default=None) if matched else None
            rows.append({
                "image": img_p.name, "class": names[c] if c < len(names) else str(c), "matched": int(matched),
                "h_pitch": match.h_pitch if matched else None,
                "h_yaw": match.h_yaw if matched else None,
                "posture_conf": match.posture_conf if matched else None,
                "shoulder_px": match.ws if matched else None,
                "phone_conf": a.phone.conf if a else None,
                "d_phone": a.d_ph if a else None,
                "hand_rel_y": hand_y,
            })
        if n % 50 == 0:
            print(f"  {n}/{len(pairs)}")

    csv_p = out / "features.csv"
    with open(csv_p, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["image"])
        wr.writeheader()
        wr.writerows(rows)
    print(f"\nCaractéristiques écrites dans {csv_p} ({len(rows)} annotations)")
    report(rows, head_pos, head_neg, phone_pos, out, current_pitch, args.max_fpr)


def _vals(rows, classes, key):
    return np.array([r[key] for r in rows if r["class"] in classes and r[key] is not None], dtype=float)


def report(rows, head_pos, head_neg, phone_pos, out: Path, current_pitch: float = 0.45, max_fpr: float = 0.10):
    lines = ["# Résultats de calibration Argos", ""]
    lines.append(f"| Classe | annotations | personne retrouvée | h_pitch mesuré | h_pitch médian | "
                 f"sous τpitch={current_pitch:.2f} | abs(h_yaw) médian | tél. détecté | d_phone médian |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in sorted({r["class"] for r in rows}):
        rs = [r for r in rows if r["class"] == c]
        hp = _vals(rs, {c}, "h_pitch")
        hy = np.abs(_vals(rs, {c}, "h_yaw"))
        dp = _vals(rs, {c}, "d_phone")
        ph_rate = np.mean([r["phone_conf"] is not None for r in rs if r["matched"]] or [0])
        med = lambda v: np.median(v) if len(v) else float("nan")  # noqa: E731
        lines.append(f"| {c} | {len(rs)} | {np.mean([r['matched'] for r in rs]):.0%} | {len(hp)} | "
                     f"{med(hp):.2f} | {rate_below(hp, current_pitch):.0%} | {med(hy):.2f} | "
                     f"{ph_rate:.0%} | {med(dp):.2f} |")

    if head_neg and not head_pos:
        neg = _vals(rows, head_neg, "h_pitch")
        t = max_threshold_for_fpr(neg, max_fpr)
        lines += ["", "## τpitch : fausses alarmes sur des activités normales (pas de classe « tête baissée »)",
                  f"- {sorted(head_neg)} : n={len(neg)} ; au τpitch actuel ({current_pitch:.2f}), "
                  f"**{rate_below(neg, current_pitch):.0%}** passent pour « tête baissée » image par image"]
        if t is not None:
            lines.append(f"- pour rester sous {max_fpr:.0%} image par image : **τpitch ≤ {t:.2f}** "
                         "(borne haute ; la borne basse viendra d'une classe « tête baissée », ex. SCB-Dataset)")
        plot_hist(np.array([]), neg, current_pitch, "h_pitch", out / "h_pitch.png", [], sorted(head_neg))

    if head_pos and head_neg:
        pos, neg = _vals(rows, head_pos, "h_pitch"), _vals(rows, head_neg, "h_pitch")
        t, tpr, fpr, j = best_threshold(pos, neg, lower_is_positive=True)
        lines += ["", "## τpitch (tête baissée)",
                  f"- positifs {sorted(head_pos)} : n={len(pos)}, négatifs {sorted(head_neg)} : n={len(neg)}"]
        if t is not None:
            lines.append(f"- seuil proposé **τpitch = {t:.2f}** (détection {tpr:.0%}, fausses alarmes {fpr:.0%} "
                         f"par image ; les règles k-sur-W réduiront encore les fausses alarmes)")
        plot_hist(pos, neg, t, "h_pitch", out / "h_pitch.png", sorted(head_pos), sorted(head_neg))

    if phone_pos:
        pos = _vals(rows, phone_pos, "d_phone")
        others = {r["class"] for r in rows} - phone_pos
        neg = _vals(rows, others, "d_phone")
        lines += ["", "## τd (distance téléphone-poignet)"]
        if len(pos):
            lines.append(f"- {sorted(phone_pos)} : téléphone associé dans {len(pos)} cas, "
                         f"d_phone médian {np.median(pos):.2f}, 90e centile {np.percentile(pos, 90):.2f}")
            lines.append(f"- proposition : **τd ≈ {np.percentile(pos, 90):.2f}** (couvre 90 % des cas positifs)")
            pc = _vals(rows, phone_pos, "phone_conf")
            lines.append(f"- confiance du téléphone : médiane {np.median(pc):.2f} → à comparer avec "
                         "thresholds.phone_conf et confidence.high")
        else:
            lines.append("- aucun téléphone associé : essayez --imgsz 1280 ou un détecteur plus grand (yolo11s.pt)")
        if len(neg):
            lines.append(f"- autres classes : téléphone associé dans {len(neg)} cas (fausses pistes possibles)")
        plot_hist(pos, neg, None, "d_phone", out / "d_phone.png", sorted(phone_pos), ["autres"])

    txt = "\n".join(lines)
    (out / "rapport.md").write_text(txt, encoding="utf-8")
    print("\n" + txt)


def plot_hist(pos, neg, thr, key, path, pos_lbl, neg_lbl):
    if not len(pos) and not len(neg):
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(7, 4))
    allv = np.concatenate([pos, neg]) if len(neg) else pos
    bins = np.linspace(np.percentile(allv, 1), np.percentile(allv, 99), 40)
    if len(neg):
        ax.hist(neg, bins=bins, alpha=0.6, density=True, label=", ".join(neg_lbl), color="#888888")
    if len(pos):
        ax.hist(pos, bins=bins, alpha=0.7, density=True, label=", ".join(pos_lbl), color="#d9534f")
    if thr is not None:
        ax.axvline(thr, color="k", ls="--", label=f"seuil {thr:.2f}")
    ax.set_xlabel(key)
    ax.set_ylabel("densité")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    main()
