"""End-to-end API tests: device -> backend -> notification -> review -> clip."""
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ["ARGOS_DB_URL"] = f"sqlite:///{TMP}/test.db"
os.environ["ARGOS_DATA_DIR"] = TMP
os.environ["ARGOS_WEB_DIR"] = f"{TMP}/noweb"

from fastapi.testclient import TestClient  # noqa: E402

from app.db import Classroom, Event, SessionLocal, User, init_db, utcnow  # noqa: E402
from app.main import app, purge_expired  # noqa: E402
from app.security import hash_key, hash_password  # noqa: E402

init_db()
_db = SessionLocal()
room_a = Classroom(name="A", device_key_hash=hash_key("key-a"))
room_b = Classroom(name="B", device_key_hash=hash_key("key-b"))
_db.add_all([room_a, room_b])
_db.commit()
prof = User(username="prof", role="teacher", password_hash=hash_password("motdepasse"))
prof.classrooms = [room_a]
admin = User(username="admin", role="admin", password_hash=hash_password("adminadmin"))
_db.add_all([prof, admin])
_db.commit()
_db.close()

# timestamps relative to now: a fixed date would fall outside the 24 h window of /api/summary
_NOW = utcnow()
META = {"category": "possible_phone_use", "confidence": 0.82, "tier": "high", "track_id": 3,
        "started_at": (_NOW - timedelta(seconds=3)).isoformat(), "detected_at": _NOW.isoformat(),
        "box": [1, 2, 3, 4], "details": {"hits": 12}}


def login(c, u, p):
    r = c.post("/api/auth/login", data={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def post_event(c, key, meta=META, clip=b"\x00\x00\x00\x18ftypmp42"):
    files = {"meta": (None, json.dumps(meta), "application/json")}
    if clip is not None:
        files["clip"] = ("c.mp4", clip, "video/mp4")
    return c.post("/api/device/events", files=files, headers={"X-Device-Key": key})


def test_full_flow():
    with TestClient(app) as c:
        assert c.post("/api/auth/login", data={"username": "prof", "password": "faux"}).status_code == 401
        assert post_event(c, "mauvaise-cle").status_code == 401

        tok = login(c, "prof", "motdepasse")
        h = {"Authorization": f"Bearer {tok}"}
        with c.websocket_connect(f"/ws?token={tok}") as ws:
            r = post_event(c, "key-a")
            assert r.status_code == 201
            msg = json.loads(ws.receive_text())
            assert msg["type"] == "new_event" and msg["notify"] is True
            ev_id = msg["event"]["id"]
            assert msg["event"]["has_clip"]

        # the teacher of classroom A cannot see classroom B
        post_event(c, "key-b", clip=None)
        evs = c.get("/api/events", headers=h).json()
        assert [e["classroom"] for e in evs] == ["A"]

        # clip: header or ?t= (web player), and logged
        assert c.get(f"/api/events/{ev_id}/clip", headers=h).status_code == 200
        assert c.get(f"/api/events/{ev_id}/clip?t={tok}").status_code == 200
        assert c.get(f"/api/events/{ev_id}/clip").status_code == 401

        # dismissal => clip deleted
        r = c.post(f"/api/events/{ev_id}/review", json={"decision": "dismissed"}, headers=h)
        assert r.json()["status"] == "dismissed" and r.json()["has_clip"] is False
        assert c.get(f"/api/events/{ev_id}/clip", headers=h).status_code == 404

        # roles: a teacher cannot read the access log
        assert c.get("/api/admin/access-log", headers=h).status_code == 403
        atok = login(c, "admin", "adminadmin")
        logs = c.get(f"/api/admin/access-log?event_id={ev_id}", headers={"Authorization": f"Bearer {atok}"}).json()
        assert {"view_clip", "review", "delete_clip"} <= {a["action"] for a in logs}
        assert len(c.get("/api/events", headers={"Authorization": f"Bearer {atok}"}).json()) == 2

        s = c.get("/api/summary", headers=h).json()
        assert s and s[0]["category"] == "possible_phone_use"


def test_medium_notifies_by_default_and_bad_ws_token(monkeypatch):
    import app.main as m
    with TestClient(app) as c:
        tok = login(c, "prof", "motdepasse")
        with c.websocket_connect(f"/ws?token={tok}") as ws:
            post_event(c, "key-a", meta={**META, "tier": "medium", "confidence": 0.4}, clip=None)
            assert json.loads(ws.receive_text())["notify"] is True      # decision: every detection
            monkeypatch.setattr(m, "NOTIFY_TIERS", {"high"})              # rule of the original report
            post_event(c, "key-a", meta={**META, "tier": "medium", "confidence": 0.4}, clip=None)
            assert json.loads(ws.receive_text())["notify"] is False
        try:
            with c.websocket_connect("/ws?token=faux") as ws:
                ws.receive_text()
            raise AssertionError("connexion acceptée avec un faux jeton")
        except Exception as e:
            assert "4401" in repr(e) or "Disconnect" in type(e).__name__


def test_dates_returned_in_utc_with_timezone():
    """A time sent as +01:00 (Tunisia) is stored in UTC and returned with +00:00."""
    with TestClient(app) as c:
        local = (_NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat() + "+01:00"
        ev_id = post_event(c, "key-a", meta={**META, "detected_at": local}, clip=None).json()["id"]
        h = {"Authorization": f"Bearer {login(c, 'prof', 'motdepasse')}"}
        ev = next(e for e in c.get("/api/events", headers=h).json() if e["id"] == ev_id)
        assert ev["detected_at"].endswith(("+00:00", "Z"))
        assert ev["detected_at"].startswith(_NOW.strftime("%Y-%m-%dT%H:%M"))


def test_register_teacher_with_enit_email():
    with TestClient(app) as c:
        # rejected addresses: student, other domain, no dot
        for bad in ["amine.trabelsi@etudiant-enit.utm.tn", "amine.trabelsi@gmail.com",
                    "amine@enit.utm.tn", "a.b@enit.utm.tn.evil.com"]:
            r = c.post("/api/auth/register", json={"email": bad, "password": "motdepasse1"})
            assert r.status_code == 422, bad
        assert c.post("/api/auth/register", json={"email": "x.y@enit.utm.tn", "password": "court"}).status_code == 422

        r = c.post("/api/auth/register", json={"email": " Sarra.Ben-Ali@ENIT.utm.tn ", "password": "motdepasse1"})
        assert r.status_code == 201, r.text
        new = r.json()
        assert new["username"] == "sarra.ben-ali@enit.utm.tn"
        assert new["full_name"] == "Sarra Ben-Ali"
        assert new["role"] == "teacher" and new["classrooms"] == []
        # same address (different case): rejected
        assert c.post("/api/auth/register",
                      json={"email": "sarra.ben-ali@enit.utm.tn", "password": "motdepasse1"}).status_code == 409

        # e-mail login, case-insensitive; no alert visible until the admin assigns a classroom
        tok = login(c, "SARRA.BEN-ALI@enit.utm.tn", "motdepasse1")
        h = {"Authorization": f"Bearer {tok}"}
        assert c.get("/api/events", headers=h).json() == []
        assert c.get("/api/me", headers=h).json()["classrooms"] == []
        # a teacher cannot assign a classroom to themselves
        assert c.put(f"/api/admin/users/{new['id']}/classrooms", json={"classroom_ids": [1]},
                     headers=h).status_code == 403

        ah = {"Authorization": f"Bearer {login(c, 'admin', 'adminadmin')}"}
        assert any(u["username"] == new["username"] for u in c.get("/api/admin/users", headers=ah).json())
        room_a = next(r for r in c.get("/api/admin/classrooms", headers=ah).json() if r["name"] == "A")
        assert c.put(f"/api/admin/users/{new['id']}/classrooms", json={"classroom_ids": [999]},
                     headers=ah).status_code == 422
        r = c.put(f"/api/admin/users/{new['id']}/classrooms", json={"classroom_ids": [room_a["id"]]}, headers=ah)
        assert [x["name"] for x in r.json()["classrooms"]] == ["A"]
        assert {e["classroom"] for e in c.get("/api/events", headers=h).json()} == {"A"}
        logs = {a["action"] for a in c.get("/api/admin/access-log", headers=ah).json()}
        assert {"register", "set_classrooms"} <= logs


def test_conflict_category_accepted():
    with TestClient(app) as c:
        r = post_event(c, "key-a", meta={**META, "category": "possible_conflict"}, clip=None)
        assert r.status_code == 201


def test_purge_expired():
    with TestClient(app) as c:
        post_event(c, "key-a")
    db = SessionLocal()
    ev = db.query(Event).filter(Event.clip_path.isnot(None)).order_by(Event.id.desc()).first()
    path = Path(ev.clip_path)
    ev.clip_expires_at = utcnow().replace(year=2020)
    db.commit()
    db.close()
    assert purge_expired() >= 1
    assert not path.exists()


JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_phone_camera_relay():
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {login(c, 'prof', 'motdepasse')}"}
        dev = {"X-Device-Key": "key-a"}
        assert c.post("/api/camera/frame", content=JPEG).status_code == 401
        assert c.get("/api/device/frame", headers={"X-Device-Key": "faux"}).status_code == 401
        # classroom B: not the teacher's
        assert c.post(f"/api/camera/frame?classroom_id={room_b.id}", content=JPEG, headers=h).status_code == 403
        assert c.post("/api/camera/frame", content=b"pas une image", headers=h).status_code == 415

        r = c.post("/api/camera/frame", content=JPEG, headers=h)   # default classroom: their own (A)
        assert r.status_code == 200 and r.json()["classroom_id"] == room_a.id
        seq = r.json()["seq"]
        r = c.get("/api/device/frame", headers=dev)
        assert r.status_code == 200 and r.content == JPEG and r.headers["X-Frame-Seq"] == str(seq)
        assert c.get(f"/api/device/frame?after={seq}", headers=dev).status_code == 204   # nothing new
        assert c.get("/api/device/frame", headers={"X-Device-Key": "key-b"}).status_code == 204


def test_relay_drops_stale_frames():
    from app.main import FrameRelay
    r = FrameRelay()
    r.put(1, JPEG)
    assert r.get(1, 0, max_age_s=60) is not None
    assert r.get(1, 0, max_age_s=-1) is None      # too old: the device does not analyse it


def test_clips_expire_after_24h():
    with TestClient(app) as c:
        eid = post_event(c, "key-a").json()["id"]
    db = SessionLocal()
    try:
        ev = db.get(Event, eid)
        left = (ev.clip_expires_at.replace(tzinfo=None) - utcnow().replace(tzinfo=None)).total_seconds()
        assert 23.9 * 3600 < left <= 24 * 3600
    finally:
        db.close()


def test_live_status_for_camera_test_screen():
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {login(c, 'prof', 'motdepasse')}"}
        st = {"persons": 2, "phones": 1, "fps": 0.8, "people": [{"id": 1, "phone_near": True}]}
        assert c.get("/api/camera/status", headers=h).status_code in (200, 204)
        assert c.post("/api/device/status", json=st, headers={"X-Device-Key": "faux"}).status_code == 401
        assert c.post("/api/device/status", content=b"[1]", headers={"X-Device-Key": "key-a"}).status_code == 422
        assert c.post("/api/device/status", json=st, headers={"X-Device-Key": "key-a"}).status_code == 204
        r = c.get("/api/camera/status", headers=h)
        assert r.status_code == 200 and r.json()["persons"] == 2 and r.json()["age_s"] >= 0
        # the teacher of A cannot read classroom B
        assert c.get(f"/api/camera/status?classroom_id={room_b.id}", headers=h).status_code == 403


def test_phone_frames_refused_when_pi_camera_works():
    with TestClient(app) as c:
        h = {"Authorization": f"Bearer {login(c, 'prof', 'motdepasse')}"}
        dev = {"X-Device-Key": "key-a"}
        c.post("/api/device/status", json={"source": "picamera", "persons": 0, "people": []}, headers=dev)
        r = c.post("/api/camera/frame", content=JPEG, headers=h)
        assert r.status_code == 409 and "boîtier" in r.json()["detail"]
        c.post("/api/device/status", json={"source": "phone", "persons": 0, "people": []}, headers=dev)
        assert c.post("/api/camera/frame", content=JPEG, headers=h).status_code == 200
