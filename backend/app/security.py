"""JWT authentication, passwords (bcrypt) and server-side role checks."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .db import Classroom, SessionLocal, User, utcnow

def _load_secret() -> str:
    if os.environ.get("ARGOS_SECRET"):
        return os.environ["ARGOS_SECRET"]
    p = Path(os.environ.get("ARGOS_DATA_DIR", ".")) / ".argos_secret"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(secrets.token_urlsafe(48))
        p.chmod(0o600)
    return p.read_text().strip()


SECRET = _load_secret()
TOKEN_HOURS = int(os.environ.get("ARGOS_TOKEN_HOURS", "12"))
oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bcrypt.checkpw(p.encode(), h.encode())


def hash_key(k: str) -> str:
    return hashlib.sha256(k.encode()).hexdigest()


def make_token(user: User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS)
    return jwt.encode({"sub": str(user.id), "role": user.role, "exp": exp}, SECRET, algorithm="HS256")


def user_from_token(token: str | None, db: Session) -> User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Non authentifié")
    try:
        data = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Jeton invalide ou expiré")
    user = db.get(User, int(data["sub"]))
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Utilisateur inconnu")
    return user


def current_user(token: str | None = Depends(oauth2), db: Session = Depends(get_db)) -> User:
    return user_from_token(token, db)


def current_user_or_query(token: str | None = Depends(oauth2), t: str | None = Query(None),
                          db: Session = Depends(get_db)) -> User:
    """For video: the web player cannot send headers, so ?t=<token> is accepted."""
    return user_from_token(token or t, db)


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Réservé à l'administrateur")
    return user


def current_device(x_device_key: str = Header(...), db: Session = Depends(get_db)) -> Classroom:
    room = db.query(Classroom).filter_by(device_key_hash=hash_key(x_device_key)).first()
    if not room:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clé de boîtier inconnue")
    room.last_seen = utcnow()
    db.commit()
    return room


def visible_classroom_ids(user: User) -> list[int] | None:
    """None = all (admin)."""
    return None if user.role == "admin" else [c.id for c in user.classrooms]
