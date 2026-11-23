"""Argos API: receives events from the device, notifies the teacher in real time,
handles review (confirm / dismiss), clip access and automatic clip deletion.

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile,
                     WebSocket, WebSocketDisconnect)
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import AccessLog, Classroom, Event, SessionLocal, User, init_db, utcnow
from .security import (current_device, current_user, current_user_or_query, get_db, hash_key,
                       hash_password, make_token, require_admin, user_from_token, verify_password,
                       visible_classroom_ids)

log = logging.getLogger("argos.api")
DATA_DIR = Path(os.environ.get("ARGOS_DATA_DIR", "."))
CLIP_DIR = DATA_DIR / "clips"
# Clips are deleted 24 h after reception (security / privacy), checked every 10 min.
RETENTION_HOURS = float(os.environ.get("ARGOS_RETENTION_HOURS", "24"))
PURGE_EVERY_S = float(os.environ.get("ARGOS_PURGE_EVERY_S", "600"))
# Phone camera (test mode): only the latest frame is kept, in memory, never on disk.
FRAME_MAX_BYTES = int(os.environ.get("ARGOS_FRAME_MAX_BYTES", str(2 * 1024 * 1024)))
FRAME_MAX_AGE_S = float(os.environ.get("ARGOS_FRAME_MAX_AGE_S", "3"))
JPEG_MAGIC = bytes([0xFF, 0xD8])
STATUS_MAX_BYTES = 16 * 1024
STATUS_MAX_AGE_S = float(os.environ.get("ARGOS_STATUS_MAX_AGE_S", "10"))
DELETE_ON_DISMISS = os.environ.get("ARGOS_DELETE_ON_DISMISS", "1") == "1"
# Tiers that trigger a notification. Decision of 23 Nov: every detection that is sent (high and
# medium). The original report planned "high" only: ARGOS_NOTIFY_TIERS=high to go back to it.
NOTIFY_TIERS = {t.strip() for t in os.environ.get("ARGOS_NOTIFY_TIERS", "high,medium").split(",") if t.strip()}
WEB_DIR = Path(os.environ.get("ARGOS_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
CATEGORIES = {"possible_phone_use", "persistent_head_down", "possible_conflict"}
# Teacher self-registration: only firstname.lastname@<domain> addresses are accepted.
# Students have @etudiant-enit.utm.tn addresses, which this pattern rejects.
TEACHER_EMAIL_DOMAIN = os.environ.get("ARGOS_TEACHER_EMAIL_DOMAIN", "enit.utm.tn").lower()
_NAME_PART = r"[a-z]+(?:-[a-z]+)*"
TEACHER_EMAIL_RE = re.compile(rf"^({_NAME_PART})\.({_NAME_PART})\d*@{re.escape(TEACHER_EMAIL_DOMAIN)}$")


# ---------------------------------------------------------------- real time
class Hub:
    """Open WebSocket connections, per user."""

    def __init__(self):
        self.conns: dict[WebSocket, list[int] | None] = {}   # None = admin (all classrooms)

    async def add(self, ws: WebSocket, classroom_ids: list[int] | None):
        await ws.accept()
        self.conns[ws] = classroom_ids

    def remove(self, ws: WebSocket):
        self.conns.pop(ws, None)

    async def notify(self, classroom_id: int, message: dict):
        dead = []
        for ws, ids in list(self.conns.items()):
            if ids is not None and classroom_id not in ids:
                continue
            try:
                await ws.send_text(json.dumps(jsonable_encoder(message)))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.remove(ws)


hub = Hub()


# ---------------------------------------------------------------- phone camera (test mode)
class FrameRelay:
    """Latest JPEG frame sent by the phone, per classroom. In memory only:
    nothing is written to disk, each frame replaces the previous one."""

    def __init__(self):
        self.frames: dict[int, tuple[int, float, bytes]] = {}   # classroom -> (sequence, received at, jpeg)

    def put(self, classroom_id: int, jpeg: bytes) -> int:
        seq = self.frames.get(classroom_id, (0, 0.0, b""))[0] + 1
        self.frames[classroom_id] = (seq, time.monotonic(), jpeg)
        return seq

    def get(self, classroom_id: int, after: int, max_age_s: float) -> tuple[int, bytes] | None:
        f = self.frames.get(classroom_id)
        if not f or f[0] <= after or time.monotonic() - f[1] > max_age_s:
            return None
        return f[0], f[2]


relay = FrameRelay()
# Numeric summary of what the device sees (no image), per classroom, in memory only.
live_status: dict[int, tuple[float, dict]] = {}


# ---------------------------------------------------------------- automatic deletion
def purge_expired() -> int:
    db = SessionLocal()
    n = 0
    try:
        now = utcnow()
        for ev in db.query(Event).filter(Event.clip_path.isnot(None), Event.clip_expires_at < now):
            Path(ev.clip_path).unlink(missing_ok=True)
            ev.clip_path = None
            db.add(AccessLog(event_id=ev.id, action="auto_delete", info="durée de conservation dépassée"))
            n += 1
        db.commit()
    finally:
        db.close()
    if n:
        log.info("%d clip(s) supprimé(s)", n)
    return n


async def _purge_loop():
    while True:
        try:
            purge_expired()
        except Exception:
            log.exception("échec de la suppression automatique")
        await asyncio.sleep(PURGE_EVERY_S)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(_purge_loop())
    yield
    task.cancel()


app = FastAPI(title="Argos API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("ARGOS_CORS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])


# ---------------------------------------------------------------- schemas
class EventIn(BaseModel):
    category: str
    confidence: float = Field(ge=0, le=1)
    tier: Literal["high", "medium"]
    track_id: int | None = None
    started_at: datetime | None = None
    detected_at: datetime | None = None
    box: list[float] | None = None
    teacher_facing_board: bool = False
    details: dict = {}


class ReviewIn(BaseModel):
    decision: Literal["confirmed", "dismissed"]


class UserIn(BaseModel):
    username: str
    password: str = Field(min_length=8)
    full_name: str = ""
    role: Literal["teacher", "admin"] = "teacher"
    classroom_ids: list[int] = []


class ClassroomIn(BaseModel):
    name: str


class RegisterIn(BaseModel):
    email: str = Field(max_length=128)
    password: str = Field(min_length=8, max_length=128)


class ClassroomsIn(BaseModel):
    classroom_ids: list[int]


def user_out(u: User) -> dict:
    return {"id": u.id, "username": u.username, "full_name": u.full_name, "role": u.role,
            "classrooms": [{"id": c.id, "name": c.name} for c in u.classrooms]}


def as_utc(d: datetime | None) -> datetime | None:
    """SQLite drops the time zone: dates are stored in UTC (see device_event) and returned
    with +00:00 so the app converts them to local time."""
    if d is None:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def event_out(ev: Event) -> dict:
    return {
        "id": ev.id, "classroom_id": ev.classroom_id, "classroom": ev.classroom.name if ev.classroom else None,
        "category": ev.category, "confidence": ev.confidence, "tier": ev.tier, "track_id": ev.track_id,
        "started_at": as_utc(ev.started_at), "detected_at": as_utc(ev.detected_at), "status": ev.status,
        "reviewed_at": as_utc(ev.reviewed_at), "has_clip": ev.clip_path is not None,
        "teacher_facing_board": (ev.details or {}).get("teacher_facing_board", False),
    }


def _get_visible_event(db: Session, user: User, event_id: int) -> Event:
    ev = db.get(Event, event_id)
    ids = visible_classroom_ids(user)
    if not ev or (ids is not None and ev.classroom_id not in ids):
        raise HTTPException(404, "Événement introuvable")
    return ev


# ---------------------------------------------------------------- authentication
@app.post("/api/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    name = form.username.strip()
    if "@" in name:   # e-mail address: case-insensitive
        user = db.query(User).filter(func.lower(User.username) == name.lower()).first()
    else:
        user = db.query(User).filter_by(username=name).first()
    if not user or not verify_password(form.password, user.password_hash):
        raise HTTPException(401, "Identifiant ou mot de passe incorrect")
    return {"access_token": make_token(user), "token_type": "bearer",
            "user": {"id": user.id, "username": user.username, "full_name": user.full_name, "role": user.role}}


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    """Teacher registration with a firstname.lastname@enit.utm.tn address.

    The account is active but sees no classroom: the admin assigns its classrooms
    (PUT /api/admin/users/{id}/classrooms). Without e-mail verification, this is what stops
    someone from using a colleague's address to see alerts."""
    email = body.email.strip().lower()
    m = TEACHER_EMAIL_RE.match(email)
    if not m:
        raise HTTPException(422, f"Adresse invalide : utilisez prenom.nom@{TEACHER_EMAIL_DOMAIN}")
    if db.query(User).filter(func.lower(User.username) == email).first():
        raise HTTPException(409, "Un compte existe déjà avec cette adresse")
    full_name = " ".join(p.replace("-", " ").title().replace(" ", "-") for p in m.groups())
    u = User(username=email, full_name=full_name, role="teacher", password_hash=hash_password(body.password))
    db.add(u)
    db.commit()
    db.add(AccessLog(user_id=u.id, action="register", info=email))
    db.commit()
    return user_out(u)


@app.get("/api/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


# ---------------------------------------------------------------- Raspberry Pi device
@app.post("/api/device/events", status_code=201)
async def device_event(meta: str = Form(...), clip: UploadFile | None = File(None),
                       room: Classroom = Depends(current_device), db: Session = Depends(get_db)):
    data = EventIn.model_validate_json(meta)
    if data.category not in CATEGORIES:
        raise HTTPException(422, "Catégorie inconnue")
    details = dict(data.details)
    details["teacher_facing_board"] = data.teacher_facing_board
    ev = Event(classroom_id=room.id, category=data.category, confidence=data.confidence, tier=data.tier,
               track_id=data.track_id, started_at=as_utc(data.started_at),
               detected_at=as_utc(data.detected_at) or utcnow(),
               box=data.box, details=details)
    if clip is not None:
        path = CLIP_DIR / f"{uuid.uuid4().hex}.mp4"
        with open(path, "wb") as f:
            while chunk := await clip.read(1 << 20):
                f.write(chunk)
        ev.clip_path = str(path)
        ev.clip_expires_at = utcnow() + timedelta(hours=RETENTION_HOURS)
    db.add(ev)
    db.commit()
    db.refresh(ev)
    payload = event_out(ev)
    await hub.notify(room.id, {"type": "new_event", "notify": ev.tier in NOTIFY_TIERS, "event": payload})
    return {"id": ev.id}


@app.get("/api/device/frame")
def device_frame(after: int = 0, room: Classroom = Depends(current_device)):
    """Phone frame for the device: 204 if there is nothing newer than `after`."""
    f = relay.get(room.id, after, FRAME_MAX_AGE_S)
    if f is None:
        return Response(status_code=204)
    return Response(f[1], media_type="image/jpeg", headers={"X-Frame-Seq": str(f[0])})


@app.post("/api/device/status", status_code=204)
async def device_status(request: Request, room: Classroom = Depends(current_device)):
    body = await request.body()
    if len(body) > STATUS_MAX_BYTES:
        raise HTTPException(413, "Résumé trop grand")
    try:
        data = json.loads(body)
    except ValueError:
        raise HTTPException(422, "JSON attendu")
    if not isinstance(data, dict):
        raise HTTPException(422, "Objet JSON attendu")
    live_status[room.id] = (time.monotonic(), data)
    return Response(status_code=204)


# ---------------------------------------------------------------- teacher
def _own_classroom(user: User, classroom_id: int | None) -> int:
    ids = visible_classroom_ids(user)
    if classroom_id is None:
        if not ids:
            raise HTTPException(422, "Préciser classroom_id" if ids is None else "Aucune classe attribuée")
        return ids[0]
    if ids is not None and classroom_id not in ids:
        raise HTTPException(403, "Classe non autorisée")
    return classroom_id


@app.get("/api/camera/status")
def camera_status(classroom_id: int | None = None, user: User = Depends(current_user)):
    """For the "Camera test" screen: what the device sees right now (204 if nothing recent)."""
    cid = _own_classroom(user, classroom_id)
    st = live_status.get(cid)
    if st is None or time.monotonic() - st[0] > STATUS_MAX_AGE_S:
        return Response(status_code=204)
    return {"age_s": round(time.monotonic() - st[0], 1), **st[1]}


@app.post("/api/camera/frame")
async def camera_frame(request: Request, classroom_id: int | None = None,
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Test mode: the teacher's phone acts as the camera for THEIR classroom."""
    classroom_id = _own_classroom(user, classroom_id)
    st = live_status.get(classroom_id)
    if st and time.monotonic() - st[0] <= STATUS_MAX_AGE_S and st[1].get("source") == "picamera":
        # one camera at a time: the device camera has priority
        raise HTTPException(409, "La caméra du boîtier fonctionne : le téléphone n'est pas utilisé")
    if db.get(Classroom, classroom_id) is None:
        raise HTTPException(404, "Classe inconnue")
    body = await request.body()
    if len(body) > FRAME_MAX_BYTES:
        raise HTTPException(413, "Image trop grande")
    if not body.startswith(JPEG_MAGIC):
        raise HTTPException(415, "JPEG attendu")
    return {"seq": relay.put(classroom_id, body), "classroom_id": classroom_id}


@app.get("/api/events")
def list_events(status: str | None = Query(None), tier: str | None = Query(None),
                classroom_id: int | None = None, limit: int = Query(100, le=500),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Event)
    ids = visible_classroom_ids(user)
    if ids is not None:
        q = q.filter(Event.classroom_id.in_(ids))
    if status:
        q = q.filter(Event.status == status)
    if tier:
        q = q.filter(Event.tier == tier)
    if classroom_id:
        q = q.filter(Event.classroom_id == classroom_id)
    return [event_out(e) for e in q.order_by(Event.detected_at.desc()).limit(limit)]


@app.post("/api/events/{event_id}/review")
async def review(event_id: int, body: ReviewIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    ev = _get_visible_event(db, user, event_id)
    ev.status, ev.reviewed_by, ev.reviewed_at = body.decision, user.id, utcnow()
    db.add(AccessLog(user_id=user.id, event_id=ev.id, action="review", info=body.decision))
    if body.decision == "dismissed" and DELETE_ON_DISMISS and ev.clip_path:
        Path(ev.clip_path).unlink(missing_ok=True)
        ev.clip_path = None
        db.add(AccessLog(user_id=user.id, event_id=ev.id, action="delete_clip", info="alerte rejetée"))
    db.commit()
    await hub.notify(ev.classroom_id, {"type": "event_updated", "notify": False, "event": event_out(ev)})
    return event_out(ev)


@app.get("/api/events/{event_id}/clip")
def get_clip(event_id: int, user: User = Depends(current_user_or_query), db: Session = Depends(get_db)):
    ev = _get_visible_event(db, user, event_id)
    if not ev.clip_path or not Path(ev.clip_path).exists():
        raise HTTPException(404, "Clip non disponible (supprimé ou jamais enregistré)")
    db.add(AccessLog(user_id=user.id, event_id=ev.id, action="view_clip"))
    db.commit()
    return FileResponse(ev.clip_path, media_type="video/mp4")


@app.get("/api/summary")
def summary(hours: float = 24, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Session summary: number of events per category, tier and status."""
    since = utcnow() - timedelta(hours=hours)
    q = db.query(Event.category, Event.tier, Event.status, func.count()).filter(Event.detected_at >= since)
    ids = visible_classroom_ids(user)
    if ids is not None:
        q = q.filter(Event.classroom_id.in_(ids))
    return [{"category": c, "tier": t, "status": s, "count": n}
            for c, t, s, n in q.group_by(Event.category, Event.tier, Event.status)]


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        ids = visible_classroom_ids(user_from_token(token, db))
    except HTTPException:
        await ws.close(code=4401)
        return
    finally:
        db.close()
    await hub.add(ws, ids)
    try:
        while True:
            await ws.receive_text()   # client ping; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(ws)


# ---------------------------------------------------------------- administrator
@app.post("/api/admin/classrooms", status_code=201)
def create_classroom(body: ClassroomIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    import secrets
    key = secrets.token_urlsafe(24)
    room = Classroom(name=body.name, device_key_hash=hash_key(key))
    db.add(room)
    db.commit()
    return {"id": room.id, "name": room.name, "device_key": key,
            "note": "Copiez cette clé dans config/edge.yaml ; elle ne sera plus affichée."}


@app.get("/api/admin/classrooms")
def list_classrooms(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name, "last_seen": c.last_seen,
             "teachers": [t.username for t in c.teachers]} for c in db.query(Classroom)]


@app.post("/api/admin/users", status_code=201)
def create_user(body: UserIn, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    if db.query(User).filter_by(username=body.username).first():
        raise HTTPException(409, "Nom d'utilisateur déjà pris")
    u = User(username=body.username, full_name=body.full_name, role=body.role,
             password_hash=hash_password(body.password))
    u.classrooms = db.query(Classroom).filter(Classroom.id.in_(body.classroom_ids)).all()
    db.add(u)
    db.commit()
    return {"id": u.id, "username": u.username, "role": u.role}


@app.get("/api/admin/users")
def list_users(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.query(User).order_by(User.id)]


@app.put("/api/admin/users/{user_id}/classrooms")
def set_user_classrooms(user_id: int, body: ClassroomsIn, admin: User = Depends(require_admin),
                        db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if not u:
        raise HTTPException(404, "Utilisateur introuvable")
    rooms = db.query(Classroom).filter(Classroom.id.in_(body.classroom_ids)).all()
    if len(rooms) != len(set(body.classroom_ids)):
        raise HTTPException(422, "Classe inconnue")
    u.classrooms = rooms
    db.add(AccessLog(user_id=admin.id, action="set_classrooms",
                     info=f"{u.username}: {', '.join(r.name for r in rooms) or 'aucune'}"))
    db.commit()
    return user_out(u)


@app.get("/api/admin/access-log")
def access_log(event_id: int | None = None, limit: int = Query(200, le=1000),
               _: User = Depends(require_admin), db: Session = Depends(get_db)):
    q = db.query(AccessLog)
    if event_id:
        q = q.filter(AccessLog.event_id == event_id)
    return [{"at": a.at, "user_id": a.user_id, "event_id": a.event_id, "action": a.action, "info": a.info}
            for a in q.order_by(AccessLog.at.desc()).limit(limit)]


# ---------------------------------------------------------------- web app (Flutter build)
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
