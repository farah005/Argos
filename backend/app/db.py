"""SQLite database (SQLAlchemy). To move to PostgreSQL, change ARGOS_DB_URL."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, Column, DateTime, Float, ForeignKey, Integer, String, Table, Text,
                        create_engine)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

DB_URL = os.environ.get("ARGOS_DB_URL", "sqlite:///./argos.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False} if DB_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


teacher_classroom = Table(
    "teacher_classroom", Base.metadata,
    Column("user_id", ForeignKey("users.id"), primary_key=True),
    Column("classroom_id", ForeignKey("classrooms.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False)
    full_name = Column(String(128), default="")
    password_hash = Column(String(128), nullable=False)
    role = Column(String(16), nullable=False)          # "teacher" | "admin"
    classrooms = relationship("Classroom", secondary=teacher_classroom, back_populates="teachers")


class Classroom(Base):
    __tablename__ = "classrooms"
    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)
    device_key_hash = Column(String(128), nullable=False)
    last_seen = Column(DateTime(timezone=True))
    teachers = relationship("User", secondary=teacher_classroom, back_populates="classrooms")


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    classroom_id = Column(ForeignKey("classrooms.id"), nullable=False, index=True)
    category = Column(String(32), nullable=False)
    confidence = Column(Float, nullable=False)
    tier = Column(String(8), nullable=False)            # high | medium
    track_id = Column(Integer)                          # session ID, not an identity
    started_at = Column(DateTime(timezone=True))
    detected_at = Column(DateTime(timezone=True), default=utcnow, index=True)
    received_at = Column(DateTime(timezone=True), default=utcnow)
    box = Column(JSON)
    details = Column(JSON)
    status = Column(String(16), default="pending", index=True)   # pending | confirmed | dismissed
    reviewed_by = Column(ForeignKey("users.id"))
    reviewed_at = Column(DateTime(timezone=True))
    clip_path = Column(Text)
    clip_expires_at = Column(DateTime(timezone=True))
    classroom = relationship("Classroom")


class AccessLog(Base):
    __tablename__ = "access_log"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime(timezone=True), default=utcnow)
    user_id = Column(ForeignKey("users.id"))            # None = system action
    event_id = Column(ForeignKey("events.id"))
    action = Column(String(32), nullable=False)         # view_clip | review | auto_delete | ...
    info = Column(Text, default="")


def init_db() -> None:
    Base.metadata.create_all(engine)
