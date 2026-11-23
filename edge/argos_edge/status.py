"""Summary of what the device sees, for the app's hidden "Camera test" screen.

No images: number of people and phones, boxes in normalised coordinates (0..1, to draw
on the phone preview) and, for each track (session ID, never linked to identity), the
progress of each rule. "alert" = red box in the app.
"""
from __future__ import annotations

from .association import Assignment, Phone
from .features import PersonFeatures


def _norm(box, w: int, h: int) -> list[float]:
    x1, y1, x2, y2 = (float(v) for v in box)
    return [round(min(max(x1 / w, 0), 1), 3), round(min(max(y1 / h, 0), 1), 3),
            round(min(max(x2 / w, 0), 1), 3), round(min(max(y2 / h, 0), 1), 3)]


def live_status(persons: list[PersonFeatures], phones: list[Phone], assigns: list[Assignment],
                teacher_id: int | None, progress: dict, pitch_thr: float, fps: float,
                size: tuple[int, int], source: str, alerted: set[int] = frozenset()) -> dict:
    w, h = size
    in_hand = {a.person.track_id for a in assigns}
    people = []
    for p in persons:
        if p.track_id == teacher_id:
            continue
        pr = progress.get(p.track_id, {})
        head_down = p.h_pitch is not None and p.h_pitch < pitch_thr
        phone_near = p.track_id in in_hand
        people.append({
            "id": p.track_id,
            "box": _norm(p.box, w, h),
            "head_down": head_down,
            "head_visible": p.h_pitch is not None,
            "phone_near": phone_near,
            "alert": head_down or phone_near or p.track_id in alerted,
            "phone_progress": list(pr.get("phone", (0, 0))),
            "head_down_progress": list(pr.get("posture", (0, 0))),
        })
    return {"source": source, "persons": len(people), "phones": len(phones),
            "phone_boxes": [_norm(ph.box, w, h) for ph in phones],
            "teacher": teacher_id is not None, "fps": round(fps, 2), "people": people}
